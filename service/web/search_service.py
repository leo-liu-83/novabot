# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : search_service.py
# @Software    : VSCode
# @Datetime    : 2026/10/15 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 搜索三档解析环（E8.6）：用户自备 > 全局配置 > 免费兜底。只有非用户档计配额

import json
import os
import secrets
from dataclasses import dataclass
from typing import Iterable, Optional, cast

import httpx

from config import config
from logger import logger
from service.mcp.mcp_client import MCPError, mcp_call
from service.web.provider_service import user_web_provider_service
from service.web.quota_service import web_quota_service

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
FIRECRAWL_MCP_URL = "https://mcp.firecrawl.dev/v2/mcp"
# Keenable：有key按组织限流，免key走公共池（每IP 1000次/小时）
KEENABLE_API_URL = "https://api.keenable.ai/v1/search"
KEENABLE_PUBLIC_URL = "https://api.keenable.ai/v1/search/public"

# 系统内置的免key原生能力，按额度从大到小排：额度大的先用，稀有的留到最后。
# 正常路径下由config/config.json的web.search.builtin_providers定义（随发行版走，
# 相当于"系统提供的能力"）；这里的是兜底默认，配置没给时用。
# 2026-10-20 实测免key可达且适配器均能解析出结果（keenable 1000次/小时/IP，
# 响应头带X-RateLimit-*；其余三家不返回限流头，额度数字出自官方文档）
FREE_CHAIN = ("keenable", "firecrawl", "parallel", "exa")

MAX_RESULTS_CAP = 10
DEFAULT_RESULTS = 5

# 每次进程一个随机id：Parallel用它做限流，不带任何用户标识（Hermes隐私处理照抄）
_SESSION_ID = secrets.token_hex(8)

_ERR_DISABLED = "NovaBot 自带的网络搜索已被管理员禁用。请询问管理员用 /web-limit 恢复，或用 /web-config 配置你自有的搜索接口（自备接口不受配额限制）。"
_ERR_EXHAUSTED = "NovaBot 自带的网络搜索今日次数已用完（{limit} 次/天）。明天自动恢复；也可请管理员用 /web-limit 上调，或用 /web-config 配置自有接口。"
_ERR_ALL_FAILED = "所有搜索通道均失败：{reasons}\n可用 /web-config 配置自备接口，或在 novabot.json 的 web.search.providers 配全局接口。"


@dataclass(frozen=True)
class SearchOutcome:
    ok: bool
    text: str = ""
    error: str = ""
    source: str = ""
    quota_used: bool = False
    # 降级途中失败的档：(档名, 原因, 档位)，档位取 user/global/builtin
    degraded: tuple[tuple[str, str, str], ...] = ()


def _clamp(max_results) -> int:
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = DEFAULT_RESULTS

    return max(1, min(n, MAX_RESULTS_CAP))


def _render_hits(hits: list[tuple[str, str, str]]) -> str:
    return "\n\n".join(f"Title: {t}\nURL: {u}\nSnippet: {c or ''}" for t, u, c in hits)


def _load_json(raw: str, provider: str) -> dict:
    try:
        data = json.loads(raw)
    except ValueError as err:
        raise MCPError(f"{provider}返回的不是JSON") from err

    if not isinstance(data, dict):
        raise MCPError(f"{provider}返回的不是JSON对象")

    return data


def _get_json(url: str, params: dict, headers: Optional[dict] = None) -> dict:
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, params=params, headers=headers or {})
    except httpx.HTTPError as err:
        raise MCPError(f"请求失败: {err}") from err

    if resp.status_code >= 400:
        raise MCPError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError as err:
        raise MCPError("响应不是JSON——endpoint可能没开JSON格式（SearXNG需在settings.yml启用json）") from err

    if not isinstance(data, dict):
        raise MCPError("响应不是JSON对象")

    return data


def _bearer(row: dict) -> Optional[dict]:
    key = row.get("api_key") or ""
    return {"Authorization": f"Bearer {key}"} if key else None


def _search_exa(row: dict, query: str, n: int) -> str:
    key = row.get("api_key") or ""
    headers = {"x-api-key": key} if key else None
    text = mcp_call(EXA_MCP_URL, "web_search_exa", {"query": query, "numResults": n}, headers)

    return text if "Title:" in text else ""


def _search_parallel(row: dict, query: str, n: int) -> str:
    key = row.get("api_key") or ""
    headers = {"authorization": f"Bearer {key}"} if key else None
    raw = mcp_call(
        PARALLEL_MCP_URL, "web_search",
        {"objective": query, "search_queries": [query], "session_id": _SESSION_ID},
        headers,
    )
    data = _load_json(raw, "parallel")

    hits = []
    for item in (data.get("results") or [])[:n]:
        excerpts = item.get("excerpts") or []
        joined = " ".join(excerpts) if isinstance(excerpts, list) else str(excerpts)
        hits.append((item.get("title", ""), item.get("url", ""), joined))

    return _render_hits(hits)


def _search_firecrawl(row: dict, query: str, n: int) -> str:
    raw = mcp_call(
        row.get("endpoint") or FIRECRAWL_MCP_URL,
        "firecrawl_search", {"query": query, "limit": n}, _bearer(row),
    )
    data = _load_json(raw, "firecrawl")

    hits = [
        (w.get("title", ""), w.get("url", ""), w.get("description", ""))
        for w in ((data.get("data") or {}).get("web") or [])
        if isinstance(w, dict)
    ]

    return _render_hits(hits[:n])


def _search_keenable(row: dict, query: str, n: int) -> str:
    """Keenable：公共池免 key（每IP 1000次/小时，响应头带X-RateLimit-*）。

    有key走/v1/search（按组织限流），无key走/v1/search/public（公共池）。
    公共端点强制要求X-Keenable-Title头——缺了会400 Missing app identifier。
    """
    key = row.get("api_key") or ""
    if key:
        url, headers = KEENABLE_API_URL, {"X-API-Key": key}
    else:
        url, headers = KEENABLE_PUBLIC_URL, {"X-Keenable-Title": "NovaBot"}

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json={"query": query}, headers=headers)
    except httpx.HTTPError as err:
        raise MCPError(f"请求失败: {err}") from err

    if resp.status_code >= 400:
        raise MCPError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = _load_json(resp.text, "keenable")
    hits = [
        (r.get("title", ""), r.get("url", ""),
         r.get("description") or r.get("snippet") or "")
        for r in (data.get("results") or [])[:n]
        if isinstance(r, dict)
    ]

    return _render_hits(hits)


def _search_tavily(row: dict, query: str, n: int) -> str:
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.tavily.com/search",
                json={"query": query, "max_results": n},
                headers=_bearer(row) or {"X-Tavily-Access-Mode": "keyless"},
            )
    except httpx.HTTPError as err:
        raise MCPError(f"请求失败: {err}") from err

    if resp.status_code >= 400:
        raise MCPError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = _load_json(resp.text, "tavily")

    return _render_hits([(r.get("title", ""), r.get("url", ""), r.get("content", ""))
                         for r in data.get("results", []) if isinstance(r, dict)])


def _search_brave(row: dict, query: str, n: int) -> str:
    data = _get_json("https://api.search.brave.com/res/v1/web/search",
                     {"q": query, "count": n},
                     {"X-Subscription-Token": row.get("api_key") or ""})

    return _render_hits([(r.get("title", ""), r.get("url", ""), r.get("description", ""))
                         for r in (data.get("web") or {}).get("results", []) if isinstance(r, dict)])


def _search_serpapi(row: dict, query: str, n: int) -> str:
    data = _get_json("https://serpapi.com/search",
                     {"engine": "google", "q": query, "num": n, "api_key": row.get("api_key") or ""})
    if data.get("error"):
        raise MCPError(str(data["error"])[:200])

    return _render_hits([(r.get("title", ""), r.get("link", ""), r.get("snippet", ""))
                         for r in data.get("organic_results", []) if isinstance(r, dict)])


def _search_searxng(row: dict, query: str, n: int) -> str:
    endpoint = (row.get("endpoint") or "").rstrip("/")
    data = _get_json(f"{endpoint}/search", {"q": query, "format": "json", "pageno": 1})

    return _render_hits([(r.get("title", ""), r.get("url", ""), r.get("content", ""))
                         for r in data.get("results", []) if isinstance(r, dict)][:n])


def _search_custom_mcp(row: dict, query: str, n: int) -> str:
    extra = row.get("extra") or {}
    tool = extra.get("tool")
    if not tool:
        raise MCPError("custom_mcp 需要在 extra 里指定 tool（MCP工具名）")

    headers = dict(extra.get("headers") or {})
    if row.get("api_key"):
        headers.setdefault("authorization", f"Bearer {row['api_key']}")

    args = {"query": query, "numResults": n, **(extra.get("args") or {})}
    return mcp_call(row["endpoint"], tool, args, headers or None)


_ADAPTERS = {
    "exa": _search_exa,
    "parallel": _search_parallel,
    "firecrawl": _search_firecrawl,
    "keenable": _search_keenable,
    "tavily": _search_tavily,
    "brave": _search_brave,
    "serpapi": _search_serpapi,
    "searxng": _search_searxng,
    "custom_mcp": _search_custom_mcp,
}


def builtin_rows() -> list[dict]:
    """系统内置的免key原生能力。

    这一档是**代码固化**的：适配器与集合都写死在代码里，放进配置文件没有意义
    （能配的只有顺序，而顺序由额度决定、也是固定的）。环境变量可给某一档加key抬额度。
    """
    return [
        {"provider": name, "api_key": os.environ.get(f"{name.upper()}_API_KEY") or None,
         "endpoint": None, "extra": {}}
        for name in FREE_CHAIN
    ]


_legacy_warned = False


def _global_rows(providers) -> list:
    """全局档：admin配的接口，所有账号共享。存在数据库的global scope里。

    旧配置文件（novabot.json的web.search.providers）那一档仍然并入，但排在数据库之后——
    存量部署不能因为搬了家就静默失效。数据库优先（同名遮蔽）。
    """
    global _legacy_warned

    rows: list[dict] = []
    lister = getattr(providers, "enabled_global", None)
    if callable(lister):
        rows = [dict(r) for r in cast(Iterable[dict], lister())]

    legacy = cast(Iterable[dict], getattr(config, "WEB_SEARCH_PROVIDERS", ()) or ())
    known = {r.get("provider") for r in rows}
    for row in legacy:
        if row.get("provider") and row["provider"] not in known:
            rows.append(dict(row))
            if not _legacy_warned:
                _legacy_warned = True
                logger.warning(
                    "[web] novabot.json 的 web.search.providers 已改由数据库管理（/web-config 的"
                    "「管理全局搜索接口」）。旧配置仍生效，但建议迁移。")

    return rows


def _try_chain(rows: list, query: str, n: int, attempts: list, tier: str = "builtin") -> tuple:
    """按顺序试。空结果与异常都视为该档不可用，继续下一档。

    attempts 收 (档名, 原因, 档位)，供上层把"哪个端点连不上"讲给用户听。
    """
    for row in rows:
        name = row.get("provider") or ""
        adapter = _ADAPTERS.get(name)
        if adapter is None:
            attempts.append((name, "无适配器", tier))
            continue

        try:
            text = adapter(row, query, n)
        except (MCPError, httpx.HTTPError, KeyError, TypeError, ValueError) as err:
            attempts.append((name, str(err)[:120], tier))
            continue

        if not (text or "").strip():
            attempts.append((name, "无结果", tier))
            continue

        return text, name

    return "", ""


def search(uid: str, query: str, max_results=DEFAULT_RESULTS,
           *, providers=None, quota=None, globals_=None) -> SearchOutcome:
    """三档解析：用户自备 > 全局配置 > 免费兜底。

    配额规则：只有非用户档才计一次——用户自备的账由用户自己付；
    全局与免费档都是NovaBot管理的共享容量，共用同一个闸门。
    """
    providers = providers if providers is not None else user_web_provider_service
    quota = quota if quota is not None else web_quota_service
    if globals_ is None:
        globals_ = _global_rows(providers)

    n = _clamp(max_results)
    query = (query or "").strip()
    if not query:
        return SearchOutcome(False, error="搜索词不能为空")

    attempts: list[tuple[str, str, str]] = []

    text, name = _try_chain(providers.enabled_for(uid), query, n, attempts, "user")
    if text:
        return SearchOutcome(True, text=text, source=f"user:{name}", quota_used=False)

    global_rows = [dict(r) for r in (globals_ or ()) if r.get("provider")]
    global_names = set()
    for row in global_rows:
        global_names.add(row["provider"])
    builtin_only = [r for r in builtin_rows() if r["provider"] not in global_names]

    allowed, state = quota.consume(uid)
    if not allowed:
        if not state.enabled:
            return SearchOutcome(False, error=_ERR_DISABLED, degraded=tuple(attempts))
        return SearchOutcome(False, error=_ERR_EXHAUSTED.format(limit=state.daily_limit),
                              degraded=tuple(attempts))

    text, name = _try_chain(global_rows, query, n, attempts, "global")
    if not text:
        text, name = _try_chain(builtin_only, query, n, attempts, "builtin")
    if text:
        tier = "nova-global" if name in global_names else "nova-free"
        return SearchOutcome(True, text=text, source=f"{tier}:{name}", quota_used=True,
                              degraded=tuple(attempts))

    quota.refund(uid)
    reasons = "；".join(f"{n}: {r}" for n, r, _tier in attempts) or "无可用通道"
    return SearchOutcome(False, error=_ERR_ALL_FAILED.format(reasons=reasons),
                          degraded=tuple(attempts))
