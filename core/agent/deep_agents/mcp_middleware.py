# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : mcp_middleware.py
# @Software    : VSCode
# @Author      : leo liu
# @Version     : 1.0
# @Description : MCP工具的三态权限边界（E9.2.2）：逐工具ask在调用点判，allow直接放行，deny不可见也不可调用

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.types import interrupt

from .hitl import is_approved

MCP_CALL_TOOL = "mcp_call"

_DENY_MESSAGE = (
    "[mcp] 该工具已被设为不用（deny）：{name}。"
    "请改用其他MCP工具，或让用户到 /mcp 调整工具权限。"
)
_REFUSE_MESSAGE = "[mcp] 用户拒绝了对 {name} 的调用。请改用其他工具，不要重试同一调用。"


class McpToolPolicyMiddleware(AgentMiddleware[Any, Any, Any]):
    """逐工具判 allow/ask/deny。

    延迟发现形态下所有MCP调用都走同一个 mcp_call 元工具，interrupt_on 只能按
    工具名静态挂——那样 allow 的工具也会被一起问。模式按 (server, tool) 在调用点判，
    payload与resume形状对齐langchain HITL中间件（action_requests/decisions），
    复用TUI与API现成的确认流。
    """

    def __init__(self, modes: dict[tuple[str, str], str]) -> None:
        self.modes = dict(modes or {})

    @staticmethod
    def _target(tool_call: dict) -> tuple[str, str, dict]:
        args = tool_call.get("args") or {}
        return (str(args.get("server", "")), str(args.get("tool", "")),
                dict(args.get("arguments") or {}))

    def _refusal(self, request: Any) -> ToolMessage | None:
        tool_call = getattr(request, "tool_call", None) or {}
        if str(tool_call.get("name", "")) != MCP_CALL_TOOL:
            return None

        server, tool, arguments = self._target(tool_call)
        mode = self.modes.get((server, tool))
        # 没登记的工具（不存在/被裁掉）交给mcp_call自己报错，别在这里拦成权限问题
        if mode is None or mode == "allow":
            return None

        label = f"{server}.{tool}"
        if mode == "deny":
            return ToolMessage(content=_DENY_MESSAGE.format(name=label),
                               tool_call_id=tool_call["id"], status="error")

        decision = interrupt({
            "action_requests": [{
                "name": f"mcp:{label}",
                "args": arguments,
                "description": f"允许调用 MCP 工具 {label} ？",
            }],
        })
        if not is_approved(decision):
            return ToolMessage(content=_REFUSE_MESSAGE.format(name=label),
                               tool_call_id=tool_call["id"], status="error")

        return None

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], ToolMessage | Command[Any]]) -> ToolMessage | Command[Any]:
        refusal = self._refusal(request)
        return refusal if refusal else handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[ToolMessage | Command[Any]]]) -> ToolMessage | Command[Any]:
        refusal = self._refusal(request)
        return refusal if refusal else await handler(request)
