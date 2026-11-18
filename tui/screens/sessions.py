# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : sessions.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 16:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : TUI会话注册表（SQLite读写穿透；thread活性归 core.agent.persistence，表现层不再判）

import time
from typing import Optional

from service.session.approvals import clear_session
from service.session.session_service import session_service
from service.user.user_service import get_current_ctx
from tui.i18n import t

MAX_SESSIONS = 20  # 选择弹窗展示上限
TITLE_MAX = 30     # 会话标题字数上限（汉字按字符计）
CACHE_LIMIT = 200  # 进程内会话缓存上限；只裁剪缓存，绝不删库

SESSIONS: list["SessionEntry"] = []
_loaded = False

class SessionEntry(dict):
    """ {id,title,model,thinking,thinking_level,context_tokens,context_limit,project_id,project_name,project_path} """

def _uid() -> str:
    return get_current_ctx().uid

def reset_session_cache() -> None:
    """身份切换后必须调用：SESSIONS里残留着上一身份的会话，不清会串号"""
    global _loaded

    SESSIONS.clear()
    _loaded = False

def _load() -> None:
    global SESSIONS, _loaded
    if _loaded:
        return

    SESSIONS = [
        SessionEntry({"id": r["id"], "title": r["title"], "model": r["model"], "thinking": r["thinking"],
                      "thinking_level": r.get("thinking_level"),
                      "updated_at": r.get("updated_at", 0), "tokens_in": r.get("tokens_in", 0), "tokens_out": r.get("tokens_out", 0),
                      "speed": r.get("speed", 0.0), "context_tokens": r.get("context_tokens", 0),
                      "context_limit": r.get("context_limit", 0),
                      "project_id": r.get("project_id"), "project_name": r.get("project_name"),
                      "project_path": r.get("project_path")})
        for r in session_service.list_sessions(CACHE_LIMIT, _uid())
    ]
    _loaded = True

def list_sessions() -> list[SessionEntry]:
    _load()
    return SESSIONS

def current_project() -> Optional[dict]:
    """当前目录登记的项目；没开通项目功能或查询失败时返回 None。

    TUI 是目录级工具：在项目目录里新建的会话直接属于该项目，否则用户每次都要
    先建项目再把会话转进去。
    """
    try:
        if not get_current_ctx().can_projects:
            return None
        from .projects import project_at_current
        return project_at_current(_uid())
    except Exception:
        # 项目查询失败不该拦住建会话：退化成无项目会话
        return None

def create_session(title: str = "新对话", model: Optional[str]=None,
                   thinking_level: Optional[str]=None) -> SessionEntry:
    _load()
    project = current_project()

    # 键由服务层签发（它同时就是 agent 的 thread_id），TUI 不自己造 uuid
    session_id = session_service.create(title=title[:TITLE_MAX], model=model,
                                        thinking_level=thinking_level,
                                        user_id=_uid(), project_id=project["id"] if project else None)
    entry = SessionEntry({"id": session_id, "title": title[:TITLE_MAX], "model": model,
                          "thinking": None if thinking_level is None else thinking_level != "off",
                          "thinking_level": thinking_level,
                          "updated_at": int(time.time()), "tokens_in": 0, "tokens_out": 0, "speed": 0.0,
                          "context_tokens": 0, "context_limit": 0,
                          "project_id": project["id"] if project else None,
                          "project_name": project["name"] if project else None,
                          "project_path": project["path"] if project else None})

    SESSIONS.insert(0, entry)

    # 只裁剪进程内缓存，历史会话永久保留在SQLite，删除仅能通过用户手动delete_session
    if len(SESSIONS) > CACHE_LIMIT:
        SESSIONS.pop()

    return entry

def join_project(entry: SessionEntry, project: dict) -> bool:
    """把会话转入项目（无项目 → 项目），返回是否真的改了。

    与 Web 的 `PATCH /sessions/{id}/project` 同口径：归属变了就清掉旧上下文里
    批准过的路径，否则旧会话的“总是允许”会被带进新项目。
    """
    uid = _uid()
    status = session_service.set_session_project(entry["id"], project["id"], uid)
    if status not in ("ok", "unchanged"):
        return False
    if status == "ok":
        clear_session(uid, entry["id"])
    entry["project_id"] = project["id"]
    entry["project_name"] = project["name"]
    entry["project_path"] = project["path"]
    return status == "ok"


_UNSET = object()


def update_session(entry: SessionEntry, title: Optional[str]=None, model: Optional[str]=None,
                   thinking_level=_UNSET) -> None:
    """只更新本进程缓存。

    会话行的写入归服务层记录器（`service.session.recorder`）：表现层再 save 一次就是第二个写入者，
    两处口径迟早会漂。缓存留着是因为选择弹窗与切换会话都要读它，那是界面状态不是事实。
    """
    if title:
        entry["title"] = title[:TITLE_MAX]
    if model is not None:
        entry["model"] = model
    if thinking_level is not _UNSET:
        # None 是合法取值（auto）：不能用"没传"和"设为 auto"共用一个哨兵
        entry["thinking_level"] = thinking_level
        entry["thinking"] = None if thinking_level is None else thinking_level != "off"
    entry["updated_at"] = int(time.time())

def delete_session(entry: SessionEntry) -> None:
    if entry in SESSIONS:
        SESSIONS.remove(entry)
    session_service.delete(entry["id"], _uid())

async def delete_session_wait(entry: SessionEntry) -> bool:
    """异步安全删除：等在途 ChatService 释放 checkpoint 租约后再清理。"""
    deleted = await session_service.delete_wait(entry["id"], _uid())
    if deleted and entry in SESSIONS:
        SESSIONS.remove(entry)
    return deleted

def get_messages(session_id: str, limit: int=20, before_id: Optional[int]=None) -> list[dict]:
    return session_service.get_messages(session_id, limit, _uid(), before_id)

def count_messages(session_id: str, before_id: Optional[int]=None) -> int:
    return session_service.count_messages(session_id, _uid(), before_id)

def get_history(session_id: str, limit: Optional[int]=20) -> list[dict]:
    return session_service.get_history(session_id, limit, _uid())
