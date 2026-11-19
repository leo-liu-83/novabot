# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : registry.py
# @Software    : VSCode
# @Author      : leo liu
# @Version     : 1.0
# @Description : MCP server注册表：系统层（全局共享，默认启动）与用户层（每用户自配，默认关闭）

import json
import os
import sqlite3
import threading
import time
from typing import Optional

from config import config
from service.db_base import SqliteService
from util.secrets import is_ciphertext

# 每用户可连接的server数量上限（用户层）。系统层不占用户额度——
# 否则用户额度会被全局配置占掉，用户自己一个都连不上。
DEFAULT_MAX_USER_SERVERS = 3

# 每个server的审批模式：ask=每次确认（默认）、allow=信任免确认、deny=不用
MCP_MODES = ("ask", "allow", "deny")


def _ciphertext_headers(headers: dict) -> bool:
    """请求头里是否还有没解开的密文：这种值不能当 token 发给 MCP server。"""
    return any(is_ciphertext(value) for value in (headers or {}).values())


def resolve_tool_mode(server_mode: str, tool_mode: Optional[str]) -> str:
    """单个工具的生效模式：工具级覆盖优先，缺失/非法时继承 server。

    继承而不是默认 ask，是为了让"整个 server 信任"仍然一次生效；
    要收紧某个写操作，就给那一个工具单独设 ask/deny。
    """
    if tool_mode in MCP_MODES:
        return str(tool_mode)

    return server_mode if server_mode in MCP_MODES else "ask"

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_mcp_servers (
    uid        TEXT NOT NULL,
    name       TEXT NOT NULL,
    url        TEXT NOT NULL,
    headers    TEXT NOT NULL DEFAULT '{}',
    enabled    INTEGER NOT NULL DEFAULT 0,
    mode       TEXT NOT NULL DEFAULT 'ask',
    -- 上次成功探测到的工具名（JSON 数组）：只是界面缓存，真正注册工具时仍会实时连接
    tools      TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    PRIMARY KEY (uid, name)
);

-- admin对每个账号的MCP能力总开关：开=该用户可用自己的MCP server
CREATE TABLE IF NOT EXISTS user_mcp_grants (
    uid        TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

-- 用户自己设的"最多同时连几个"
CREATE TABLE IF NOT EXISTS user_mcp_limits (
    uid         TEXT PRIMARY KEY,
    max_servers INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

-- 工具级模式覆盖（E9.2.2）：缺行 = 继承所属 server 的 mode
CREATE TABLE IF NOT EXISTS user_mcp_tool_modes (
    uid        TEXT NOT NULL,
    server     TEXT NOT NULL,
    tool       TEXT NOT NULL,
    mode       TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (uid, server, tool)
);
"""

# 迁移：老库里的user_mcp_servers缺mode列（E8.11引入该表时还没有mode）
MIGRATIONS = [
    ("user_mcp_servers", "mode", "TEXT NOT NULL DEFAULT 'ask'"),
    # 工具列表只做界面缓存：老库补一列默认空数组，不影响实时连接与调用
    ("user_mcp_servers", "tools", "TEXT NOT NULL DEFAULT '[]'"),
]


class McpRegistry(SqliteService):
    """用户层MCP server的存储、限额与能力开关。

    权限分工（用户 2026-10-21 定）：
    - **admin 只管"开不开"**：user_mcp_grants 决定该账号能不能用自己的 MCP server
    - **用户自己管"怎么用"**：配哪些 server、每个 server 的审批模式(ask/allow/deny)、
      逐工具覆盖、启停、最多同时连几个（user_mcp_limits）

    为什么审批模式交给用户自己：MCP 没有可验证的"只读"——annotations.readOnlyHint 是
    server 自述的提示，规范明说不可作为安全判据（除非该 server 本就可信）。
    所以"免确认"的授权只能来自人的显式决定，而不是 server 的自我声明。
    """

    def _on_new_connection(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
        for table, column, declaration in MIGRATIONS:
            if column in self._table_columns(conn, table):
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        conn.commit()

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set:
        # 不自己加锁：调用方已持锁，Lock不可重入，重复加锁会死锁
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

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

    @staticmethod
    def _decode_json_columns(row: dict) -> None:
        """把 SQLite 里的 JSON 文本列还原成 Python 值；坏数据按空值处理。"""
        try:
            row["headers"] = json.loads(row.get("headers") or "{}")
        except ValueError:
            row["headers"] = {}
        try:
            tools = json.loads(row.get("tools") or "[]")
        except ValueError:
            tools = []
        # 只接受字符串数组：old/异常数据不能把界面渲染搞崩
        row["tools"] = [str(t) for t in tools if isinstance(t, (str, int, float))] if isinstance(tools, list) else []

    # ── 用户层server ──────────────────────────────────────────

    def list_user_servers(self, uid: str) -> list[dict]:
        rows = self._query(
            "SELECT name, url, headers, enabled, mode, tools, created_at "
            "FROM user_mcp_servers WHERE uid = ? ORDER BY created_at, name", (uid,))
        for row in rows:
            self._decode_json_columns(row)

        return rows

    def upsert_user_server(self, uid: str, name: str, url: str,
                           headers: Optional[dict] = None, mode: str = "ask",
                           allow_private: bool = False) -> dict:
        """新增/更新用户层server。审批模式默认ask（每次确认）。

        URL是出站地址，配置期就收紧：云元数据/链路本地任何身份都拦，
        回环与内网仅管理员可配（本地MCP server常跑在本机或局域网）。
        """
        from service.web.web_policy import EndpointPolicyError, validate_endpoint

        clean = (name or "").strip()
        if not clean or not (url or "").strip():
            raise ValueError("MCP server 需要 name 与 url")
        if mode not in MCP_MODES:
            raise ValueError(f"审批模式只能是 {'/'.join(MCP_MODES)}")

        try:
            endpoint = validate_endpoint(url, allow_private=allow_private)
        except EndpointPolicyError as err:
            raise ValueError(str(err)) from err

        # URL 变了，旧工具列表一定属于旧端点，必须清掉；只改模式则保留界面缓存
        self._execute(
            "INSERT INTO user_mcp_servers (uid, name, url, headers, enabled, mode, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?) ON CONFLICT(uid, name) DO UPDATE SET "
            "url=excluded.url, headers=excluded.headers, mode=excluded.mode, "
            "tools=CASE WHEN url = excluded.url THEN tools ELSE '[]' END",
            (uid, clean, endpoint, json.dumps(headers or {}, ensure_ascii=False),
             mode, int(time.time())),
        )

        return self.get_user_server(uid, clean) or {}

    def get_user_server(self, uid: str, name: str) -> Optional[dict]:
        rows = self._query(
            "SELECT name, url, headers, enabled, mode, tools, created_at "
            "FROM user_mcp_servers WHERE uid = ? AND name = ?", (uid, name))
        if not rows:
            return None
        self._decode_json_columns(rows[0])

        return rows[0]

    def remove_user_server(self, uid: str, name: str) -> bool:
        # 先确认存在再删：原实现返回的是"删完还查得到吗"，永远为 False，
        # 调用方据此判断"没删到"会把成功当成 404。
        existed = bool(self._query(
            "SELECT 1 FROM user_mcp_servers WHERE uid = ? AND name = ?", (uid, name)))
        if not existed:
            return False

        self._execute("DELETE FROM user_mcp_servers WHERE uid = ? AND name = ?", (uid, name))
        # 级联删工具级覆盖：server 都没了，孤立的工具模式只会让下次同名 server 继承到旧规则
        self.clear_server_tool_modes(uid, name)

        return True

    def set_user_server_enabled(self, uid: str, name: str, enabled: bool) -> bool:
        """用户自己开/关自己配的server。开启还需该账号的MCP能力已被admin开放。"""
        self._execute("UPDATE user_mcp_servers SET enabled = ? WHERE uid = ? AND name = ?",
                      (1 if enabled else 0, uid, name))

        return bool(self._query(
            "SELECT 1 FROM user_mcp_servers WHERE uid = ? AND name = ?", (uid, name)))

    def set_user_server_mode(self, uid: str, name: str, mode: str) -> bool:
        """用户自己设该server的审批模式：ask（每次确认）/ allow（信任，免确认）/ deny（不用）。"""
        if mode not in MCP_MODES:
            raise ValueError(f"审批模式只能是 {'/'.join(MCP_MODES)}")

        self._execute("UPDATE user_mcp_servers SET mode = ? WHERE uid = ? AND name = ?",
                      (mode, uid, name))

        return bool(self._query(
            "SELECT 1 FROM user_mcp_servers WHERE uid = ? AND name = ?", (uid, name)))

    def set_user_server_tools(self, uid: str, name: str, tools: list[str]) -> None:
        """缓存一次成功探测到的工具名，仅用于界面展示。

        工具列表是远端状态，真正注册给模型时仍按实时连接发现；这里存下来只为
        刷新页面后不用重新连一遍就能看到上次的结果。
        """
        clean: list[str] = []
        for tool in tools or []:
            text = str(tool).strip()
            if text and text not in clean:
                clean.append(text)

        self._execute("UPDATE user_mcp_servers SET tools = ? WHERE uid = ? AND name = ?",
                      (json.dumps(clean, ensure_ascii=False), uid, name))

    # ── 工具级覆盖（逐 server 的工具白名单）────────────────────

    def list_tool_modes(self, uid: str, server: str) -> dict[str, str]:
        """该 server 下被单独设过模式的工具：{工具名: 模式}。没有覆盖的工具不在里面（继承 server）。"""
        rows = self._query(
            "SELECT tool, mode FROM user_mcp_tool_modes WHERE uid = ? AND server = ?",
            (uid, server))

        return {str(r["tool"]): str(r["mode"]) for r in rows}

    def set_tool_mode(self, uid: str, server: str, tool: str, mode: str) -> None:
        """给单个工具设模式（覆盖 server 模式）。"""
        if mode not in MCP_MODES:
            raise ValueError(f"审批模式只能是 {'/'.join(MCP_MODES)}")
        if not (server or "").strip() or not (tool or "").strip():
            raise ValueError("工具级模式需要 server 与 tool 名")

        self._execute(
            "INSERT INTO user_mcp_tool_modes (uid, server, tool, mode, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(uid, server, tool) DO UPDATE SET "
            "mode=excluded.mode, updated_at=excluded.updated_at",
            (uid, server.strip(), tool.strip(), mode, int(time.time())),
        )

    def clear_tool_mode(self, uid: str, server: str, tool: str) -> None:
        """删掉单个工具的覆盖，回到继承 server 模式。"""
        self._execute(
            "DELETE FROM user_mcp_tool_modes WHERE uid = ? AND server = ? AND tool = ?",
            (uid, server, tool))

    def clear_server_tool_modes(self, uid: str, server: str) -> None:
        """删掉某个 server 的全部工具级覆盖。"""
        self._execute(
            "DELETE FROM user_mcp_tool_modes WHERE uid = ? AND server = ?", (uid, server))

    # ── admin 侧：MCP能力总开关 ────────────────────────────────

    def is_user_mcp_granted(self, uid: str) -> bool:
        """admin是否开放了该账号的MCP能力。默认关闭。"""
        rows = self._query("SELECT enabled FROM user_mcp_grants WHERE uid = ?", (uid,))

        return bool(rows and rows[0]["enabled"])

    def set_user_mcp_granted(self, uid: str, enabled: bool) -> None:
        """admin开放/关闭某账号的MCP能力。"""
        self._execute(
            "INSERT INTO user_mcp_grants (uid, enabled, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
            (uid, 1 if enabled else 0, int(time.time())),
        )

    # ── 每用户连接数上限 ────────────────────────────────────────

    def get_user_limit(self, uid: str) -> int:
        rows = self._query("SELECT max_servers FROM user_mcp_limits WHERE uid = ?", (uid,))
        if rows:
            return int(rows[0]["max_servers"])

        # 配置里的全局上限；再退到内置默认
        data = getattr(config, "CONFIG_DATA", None)
        mcp = getattr(data, "mcp", None) if data else None
        if mcp is not None and mcp.max_user_servers is not None:
            return int(mcp.max_user_servers)

        return DEFAULT_MAX_USER_SERVERS

    def set_user_limit(self, uid: str, max_servers: int) -> None:
        if max_servers < 0:
            raise ValueError("上限不能为负")

        self._execute(
            "INSERT INTO user_mcp_limits (uid, max_servers, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET max_servers=excluded.max_servers, "
            "updated_at=excluded.updated_at",
            (uid, int(max_servers), int(time.time())),
        )

    # ── 汇总：当前身份可连接的server ───────────────────────────

    def connectable_servers(self, uid: str) -> tuple[list[dict], list[str]]:
        """可连接的server = 系统层(启用的) + 用户层(admin已开放该账号能力、用户自己启用且未deny的)。

        用户层数量受该用户自己设的上限约束（user_mcp_limits，默认取config.json的全局默认）。
        返回 (server列表, 提示信息)。
        """
        servers: list[dict] = []
        notes: list[str] = []

        for entry in (getattr(config, "MCP_SYSTEM_SERVERS", None) or []):
            if not entry.get("enabled", True):
                continue
            headers = entry.get("headers") or {}
            if _ciphertext_headers(headers):
                # 解不开的密文不能当 token 发给 MCP server，跳过并说明原因
                notes.append(
                    f"MCP server「{entry.get('name', '')}」的请求头仍是密文"
                    "（主密钥缺失或与配置不匹配），已跳过")
                continue
            servers.append({
                "layer": "system",
                "name": entry.get("name", ""),
                "url": entry.get("url", ""),
                "headers": headers,
                "mode": "allow",
            })

        granted = self.is_user_mcp_granted(uid)
        user_rows = self.list_user_servers(uid)
        if not granted:
            if user_rows:
                notes.append("管理员尚未开放你的 MCP 能力，你的 server 不会被连接")
            return servers, notes

        usable = [r for r in user_rows if r["enabled"] and r.get("mode") != "deny"]
        limit = self.get_user_limit(uid)
        if len(usable) > limit:
            notes.append(
                f"你启用的MCP server {len(usable)} 个，超过你自己设的上限 {limit} 个，仅连接前 {limit} 个")
            usable = usable[:limit]

        for row in usable:
            headers = row["headers"] or {}
            if _ciphertext_headers(headers):
                notes.append(
                    f"MCP server「{row['name']}」的请求头仍是密文，已跳过")
                continue
            servers.append({
                "layer": "user",
                "name": row["name"],
                "url": row["url"],
                "headers": headers,
                "mode": row.get("mode") or "ask",
            })

        denied = [r for r in user_rows if r.get("mode") == "deny"]
        if denied:
            notes.append("你设为不用(deny)的 server 未连接：" + "、".join(r["name"] for r in denied))

        return servers, notes


mcp_registry = McpRegistry()
