# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : web_policy.py
# @Software    : VSCode
# @Datetime    : 2026/10/14 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 抓取权限的纯解析层（不import config，避免base_config循环依赖）

import ipaddress
import socket

PERMISSIONS = ("ask", "allow", "deny")
DEFAULT_PERMISSION = "ask"


def parse_permission(value) -> str:
    """认不出的一律按ask——与exec.level认不出按T0同一fail-closed立场。"""
    text = str(value or "").strip().lower()
    return text if text in PERMISSIONS else DEFAULT_PERMISSION


def host_allowed(host: str, allowlist: tuple) -> bool:
    """白名单按域名后缀匹配：github.com命中github.com与raw.github.com。
    必须是点边界后缀——allowlist里的example.com不得命中notexample.com。
    """
    if not host:
        return False

    h = host.lower().rstrip(".")
    for entry in allowlist:
        e = str(entry).lstrip(".").lower().rstrip(".")
        if not e:
            continue
        if h == e or h.endswith(f".{e}"):
            return True

    return False


# 主机禁止原因用码表示，调用方自己决定措辞与是否放行
BLOCK_NO_HOST = "no_host"
BLOCK_LOCALHOST_NAME = "localhost_name"
BLOCK_LOOPBACK = "loopback"
BLOCK_PRIVATE = "private"
BLOCK_LINK_LOCAL = "link_local"
BLOCK_RESERVED = "reserved"
BLOCK_UNRESOLVED = "unresolved"

_BLOCK_MESSAGES = {
    BLOCK_NO_HOST: "缺少主机名",
    BLOCK_LOCALHOST_NAME: "禁止访问本机地址",
    BLOCK_LOOPBACK: "禁止访问回环地址",
    BLOCK_PRIVATE: "禁止访问内网地址",
    BLOCK_LINK_LOCAL: "禁止访问链路本地/云元数据地址",
    BLOCK_RESERVED: "禁止访问保留地址",
    BLOCK_UNRESOLVED: "域名无法解析",
}

# 云元数据与链路本地：任何身份、任何用途都不该由用户配置触达
ALWAYS_BLOCKED = (BLOCK_LINK_LOCAL, BLOCK_RESERVED)

# 回环与内网：自建SearXNG/本地MCP常见于本机或局域网，仅管理员可配
PRIVATE_ALLOWED_FOR_ADMIN = (BLOCK_LOOPBACK, BLOCK_PRIVATE, BLOCK_LOCALHOST_NAME)


def host_block_code(host: str, *, resolve: bool = True) -> str:
    """返回禁止原因码，空串表示放行。

    resolve=False时只检查IP字面量，不做DNS解析——配置期解析DNS太脆：
    离线或DNS抖动会把合法公网域名误判为不可用。真正的解析校验放在请求期（webfetch走这条）。
    """
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return BLOCK_NO_HOST

    if h == "localhost" or h.endswith(".localhost"):
        return BLOCK_LOCALHOST_NAME

    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        if not resolve:
            return ""
        try:
            infos = socket.getaddrinfo(h, None)
        except OSError:
            return BLOCK_UNRESOLVED
    else:
        infos = [(None, None, None, None, (str(ip), 0))]

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_unspecified:
            return BLOCK_LINK_LOCAL
        if ip.is_multicast:
            return BLOCK_RESERVED
        if ip.is_loopback:
            return BLOCK_LOOPBACK
        if ip.is_private:
            return BLOCK_PRIVATE
        if ip.is_reserved:
            return BLOCK_RESERVED

    return ""


def host_block_message(host: str, *, resolve: bool = True) -> str:
    code = host_block_code(host, resolve=resolve)
    return _BLOCK_MESSAGES.get(code, "")


class EndpointPolicyError(ValueError):
    """endpoint被策略拒绝（配置期）"""


def validate_endpoint(url: str, *, allow_private: bool = False) -> str:
    """校验一个由用户填写的出站endpoint，返回规范化后的URL。

    出站地址是数据出口，配置期就收紧：只许http/https且必须带主机名；
    云元数据/链路本地/保留地址任何身份都不该触达；回环与内网仅管理员可配
    （自建SearXNG/本地MCP常跑在本机或局域网，是合理用法）。

    配置期只查IP字面量，不做DNS解析——离线或DNS抖动会把合法公网域名误判为不可用。
    代价是存在DNS rebinding窗口（解析与请求分时），与web provider同一姿态（README §10 已记）。
    """
    from urllib.parse import urlparse

    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise EndpointPolicyError(f"endpoint必须是完整的http/https地址: {url!r}")

    code = host_block_code(parsed.hostname, resolve=False)
    if not code:
        return parsed.geturl()

    if code in ALWAYS_BLOCKED or not (allow_private and code in PRIVATE_ALLOWED_FOR_ADMIN):
        hint = ("（自建服务可用内网地址，但该配置只能由管理员添加）"
                if code in PRIVATE_ALLOWED_FOR_ADMIN else "")
        raise EndpointPolicyError(
            f"endpoint{host_block_message(parsed.hostname, resolve=False)}{hint}: {url!r}")

    return parsed.geturl()
