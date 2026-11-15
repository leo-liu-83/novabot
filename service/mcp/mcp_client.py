# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : mcp_client.py
# @Software    : VSCode
# @Datetime    : 2026/10/14 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 最小MCP客户端：直接tools/call（无initialize握手，学opencode），JSON与SSE双解

import json
import re
from typing import Optional

import httpx

_TIMEOUT_SECONDS = 30

# 很多provider返HTTP 200但错误在body里（Hermes教训），只看状态码会卡死在坏provider上
_RATE_LIMIT_MARKERS = (
    "rate limit", "rate-limit", "ratelimit",
    "too many requests", "429", "quota exceeded", "slow down",
)

# SSE帧必须强制UTF-8：text/*无charset时httpx的resp.text会猜ISO-8859-1导致乱码（Hermes锁过这个坑）
_SSE_DATA = re.compile(r"^data:\s*(.*)$")


class MCPError(RuntimeError):
    pass


class MCPRateLimitedError(MCPError):
    pass


def _is_rate_limited(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def _raise_for_envelope(envelope: dict) -> Optional[str]:
    error = envelope.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error)
    elif error is not None:
        message = str(error)
    else:
        message = ""

    if message:
        if _is_rate_limited(message):
            raise MCPRateLimitedError(message)
        raise MCPError(message)

    result = envelope.get("result")
    if not isinstance(result, dict):
        return None

    parts = [p.get("text", "") for p in (result.get("content") or []) if isinstance(p, dict) and p.get("type") == "text"]
    if not parts:
        return None

    text = "\n".join(t for t in parts if t)
    # 只有信封声明失败才做限流字样嗅探。成功结果绝不做：Parallel/Firecrawl的text是
    # 结果JSON，页面正文里出现"rate limit"或带429的URL会把成功的搜索误判丢弃。
    if not result.get("isError"):
        return text

    if _is_rate_limited(text):
        raise MCPRateLimitedError(text)

    raise MCPError(text or "MCP tool returned an error with no text content")


def _parse_body(raw: bytes) -> dict:
    try:
        envelope = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(envelope, dict):
            return envelope
    except (ValueError, UnicodeDecodeError):
        pass

    # SSE：逐行取 data: 帧，优先带 result/error 的那条。
    # 只按\n切：str.splitlines()会把U+0085(NEL)等也当行边界，正好切碎帧内的JSON（Hermes锁过的坑）
    decoded = raw.decode("utf-8", errors="replace")
    fallback = None
    for line in decoded.split("\n"):
        match = _SSE_DATA.match(line.strip())
        if not match or match.group(1) in ("", "[DONE]"):
            continue
        try:
            frame = json.loads(match.group(1))
        except ValueError:
            continue
        if not isinstance(frame, dict):
            continue
        if "result" in frame or "error" in frame:
            return frame
        fallback = fallback or frame

    if fallback is not None:
        return fallback

    raise MCPError("响应既不是JSON-RPC也不是可解析的SSE帧")


def mcp_call(url: str, tool: str, arguments: dict, headers: Optional[dict] = None,
             timeout: int = _TIMEOUT_SECONDS) -> str:
    """裸 tools/call：不握手、不管session。

    只对"愿意被裸调"的端点有效（我们用的搜索类MCP端点）。正规MCP server要求
    initialize握手——那种用 McpSession。
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                url,
                json=payload,
                headers={"Accept": "application/json, text/event-stream", **(headers or {})},
            )
    except httpx.HTTPError as err:
        raise MCPError(f"MCP请求失败: {err}") from err

    if resp.status_code == 429:
        raise MCPRateLimitedError(f"HTTP 429: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise MCPError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    envelope = _parse_body(resp.content)
    text = _raise_for_envelope(envelope)
    if text is None:
        raise MCPError("MCP响应里没有text内容")

    return text


# 协议版本按新到旧尝试协商：服务端可能只支持旧版
_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
_CLIENT_INFO = {"name": "novabot", "version": "1.0"}


class McpSession:
    """一次MCP会话：initialize握手 → notifications/initialized → tools/list / tools/call。

    只覆盖 Streamable HTTP（POST + JSON/SSE双解）。stdio传输与OAuth不在本版范围
    （见 TODO E9.1）；需要时再评估引官方SDK——那时只替换传输层，上层设计不变。
    """

    def __init__(self, url: str, headers: Optional[dict] = None, timeout: int = _TIMEOUT_SECONDS):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self.protocol_version: Optional[str] = None
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self._next_id = 0

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _post(self, payload: dict) -> httpx.Response:
        headers = {"Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        try:
            with httpx.Client(timeout=self.timeout) as client:
                return client.post(self.url, json=payload, headers=headers)
        except httpx.HTTPError as err:
            raise MCPError(f"MCP请求失败: {err}") from err

    def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._id(), "method": method}
        if params is not None:
            payload["params"] = params

        resp = self._post(payload)
        if resp.status_code == 429:
            raise MCPRateLimitedError(f"HTTP 429: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise MCPError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        envelope = _parse_body(resp.content)
        error = envelope.get("error")
        if error is not None:
            message = str(error.get("message") or error) if isinstance(error, dict) else str(error)
            if _is_rate_limited(message):
                raise MCPRateLimitedError(message)
            raise MCPError(message)

        result = envelope.get("result")
        return result if isinstance(result, dict) else {}

    def initialize(self) -> dict:
        """握手并协商协议版本。版本不被支持时回退到更旧的版本重试。"""
        last_error = ""
        for version in _PROTOCOL_VERSIONS:
            params = {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            }
            payload = {"jsonrpc": "2.0", "id": self._id(), "method": "initialize", "params": params}

            resp = self._post(payload)
            if resp.status_code in (401, 403):
                # 鉴权问题与版本无关，换版本重试没有意义
                raise MCPError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                continue

            envelope = _parse_body(resp.content)
            error = envelope.get("error")
            if error is not None:
                last_error = str(error.get("message") or error) if isinstance(error, dict) else str(error)
                continue

            result = envelope.get("result") or {}
            self.protocol_version = result.get("protocolVersion") or version
            self.server_info = result.get("serverInfo") or {}
            self.capabilities = result.get("capabilities") or {}
            # session id在响应头里（Streamable HTTP）
            sid = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid

            # initialized通知：服务端回202空体，不能当JSON解析
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return result

        raise MCPError(f"MCP initialize 失败（试过协议版本{','.join(_PROTOCOL_VERSIONS)}）: {last_error}")

    def list_tools(self) -> list[dict]:
        """列出工具（跟随 nextCursor 翻页）。"""
        tools: list[dict] = []
        cursor: Optional[str] = None

        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._rpc("tools/list", params)
            tools.extend(t for t in (result.get("tools") or []) if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break

        return tools

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> str:
        """调用工具，返回文本内容。错误由 _raise_for_envelope 统一转成 MCPError。"""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        text = _raise_for_envelope({"result": result})
        return text or ""

    def probe(self) -> dict:
        """探测：握手 + 列工具。用于"配了不等于能用"的自检（见 TODO E8.15）。"""
        self.initialize()
        tools = self.list_tools()

        return {
            "server_info": self.server_info,
            "protocol_version": self.protocol_version,
            "session_id": self.session_id,
            "tools": [{"name": t.get("name", ""), "description": t.get("description", "")}
                      for t in tools],
        }
