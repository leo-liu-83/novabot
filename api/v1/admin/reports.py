# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Description : 管理端统计报表：用户活跃、模型用量、token 消耗

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from api.resp import response_code
from api.v1.auth.deps import require_admin
from service.session.session_service import session_service
from service.user.user_service import UserCtx

router = APIRouter(prefix="/admin/reports")


@router.get("", summary="管理端统计报表")
async def admin_report(
    days: int = Query(30, ge=1, le=365, description="统计最近 N 天，含今天"),
    bucket: Literal["day", "week"] = Query("day", description="时间粒度"),
    limit: int = Query(20, ge=1, le=100, description="Top 用户/模型条数"),
    _: UserCtx = Depends(require_admin),
) -> Any:
    """只读聚合接口；不返回消息正文，只返回账号、模型和 token 统计。"""
    data = await asyncio.to_thread(
        session_service.admin_report, days=days, bucket=bucket, limit=limit,
    )
    return response_code.resp_200(data)
