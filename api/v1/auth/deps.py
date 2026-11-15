# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : deps.py
# @Software    : VSCode
# @Datetime    : 2026/10/13 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : API鉴权依赖：从Bearer token解析调用者身份

from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from api.resp.errors import api_error

from config import config
from const.messages import msg
from service.user.user_service import UserCtx, build_ctx, default_ctx, user_service


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None

    return token.strip() or None


def resolve_user_ctx(request: Request) -> UserCtx:
    """业务接口的身份依赖。

    匿名身份只存在于TUI，API一律要求凭证：multi_user=false时不鉴权（单用户裸API保持原样）；
    开启后无token与token解析失败都401，但detail不同——前者是漏传，后者是过期/吊销。
    """
    if not config.MULTI_USER:
        return default_ctx()

    token = bearer_token(request)
    if not token:
        raise api_error("auth.token_missing")

    uid = user_service.resolve_token(token)
    if not uid:
        raise api_error("auth.token_invalid")

    user = user_service.get_user(uid)
    if not user:
        user_service.revoke_all(uid)
        raise api_error("auth.token_invalid")

    return build_ctx(uid, user["username"], bool(user["is_admin"]),
                     user_service.get_exec_grant(uid),
                     projects_enabled=user_service.get_projects_enabled(uid))


def require_admin(ctx: UserCtx = Depends(resolve_user_ctx)) -> UserCtx:
    """管理接口依赖：多用户下必须是 admin；单用户机主即管理员。

    单用户的 default 身份在 ctx 里 is_admin=False（它决定默认执行档位，不能乱改），
    所以这里单独放行"单用户 + default"，不把管理员语义塞进 build_ctx。
    """
    if ctx.is_admin or (ctx.is_default and not config.MULTI_USER):
        return ctx

    raise api_error("auth.admin_required")