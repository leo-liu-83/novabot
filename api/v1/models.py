"""Safe model metadata for the Web model picker (never returns provider secrets)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth import resolve_user_ctx
from api.v1.auth.deps import require_admin
from config import config
from service.config import user_config_store
from service.model_catalog import model_catalog
from service.user.user_service import UserCtx

router = APIRouter(prefix="/models")


class SetDefaultModelInput(BaseModel):
    """`model` 用列表里的 id（`provider/model`），与 `agent.model` 同一写法。"""

    model: str


@router.get("", summary="列出当前配置中的模型")
async def list_models(ctx: UserCtx = Depends(resolve_user_ctx)):
    data = config.CONFIG_DATA
    providers = []
    if data and data.models:
        for provider in model_catalog.providers():
            providers.append({
                "code": provider.code,
                "name": provider.name,
                "models": [{
                    "id": f"{provider.code}/{model.code}",
                    "code": model.code,
                    "name": model.name,
                    "enable_thinking": bool(model.enable_thinking),
                    "num_ctx": model.num_ctx or config.LLM_MAX_INPUT_TOKENS,
                    # 只给是否配置 key，不回传 key/headers/base_url。
                    "configured": bool(provider.api_key),
                } for model in (provider.models or [])],
            })
    default_model = data.agent.model if data and data.agent else ""
    return response_code.resp_200({"default_model": default_model, "providers": providers})


@router.post("/default", summary="设置 agent 默认模型")
async def set_default_model(
    payload: SetDefaultModelInput, ctx: UserCtx = Depends(require_admin)
):
    """把某个模型设为 agent 默认模型，并写进用户配置的 `agent.model`。

    只校验它在当前模型目录里，不试探可用性：目录里的模型已经是当前配置认可的，
    真发一次请求既慢又会被临时网络问题误伤。
    """
    model = (payload.model or "").strip()
    known = {
        f"{provider.code}/{item.code}"
        for provider in model_catalog.providers()
        for item in (provider.models or [])
    }
    if model not in known:
        raise api_error("model.not_found", model=model)
    try:
        user_config_store.set_agent_model(model)
    except user_config_store.UserConfigError as exc:
        raise api_error(exc.code, message=str(exc)) from exc
    return response_code.resp_200({"default_model": model})
