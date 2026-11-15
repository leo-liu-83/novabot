# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : views.py
# @Software    : VSCode
# @Datetime    : 2026/10/13 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : HTTP认证端点：签发opaque token（与TUI共用user_service，不改全局身份）

from typing import Any
from urllib.parse import urlsplit
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth.deps import bearer_token, resolve_user_ctx
from config import config
from const.messages import msg
from core.agent.deep_agents.exec_policy import effective_ceiling
from exception.user_exception import AuthError
from logger import logger
from service.attachment.attachments import max_file_bytes, max_per_message
from service.mcp.registry import mcp_registry
from service.session.session_service import session_service
from service.project.project_service import project_service
from service.user.user_service import UserCtx, user_service
from service.web.quota_service import next_reset_at, web_quota_service
from .schema import CredentialsSchema

router = APIRouter(prefix="/auth")


class PasswordChangeSchema(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class DeleteAccountSchema(BaseModel):
    password: str = Field(min_length=1)
    purge_files: bool = False


class AvatarInput(BaseModel):
    # 前端已裁成 256×256，正常只有几十 KB；上限只防直连 API 塞超大字符串
    avatar: str = Field(min_length=1, max_length=2_000_000)


class SetupAdminSchema(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1)
    password2: str = Field(min_length=1)


def _same_origin(request: Request) -> bool:
    """Origin 缺失（curl 等非浏览器）放行；有 Origin 时只比较 netloc 与 Host。

    只比 host:port 不比 scheme：反向代理终止 TLS 时 request.url.scheme 不可靠。
    也不信任 X-Forwarded-Host——客户端可伪造，会绕过同源校验。
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    if origin == "null":
        return False
    host = request.headers.get("host", "")
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


@router.get("/status", summary="读取认证与首次启动状态")
async def status_info() -> Any:
    """供 Web 决定显示登录页、首次启动提示或单用户控制台，不泄露账号信息。"""
    return response_code.resp_200({
        "multi_user": config.MULTI_USER,
        "setup_required": bool(config.MULTI_USER and not user_service.has_admin()),
    })


@router.get("/me", summary="读取当前身份")
async def me(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    return response_code.resp_200(_user_payload(ctx))


@router.put("/avatar", summary="更新当前账号头像")
async def update_avatar(input: AvatarInput, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    if ctx.is_default:
        raise api_error("auth.no_account")
    try:
        avatar = user_service.set_avatar(ctx.uid, input.avatar)
    except ValueError as exc:
        # 归一化失败属于配置期校验：给稳定 code，让前端能翻译；原始细节只进日志
        logger.warning(f"头像校验失败 uid={ctx.uid}: {exc}")
        raise api_error("auth.avatar_invalid") from exc

    return response_code.resp_200({"avatar": avatar})


@router.delete("/avatar", summary="清除当前账号头像")
async def delete_avatar(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    if ctx.is_default:
        raise api_error("auth.no_account")
    user_service.clear_avatar(ctx.uid)
    return response_code.resp_200({"avatar": ""})


@router.post("/setup", summary="创建首个管理员（仅多用户、无管理员时）")
async def setup_admin(input: SetupAdminSchema, request: Request) -> Any:
    """Web 首次初始化。不限制来源 IP，靠"无管理员"状态与 Origin 同源校验挡住抢注。"""
    if not config.MULTI_USER:
        raise api_error("auth.multi_user_required")
    if user_service.has_admin():
        raise api_error("auth.admin_exists")
    if not _same_origin(request):
        raise api_error("auth.cross_origin_blocked")
    if input.password != input.password2:
        raise api_error("auth.password_mismatch")

    try:
        ctx, token = user_service.create_admin(
            input.username, input.password, remember=config.REMEMBER_LOGIN
        )
    except AuthError as exc:
        raise api_error(exc.code or "auth.invalid_input", message=exc.err_desc) from exc

    client = request.client.host if request.client else "-"
    logger.info(f"首次初始化：创建管理员 {ctx.username} client={client}")
    return response_code.resp_200({
        "token": token,
        "uid": ctx.uid,
        "username": ctx.username,
        "user": _user_payload(ctx),
    })


@router.post("/logout", summary="吊销当前 Web token")
async def logout(request: Request) -> Any:
    # 不调用 user_service.logout：login.json 是 TUI 的持久登录态，Web 登出不能顺手登出 TUI。
    token = bearer_token(request)
    if token:
        user_service.revoke_token(token)
    return response_code.resp_200({"logged_out": True})


@router.post("/password", summary="修改密码并轮换当前 token")
async def change_password(input: PasswordChangeSchema, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    if ctx.is_default:
        raise api_error("auth.no_account")

    try:
        token = user_service.change_password(ctx.uid, input.old_password, input.new_password)
    except AuthError as exc:
        raise api_error(exc.code or "auth.invalid_input", status_code=status.HTTP_401_UNAUTHORIZED, message=exc.err_desc) from exc

    # 改密会吊销所有旧 token；把新 token 还给当前浏览器，避免用户刚改完就被登出。
    return response_code.resp_200({"token": token, "user": _user_payload(ctx)})


@router.delete("/account", summary="注销当前 Web 账号")
async def delete_account(input: DeleteAccountSchema, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    if ctx.is_default:
        raise api_error("auth.no_account")

    try:
        # 先验密码再动数据：密码错时不应先挡掉该账号的在途请求。
        user_service.verify_password_for(ctx.uid, input.password)
    except AuthError as exc:
        raise api_error(exc.code or "auth.invalid_input", status_code=status.HTTP_401_UNAUTHORIZED, message=exc.err_desc) from exc

    # 最后一个管理员注销会把部署锁死在"没有管理员、又不能在 Web 创建"的状态。
    if ctx.is_admin and user_service.count_admins() <= 1:
        raise api_error("auth.last_admin_delete")

    # 先封锁新会话、等在途 turn 释放租约，再删用户行/目录（与 TUI 注销同一顺序）。
    await session_service.delete_user_sessions_wait(ctx.uid)
    user_service.delete_account(ctx.uid, input.password, input.purge_files)
    project_service.delete_projects_by_user(ctx.uid)
    return response_code.resp_200({"uid": ctx.uid, "deleted": True})


@router.post("/login", summary="登录")
async def login(*, input: CredentialsSchema) -> Any:
    ctx, token = user_service.login(input.username, input.password)

    return response_code.resp_200({
        "token": token,
        # 兼容旧客户端的扁平字段；新客户端读 user 对象
        "uid": ctx.uid,
        "username": ctx.username,
        "user": _user_payload(ctx),
    })


@router.post("/register", summary="注册并登录")
async def register(*, input: CredentialsSchema) -> Any:
    # 尚无admin时拒绝注册：首个管理员只能通过 /auth/setup（Web 初始化）或 TUI 建号流程创建。
    # 否则能访问API的人可以抢先建普通账号，再绕开"首个admin由机主本人创建"这条前提。
    if not user_service.has_admin():
        return response_code.resp_403(message=msg.MSG_AUTH_ADMIN_REQUIRED, error="auth.register_blocked")

    ctx, token = user_service.register(input.username, input.password)

    return response_code.resp_200({
        "token": token,
        # 兼容旧客户端的扁平字段；新客户端读 user 对象
        "uid": ctx.uid,
        "username": ctx.username,
        "user": _user_payload(ctx),
    })


def _user_payload(ctx: UserCtx) -> dict:
    if ctx.is_default:
        role = "owner" if not config.MULTI_USER else "anon"
    else:
        role = "admin" if ctx.is_admin else "user"
    # 对外显示"实际能到哪一档"：admin 的身份天花板是 T4，不能因为授权表没行就报 T1
    ceiling = effective_ceiling(ctx, config.EXEC_LEVEL)
    as_level = lambda value: f"T{int(value)}"
    # default 身份（单用户机主 / 多用户匿名）没有账号行，也就没有注册时间；
    # 显式给 None，前端据此隐藏“加入时间”，而不是显示一个空破折号。
    created_at = None
    mcp_granted = False
    avatar = ""
    agent_avatar = ""
    if not ctx.is_default:
        row = user_service.get_user(ctx.uid)
        if row:
            created_at = row.get("created_at")
            avatar = row.get("avatar") or ""
            agent_avatar = row.get("agent_avatar") or ""
        # MCP 能力开关是 admin 给账号的授权，不属于 UserCtx；身份接口要带上，
        # 否则 Web 刷新后只能按默认值显示“未授权”，直到打开用户管理页才纠正。
        mcp_granted = mcp_registry.is_user_mcp_granted(ctx.uid)
    has_soul = bool(ctx.soul_path) and os.path.isfile(ctx.soul_path)
    quota = web_quota_service.state(ctx.uid)
    return {
        "uid": ctx.uid,
        "username": ctx.username,
        "role": role,
        "multi_user": config.MULTI_USER,
        "is_admin": ctx.is_admin,
        "default_level": as_level(ctx.default_level),
        "ceiling": as_level(ceiling),
        "created_at": created_at,
        "avatar": avatar,
        "agent_avatar": agent_avatar,
        "has_soul": has_soul,
        "mcp_granted": mcp_granted,
        "can_projects": ctx.can_projects,
        "attachment_limits": {
            # 管理员/单用户机主按配置不受限；多用户匿名者仍按普通限制
            "max_per_message": max_per_message(ctx.is_admin or (ctx.is_default and not config.MULTI_USER)),
            "max_file_bytes": max_file_bytes(ctx.is_admin or (ctx.is_default and not config.MULTI_USER)),
        },
        "web_quota": {
            "enabled": quota.enabled, "daily_limit": quota.daily_limit,
            "used": quota.used, "reset_at": next_reset_at(),
        },
    }
