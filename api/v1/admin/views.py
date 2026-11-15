"""Web 管理端：用户列表、执行天花板、搜索配额、MCP 组合与账号生命周期。

判定全部复用服务层（user_service / web_quota_service / mcp_registry），
这里只做"谁的请求"和"值的边界"，不另造一套权限口径。
"""

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth.deps import require_admin
from config import config
from core.agent.deep_agents.exec_policy import IMPLEMENTED_MAX, parse_level
from exception.user_exception import AuthError
from service.mcp.registry import mcp_registry
from service.project.project_service import project_service
from service.session.session_service import session_service
from service.user.user_service import UserCtx, user_service
from service.web.quota_service import web_quota_service

router = APIRouter(prefix="/admin")

CeilingName = Literal["T0", "T1", "T2", "T3", "T4"]


class CreateUserSchema(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8)
    is_admin: bool = False


class UpdateUserSchema(BaseModel):
    is_admin: Optional[bool] = None
    # 缺省=不改；显式 null=清除授权。用 model_fields_set 区分这两件事。
    exec_ceiling: Optional[CeilingName] = None
    quota_enabled: Optional[bool] = None
    quota_limit: Optional[int] = Field(default=None, ge=-1)
    mcp_granted: Optional[bool] = None
    projects_enabled: Optional[bool] = None


class ResetPasswordSchema(BaseModel):
    new_password: str = Field(min_length=8)


def _user_payload(uid: str, username: str, is_admin: bool, created_at: int,
                  avatar: str = "", projects_enabled: bool = False) -> dict:
    ceiling = user_service.get_exec_grant(uid)
    quota = web_quota_service.state(uid)
    return {
        "uid": uid,
        "username": username,
        "is_admin": is_admin,
        "created_at": created_at,
        "avatar": avatar or "",
        "exec_ceiling": f"T{int(ceiling)}" if ceiling is not None else None,
        "web_quota": {
            "enabled": quota.enabled,
            "daily_limit": quota.daily_limit,
            "used": quota.used,
        },
        "mcp_granted": mcp_registry.is_user_mcp_granted(uid),
        "projects_enabled": projects_enabled,
        # admin 天然可建项目；普通账号看授权开关
        "can_projects": is_admin or projects_enabled,
    }


def _find_user(uid: str) -> dict:
    for row in user_service.list_users():
        if row["uid"] == uid:
            return row
    raise api_error("admin.user_not_found")


def _ensure_not_last_admin(row: dict, *, action: str) -> None:
    if row["is_admin"] and user_service.count_admins() <= 1:
        code = "admin.last_admin_delete" if action == "删除" else "admin.last_admin_demote"
        raise api_error(code)


@router.get("/users", summary="账号与授权列表")
async def list_users(ctx: UserCtx = Depends(require_admin)) -> Any:
    rows = [
        _user_payload(row["uid"], row["username"], bool(row["is_admin"]), row["created_at"],
                      row.get("avatar") or "", bool(row.get("projects_enabled")))
        for row in user_service.list_users()
    ]
    return response_code.resp_200({"users": rows})


@router.post("/users", summary="管理员创建账号")
async def create_user(input: CreateUserSchema, ctx: UserCtx = Depends(require_admin)) -> Any:
    try:
        uid = user_service.create_user(input.username, input.password, is_admin=input.is_admin)
    except AuthError as exc:
        raise api_error(exc.code or "auth.invalid_input", status_code=status.HTTP_409_CONFLICT, message=exc.err_desc) from exc

    row = _find_user(uid)
    return response_code.resp_200(_user_payload(row["uid"], row["username"], bool(row["is_admin"]),
                                                row["created_at"], row.get("avatar") or ""))


@router.patch("/users/{uid}", summary="修改执行天花板、配额、MCP 授权与角色")
async def update_user(
    uid: str, input: UpdateUserSchema, ctx: UserCtx = Depends(require_admin)
) -> Any:
    row = _find_user(uid)
    fields = input.model_fields_set

    if "is_admin" in fields and input.is_admin is not None:
        if input.is_admin is False:
            _ensure_not_last_admin(row, action="降级")
        user_service.set_admin(row["username"], input.is_admin)

    if "exec_ceiling" in fields:
        if input.exec_ceiling is None:
            user_service.clear_exec_grant(uid)
        else:
            level = parse_level(input.exec_ceiling)
            cap = min(parse_level(config.EXEC_LEVEL), IMPLEMENTED_MAX)
            if level > cap:
                raise api_error("admin.ceiling_cap", cap=int(cap), level=int(level))
            user_service.set_exec_grant(uid, level)

    if "quota_enabled" in fields or "quota_limit" in fields:
        current = web_quota_service.state(uid)
        enabled = current.enabled if input.quota_enabled is None else input.quota_enabled
        limit = current.daily_limit if input.quota_limit is None else input.quota_limit
        web_quota_service.set_quota(uid, enabled, limit)

    if "mcp_granted" in fields and input.mcp_granted is not None:
        mcp_registry.set_user_mcp_granted(uid, input.mcp_granted)

    if "projects_enabled" in fields and input.projects_enabled is not None:
        user_service.set_projects_enabled(uid, input.projects_enabled)

    row = _find_user(uid)
    return response_code.resp_200(_user_payload(row["uid"], row["username"], bool(row["is_admin"]),
                                                row["created_at"], row.get("avatar") or "",
                                                bool(row.get("projects_enabled"))))


@router.post("/users/{uid}/reset-password", summary="管理员重置指定账号的密码")
async def reset_user_password(
    uid: str, input: ResetPasswordSchema, ctx: UserCtx = Depends(require_admin)
) -> Any:
    row = _find_user(uid)
    try:
        user_service.reset_password_by_admin(uid, input.new_password)
    except AuthError as exc:
        raise api_error(exc.code or "auth.invalid_input", message=exc.err_desc) from exc

    return response_code.resp_200({
        "uid": uid,
        "username": row["username"],
        "reset": True,
    })


@router.delete("/users/{uid}", summary="管理员删除账号")
async def delete_user(uid: str, ctx: UserCtx = Depends(require_admin)) -> Any:
    row = _find_user(uid)
    _ensure_not_last_admin(row, action="删除")

    # 先挡新会话、等在途 turn 释放租约，再删用户行与目录；顺序反过来会让旧请求把数据写回。
    await session_service.delete_user_sessions_wait(uid)
    if not user_service.delete_account_by_admin(uid):
        raise api_error("admin.user_not_found")
    # 与 session 清理同层：user_service 不依赖项目服务，也避免手工 db_path 的实例清错库
    project_service.delete_projects_by_user(uid)

    return response_code.resp_200({"uid": uid, "deleted": True})
