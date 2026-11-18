# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : subagent_gate.py
# @Description : 子代理并发闸门与单轮预算

"""子代理的并发闸门。

规则（见 docs/superpowers/specs/2026-11-15-子代理-design.md §9）：

- 全局同时最多 3 个子代理；
- `general` 每会话最多 1 个（跨会话互不阻塞）；
- 单轮 `task` 调用上限 6，按 `tool_call_id` 去重——interrupt-resume 会把同一次调用
  重新走一遍，不能因此多算；
- 活跃段最长 5 分钟；排队超过 60 秒返回工具错误文本，让主模型改走串行或自己做。

状态是进程级的：`Agent`（以及它的中间件）每轮重建，闸门不能跟着重建，
否则并发上限形同虚设。
"""

import asyncio
from typing import Any, Awaitable, Callable, Optional

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

GLOBAL_LIMIT = 3
GENERAL_LIMIT_PER_SESSION = 1
TURN_LIMIT = 6
ACTIVE_TIMEOUT_SECONDS = 300.0
QUEUE_TIMEOUT_SECONDS = 60.0
GENERAL_SPEC_NAME = "general-purpose"

# 信号量绑定事件循环（TUI/API 可能是不同 loop），按 loop id 分开存放
_global_semaphores: dict[int, asyncio.Semaphore] = {}
_session_semaphores: dict[tuple[int, str], asyncio.Semaphore] = {}
# 会话 -> 本轮已计过的 tool_call_id（有序、限长，只用于去重与计数）
_counted: dict[str, list[str]] = {}
MAX_TRACKED_IDS = 200


def reset_turn(session_id: str) -> None:
    """新一轮对话开始时清空预算；resume 不属于新的一轮，不能调这个。"""
    _counted.pop(session_id, None)


def count_call(session_id: str, call_id: str) -> bool:
    """记一次 `task` 调用并判断是否超预算；同一 id 重复进入（resume）只算一次。"""
    seen = _counted.setdefault(session_id, [])
    key = call_id or "*"
    if call_id and call_id in seen:
        return True
    if len(seen) >= TURN_LIMIT:
        return False
    seen.append(key)
    if len(seen) > MAX_TRACKED_IDS:
        del seen[: len(seen) - MAX_TRACKED_IDS]
    return True


def _loop_key() -> int:
    return id(asyncio.get_running_loop())


def _global_semaphore() -> asyncio.Semaphore:
    key = _loop_key()
    sem = _global_semaphores.get(key)
    if sem is None:
        sem = _global_semaphores[key] = asyncio.Semaphore(GLOBAL_LIMIT)
    return sem


def _general_semaphore(session_id: str) -> asyncio.Semaphore:
    key = (_loop_key(), session_id)
    sem = _session_semaphores.get(key)
    if sem is None:
        sem = _session_semaphores[key] = asyncio.Semaphore(GENERAL_LIMIT_PER_SESSION)
    return sem


async def _acquire(sem: asyncio.Semaphore) -> bool:
    try:
        await asyncio.wait_for(sem.acquire(), QUEUE_TIMEOUT_SECONDS)
        return True
    except asyncio.TimeoutError:
        return False


def _error(call_id: str, text: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id or "", status="error")


class SubagentGateMiddleware(AgentMiddleware):
    """把 `task` 的工具调用收进闸门；其他工具原样放行。"""

    name = "subagent_gate"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    def wrap_tool_call(self, request: Any,
                       handler: Callable[[Any], ToolMessage]) -> ToolMessage:
        # 同步路径不经过闸门：NovaBot 的对话链路一律走异步（astream/ainvoke）
        return handler(request)

    async def awrap_tool_call(
        self, request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        call = getattr(request, "tool_call", None) or {}
        if call.get("name") != "task":
            return await handler(request)

        call_id = str(call.get("id") or "")
        args = call.get("args") or {}
        role = str(args.get("subagent_type") or "")

        if not count_call(self.session_id, call_id):
            return _error(
                call_id,
                f"本轮已派发 {TURN_LIMIT} 次子代理，超出上限；剩下的工作请自己完成，"
                f"或留到下一轮再派。",
            )

        held: list[asyncio.Semaphore] = []
        global_sem = _global_semaphore()
        if not await _acquire(global_sem):
            return _error(call_id, "子代理排队超过 60 秒，请改为串行派发或自己完成。")
        held.append(global_sem)

        if role == GENERAL_SPEC_NAME:
            session_sem = _general_semaphore(self.session_id)
            if not await _acquire(session_sem):
                global_sem.release()
                held.pop()
                return _error(call_id, "本会话已有执行子代理在运行，请等它结束后再派。")
            held.append(session_sem)

        try:
            return await asyncio.wait_for(handler(request), ACTIVE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return _error(call_id, "子代理执行超过 5 分钟，已中止；请缩小任务范围后重试。")
        finally:
            for sem in held:
                sem.release()
