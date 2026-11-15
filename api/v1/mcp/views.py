"""MCP 用户层配置：server 增删改、审批模式、逐工具覆盖与工具探测。

系统层 server 只读展示（来自内置配置，改它要改仓库），用户层才能通过这里维护。
所有 URL 都走 registry 的统一校验（配置期收紧 SSRF），这里不再另写一份。
"""

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth import resolve_user_ctx
from api.v1.auth.deps import require_admin
from config import config
from service.mcp.registry import MCP_MODES, mcp_registry
from service.user.user_service import UserCtx, user_service

router = APIRouter(prefix="/mcp")

Mode = Literal["ask", "allow", "deny"]


class ServerInput(BaseModel):
    url: str = Field(min_length=1)
    headers: Optional[Dict[str, str]] = None
    mode: Mode = "ask"


class ServerPatch(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[Mode] = None


class ToolModeInput(BaseModel):
    mode: Mode


def _masked_server(uid: str, row: dict) -> dict:
    overrides = mcp_registry.list_tool_modes(uid, row["name"])
    return {
        "name": row["name"],
        "url": row["url"],
        "enabled": bool(row["enabled"]),
        "mode": row.get("mode") or "ask",
        # header 可能带 Authorization/Bearer，列表只说明有没有，不回传值
        "has_headers": bool(row.get("headers")),
        # 上次探测到的工具（可能为空）：界面刷新时先展示缓存，点“刷新工具”再实时拉取
        "tools": [
            {"name": name, "mode": overrides.get(name)}
            for name in (row.get("tools") or [])
        ],
    }


def _system_servers() -> list[dict]:
    return [
        {
            "name": entry.get("name", ""),
            "url": entry.get("url", ""),
            "enabled": bool(entry.get("enabled", True)),
        }
        for entry in (getattr(config, "MCP_SYSTEM_SERVERS", None) or [])
    ]


def _find(uid: str, name: str) -> dict:
    row = mcp_registry.get_user_server(uid, name)
    if row is None:
        raise api_error("mcp.server_not_found")

    return row


@router.get("/servers", summary="列出系统层与用户层 MCP server")
async def list_servers(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    return response_code.resp_200({
        "granted": mcp_registry.is_user_mcp_granted(ctx.uid),
        "limit": mcp_registry.get_user_limit(ctx.uid),
        "servers": [_masked_server(ctx.uid, row) for row in mcp_registry.list_user_servers(ctx.uid)],
        "system": _system_servers(),
        "modes": list(MCP_MODES),
    })


@router.put("/servers/{name}", summary="新增或更新用户 MCP server")
async def upsert_server(name: str, input: ServerInput, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    try:
        row = mcp_registry.upsert_user_server(
            ctx.uid, name, input.url, headers=input.headers, mode=input.mode,
            # 回环/私网地址只有管理员能配：本地 MCP server 是合理用法，也是 SSRF 面
            allow_private=ctx.is_admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return response_code.resp_200(_masked_server(ctx.uid, row))


@router.patch("/servers/{name}", summary="启用/停用或切换审批模式")
async def patch_server(name: str, input: ServerPatch, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _find(ctx.uid, name)
    if "enabled" in input.model_fields_set and input.enabled is not None:
        mcp_registry.set_user_server_enabled(ctx.uid, name, input.enabled)
    if "mode" in input.model_fields_set and input.mode is not None:
        mcp_registry.set_user_server_mode(ctx.uid, name, input.mode)

    return response_code.resp_200(_masked_server(ctx.uid, _find(ctx.uid, name)))


@router.delete("/servers/{name}", summary="删除用户 MCP server")
async def delete_server(name: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    if not mcp_registry.remove_user_server(ctx.uid, name):
        raise api_error("mcp.server_not_found")

    return response_code.resp_200({"name": name, "deleted": True})


@router.get("/servers/{name}/tools", summary="连接并列出 server 工具（含逐工具模式）")
async def list_server_tools(name: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    row = _find(ctx.uid, name)

    from service.mcp.tools import session_for

    def _fetch() -> list[str]:
        session = session_for(ctx.uid, {"name": row["name"], "url": row["url"],
                                        "headers": row.get("headers") or {}})
        return [str(t.get("name")) for t in session.list_tools()
                if isinstance(t, dict) and t.get("name")]

    try:
        tool_names = await run_in_threadpool(_fetch)
    except Exception as exc:  # noqa: BLE001 - MCP 错误类型来自客户端实现，统一报可读原因
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"连接 MCP server 失败：{exc}"
        ) from exc

    overrides = mcp_registry.list_tool_modes(ctx.uid, name)
    # 探测成功才更新缓存：连不上时保留上次结果，页面刷新后至少还能看到旧列表
    mcp_registry.set_user_server_tools(ctx.uid, name, tool_names)
    return response_code.resp_200({
        "server": name,
        "server_mode": row.get("mode") or "ask",
        "tools": [{"name": tool, "mode": overrides.get(tool)} for tool in tool_names],
    })


@router.put("/servers/{name}/tools/{tool}", summary="设置单工具审批模式")
async def set_tool_mode(
    name: str, tool: str, input: ToolModeInput, ctx: UserCtx = Depends(resolve_user_ctx)
) -> Any:
    _find(ctx.uid, name)
    mcp_registry.set_tool_mode(ctx.uid, name, tool, input.mode)
    return response_code.resp_200({"server": name, "tool": tool, "mode": input.mode})


@router.delete("/servers/{name}/tools/{tool}", summary="恢复继承 server 模式")
async def clear_tool_mode(name: str, tool: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _find(ctx.uid, name)
    mcp_registry.clear_tool_mode(ctx.uid, name, tool)
    return response_code.resp_200({"server": name, "tool": tool, "mode": None})


@router.get("/admin/servers", summary="管理员查看任一账号的 MCP 配置")
async def admin_list_servers(uid: str, ctx: UserCtx = Depends(require_admin)) -> Any:
    if user_service.get_user(uid) is None:
        raise api_error("mcp.account_not_found")

    return response_code.resp_200({
        "granted": mcp_registry.is_user_mcp_granted(uid),
        "servers": [_masked_server(uid, row) for row in mcp_registry.list_user_servers(uid)],
    })
