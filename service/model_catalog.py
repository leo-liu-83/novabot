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
from service.config.dtos import ModelDto, ProviderDto
from util.request_headers import resolve_runtime_headers

log = logging.getLogger(__name__)


def _positive_int(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


class ModelCatalog:
    TTL = 300.0
    RETRY_TTL = 60.0
    TIMEOUT = 5.0

    def __init__(self, fetcher: Optional[Callable[[ProviderDto], list[ModelDto]]] = None):
        self._fetcher = fetcher or self._fetch_models
        self._lock = threading.RLock()
        # 同 code 的 provider 可能有多份（内置 + 用户覆盖），按 code + 发现地址区分
        self._cache: dict[tuple[str, str], tuple[float, list[ModelDto]]] = {}

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
            except Exception as exc:
                log.warning("模型发现失败 [%s]: %s", provider.code, exc)
                models = []
                ttl = self.RETRY_TTL

            self._cache[key] = (now + ttl, models)
            return models

    def _fetch_models(self, provider: ProviderDto) -> list[ModelDto]:
        url = self._models_url(provider)

        headers = {}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
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
