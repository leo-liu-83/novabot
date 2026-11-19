# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
"""模型请求头里的动态字段解析。"""

from typing import Optional
from urllib.parse import urlparse

from exception.model_exception import ModelCallError
from util.secrets import is_ciphertext

# 请求头里可用的会话占位符；发送前替换成当前会话 ID
SESSION_ID_PLACEHOLDER = "${session_id}"
# 这个头按语义就是会话 ID：只要配置了，运行时有会话 ID 就自动替换
SESSION_ID_HEADER = "x-opencode-session"


def resolve_runtime_headers(headers: Optional[dict], session_id: Optional[str]) -> Optional[dict]:
    """把配置里的静态请求头解析成本次调用使用的请求头。

    - 值里的 `${session_id}` 替换成当前会话 ID；拿不到会话 ID 时跳过该头，
      避免把占位符原样发给平台。
    - `x-opencode-session` 按 OpenCode Go 的语义特殊处理：配置了就自动填会话 ID，
      不必写占位符；没有会话 ID 时保留配置值作为兜底。
    """
    if not headers:
        return None

    sid = str(session_id) if session_id else None
    resolved = {}
    for key, value in headers.items():
        text = str(value)
        if is_ciphertext(text):
            raise ModelCallError(
                f"请求头 {key} 仍是密文（主密钥缺失或与配置不匹配）："
                "请补回主密钥后重启，或把该值改回明文")
        if key.lower() == SESSION_ID_HEADER and sid:
            resolved[key] = sid
            continue
        if SESSION_ID_PLACEHOLDER in text:
            if not sid:
                continue
            text = text.replace(SESSION_ID_PLACEHOLDER, sid)
        resolved[key] = text
    return resolved or None


def add_provider_session_header(headers: Optional[dict], base_url: Optional[str],
                                session_id: Optional[str]) -> Optional[dict]:
    """给 OpenCode Go 补上会话路由头。

    该平台要求 `x-opencode-session`，缺失时 chat 接口直接 400；
    安装向导生成的配置不会自带 headers，所以这里按端点域名兜底。
    只对 opencode.ai 生效，不会把会话 ID 发给其它服务。
    """
    if not session_id or not base_url:
        return headers

    try:
        host = (urlparse(base_url).hostname or "").lower()
    except ValueError:
        return headers

    if host != "opencode.ai" and not host.endswith(".opencode.ai"):
        return headers

    merged = dict(headers or {})
    merged[SESSION_ID_HEADER] = str(session_id)
    return merged
