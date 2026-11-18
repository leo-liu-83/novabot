# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : llm_manager.py
# @Software    : VSCode
# @Datetime    : 2026/04/28 09:55:37
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from typing import Optional

from config import config
from core.llm.thinking import AUTO, normalize_level
from util.secrets import is_ciphertext
from enums import ModelApiTypeEnum
from exception.model_exception import ModelCallError, ModelPropError
from core.llm.http.openai import HttpOpenAi
from service.model_catalog import model_catalog
from util.request_headers import add_provider_session_header, resolve_runtime_headers

class LlmManager:
    def __init__(self):
        self.__meta__ = config.CONFIG_DATA
        # 模型定义缓存，key为provider/model
        self.model_defs: dict[str, dict] = {}

    # 获取LLM实例，model格式为provider/model。
    # thinking 接受档位（off/low/medium/high）或老的布尔值；None = 用模型定义的默认档位
    def get_llm(self, model: str, thinking=None,
                session_id: Optional[str]=None) -> HttpOpenAi:
        model_def = self._get_model_define(model)

        if is_ciphertext(model_def.get("api_key")):
            raise ModelCallError(
                f"模型 {model} 的密钥无法解密：本机主密钥缺失或与配置不匹配。"
                "请补回原主密钥，或重新填写该 provider 的 key"
                "（python -m util.secret_cli status 可查看状态）")

        api = model_def["api"] or ModelApiTypeEnum.OPENAI.value
        if api == ModelApiTypeEnum.OPENAI.value:
            llm = self._get_open_ai_llm(model_def, session_id=session_id)
        else:
            raise ModelPropError(f"暂不支持的模型接口类型: {api}")

        level = normalize_level(thinking)
        if level is not None:
            llm.thinking_level = level

        return llm

    # 获取OpenAI接口风格LLM
    def _get_open_ai_llm(self, model_def: dict, session_id: Optional[str]=None):
        num_ctx = model_def.get("num_ctx")
        max_input_tokens = (
            num_ctx
            if isinstance(num_ctx, int) and not isinstance(num_ctx, bool) and num_ctx > 0
            else config.LLM_MAX_INPUT_TOKENS
        )

        headers = resolve_runtime_headers(model_def.get("headers"), session_id)
        headers = add_provider_session_header(headers, model_def.get("base_url"), session_id)

        return HttpOpenAi(
            url=model_def["base_url"] + config.MODEL_CHAT_SUFFIX,
            api_key=model_def["api_key"],
            model=model_def["code"],
            headers=headers,
            enable_thinking=model_def["enable_thinking"],
            thinking_level=model_def.get("thinking_level"),
            thinking_style=model_def.get("thinking_style"),
            thinking_off_value=model_def.get("thinking_off_value"),
            max_output_tokens=model_def.get("max_output_tokens"),
            max_input_tokens=max_input_tokens
        )

    # 当前 provider 列表：全局配置走动态目录，测试/自定义 meta 直接用自己的列表
    def _providers(self):
        meta = self.__meta__
        if meta is config.CONFIG_DATA:
            return model_catalog.providers()
        return meta.models if meta else []

    # 加载并缓存模型定义
    def _get_model_define(self, model: str) -> dict:
        if not model or "/" not in model:
            raise ModelPropError("模型Code需按provider/model格式定义")

        if model in self.model_defs:
            return self.model_defs[model]

        if not self.__meta__ or not self.__meta__.models:
            raise ModelPropError("模型配置未加载")

        provider_code, model_code = model.split("/", 1)

        models = self._providers()

        providers = [obj for obj in models if getattr(obj, "code") == provider_code]
        if not providers:
            raise ModelPropError(f"未找到模型供应商: {provider_code}")

        # 同code取最后一个，用户配置覆盖内置配置
        provider = providers[-1]

        if not provider.base_url:
            raise ModelPropError(f"供应商{provider_code}缺少base_url配置")

        provider_models = [obj for obj in (provider.models or []) if getattr(obj, "code") == model_code]
        if not provider_models:
            raise ModelPropError(f"供应商{provider_code}下未找到模型: {model_code}")

        provider_model = provider_models[-1]

        # 请求头合并顺序：Host（旧 host 字段）→ provider.headers → 模型 headers。
        # 越靠后优先级越高，方便网关级默认头被单个模型覆盖。
        headers = {}
        if getattr(provider, "host", ""):
            headers["Host"] = provider.host
        headers.update(getattr(provider, "headers", None) or {})
        headers.update(getattr(provider_model, "headers", None) or {})

        model_def = {
            "provider": provider.name,
            "base_url": provider.base_url,
            "api": provider.api,
            "api_key": provider.api_key,
            "headers": headers or None,
            "code": provider_model.code,
            "name": provider_model.name,
            "stream": provider_model.stream,
            "enable_thinking": provider_model.enable_thinking,
            # 档位默认值：模型级 > provider 级 > 老字段 enable_thinking。
            # 动态发现出来的模型没有"默认档位"这回事：auto 表示不覆盖，
            # 别替它决定（把默认从"开"改成"关"是行为事故）
            "thinking_level": _default_level(provider, provider_model),
            "thinking_levels": (getattr(provider_model, "thinking_levels", None)
                                or getattr(provider, "thinking_levels", None)),
            "thinking_off_value": (getattr(provider_model, "thinking_off_value", None)
                                   or getattr(provider, "thinking_off_value", None)),
            "thinking_style": (getattr(provider_model, "thinking_style", None)
                               or getattr(provider, "thinking_style", None)),
            "num_ctx": provider_model.num_ctx,
            "max_output_tokens": getattr(provider_model, "max_output_tokens", None),
            "cost": {
                "input": provider_model.cost.input if provider_model.cost else 0,
                "output": provider_model.cost.output if provider_model.cost else 0,
                "cache_read": provider_model.cost.cache_read if provider_model.cost else 0,
                "cache_write": provider_model.cost.cache_write if provider_model.cost else 0
            }
        }

        self.model_defs[model] = model_def
        return model_def

def _default_level(provider, model):
    explicit = (getattr(model, "thinking_default", None)
                or getattr(provider, "thinking_default", None))
    if explicit:
        return normalize_level(explicit) or AUTO
    if getattr(provider, "dynamic_models", False):
        return AUTO
    return "medium" if model.enable_thinking else "off"


llm_manager = LlmManager()
