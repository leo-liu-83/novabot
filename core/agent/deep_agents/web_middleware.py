# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : web_middleware.py
# @Software    : VSCode
# @Datetime    : 2026/10/14 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : webfetch的三态权限边界（E8.3）：ask逐URL确认（白名单免确认），deny工具不可见也不可调用

from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.types import interrupt

from service.web.web_policy import host_allowed
from .hitl import is_approved

WEBFETCH_TOOL = "webfetch"

_DENY_MESSAGE = (
    "[web fetch] webfetch 已被配置禁用（web.fetch.permission=deny）。"
    "请基于 websearch 结果完成任务；确需抓取请让用户调整配置。"
)
_REFUSE_MESSAGE = "[web fetch] 用户拒绝了对该地址的抓取。请改用其他来源，不要重试同一URL。"


def _needs_confirm(permission: str, allowlist: tuple, url: str) -> bool:
    if permission != "ask":
        return False

    host = urlparse(url or "").hostname or ""
    return not host_allowed(host, allowlist)


class WebFetchPolicyMiddleware(AgentMiddleware[Any, Any, Any]):
    """ask时逐URL弹确认——interrupt_on是静态按工具打断的，白名单免确认必须在调用点判定。

    payload与resume形状对齐langchain HITL中间件（action_requests/decisions），
    复用TUI与API现成的确认流，不新建第二套交互。
    """

    def __init__(self, permission: str, allowlist: tuple = ()) -> None:
        self.permission = permission
        self.allowlist = tuple(allowlist or ())

    @staticmethod
    def _tool_name(tool: Any) -> str:
        if isinstance(tool, dict):
            return str(tool.get("name") or tool.get("function", {}).get("name") or "")

        return str(getattr(tool, "name", "") or "")

    def _strip_denied(self, tools: list[Any]) -> list[Any]:
        if self.permission != "deny":
            return tools

        return [t for t in tools if self._tool_name(t) != WEBFETCH_TOOL]

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(request.override(tools=self._strip_denied(request.tools)))

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        return await handler(request.override(tools=self._strip_denied(request.tools)))

    def _refusal(self, request: Any) -> ToolMessage | None:
        tool_call = getattr(request, "tool_call", None) or {}
        if str(tool_call.get("name", "")) != WEBFETCH_TOOL:
            return None

        args = tool_call.get("args") or {}
        url = str(args.get("url", ""))

        if self.permission == "deny":
            return ToolMessage(content=_DENY_MESSAGE, tool_call_id=tool_call["id"], status="error")

        if _needs_confirm(self.permission, self.allowlist, url):
            decision = interrupt({
                "action_requests": [{
                    "name": WEBFETCH_TOOL,
                    "args": {"url": url},
                    "description": f"允许抓取 {url} ？",
                }],
            })
            if not is_approved(decision):
                return ToolMessage(content=_REFUSE_MESSAGE, tool_call_id=tool_call["id"], status="error")

        return None

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], ToolMessage | Command[Any]]) -> ToolMessage | Command[Any]:
        refusal = self._refusal(request)
        return refusal if refusal else handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[ToolMessage | Command[Any]]]) -> ToolMessage | Command[Any]:
        refusal = self._refusal(request)
        return refusal if refusal else await handler(request)
