# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
"""模型目录：按 provider 配置动态发现 OpenAI 兼容的 /models 列表。

配置里给 provider 打开 `dynamic_models` 后，模型列表不再需要手写：
- 没写 `models`：暴露 /models 返回的全部 chat 模型
- 写了 `models`：只使用这些静态模型，且完全不请求 /models
发现接口失败时不影响启动；静态 models 始终按配置原样使用。
"""

import logging
import threading
import time
from dataclasses import replace
from typing import Callable, Optional

import httpx

from config import config
from core.llm.thinking import LEVELS, levels_for, normalize_level, resolve_style
from service.config.dtos import ModelDto, ProviderDto
from util.request_headers import resolve_runtime_headers
from util.secrets import is_ciphertext

log = logging.getLogger(__name__)


def _positive_int(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def has_credentials(provider: ProviderDto) -> bool:
    """provider 是否带了可用的凭据：明文 key，或非密文的 Authorization 请求头。

    内网网关常常不收 key、只认 `headers.Authorization`；这类 provider 没有 api_key
    但照样能用。密文说明本机没有主密钥/不匹配，不能当作可用凭据。
    """
    key = provider.api_key
    if key and not is_ciphertext(key):
        return True
    headers = provider.headers if isinstance(provider.headers, dict) else {}
    for name, value in headers.items():
        if str(name).lower() != "authorization":
            continue
        if isinstance(value, str) and value.strip() and not is_ciphertext(value):
            return True
    return False


def is_usable(model_id: str) -> bool:
    """静态配置能判断的部分：provider 存在、有凭据、静态模型在清单里。

    动态发现型 provider 不查模型清单——那要发一次网络请求，而发现结果本来就可能有延迟；
    真正调用时的错误由模型层报，这里只挡住"模型 id 写错/没配凭据"这类必然失败。
    """
    provider_code, _, model_code = (model_id or "").partition("/")
    if not provider_code or not model_code:
        return False
    data = config.CONFIG_DATA
    providers = [p for p in (data.models if data else []) if p.code == provider_code]
    if not providers:
        return False
    provider = providers[-1]  # 与 llm_manager 一致：同 code 取最后一个（用户配置覆盖内置）
    if not has_credentials(provider):
        return False
    if provider.dynamic_models:
        return True
    return any(m.code == model_code for m in (provider.models or []))


def thinking_options(model_id: str) -> dict:
    """某个模型可选的思考档位与默认档位；查不到时给通用四档。

    UI 只按这里给的档位画选项：只认开关的端点不该出现"高/中/低"三个看起来一样的档。
    """
    provider_code, _, model_code = (model_id or "").partition("/")
    data = config.CONFIG_DATA
    for provider in reversed(list(data.models if data and data.models else [])):
        if provider.code != provider_code:
            continue
        model = next((item for item in reversed(list(provider.models or []))
                      if item.code == model_code), None)
        # 动态发现型 provider 没有静态模型条目：档位默认只能来自 provider 级配置
        style = resolve_style(provider.base_url,
                              (model.thinking_style if model else None)
                              or getattr(provider, "thinking_style", None))
        levels = levels_for(style, (model.thinking_levels if model else None)
                            or getattr(provider, "thinking_levels", None))
        explicit = normalize_level(getattr(provider, "thinking_default", None))
        if model is not None:
            # 静态模型：模型级 > provider 级 > 老字段 enable_thinking
            default = (normalize_level(model.thinking_default) or explicit
                       or ("medium" if model.enable_thinking else "off"))
        else:
            # 发现出来的模型没有模型级配置：没配 provider 默认就跟随模型自身
            default = explicit
        return {"default": default, "levels": list(levels)}
    return {"default": None, "levels": list(LEVELS)}


class ModelCatalog:
    TTL = 300.0
    RETRY_TTL = 60.0
    TIMEOUT = 5.0

    def __init__(self, fetcher: Optional[Callable[[ProviderDto], list[ModelDto]]] = None):
        self._fetcher = fetcher or self._fetch_models
        self._lock = threading.RLock()
        # 同 code 的 provider 可能有多份（内置 + 用户覆盖），按 code + 发现地址区分
        self._cache: dict[tuple[str, str], tuple[float, list[ModelDto]]] = {}
        # 最近一次发现失败的原因：界面据此说清"为什么没有模型"，而不是只展示空列表
        self._errors: dict[str, str] = {}

    def providers(self) -> list[ProviderDto]:
        """返回当前可用 provider；动态 provider 的 models 已合并发现结果。"""
        data = config.CONFIG_DATA
        if not data or not data.models:
            return []

        providers = []
        for provider in data.models:
            if not provider.dynamic_models:
                providers.append(provider)
            else:
                providers.append(self._with_discovered_models(provider))
        return providers

    def errors(self) -> dict[str, str]:
        """provider code → 最近一次模型发现失败的原因（成功一次就清掉）。"""
        with self._lock:
            return dict(self._errors)

    def invalidate(self, code: Optional[str] = None) -> None:
        with self._lock:
            if code is None:
                self._cache.clear()
                return
            for key in [key for key in self._cache if key[0] == code]:
                self._cache.pop(key, None)

    def _with_discovered_models(self, provider: ProviderDto) -> ProviderDto:
        # 配了 models 就是静态白名单：只允许这些模型，且完全不请求 /models。
        # 需要自动发现时把 models 留空。
        if provider.models:
            return provider

        discovered = self._discover(provider)
        if not discovered:
            return provider

        return replace(provider, models=discovered)

    @staticmethod
    def _models_url(provider: ProviderDto) -> str:
        return provider.models_url or provider.base_url.rstrip("/") + "/models"

    def _discover(self, provider: ProviderDto) -> list[ModelDto]:
        now = time.monotonic()
        key = (provider.code, self._models_url(provider))
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]

            try:
                models = self._fetcher(provider)
                ttl = self.TTL
                self._errors.pop(provider.code, None)
            except Exception as exc:
                log.warning("模型发现失败 [%s]: %s", provider.code, exc)
                self._errors[provider.code] = f"{type(exc).__name__}: {exc}"[:200]
                models = []
                ttl = self.RETRY_TTL

            self._cache[key] = (now + ttl, models)
            return models

    def _fetch_models(self, provider: ProviderDto) -> list[ModelDto]:
        url = self._models_url(provider)

        headers = {}
        if provider.api_key and not is_ciphertext(provider.api_key):
            headers["Authorization"] = f"Bearer {provider.api_key}"
        elif is_ciphertext(provider.api_key):
            log.warning("模型发现跳过：%s 的 key 是密文（主密钥缺失或不匹配）", provider.code)
        if provider.host:
            headers["Host"] = provider.host
        headers.update(resolve_runtime_headers(provider.headers, None) or {})

        with httpx.Client(timeout=self.TIMEOUT, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()

        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []

        models = []
        for row in rows:
            model = self._to_model(row)
            if model:
                models.append(model)
        return models

    @staticmethod
    def _to_model(row) -> Optional[ModelDto]:
        if not isinstance(row, dict):
            return None

        code = str(row.get("id") or "").strip()
        if not code:
            return None

        # OpenCode 会标 type=chat；其它平台的 /models 通常没有这个字段
        kind = row.get("type")
        if kind and str(kind).lower() not in ("chat", "language", "text"):
            return None

        # OpenRouter 用 architecture.output_modalities 标多模态；只要文本输出的模型
        architecture = _dict(row.get("architecture"))
        outputs = architecture.get("output_modalities")
        if isinstance(outputs, list) and outputs:
            if "text" not in {str(item).lower() for item in outputs}:
                return None

        limit = _dict(row.get("limit"))
        top_provider = _dict(row.get("top_provider"))
        num_ctx = _positive_int(
            row.get("context_length")
            or row.get("max_context_length")
            or row.get("context_window")
            or limit.get("context")
        )
        max_output = _positive_int(
            top_provider.get("max_completion_tokens")
            or limit.get("output")
            or row.get("max_output_tokens")
            or row.get("max_completion_tokens")
        )

        return ModelDto(
            code=code,
            name=str(row.get("name") or code),
            num_ctx=num_ctx,
            max_output_tokens=max_output or None,
        )


model_catalog = ModelCatalog()
