# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : mcp_tools.py
# @Software    : VSCode
# @Author      : leo liu
# @Version     : 1.0
# @Description : 把MCP工具装配进模型可见的tools列表（逐工具算allow/ask/deny，deny不注册）

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from langchain_core.tools import BaseTool

from logger import logger
from service.mcp.catalog import build_catalog
from service.mcp.mcp_client import MCPError, McpSession
from service.mcp.registry import mcp_registry, resolve_tool_mode
from service.notice.sink import NoticeSink
from service.mcp.tools import (
    McpEntry,
    build_mcp_call_tool,
    build_mcp_list_tool,
    invalidate_session,
    session_for,
)
from service.user.user_service import UserCtx

# 工具总数上限：每个工具的inputSchema也吃上下文，描述预算管不住schema
MAX_MCP_TOOLS = 30


def _connect_and_list(uid: str, server: dict) -> tuple[dict, McpSession, list[dict]]:
    session = session_for(uid, server)
    return server, session, session.list_tools()


def build_mcp_tools(ctx: UserCtx, notices: Optional[NoticeSink] = None,
                    servers: Optional[list] = None, on_used=None) -> tuple[list[BaseTool], dict[tuple[str, str], str], list[str]]:
    """为当前身份装配MCP工具。

    返回 (工具列表, 每个工具的生效模式, 提示信息)。生效模式是
    {(server, tool): allow|ask|deny}，供 McpToolPolicyMiddleware 在调用点逐条判；
    工具级覆盖优先于 server 模式（见 resolve_tool_mode）。

    连不上的server跳过——一个MCP server不可用不该让整个agent起不来。
    """
    if servers is None:
        servers, notes = mcp_registry.connectable_servers(ctx.uid)
    else:
        notes = []
    # deny的server根本不注册：模型看不到它的工具
    servers = [s for s in servers if s.get("mode") != "deny"]
    if not servers:
        return [], {}, notes

    connected: list[tuple[dict, McpSession, list[dict]]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(servers))) as pool:
        futures = [pool.submit(_connect_and_list, ctx.uid, s) for s in servers]
        for server, future in zip(servers, futures):
            try:
                connected.append(future.result())
            except (MCPError, OSError, ValueError) as err:
                reason = str(err)[:120]
                notes.append(f"MCP server `{server['name']}` 连接失败：{reason}")
                if notices is not None:
                    notices.add(
                        f"mcp:{server['name']}",
                        f"⚠ MCP server `{server['name']}` 连接失败（{reason}），该 server 的工具本次不可用",
                    )

    if not connected:
        return [], {}, notes

    modes = {s["name"]: s.get("mode") or "ask" for s, _, _ in connected}
    # 工具级覆盖：缺行=继承 server 模式，所以只取被单独设过的工具
    overrides = {s["name"]: mcp_registry.list_tool_modes(ctx.uid, s["name"])
                 for s, _, _ in connected}
    catalog, catalog_notes = build_catalog(
        [(s["name"], tools) for s, _, tools in connected])
    notes.extend(catalog_notes)

    sessions = {s["name"]: session for s, session, _ in connected}
    server_rows = {s["name"]: s for s, _, _ in connected}

    # 延迟发现形态：提示词里只有"查工具"与"调用"两个工具，
    # 每个MCP工具的inputSchema不进提示词（要查才给）——工具多了才不会撑爆上下文
    entries: list[McpEntry] = []
    modes_by_tool: dict[tuple[str, str], str] = {}
    denied: list[str] = []
    for item in catalog:
        session = sessions.get(item.server)
        if session is None:
            continue
        mode = resolve_tool_mode(modes.get(item.server, "ask"),
                                 overrides.get(item.server, {}).get(item.name))
        # deny 也要留在模式表里：工具虽然不注册，模型硬调时中间件要能明确拒掉
        modes_by_tool[(item.server, item.name)] = mode
        if mode == "deny":
            denied.append(f"{item.server}.{item.name}")
            continue
        entries.append(McpEntry(
            server=item.server, name=item.name, description=item.description,
            input_schema=item.input_schema, session=session,
            mode=mode))

    if denied:
        notes.append("被设为不用(deny)的MCP工具未注册：" + "、".join(denied))

    if len(entries) > MAX_MCP_TOOLS:
        notes.append(f"MCP工具共 {len(entries)} 个，超过上限 {MAX_MCP_TOOLS}，只装配前 {MAX_MCP_TOOLS} 个")
        entries = entries[:MAX_MCP_TOOLS]

    if not entries:
        for note in notes:
            logger.info(f"[mcp] {note}")
        return [], {}, notes

    def _invalidate() -> None:
        for row in server_rows.values():
            invalidate_session(ctx.uid, row)

    tools: list[BaseTool] = [
        build_mcp_list_tool(entries),
        build_mcp_call_tool(entries, on_error=_invalidate, on_used=on_used),
    ]

    # ask 不再整体挂 interrupt：两个元工具共用一个工具名，按工具名挂只会
    # 把 allow 的调用也一起问。改为在调用点按 (server, tool) 判（见 mcp_middleware）。
    if any(mode == "ask" for mode in modes_by_tool.values()):
        notes.append("存在ask模式的MCP工具：调用时逐条确认")

    for note in notes:
        logger.info(f"[mcp] {note}")

    return tools, modes_by_tool, notes
