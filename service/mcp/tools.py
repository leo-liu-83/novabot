# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : tools.py
# @Software    : VSCode
# @Author      : leo liu
# @Version     : 1.0
# @Description : MCP工具层：调用远端工具，返回值按"不可信外部内容"包装

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from service.mcp.catalog import McpTool
from service.mcp.mcp_client import MCPError, McpSession
from service.web.external_content import cap_output, wrap_external_content

# 单次MCP工具返回的字符上限。工具可能返回结构化数据（列表、页面正文），
# 量级比搜索结果大一些；但不设上限的话，一次调用就能吃掉大半个上下文
MCP_OUTPUT_CHAR_LIMIT = 20_000

# 会话缓存：agent每个请求重建，握手一次约1~2秒，同一server不该反复握手
SESSION_TTL_SECONDS = 300
CONNECT_TIMEOUT_SECONDS = 20

_sessions: dict[tuple[str, str, str], tuple[float, McpSession]] = {}


def session_for(uid: str, server: dict) -> McpSession:
    """取（或建立）一个MCP会话，按(身份,名称,URL)缓存复用。"""
    key = (uid, server["name"], server["url"])
    hit = _sessions.get(key)
    if hit and time.time() - hit[0] < SESSION_TTL_SECONDS:
        return hit[1]

    session = McpSession(server["url"], headers=server.get("headers") or {},
                         timeout=CONNECT_TIMEOUT_SECONDS)
    session.initialize()
    _sessions[key] = (time.time(), session)

    return session


def invalidate_session(uid: str, server: dict) -> None:
    """作废缓存条目：服务端可能已丢弃该会话，下次调用前重新握手。"""
    _sessions.pop((uid, server["name"], server["url"]), None)

# JSON Schema 类型 → Python 类型。取不到就按Any处理（宽松接受，不因类型猜错而调用失败）
_JSON_TYPES = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class McpCaller(Protocol):
    """工具层只需要"能调一个工具"这一件事，不绑死具体会话实现"""

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> str: ...


def wrap_mcp_result(text: str, server: str, tool: str, *,
                    log: Optional[Callable[[str], None]] = None) -> str:
    """MCP工具的返回值是外部不可信内容：按成对随机标记包裹，并嗅探注入措辞（只记日志不阻断）。

    与websearch/webfetch同一套包装——工具返回值同样可能携带"忽略之前的指令"这类内容，
    模型必须当数据看，不能当指令。
    """
    return wrap_external_content(text, f"MCP {server}.{tool}", include_warning=True, log=log)


def json_schema_to_model(schema: dict, name: str = "McpArgs") -> type[BaseModel]:
    """把MCP的inputSchema(JSON Schema)转成pydantic模型，供LangChain工具声明参数。"""
    properties = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])

    fields: dict[str, Any] = {}
    for key, spec in properties.items():
        if not isinstance(spec, dict):
            spec = {}
        ann = _JSON_TYPES.get(str(spec.get("type") or ""), Any)
        desc = str(spec.get("description") or "")
        if key in required:
            fields[key] = (ann, Field(..., description=desc))
        else:
            fields[key] = (ann, Field(default=None, description=desc))

    return create_model(name, **fields)


def build_mcp_tool(tool: McpTool, session: McpCaller, *,
                   log: Optional[Callable[[str], None]] = None,
                   on_error: Optional[Callable[[], None]] = None) -> StructuredTool:
    """把一个MCP工具包成可调用的LangChain工具（原生直挂形态）。

    - 名字用归一化后的 server_tool（与OpenCode的命名约定一致）
    - 说明用预算截断后的版本（为空时给个占位，不能给模型空说明）
    - **返回值统一按不可信外部内容包装**——这是MCP工具与本地文件工具最本质的差别
    """
    schema = json_schema_to_model(tool.input_schema, name="McpArgs")
    description = tool.description or f"{tool.server} 的 {tool.name} 工具（服务端未提供说明）"

    def _call(**kwargs: Any) -> str:
        # 只传实际给了的参数：可选字段填None会让MCP server拒收（"acquired_after": must be a string）
        provided = {k: v for k, v in kwargs.items() if v is not None}
        try:
            raw = session.call_tool(tool.name, provided)
        except MCPError:
            # 不自动重试（工具可能有副作用，重复执行不可接受）；
            # 只作废缓存条目，让下次调用前重新握手，由模型自己决定要不要再试
            if on_error is not None:
                on_error()
            raise
        # 先截断再包装：包装后截会切掉闭合标记，破坏"外部内容"边界
        raw = cap_output(raw, MCP_OUTPUT_CHAR_LIMIT)
        return wrap_mcp_result(raw, tool.server, tool.name, log=log)

    return StructuredTool.from_function(
        _call,
        name=tool.qualified_name,
        description=description,
        args_schema=schema,
    )


class McpToolset:
    """一组MCP工具及其会话。

    持有调用计数——调用次数上限（E9.3 ③）落在这里。
    """

    def __init__(self) -> None:
        self.sessions: dict[str, McpCaller] = {}
        self.tools: list[StructuredTool] = []
        self.calls = 0

    def add_session(self, name: str, session: McpCaller) -> None:
        self.sessions[name] = session

    def add_tool(self, tool: StructuredTool) -> None:
        self.tools.append(tool)


# ── 延迟发现形态：只暴露"查工具"与"调用"两个工具 ──────────────────
# 原生直挂时每个MCP工具的inputSchema都常驻提示词（实测keenable两个工具就4105字符schema），
# 工具一多就把上下文吃掉。这里改成：提示词里只有两个工具，
# 模型先用mcp_list按需取工具与schema，再用mcp_call调用。

# 单次list返回的工具数上限：模型通常只需要一两个，别把整个目录倒给它
MCP_LIST_MAX_TOOLS = 5
# 单个工具在list输出里schema的字符上限
MCP_LIST_SCHEMA_CHARS = 1500


@dataclass
class McpEntry:
    """一个可调用的MCP工具（server + 工具名 + schema + 会话）"""
    server: str
    name: str
    description: str
    input_schema: dict
    session: McpCaller
    mode: str = "ask"

    @property
    def qualified_name(self) -> str:
        return f"{self.server}.{self.name}"


def _match_entries(entries: list, query: str) -> list:
    q = (query or "").strip().lower()
    if not q:
        return list(entries)

    hits = [e for e in entries
            if q in e.name.lower() or q in e.server.lower() or q in (e.description or "").lower()]
    if not hits:
        # 没命中就退回按server名匹配，再不行返回空让模型换词
        hits = [e for e in entries if q in e.server.lower()]

    return hits


def _format_entry(entry: McpEntry) -> str:
    schema = json.dumps(entry.input_schema or {}, ensure_ascii=False)
    if len(schema) > MCP_LIST_SCHEMA_CHARS:
        schema = schema[:MCP_LIST_SCHEMA_CHARS] + "…（已截断）"

    return (f"[{entry.server}] {entry.name}\n"
            f"  说明: {entry.description or '(无)'}\n"
            f"  参数schema: {schema}\n"
            f"  调用方式: mcp_call(server=\"{entry.server}\", tool=\"{entry.name}\", arguments={{...}})")


def build_mcp_list_tool(entries: list) -> StructuredTool:
    """查工具：按关键词列出MCP工具及其参数schema（延迟发现的第一跳）"""
    def _list(query: str = "") -> str:
        if not entries:
            return "当前没有可用的MCP工具。"

        hits = _match_entries(entries, query)
        if not hits:
            names = sorted({e.server for e in entries})
            return f"没有匹配的工具。当前可用的server: {', '.join(names)}"

        shown = hits[:MCP_LIST_MAX_TOOLS]
        body = "\n\n".join(_format_entry(e) for e in shown)
        if len(hits) > len(shown):
            body += f"\n\n（还有 {len(hits) - len(shown)} 个匹配项未列出，可用更精确的关键词再查）"

        return body

    return StructuredTool.from_function(
        _list,
        name="mcp_list",
        description=("列出可用的MCP工具及其参数schema。先用它找到工具，再用 mcp_call 调用。"
                     "query可按工具名/说明/服务名过滤；留空则列出全部。"),
        args_schema=create_model("McpListArgs", query=(str, Field(default="", description="关键词，可留空"))),
    )


def build_mcp_call_tool(entries: list, *,
                        log: Optional[Callable[[str], None]] = None,
                        on_error: Optional[Callable[[], None]] = None,
                        on_used: Optional[Callable[[str, str], None]] = None) -> StructuredTool:
    """调用工具：按server+工具名分发（延迟发现的第二跳）"""
    def _call(server: str, tool: str, arguments: Optional[dict] = None) -> str:
        target = next((e for e in entries
                       if e.server == server and e.name == tool), None)
        if target is None:
            target = next((e for e in entries
                           if e.name == tool or e.qualified_name == tool), None)
        if target is None:
            names = sorted({f"{e.server}.{e.name}" for e in entries})
            return (f"没有这个MCP工具：{server}/{tool}。当前可用：{', '.join(names[:20])}")

        if target.mode == "deny":
            return f"该MCP工具已被设为不用（deny）：{target.qualified_name}"

        payload = {k: v for k, v in (arguments or {}).items() if v is not None}
        try:
            raw = target.session.call_tool(target.name, payload)
        except MCPError as err:
            if on_error is not None:
                on_error()
            return f"[MCP调用失败] {err}"

        # 统计只认调用成功：失败是"没连上/工具报错"，算使用会把故障也报成活跃
        if on_used is not None:
            try:
                on_used(target.server, target.name)
            except Exception:  # noqa: BLE001 - 统计失败不能影响工具结果
                pass

        raw = cap_output(raw, MCP_OUTPUT_CHAR_LIMIT)
        return wrap_mcp_result(raw, target.server, target.name, log=log)

    return StructuredTool.from_function(
        _call,
        name="mcp_call",
        description=("调用一个MCP工具。先用 mcp_list 找到工具名与参数schema，"
                     "再按 server/tool/arguments 调用。arguments是按schema填的参数对象。"),
        args_schema=create_model("McpCallArgs", **{
            "server": (str, Field(description="server名，mcp_list里给出的")),
            "tool": (str, Field(description="工具名，mcp_list里给出的")),
            "arguments": (Optional[dict], Field(default=None, description="按schema填写的参数")),
        }),
    )
