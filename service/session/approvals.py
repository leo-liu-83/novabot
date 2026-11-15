# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : approvals.py
# @Description : 会话级“总是允许”放行表（内存态、按 uid+session 隔离）

"""会话确认的“总是允许”记录。

口径（spec §11）：
- 只影响当前 `(uid, session_id)`；进程重启清空，不落库、不写配置；
- 三类作用域：工具+目录、路径+读写操作、具体命令字符串；
- 记的是“具体对象”，不是“以后都别问”：一次批准不能升格成整盘可写。

为什么放服务层：会话删除/过户/转移都要清空它，而清除方在 service 层。
本模块刻意不 import 任何 service，避免环。
"""

import os
import threading
from dataclasses import dataclass, field
from typing import Iterable, Optional

WRITE_TOOLS = ("write_file", "edit_file", "delete")
READ_PATH_TOOLS = ("read_file",)
BULK_READ_TOOLS = ("ls", "glob", "grep")


@dataclass
class _Scope:
    path: str
    operations: frozenset[str]
    subtree: bool


@dataclass
class _Session:
    # (tool, scope.path) -> scope
    tools: dict[tuple[str, str], _Scope] = field(default_factory=dict)
    # scope.path -> scope
    paths: dict[str, _Scope] = field(default_factory=dict)
    commands: set[str] = field(default_factory=set)


_lock = threading.Lock()
_sessions: dict[tuple[str, str], _Session] = {}


def _key(path: str) -> str:
    """统一成 realpath + normcase：比较前先消掉符号链接与大小写差异。"""
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(path or ""))))


def _within(child: str, root: str) -> bool:
    """按路径段判断子树，不做字符串前缀匹配（/tmp/a 不能命中 /tmp/ab）。"""
    return child == root or child.startswith(root.rstrip(os.sep) + os.sep)


def _session(uid: str, session_id: str, *, create: bool = False) -> Optional[_Session]:
    key = (uid, session_id)
    found = _sessions.get(key)
    if found is None and create:
        found = _Session()
        _sessions[key] = found

    return found


def allow_tool(uid: str, session_id: str, tool: str, scope_path: str,
               operations: Iterable[str] = ("read", "write")) -> None:
    """允许某工具在某个目录子树内不再逐次确认。"""
    path = _key(scope_path)
    scope = _Scope(path=path, operations=frozenset(operations), subtree=True)
    with _lock:
        _session(uid, session_id, create=True).tools[(tool, path)] = scope


def is_tool_allowed(uid: str, session_id: str, tool: str, path: str) -> bool:
    """该工具的调用是否已被“总是允许”覆盖；写工具只认 write 操作。"""
    target = _key(path)
    with _lock:
        session = _session(uid, session_id)
        if session is None:
            return False

        for (allowed_tool, _), scope in session.tools.items():
            if allowed_tool != tool or "write" not in scope.operations:
                continue
            if _within(target, scope.path):
                return True

        return False


def allow_path(uid: str, session_id: str, path: str,
               operations: Iterable[str] = ("read",)) -> None:
    """允许一个路径（文件或目录）的指定操作；目录按子树生效。"""
    key = _key(path)
    scope = _Scope(path=key, operations=frozenset(operations),
                   subtree=os.path.isdir(key))
    with _lock:
        _session(uid, session_id, create=True).paths[key] = scope


def is_path_allowed(uid: str, session_id: str, path: str, operation: str) -> bool:
    target = _key(path)
    with _lock:
        session = _session(uid, session_id)
        if session is None:
            return False

        for scope in session.paths.values():
            if operation not in scope.operations:
                continue
            if target == scope.path or (scope.subtree and _within(target, scope.path)):
                return True

        return False


def allow_command(uid: str, session_id: str, command: str) -> None:
    text = (command or "").strip()
    if not text:
        return
    with _lock:
        _session(uid, session_id, create=True).commands.add(text)


def is_command_allowed(uid: str, session_id: str, command: str) -> bool:
    text = (command or "").strip()
    with _lock:
        session = _session(uid, session_id)
        return bool(session and text and text in session.commands)


def path_allow_rules(uid: str, session_id: str) -> list[tuple[str, tuple[str, ...]]]:
    """转成权限层规则需要的 (path_pattern, operations) 列表。

    目录同时产出自身与 `/**`：前者匹配目录本身，后者匹配其下所有文件。
    """
    with _lock:
        session = _session(uid, session_id)
        if session is None:
            return []

        rules: list[tuple[str, tuple[str, ...]]] = []
        for scope in session.paths.values():
            ops = tuple(sorted(scope.operations))
            rules.append((scope.path, ops))
            if scope.subtree:
                rules.append((f"{scope.path}/**", ops))

        return rules


def clear_session(uid: str, session_id: str) -> None:
    with _lock:
        _sessions.pop((uid, session_id), None)


def clear_all() -> None:
    """仅供测试与进程级重置。"""
    with _lock:
        _sessions.clear()


# ── 从 interrupt 动作推导作用域 ─────────────────────────────────

def _arg_path(args: dict) -> str:
    """工具参数里的路径字段：文件工具用 file_path，批量工具用 path。"""
    return str((args or {}).get("file_path") or (args or {}).get("path") or "")


def scope_for_write(path: str, project_root: Optional[str]) -> str:
    """写工具的作用域根：项目内=项目根，项目外=该文件的父目录。"""
    target = _key(path)
    if project_root:
        root = _key(project_root)
        if _within(target, root):
            return root

    parent = os.path.dirname(target)
    return parent or target


def derive_approval(tool: str, args: dict,
                    project_root: Optional[str]) -> Optional[dict]:
    """从一次确认动作推导“总是允许”的作用域；推不出来返回 None。

    只认服务端手里的工具名与参数，不接受前端传来的路径。
    """
    if not project_root:
        # P2 只在项目会话提供 always；非项目会话按普通 approve 处理
        return None

    if tool == "execute":
        command = str((args or {}).get("command") or "").strip()
        return {"kind": "command", "command": command} if command else None

    path = _arg_path(args)
    if not path:
        return None

    if tool in WRITE_TOOLS:
        return {
            "kind": "tool",
            "tool": tool,
            "scope": scope_for_write(path, project_root),
            "operations": ["write"],
        }

    if tool == "read_file":
        from service.project.sensitive import is_sensitive

        return {
            "kind": "path",
            "path": _key(path),
            "operations": ["read"],
            "sensitive": is_sensitive(path, project_root),
        }

    if tool in BULK_READ_TOOLS:
        return {"kind": "path", "path": _key(path), "operations": ["read"],
                "sensitive": False}

    return None


def record_always(uid: str, session_id: str, actions: Iterable[dict],
                  project_root: Optional[str]) -> int:
    """把一次 always 决策记到放行表；返回实际记录的条数。"""
    recorded = 0
    for action in actions or ():
        tool = str((action or {}).get("tool") or "")
        args = (action or {}).get("args") or {}
        approval = derive_approval(tool, args, project_root)
        if approval is None:
            continue

        kind = approval["kind"]
        if kind == "tool":
            allow_tool(uid, session_id, approval["tool"], approval["scope"],
                       approval["operations"])
        elif kind == "path":
            allow_path(uid, session_id, approval["path"], approval["operations"])
        elif kind == "command":
            allow_command(uid, session_id, approval["command"])
        else:
            continue

        recorded += 1

    return recorded
