# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : provider_service.py
# @Software    : VSCode
# @Datetime    : 2026/10/14 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 用户自备搜索接口的存储（E8.4配置面，E8.2解析环第一优先级）。key明文存库，加密另行立项

import json
import os
import sqlite3
import threading
import time
from typing import Optional

from config import config
from service.db_base import SqliteService

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_web_providers (
    uid       TEXT NOT NULL,
    provider  TEXT NOT NULL,
    api_key   TEXT,
    endpoint  TEXT,
    extra     TEXT NOT NULL DEFAULT '{}',
    enabled   INTEGER NOT NULL DEFAULT 1,
    added_at  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, provider)
);
"""

PROVIDER_KINDS = ("exa", "parallel", "tavily", "brave", "serpapi", "firecrawl", "keenable", "searxng", "custom_mcp")

# 只有自托管类型用得到 endpoint，其余走官方地址。前端会把不适用的端点输入框
# 隐藏起来，但隐藏字段仍可能带着浏览器自动填充或切换类型前的残留值；这种值
# 不该参与校验，否则在内置 provider 上会莫名其妙报“endpoint 不是完整地址”。
ENDPOINT_KINDS = ("searxng", "custom_mcp")

# 全局档的保留uid：admin配的接口，所有账号共享（用户自己的档用真实uid）
GLOBAL_SCOPE = "__global__"

# CREATE TABLE IF NOT EXISTS 不会给已存在的表补列。added_at 是后加的列，而存量库里
# 那张表没有它——enabled_for 按它排序，缺列直接 OperationalError: no such column。
MIGRATIONS = [
    ("user_web_providers", "added_at", "INTEGER NOT NULL DEFAULT 0"),
]


class ProviderConfigError(ValueError):
    pass


def _validate_kind(provider: str) -> str:
    name = (provider or "").strip().lower()
    if name not in PROVIDER_KINDS:
        raise ProviderConfigError(
            f"未知provider: {provider!r}，可用: {', '.join(PROVIDER_KINDS)}"
        )

    return name


def _validate_endpoint(url: str, *, allow_private: bool = False) -> str:
    """只许http/https且必须带主机名。endpoint是数据出口，配置期就收紧。

    allow_private=True时仍拦云元数据与回环，只放行自建服务常见的内网地址——
    自建SearXNG常跑在本机或局域网，这是合理用法；但169.254.169.254这类
    任何时候都不该由provider配置触达。
    """
    from urllib.parse import urlparse
    from service.web.web_policy import (
        ALWAYS_BLOCKED,
        PRIVATE_ALLOWED_FOR_ADMIN,
        host_block_code,
        host_block_message,
    )

    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ProviderConfigError(f"endpoint必须是完整的http/https地址: {url!r}")

    # 配置期只查IP字面量：这里做DNS解析会把离线/抖动误判成不可用
    code = host_block_code(parsed.hostname, resolve=False)
    if not code:
        return parsed.geturl()

    if code in ALWAYS_BLOCKED or not (allow_private and code in PRIVATE_ALLOWED_FOR_ADMIN):
        hint = "（自建服务可用内网地址，但该配置只能由管理员添加）" if code in PRIVATE_ALLOWED_FOR_ADMIN else ""
        raise ProviderConfigError(f"endpoint{host_block_message(parsed.hostname, resolve=False)}{hint}: {url!r}")

    return parsed.geturl()


class UserWebProviderService(SqliteService):
    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _on_new_connection(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
        for table, column, declaration in MIGRATIONS:
            if column in self._table_columns(conn, table):
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        conn.commit()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute(sql, params)
            conn.commit()

    def _execute_many(self, sql: str) -> None:
        with self._lock:
            conn = self._connection()
            conn.executescript(sql)
            conn.commit()

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            conn = self._connection()
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def upsert(self, uid: str, provider: str, *, api_key: Optional[str] = None,
               endpoint: Optional[str] = None, extra: Optional[dict] = None,
               allow_private: bool = False) -> None:
        name = _validate_kind(provider)
        needs_endpoint = name in ENDPOINT_KINDS
        ep = (_validate_endpoint(endpoint, allow_private=allow_private)
              if needs_endpoint and endpoint else None)

        if needs_endpoint and not ep:
            raise ProviderConfigError(f"provider {name} 必须配置endpoint")

        # added_at只在插入时写：冲突更新不动它，降级顺序保持「先配置的先用」
        self._execute(
            "INSERT INTO user_web_providers (uid, provider, api_key, endpoint, extra, enabled, added_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT(uid, provider) DO UPDATE SET "
            "api_key=excluded.api_key, endpoint=excluded.endpoint, extra=excluded.extra, enabled=1",
            (uid, name, api_key or None, ep, json.dumps(extra or {}, ensure_ascii=False), int(time.time())),
        )

    def set_enabled(self, uid: str, provider: str, enabled: bool) -> bool:
        name = provider.strip().lower()
        if not self.get(uid, name):
            return False

        self._execute(
            "UPDATE user_web_providers SET enabled = ? WHERE uid = ? AND provider = ?",
            (1 if enabled else 0, uid, name),
        )
        return True

    def get(self, uid: str, provider: str) -> Optional[dict]:
        rows = self._query(
            "SELECT provider, api_key, endpoint, extra, enabled, added_at FROM user_web_providers "
            "WHERE uid = ? AND provider = ?",
            (uid, provider.strip().lower()),
        )
        if not rows:
            return None

        row = rows[0]
        try:
            row["extra"] = json.loads(row["extra"] or "{}")
        except ValueError:
            row["extra"] = {}

        return row

    def set_extra_key(self, uid: str, provider: str, key: str, value: str) -> bool:
        """读-改-写extra：custom_mcp的tool名等零散配置走这里，避免整条upsert重传key。"""
        row = self.get(uid, provider)
        if not row:
            return False

        extra = dict(row["extra"])
        extra[key] = value
        self._execute(
            "UPDATE user_web_providers SET extra = ? WHERE uid = ? AND provider = ?",
            (json.dumps(extra, ensure_ascii=False), uid, provider.strip().lower()),
        )
        return True

    def remove(self, uid: str, provider: str) -> None:
        self._execute(
            "DELETE FROM user_web_providers WHERE uid = ? AND provider = ?",
            (uid, provider.strip().lower()),
        )

    def enabled_for(self, uid: str) -> list[dict]:
        rows = self._query(
            "SELECT provider, api_key, endpoint, extra FROM user_web_providers "
            "WHERE uid = ? AND enabled = 1 ORDER BY added_at, provider",
            (uid,),
        )
        for row in rows:
            try:
                row["extra"] = json.loads(row["extra"] or "{}")
            except ValueError:
                row["extra"] = {}

        return rows

    # ── 全局档（admin配，所有账号共享）────────────────────────
    # 与用户自备档同表同形状，只是uid用保留值——省掉一次schema迁移

    def list_global(self) -> list[dict]:
        return self.list_for(GLOBAL_SCOPE)

    def enabled_global(self) -> list[dict]:
        """全局档的可用行（带key，供搜索链调用）。list_for不返回key，故另起一个查询。"""
        rows = self._query(
            "SELECT provider, api_key, endpoint, extra FROM user_web_providers "
            "WHERE uid = ? AND enabled = 1 ORDER BY added_at, provider",
            (GLOBAL_SCOPE,),
        )
        for row in rows:
            try:
                row["extra"] = json.loads(row["extra"] or "{}")
            except ValueError:
                row["extra"] = {}

        return rows

    def upsert_global(self, provider: str, *, api_key: Optional[str] = None,
                      endpoint: Optional[str] = None,
                      extra: Optional[dict] = None) -> Optional[dict]:
        self.upsert(GLOBAL_SCOPE, provider, api_key=api_key, endpoint=endpoint,
                    extra=extra, allow_private=True)

        return self.get(GLOBAL_SCOPE, provider)

    def remove_global(self, provider: str) -> None:
        self.remove(GLOBAL_SCOPE, provider)

    def list_for(self, uid: str) -> list[dict]:
        return self._query(
            "SELECT provider, endpoint, enabled, "
            "CASE WHEN api_key IS NULL OR api_key = '' THEN 0 ELSE 1 END AS has_key "
            "FROM user_web_providers WHERE uid = ? ORDER BY provider",
            (uid,),
        )

    def count_user_providers(self, uids) -> int:
        """给定账号集合内自备 provider 的条目总数。

        账号删除后同库的 provider 行可能残留（历史数据），只按现存账号统计，
        避免把已不存在账号的配置算进系统信息。
        """
        valid = sorted({str(uid) for uid in uids if uid and uid != GLOBAL_SCOPE})
        if not valid:
            return 0

        placeholders = ",".join("?" * len(valid))
        rows = self._query(
            f"SELECT COUNT(*) AS n FROM user_web_providers WHERE uid IN ({placeholders})",
            tuple(valid),
        )
        return int(rows[0]["n"]) if rows else 0


user_web_provider_service = UserWebProviderService()
