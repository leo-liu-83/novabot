"""Safe model metadata for the Web model picker (never returns provider secrets)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth import resolve_user_ctx
from api.v1.auth.deps import require_admin
from config import config
from const.subagents import DESCRIPTIONS, ROLES, TITLES
from service.config import user_config_store
from service.model_catalog import is_usable, model_catalog, thinking_options
from util.secrets import SecretStore, config_file_values, db_values
from service.user.user_service import UserCtx

router = APIRouter(prefix="/models")


class SetDefaultModelInput(BaseModel):
    """`model` 用列表里的 id（`provider/model`），与 `agent.model` 同一写法。"""

    model: str


def _configured_subagent_model(role: str) -> str:
    data = config.CONFIG_DATA
    agent = data.agent if data else None
    subagents = agent.subagents if agent else None
    entry = (subagents or {}).get(role)
    return (entry.model if entry else "") or ""


def _secrets_status() -> dict:
    """系统信息页的加密状态：有没有主密钥、还有几条明文、几条解不开。

    明文条数按**磁盘上的文件**数，不用内存里已解密的 DTO——只有文件才是"可能被拷走"的东西。
    """
    db_path = config.db_file_path()
    store = SecretStore(db_path)
    user_config = f"{config.APP_DATA_PATH}/{config.USER_CONFIG_FILE}"
    values = config_file_values(user_config) + config_file_values(user_config + ".bak")
    values += db_values(db_path)
    status = store.status(values)
    return {
        "enabled": status["enabled"],
        "algo": status["algo"],
        "fingerprint": status["fingerprint"],
        "plaintext": status["plaintext"],
        "undecryptable": status["undecryptable"],
    }


def _subagent_payload(role: str, default_model: str) -> dict:
    """一个角色的展示信息；`fallback_model` 固定取 `agent.model`，不感知会话级 /model。"""
    configured = _configured_subagent_model(role)
    return {
        "role": role,
        "title": TITLES[role],
        "description": DESCRIPTIONS[role],
        "model": configured,
        "fallback_model": default_model,
        "inherits": not configured,
    }


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
                    # 档位：UI 按 levels 画选项，default 用于新会话初值
                    "thinking_default": thinking_options(
                        f"{provider.code}/{model.code}")["default"],
                    "thinking_levels": thinking_options(
                        f"{provider.code}/{model.code}")["levels"],
                    "num_ctx": model.num_ctx or config.LLM_MAX_INPUT_TOKENS,
                    # 只给是否配置 key，不回传 key/headers/base_url。
                    "configured": bool(provider.api_key),
                } for model in (provider.models or [])],
            })
    default_model = data.agent.model if data and data.agent else ""
    return response_code.resp_200({
        "default_model": default_model,
        "providers": providers,
        "subagents": [_subagent_payload(role, default_model) for role in ROLES],
        # provider code → 最近一次模型发现失败原因；界面用它解释"为什么没模型"
        "discovery_errors": model_catalog.errors(),
        "secrets": _secrets_status(),
    })


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


@router.put("/subagents/{role}", summary="设置某个子代理角色的模型")
async def set_subagent_model(
    role: str, payload: SetDefaultModelInput,
    ctx: UserCtx = Depends(require_admin),
):
    """空串表示"跟随会话模型"，是合法取值；非空时必须能在当前配置里用。"""
    if role not in ROLES:
        raise api_error("model.role_not_found", role=role)

    model = (payload.model or "").strip()
    if model and not is_usable(model):
        raise api_error("model.not_found", model=model)

    try:
        user_config_store.set_subagent_model(role, model)
    except user_config_store.UserConfigError as exc:
        raise api_error(exc.code, message=str(exc)) from exc

    data = config.CONFIG_DATA
    default_model = data.agent.model if data and data.agent else ""
    return response_code.resp_200(_subagent_payload(role, default_model))
