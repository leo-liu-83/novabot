# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Description : 人格页：NovaBot 头像、SOUL.md 与长期记忆只读。

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth import resolve_user_ctx
from service.persona.service import (
    has_soul,
    memory_snapshot,
    read_memory,
    read_soul,
)
from service.user.user_service import UserCtx, user_service

router = APIRouter(prefix="/persona")


class AvatarInput(BaseModel):
    # 前端已裁成 256×256，正常只有几十 KB；上限只防直连 API 塞超大字符串
    avatar: str = Field(min_length=1, max_length=2_000_000)


def _require_soul(ctx: UserCtx) -> UserCtx:
    if not has_soul(ctx):
        raise api_error("persona.unavailable")
    return ctx


@router.get("", summary="人格页初始化：头像、SOUL 与长期记忆")
async def persona_page(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_soul(ctx)
    entries, core = memory_snapshot(ctx)
    row = user_service.get_user(ctx.uid) or {}
    return response_code.resp_200({
        "avatar": row.get("agent_avatar") or "",
        "soul": read_soul(ctx),
        "memory": entries,
        "core": {"name": "core.md", "content": core},
    })


@router.put("/avatar", summary="更新 NovaBot 头像")
async def update_agent_avatar(
    input: AvatarInput, ctx: UserCtx = Depends(resolve_user_ctx)
) -> Any:
    _require_soul(ctx)
    try:
        avatar = user_service.set_agent_avatar(ctx.uid, input.avatar)
    except ValueError as exc:
        raise api_error("persona.avatar_invalid") from exc

    return response_code.resp_200({"avatar": avatar})


@router.delete("/avatar", summary="清除 NovaBot 头像")
async def delete_agent_avatar(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_soul(ctx)
    user_service.clear_agent_avatar(ctx.uid)
    return response_code.resp_200({"avatar": ""})


@router.get("/memory/{name}", summary="读取一个长期记忆文件")
async def memory_file(name: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_soul(ctx)
    content = read_memory(ctx, name)
    if content is None:
        raise api_error("persona.memory_not_found")

    return response_code.resp_200({"name": name, "content": content})
