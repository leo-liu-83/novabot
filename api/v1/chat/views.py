# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : views.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description :

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends

from api.resp import response_code
from api.v1.auth import resolve_user_ctx
from service.user.user_service import UserCtx
from .schema import ChatInputSchema, ChatResumeSchema
from .service import ChatService

router = APIRouter(prefix="/chat")

@router.post("/completions", summary="会话")
async def completions(*, input: ChatInputSchema, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    # 构造里有模型发现（HTTP）与 agent 编译：放进线程，避免阻塞事件循环拖慢其他请求
    chat_service = await asyncio.to_thread(ChatService, input=input, ctx=ctx)

    if input.streamable:
        return response_code.resp_200_streaming(_sse(chat_service.astream()))

    resp = await chat_service.ainvoke()
    return response_code.resp_200(resp)

@router.post("/resume", summary="恢复interrupt会话")
async def resume(*, input: ChatResumeSchema, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    # 恢复必须沿用原会话绑定的model/thinking，否则模型回退到默认配置
    chat_service = await asyncio.to_thread(
        ChatService,
        ChatInputSchema(
            query="",
            session_id=input.session_id,
            model=input.model,
            enable_thinking=input.enable_thinking,
            show_thinking=input.show_thinking,
        ),
        ctx=ctx,
    )

    return response_code.resp_200_streaming(_sse(chat_service.aresume(input.decision, input.interrupt_id)))

# 会话事件转SSE报文
async def _sse(events):
    async for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    yield "data: [DONE]\n\n"
