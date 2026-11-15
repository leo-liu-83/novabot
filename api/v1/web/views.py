"""Web 搜索 provider：用户自备档与全局档共用一个数据层（provider_service）。

密钥只写不读：列表永远不返回 api_key，只返回 has_key。
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth import resolve_user_ctx
from api.v1.auth.deps import require_admin
from service.user.user_service import UserCtx
from service.web.provider_service import (
    GLOBAL_SCOPE,
    PROVIDER_KINDS,
    ProviderConfigError,
    user_web_provider_service,
)
from service.web.quota_service import next_reset_at, web_quota_service

router = APIRouter(prefix="/web")


class ProviderInput(BaseModel):
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    extra: Optional[dict] = None


class ProviderToggle(BaseModel):
    enabled: bool


def _row(uid: str, provider: str) -> dict:
    for row in user_web_provider_service.list_for(uid):
        if row["provider"] == provider:
            return row

    raise api_error("provider.not_found")


@router.get("/providers", summary="列出当前用户自备的搜索 provider")
async def list_providers(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    return response_code.resp_200({
        "providers": user_web_provider_service.list_for(ctx.uid),
        "kinds": list(PROVIDER_KINDS),
    })


@router.get("/quota", summary="读取当前用户的搜索配额")
async def get_quota(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    state = web_quota_service.state(ctx.uid)
    return response_code.resp_200({
        "enabled": state.enabled, "daily_limit": state.daily_limit, "used": state.used,
        # 配额按天重置：给下一次本地零点，前端不要再用静态日期或自己猜
        "reset_at": next_reset_at(),
    })


@router.put("/providers/{provider}", summary="新增或更新用户自备 provider")
async def upsert_provider(
    provider: str, input: ProviderInput, ctx: UserCtx = Depends(resolve_user_ctx)
) -> Any:
    # 编辑时前端不会回传已保存的 key（界面上也没有明文）；省略字段 = 保留原 key，
    # 显式传 null = 清空。两者不能用同一个默认值糊过去。
    api_key = input.api_key
    if "api_key" not in input.model_fields_set:
        existing = user_web_provider_service.get(ctx.uid, provider)
        api_key = existing["api_key"] if existing else None

    try:
        user_web_provider_service.upsert(
            ctx.uid, provider, api_key=api_key, endpoint=input.endpoint, extra=input.extra
        )
    except ProviderConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return response_code.resp_200(_row(ctx.uid, provider.strip().lower()))


@router.patch("/providers/{provider}", summary="启用/停用用户 provider")
async def toggle_provider(
    provider: str, input: ProviderToggle, ctx: UserCtx = Depends(resolve_user_ctx)
) -> Any:
    if not user_web_provider_service.set_enabled(ctx.uid, provider, input.enabled):
        raise api_error("provider.not_found")

    return response_code.resp_200(_row(ctx.uid, provider.strip().lower()))


@router.delete("/providers/{provider}", summary="删除用户 provider")
async def delete_provider(provider: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    user_web_provider_service.remove(ctx.uid, provider)
    return response_code.resp_200({"provider": provider, "deleted": True})


# ── 全局档（admin）────────────────────────────────────────────────
# 复用同一张表与同一套校验；用户档优先，全局档只在用户没配置时兜底。

@router.get("/global-providers", summary="列出全局搜索 provider")
async def list_global_providers(ctx: UserCtx = Depends(require_admin)) -> Any:
    return response_code.resp_200({
        "providers": user_web_provider_service.list_global(),
        "kinds": list(PROVIDER_KINDS),
    })


@router.put("/global-providers/{provider}", summary="新增或更新全局 provider")
async def upsert_global_provider(
    provider: str, input: ProviderInput, ctx: UserCtx = Depends(require_admin)
) -> Any:
    api_key = input.api_key
    if "api_key" not in input.model_fields_set:
        existing = user_web_provider_service.get(GLOBAL_SCOPE, provider)
        api_key = existing["api_key"] if existing else None

    try:
        user_web_provider_service.upsert_global(
            provider, api_key=api_key, endpoint=input.endpoint, extra=input.extra
        )
    except ProviderConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return response_code.resp_200(_row(GLOBAL_SCOPE, provider.strip().lower()))


@router.patch("/global-providers/{provider}", summary="启用/停用全局 provider")
async def toggle_global_provider(
    provider: str, input: ProviderToggle, ctx: UserCtx = Depends(require_admin)
) -> Any:
    if not user_web_provider_service.set_enabled(GLOBAL_SCOPE, provider, input.enabled):
        raise api_error("provider.not_found")

    return response_code.resp_200(_row(GLOBAL_SCOPE, provider.strip().lower()))


@router.delete("/global-providers/{provider}", summary="删除全局 provider")
async def delete_global_provider(provider: str, ctx: UserCtx = Depends(require_admin)) -> Any:
    user_web_provider_service.remove_global(provider)
    return response_code.resp_200({"provider": provider, "deleted": True})
