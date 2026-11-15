# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : session_service.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 19:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 会话SQLite持久化

import asyncio
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from config import config
from core.agent import persistence
from service.db_base import SqliteService
from service.session import approvals
from service.project.schema import ensure_projects_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '新对话',
    model       TEXT,
    thinking    INTEGER,
    user_id     TEXT NOT NULL DEFAULT 'default',
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    speed       REAL NOT NULL DEFAULT 0,
    context_tokens INTEGER NOT NULL DEFAULT 0,
    context_limit  INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL DEFAULT 'default',
    role        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    think       TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '',
    meta        TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

-- 报告用的模型用量明细：一次模型调用一行。
-- 模型调用本身已经是低频事件（秒级），与其他 per-usage 写入同频，不额外做聚合。
CREATE TABLE IF NOT EXISTS usage_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id       TEXT NOT NULL,
    uid           TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    model         TEXT NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    elapsed_ms    INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'live',
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_uid_time ON usage_events(uid, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_model_time ON usage_events(model, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_turn ON usage_events(turn_id);
-- 旧数据回填按轮一行；同一轮同一模型只保留一条近似记录，live 行不受此约束
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_backfill ON usage_events(turn_id, model)
    WHERE source = 'backfill';

-- 一个活跃 turn 一条租约；删除先立 tombstone 阻止新租约，再等现有请求释放。
CREATE TABLE IF NOT EXISTS session_leases (
    lease_id   TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_leases_session ON session_leases(session_id, expires_at);
CREATE TABLE IF NOT EXISTS session_deletions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
-- 注销账号时保留 uid tombstone。旧请求即使已通过 HTTP 鉴权，也不能在注销清理后再创建新会话。
CREATE TABLE IF NOT EXISTS session_user_blocks (
    user_id    TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);
"""

DEFAULT_USER_ID = "default"

# 老库补列：(表, 列, 声明)。已存在的列直接跳过，可对同一库重复执行
MIGRATIONS = [
    ("sessions", "tokens_in", "INTEGER NOT NULL DEFAULT 0"),
    ("sessions", "tokens_out", "INTEGER NOT NULL DEFAULT 0"),
    ("sessions", "speed", "REAL NOT NULL DEFAULT 0"),
    ("sessions", "context_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("sessions", "context_limit", "INTEGER NOT NULL DEFAULT 0"),
    ("sessions", "user_id", f"TEXT NOT NULL DEFAULT '{DEFAULT_USER_ID}'"),
    ("messages", "user_id", f"TEXT NOT NULL DEFAULT '{DEFAULT_USER_ID}'"),
    # 附件（E10.4）：存 JSON 数组。正文里只留 [File 1] 这类 token，路径与大小在这里
    ("messages", "attachments", "TEXT NOT NULL DEFAULT ''"),
    # 结构化系统行（E10.1）：目前用于 compact 的压缩前后 token/条数/摘要；
    # 走独立列而不是塞进 think，避免历史重放把摘要当思考内容渲染
    ("messages", "meta", "TEXT NOT NULL DEFAULT ''"),
    # 项目归属：空 = 无项目会话，行为与引入项目前完全一致
    ("sessions", "project_id", "TEXT"),
]

class SessionService(SqliteService):
    """会话与消息的SQLite存储；thinking以 -1/0/1 编码NULL/False/True"""

    LEASE_TTL_SECONDS = 120
    LEASE_POLL_SECONDS = 0.05

    def _on_new_connection(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
        for table, column, declaration in MIGRATIONS:
            if column in self._table_columns(conn, table):
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        # 报告查询按 (身份, 时间) 过滤；索引必须等老库补完 user_id 列再建
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_time ON messages(user_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_time ON sessions(user_id, created_at)")
        # 项目表也在这里建：会话行要 LEFT JOIN projects 取项目名，而 TUI 只跑会话层，
        # 不会 import 项目 API，不能指望 ProjectService 先把表建出来。
        ensure_projects_schema(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)")
        self._backfill_usage_events(conn)
        conn.commit()

    @staticmethod
    def _backfill_usage_events(conn: sqlite3.Connection) -> None:
        """把旧版只有轮级 meta.turn 的历史补成调用明细。

        旧数据没有逐模型/逐调用明细，只能按会话当前模型归属，一轮一行、`calls` 隐含为 1；
        已有 live 行的 turn 直接跳过，避免覆盖新版本的准确统计。
        """
        conn.execute("DROP TABLE IF EXISTS turn_model_usage")
        rows = conn.execute(
            "SELECT m.id, m.user_id, m.session_id, m.created_at, m.meta, s.model "
            "FROM messages m JOIN sessions s ON s.id = m.session_id "
            "WHERE m.meta LIKE '%\"turn\"%' "
            "AND NOT EXISTS (SELECT 1 FROM usage_events u "
            "                WHERE u.turn_id = CAST(m.id AS TEXT) AND u.source = 'live')"
        ).fetchall()
        for row in rows:
            try:
                turn = json.loads(row["meta"] or "{}").get("turn")
            except (TypeError, ValueError):
                continue
            if not isinstance(turn, dict):
                continue
            input_tokens = turn.get("input") or 0
            output_tokens = turn.get("output") or 0
            elapsed = turn.get("elapsed") or 0
            if not (input_tokens or output_tokens or elapsed):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO usage_events "
                "(turn_id, uid, session_id, model, input_tokens, output_tokens, "
                " elapsed_ms, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'backfill', ?)",
                (
                    str(row["id"]), row["user_id"], row["session_id"], row["model"] or "",
                    int(input_tokens), int(output_tokens),
                    int(round(float(elapsed) * 1000)), int(row["created_at"]),
                ),
            )

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _execute(self, sql: str, params: tuple=()):
        with self._lock:
            conn = self._connection()
            conn.execute(sql, params)
            conn.commit()

    def _execute_many(self, sql: str):
        with self._lock:
            conn = self._connection()
            conn.executescript(sql)
            conn.commit()

    def _query(self, sql: str, params: tuple=()) -> list[dict]:
        with self._lock:
            conn = self._connection()
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    # region 会话
    def save(self, session_id: str, title: str="新对话", model: Optional[str]=None, thinking: Optional[bool]=None, user_id: str=DEFAULT_USER_ID) -> None:
        # UPSERT：重复save时保留created_at，thinking传None(auto)也能正常写入
        # 冲突分支刻意不更新user_id：会话归属只能经transfer_session变更，普通保存不得改写归属。
        # 永久 tombstone 阻止过期的旧写入在删除完成后重新 INSERT 复活会话键。
        now = int(time.time())
        self._execute(
            """INSERT INTO sessions (id, title, model, thinking, user_id, created_at, updated_at)
               SELECT ?, ?, ?, ?, ?, ?, ?
               WHERE NOT EXISTS (SELECT 1 FROM session_deletions WHERE session_id = ?)
                 AND NOT EXISTS (SELECT 1 FROM session_user_blocks WHERE user_id = ?)
               ON CONFLICT(id) DO UPDATE SET title = excluded.title, model = excluded.model,
               thinking = excluded.thinking, updated_at = excluded.updated_at
               WHERE sessions.user_id = excluded.user_id
                 AND NOT EXISTS (SELECT 1 FROM session_deletions d WHERE d.session_id = excluded.id)""",
            (session_id, title, model, self._encode_thinking(thinking), user_id, now, now, session_id, user_id)
        )

    def update_existing(self, session_id: str, title: str, model: Optional[str],
                        thinking: Optional[bool], user_id: str=DEFAULT_USER_ID) -> bool:
        """只更新已有且仍归属当前用户的会话；不能用 UPSERT 让并发删除后的记录复活。"""
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "UPDATE sessions SET title = ?, model = ?, thinking = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM session_deletions d WHERE d.session_id = sessions.id)",
                (title, model, self._encode_thinking(thinking), int(time.time()), session_id, user_id)
            )
            conn.commit()
            return cursor.rowcount == 1

    def create(self, title: str="新对话", model: Optional[str]=None, thinking: Optional[bool]=None,
               user_id: str=DEFAULT_USER_ID, project_id: Optional[str]=None) -> str:
        """签发会话键并落一行，返回这个键。

        键只在这里生成：`sessions.id` 同时就是 agent 的 `thread_id`，两端共用一把键。
        客户端自造的 id 既可能与他人撞车，也无从校验归属，因此不接受。
        """
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            blocked = conn.execute(
                "SELECT 1 FROM session_user_blocks WHERE user_id = ?", (user_id,)
            ).fetchone()
            if blocked:
                conn.rollback()
                raise ValueError("该账号正在注销或已注销，不能再创建会话")

            session_id = str(uuid.uuid4())
            now = int(time.time())
            conn.execute(
                "INSERT INTO sessions (id, title, model, thinking, user_id, project_id,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, title, model, self._encode_thinking(thinking), user_id,
                 project_id, now, now)
            )
            conn.commit()

        return session_id

    # region 活跃请求租约与删除协调
    def acquire_lease(self, session_id: str, user_id: str) -> Optional[str]:
        """原子确认归属并取得 turn 租约；删除已开始/账号被封锁时返回 None。

        与删除标记在同一个 SQLite 写事务中串行化：不会出现"删除检查完无租约，
        新请求却刚好在 purge 前插入 checkpoint"的窗口。
        """
        lease_id = str(uuid.uuid4())
        now = int(time.time())
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM session_leases WHERE expires_at <= ?", (now,))
            row = conn.execute(
                "SELECT 1 FROM sessions s "
                "WHERE s.id = ? AND s.user_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM session_deletions d WHERE d.session_id = s.id) "
                "AND NOT EXISTS (SELECT 1 FROM session_user_blocks b WHERE b.user_id = s.user_id)",
                (session_id, user_id)
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            conn.execute(
                "INSERT INTO session_leases (lease_id, session_id, user_id, expires_at) VALUES (?, ?, ?, ?)",
                (lease_id, session_id, user_id, now + self.LEASE_TTL_SECONDS)
            )
            conn.commit()

        return lease_id

    def renew_lease(self, lease_id: str) -> bool:
        now = int(time.time())
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "UPDATE session_leases SET expires_at = ? WHERE lease_id = ? AND expires_at > ?",
                (now + self.LEASE_TTL_SECONDS, lease_id, now)
            )
            conn.commit()
            return cursor.rowcount == 1

    def release_lease(self, lease_id: str) -> None:
        self._execute("DELETE FROM session_leases WHERE lease_id = ?", (lease_id,))

    def begin_delete(self, session_id: str, user_id: str) -> bool:
        """立删除 tombstone，阻止之后的新 turn；无归属则返回 False。"""
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            owned = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
            ).fetchone()
            if owned is None:
                conn.rollback()
                return False
            conn.execute(
                "INSERT INTO session_deletions (session_id, user_id, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET user_id = excluded.user_id",
                (session_id, user_id, int(time.time()))
            )
            conn.commit()

        return True

    def begin_delete_user(self, user_id: str) -> list[str]:
        """封锁该 uid 的新会话/新 turn，并为它现有的所有会话立删除 tombstone。"""
        now = int(time.time())
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO session_user_blocks (user_id, created_at) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO NOTHING", (user_id, now)
            )
            session_ids = [r[0] for r in conn.execute(
                "SELECT id FROM sessions WHERE user_id = ?", (user_id,)
            )]
            conn.executemany(
                "INSERT INTO session_deletions (session_id, user_id, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET user_id = excluded.user_id",
                [(sid, user_id, now) for sid in session_ids]
            )
            conn.commit()

        return session_ids

    def _active_leases(self, session_ids: list[str]) -> list[str]:
        now = int(time.time())
        with self._lock:
            conn = self._connection()
            conn.execute("DELETE FROM session_leases WHERE expires_at <= ?", (now,))
            if session_ids:
                marks = ",".join("?" for _ in session_ids)
                rows = conn.execute(
                    f"SELECT DISTINCT session_id FROM session_leases WHERE session_id IN ({marks})",
                    tuple(session_ids)
                ).fetchall()
            else:
                rows = []
            conn.commit()
        return [r[0] for r in rows]

    def is_busy(self, session_id: str) -> bool:
        """会话是否有在途 turn（有未过期租约即视为忙）。"""
        return bool(self._active_leases([session_id]))

    def set_session_project(self, session_id: str, project_id: Optional[str],
                            user_id: str = DEFAULT_USER_ID) -> str:
        """设置或取消会话的项目归属；返回 ok / unchanged / not_found / busy。

        `project_id=None` 表示取消归属。已有会话允许换项目或取消，但只影响之后的
        轮次：文件与消息不移动，调用方负责清掉旧上下文里的审批。

        归属校验、租约检查与 UPDATE 在同一个 BEGIN IMMEDIATE 内完成：
        先 is_busy 再 UPDATE 是两次加锁，中间可能有新一轮拿到租约。
        """
        now = int(time.time())
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM session_leases WHERE expires_at <= ?", (now,))
            row = conn.execute(
                "SELECT project_id FROM sessions WHERE id = ? AND user_id = ?"
                " AND NOT EXISTS (SELECT 1 FROM session_deletions d"
                "                 WHERE d.session_id = sessions.id)",
                (session_id, user_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return "not_found"
            if row["project_id"] == project_id:
                conn.rollback()
                return "unchanged"
            if conn.execute("SELECT 1 FROM session_leases WHERE session_id = ?",
                            (session_id,)).fetchone():
                conn.rollback()
                return "busy"
            conn.execute("UPDATE sessions SET project_id = ?, updated_at = ? WHERE id = ?",
                         (project_id, now, session_id))
            conn.commit()
            return "ok"

    def _finalize_delete(self, session_ids: list[str], user_id: str) -> list[str]:
        """租约归零后删除 DB 行；调用方随后在锁外 purge checkpoint/产物目录。"""
        if not session_ids:
            return []
        marks = ",".join("?" for _ in session_ids)
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            # 最后一刻再确认租约仍为零； acquire_lease 与这里同样用写事务
            conn.execute("DELETE FROM session_leases WHERE expires_at <= ?", (int(time.time()),))
            active = conn.execute(
                f"SELECT 1 FROM session_leases WHERE session_id IN ({marks}) LIMIT 1", tuple(session_ids)
            ).fetchone()
            if active:
                conn.rollback()
                return []
            conn.execute(
                f"DELETE FROM messages WHERE user_id = ? AND session_id IN ({marks})",
                (user_id, *session_ids)
            )
            conn.execute(
                f"DELETE FROM sessions WHERE user_id = ? AND id IN ({marks})",
                (user_id, *session_ids)
            )
            # session_deletions 是永久 tombstone：uuid 不复用，保留它可阻止迟到的旧请求
            # 通过 save()/UPSERT 把已删除会话键重新插回 sessions。
            conn.commit()

        # 会话没了，本会话的 always 记录也不能留下（内存态，不随 DB 删除自动消失）
        for session_id in session_ids:
            approvals.clear_session(user_id, session_id)

        return session_ids

    async def delete_wait(self, session_id: str, user_id: str) -> bool:
        """请求删除、等待在途 turn 结束再 purge；供 Web/TUI 调用，不阻塞事件循环。"""
        if not self.begin_delete(session_id, user_id):
            return False
        while True:
            if self._active_leases([session_id]):
                await asyncio.sleep(self.LEASE_POLL_SECONDS)
                continue
            deleted = self._finalize_delete([session_id], user_id)
            if deleted:
                persistence.purge_thread(session_id, db_path=self.db_path)
                return True
            # 并发删除者可能已完成。不能留下 tombstone 把同一 id 永久锁死。
            if not self.owns(session_id, user_id):
                return False
            await asyncio.sleep(self.LEASE_POLL_SECONDS)

    def delete_wait_sync(self, session_id: str, user_id: str) -> bool:
        """非事件循环调用方的同步包装。"""
        if not self.begin_delete(session_id, user_id):
            return False
        while True:
            if self._active_leases([session_id]):
                time.sleep(self.LEASE_POLL_SECONDS)
                continue
            deleted = self._finalize_delete([session_id], user_id)
            if deleted:
                persistence.purge_thread(session_id, db_path=self.db_path)
                return True
            if not self.owns(session_id, user_id):
                return False
            time.sleep(self.LEASE_POLL_SECONDS)

    async def delete_user_sessions_wait(self, user_id: str) -> None:
        session_ids = self.begin_delete_user(user_id)
        while self._active_leases(session_ids):
            await asyncio.sleep(self.LEASE_POLL_SECONDS)
        deleted = self._finalize_delete(session_ids, user_id)
        for session_id in deleted:
            persistence.purge_thread(session_id, db_path=self.db_path)

    def delete_user_sessions_wait_sync(self, user_id: str) -> None:
        session_ids = self.begin_delete_user(user_id)
        while self._active_leases(session_ids):
            time.sleep(self.LEASE_POLL_SECONDS)
        deleted = self._finalize_delete(session_ids, user_id)
        for session_id in deleted:
            persistence.purge_thread(session_id, db_path=self.db_path)
    # endregion

    def owns(self, session_id: str, user_id: str) -> bool:
        """这行会话是否登记在该身份名下。空 id 一律 False（新会话走 create 签发）。"""
        if not session_id:
            return False

        return bool(self._query(
            "SELECT 1 AS x FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        ))

    def touch(self, session_id: str) -> None:
        self._execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (int(time.time()), session_id))

    def save_tokens(self, session_id: str, tokens_in: int, tokens_out: int, speed: float = 0.0,
                    user_id: str=DEFAULT_USER_ID) -> None:
        """按增量累加用量。

        服务层每个请求一个记录器实例，写绝对值会把自己这一轮的增量当成会话的全部用量。
        """
        self._execute(
            "UPDATE sessions SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ?, speed = ? "
            "WHERE id = ? AND user_id = ?",
            (tokens_in, tokens_out, speed, session_id, user_id)
        )

    def record_usage_event(self, *, turn_id: str, uid: str, session_id: str,
                           model: str, input_tokens: int, output_tokens: int,
                           elapsed: float, created_at: int) -> None:
        """一次模型调用落一行明细。

        与 `save_tokens()` 同频：调用结束就写，进程被杀时不丢已经跑完的调用。
        若启动时把进行中的轮次回填成了近似行，第一条 live 记录会先清掉该轮的 backfill，
        避免近似值和真实值同时被报表累加。
        """
        with self._lock:
            conn = self._connection()
            conn.execute(
                "DELETE FROM usage_events WHERE turn_id = ? AND source = 'backfill'",
                (turn_id,),
            )
            conn.execute(
                "INSERT INTO usage_events "
                "(turn_id, uid, session_id, model, input_tokens, output_tokens, "
                " elapsed_ms, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'live', ?)",
                (
                    turn_id, uid, session_id, model or "",
                    int(input_tokens or 0), int(output_tokens or 0),
                    int(round(float(elapsed or 0.0) * 1000)), created_at,
                ),
            )
            conn.commit()

    def message_created_at(self, message_id: int, user_id: str=DEFAULT_USER_ID) -> Optional[int]:
        """读某条消息的时间：resume 分段没有自己的回合起点，用它还原自然日归属。"""
        rows = self._query(
            "SELECT created_at FROM messages WHERE id = ? AND user_id = ?",
            (message_id, user_id),
        )
        return int(rows[0]["created_at"]) if rows else None

    def save_context(self, session_id: str, tokens: int, limit: int, user_id: str=DEFAULT_USER_ID) -> None:
        """只写占用快照；带 user_id 防止同名会话串号。"""
        self._execute(
            "UPDATE sessions SET context_tokens = ?, context_limit = ? WHERE id = ? AND user_id = ?",
            (tokens, limit, session_id, user_id)
        )

    def set_message_meta(self, session_id: str, message_id: Optional[int], meta: str,
                         user_id: str=DEFAULT_USER_ID) -> bool:
        """回填一条消息的结构化数据（目前用于把本轮 token 统计写到某一行）。

        记录器每拿到一次 usage 就刷新当前锚点行，所以这里也要能清空——锚点前移时
        旧行必须还原，否则同一轮会被聚合两次。
        双重限定 session_id + user_id：message_id 来自本进程的插入，但拼错一次就会串到别的会话。
        """
        if not message_id:
            return False

        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "UPDATE messages SET meta = ? WHERE id = ? AND session_id = ? AND user_id = ?",
                (meta, message_id, session_id, user_id)
            )
            conn.commit()
            return bool(cursor.rowcount)

    def message_turn(self, message_id: int, user_id: str=DEFAULT_USER_ID) -> dict:
        """读某条消息上已有的 `meta.turn`；没有或格式不对时返回空 dict。

        同一个回合可能跨多个记录器实例（HTTP resume 是另一个实例），后写的一段要
        在已有值上累加，不能覆盖前一段。
        """
        rows = self._query(
            "SELECT meta FROM messages WHERE id = ? AND user_id = ?", (message_id, user_id)
        )
        if not rows:
            return {}

        try:
            turn = json.loads(rows[0]["meta"] or "{}").get("turn")
        except (TypeError, ValueError):
            return {}

        return turn if isinstance(turn, dict) else {}

    def last_user_message_id(self, session_id: str, user_id: str=DEFAULT_USER_ID) -> Optional[int]:
        """本轮最近一条用户行：resume 分段没有自己的 record_turn_start，靠它找锚点。"""
        rows = self._query(
            "SELECT id FROM messages WHERE session_id = ? AND user_id = ? AND role = 'user' "
            "ORDER BY id DESC LIMIT 1",
            (session_id, user_id),
        )
        return rows[0]["id"] if rows else None

    def get_session(self, session_id: str, user_id: str=DEFAULT_USER_ID) -> Optional[dict]:
        """按 (键, 归属) 读一行；不存在或不属于该身份时返回 None。"""
        # projects 表由 _on_new_connection 建好（TUI 只跑会话层），这里不依赖项目服务
        rows = self._query(
            "SELECT s.id, s.title, s.model, s.thinking, s.updated_at, s.tokens_in,"
            " s.tokens_out, s.speed, s.context_tokens, s.context_limit, s.project_id,"
            " p.name AS project_name, p.path AS project_path FROM sessions s"
            " LEFT JOIN projects p ON p.id = s.project_id"
            " WHERE s.id = ? AND s.user_id = ?",
            (session_id, user_id)
        )
        if not rows:
            return None

        row = rows[0]
        return {
            **row,
            "thinking": self._decode_thinking(row["thinking"]),
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "project_path": row["project_path"],
        }

    def rename(self, session_id: str, title: str, user_id: str=DEFAULT_USER_ID) -> bool:
        title = (title or "").strip()
        if not title:
            return False
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ? AND user_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM session_deletions d WHERE d.session_id = sessions.id)",
                (title[:80], int(time.time()), session_id, user_id)
            )
            conn.commit()
            return cursor.rowcount == 1

    def all_sessions(self) -> list[dict]:
        """所有会话的 id/归属；附件草稿清理要跨会话扫。"""
        return self._query("SELECT id, user_id FROM sessions")

    def list_sessions(self, limit: int=20, user_id: str=DEFAULT_USER_ID,
                      project_id: Optional[str]=None) -> list[dict]:
        sql = (
            "SELECT s.*, p.name AS project_name, p.path AS project_path FROM sessions s"
            " LEFT JOIN projects p ON p.id = s.project_id WHERE s.user_id = ?"
        )
        params: list = [user_id]
        if project_id:
            sql += " AND s.project_id = ?"
            params.append(project_id)
        sql += " ORDER BY s.updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._query(sql, tuple(params))
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "model": r["model"],
                "thinking": self._decode_thinking(r["thinking"]),
                "updated_at": r["updated_at"],
                "tokens_in": r["tokens_in"] or 0,
                "tokens_out": r["tokens_out"] or 0,
                "speed": r["speed"] or 0,
                "context_tokens": r["context_tokens"] or 0,
                "context_limit": r["context_limit"] or 0,
                "project_id": r["project_id"],
                "project_name": r["project_name"],
                "project_path": r["project_path"],
            }
            for r in rows
        ]

    def account_stats(self, user_id: str=DEFAULT_USER_ID) -> dict:
        """当前账号的会话数与累计 token 用量。

        `sessions.tokens_in/out` 是每个会话的累计值，直接 SUM 比按消息 meta 重算更可靠，
        也不受列表接口 `limit<=200` 的影响。
        """
        rows = self._query(
            "SELECT COUNT(*) AS sessions, "
            "COALESCE(SUM(tokens_in), 0) AS tokens_in, "
            "COALESCE(SUM(tokens_out), 0) AS tokens_out "
            "FROM sessions WHERE user_id = ?",
            (user_id,),
        )
        row = rows[0] if rows else {}
        tokens_in = int(row.get("tokens_in") or 0)
        tokens_out = int(row.get("tokens_out") or 0)
        return {
            "sessions": int(row.get("sessions") or 0),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens": tokens_in + tokens_out,
        }

    def usage_daily(self, days: int = 7, user_id: str = DEFAULT_USER_ID) -> dict:
        """按服务端本地自然日聚合当前账号的逐轮 token 用量。

        只认带 `meta.turn` 的行，不按 role 过滤：一轮的用量落在该轮最后一条 assistant
        正文行上；整轮没有正文时（只调工具就被确认/取消打断）落在当轮用户行上。
        两处都只看 `turn`，同一轮的整轮合计只会存在一处，不会重复计。
        """
        days = max(1, min(30, int(days)))
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=days - 1)
        dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
        buckets = {d: {"date": d, "input": 0, "output": 0, "total": 0} for d in dates}
        models: dict[str, dict] = {}
        totals = {"input": 0, "output": 0, "total": 0, "turns": 0}

        rows = self._query(
            "SELECT m.created_at, m.meta, s.model FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE m.user_id = ? AND s.user_id = ? AND m.meta != '' "
            "AND m.created_at >= ? "
            "AND NOT EXISTS (SELECT 1 FROM session_deletions d WHERE d.session_id = m.session_id)",
            (user_id, user_id, int(start.timestamp())),
        )

        def amount(turn: dict, key: str) -> int:
            value = turn.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return 0
            if not math.isfinite(value):
                return 0
            return max(0, int(value))

        for row in rows:
            try:
                turn = json.loads(row["meta"]).get("turn")
            except (AttributeError, TypeError, ValueError):
                continue
            if not isinstance(turn, dict):
                continue
            day = datetime.fromtimestamp(row["created_at"]).strftime("%Y-%m-%d")
            if day not in buckets:
                continue
            in_tok, out_tok = amount(turn, "input"), amount(turn, "output")
            total = in_tok + out_tok
            bucket = buckets[day]
            bucket["input"] += in_tok
            bucket["output"] += out_tok
            bucket["total"] += total
            totals["input"] += in_tok
            totals["output"] += out_tok
            totals["total"] += total
            totals["turns"] += 1
            model = row["model"] or ""
            item = models.setdefault(model, {
                "model": model, "input": 0, "output": 0, "total": 0, "turns": 0,
            })
            item["input"] += in_tok
            item["output"] += out_tok
            item["total"] += total
            item["turns"] += 1

        ranked = sorted(
            (item for item in models.values() if item["total"] > 0),
            key=lambda item: (-item["total"], item["model"]),
        )
        return {"days": days, "series": [buckets[d] for d in dates], "models": ranked, "totals": totals}

    REPORT_MAX_DAYS = 365
    REPORT_MAX_LIMIT = 100

    @staticmethod
    def _bucket_expr(column: str, bucket: str) -> str:
        """把时间戳按服务端本地自然日/周一聚合成可 GROUP BY 的日期字符串。"""
        if bucket == "week":
            # SQLite 的 weekday 0 会前进到下一个周日，再退 6 天即本周周一
            return f"date({column}, 'unixepoch', 'localtime', 'weekday 0', '-6 days')"

        return f"date({column}, 'unixepoch', 'localtime')"

    @staticmethod
    def _share(part: int, total: int) -> float:
        return round(part * 100.0 / total, 1) if total else 0.0

    def admin_report(self, days: int = 30, bucket: str = "day", limit: int = 20) -> dict:
        """管理端报表：用户活跃、模型用量、token 消耗。

        口径：
        - 活跃/轮次/会话按 messages 与 sessions 的写入时间统计
        - token/模型/调用次数按 usage_events 统计；历史回填行 source='backfill'
        - 活跃用户按“用户消息”去重，不以登录/打开页面为准
        - 时间一律使用服务端本地自然日，周粒度按周一起算
        """
        days = max(1, min(self.REPORT_MAX_DAYS, int(days)))
        limit = max(1, min(self.REPORT_MAX_LIMIT, int(limit)))
        bucket = "week" if str(bucket).lower() == "week" else "day"

        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=days - 1)
        start_ts = int(start.timestamp())
        end_ts = int((today + timedelta(days=1)).timestamp())
        today_ts = int(today.timestamp())
        week_ts = int((today - timedelta(days=6)).timestamp())
        month_ts = int((today - timedelta(days=29)).timestamp())

        if bucket == "week":
            first = start - timedelta(days=start.weekday())
            last = today - timedelta(days=today.weekday())
            keys = []
            cursor = first
            while cursor <= last:
                keys.append(cursor.strftime("%Y-%m-%d"))
                cursor += timedelta(days=7)
        else:
            keys = [
                (start + timedelta(days=index)).strftime("%Y-%m-%d")
                for index in range(days)
            ]

        msg_bucket = self._bucket_expr("created_at", bucket)
        usage_bucket = self._bucket_expr("created_at", bucket)

        def one(sql: str, params: tuple = ()) -> dict:
            rows = self._query(sql, params)
            return rows[0] if rows else {}

        def count(sql: str, params: tuple = ()) -> int:
            return int(one(sql, params).get("n") or 0)

        active_users = count(
            "SELECT COUNT(DISTINCT user_id) AS n FROM messages "
            "WHERE role = 'user' AND created_at >= ? AND created_at < ?",
            (start_ts, end_ts),
        )
        turns = count(
            "SELECT COUNT(*) AS n FROM messages "
            "WHERE role = 'user' AND created_at >= ? AND created_at < ?",
            (start_ts, end_ts),
        )
        sessions = count(
            "SELECT COUNT(*) AS n FROM sessions WHERE created_at >= ? AND created_at < ?",
            (start_ts, end_ts),
        )
        new_users = count(
            "SELECT COUNT(*) AS n FROM users WHERE created_at >= ? AND created_at < ?",
            (start_ts, end_ts),
        )
        dau = count(
            "SELECT COUNT(DISTINCT user_id) AS n FROM messages "
            "WHERE role = 'user' AND created_at >= ?",
            (today_ts,),
        )
        wau = count(
            "SELECT COUNT(DISTINCT user_id) AS n FROM messages "
            "WHERE role = 'user' AND created_at >= ?",
            (week_ts,),
        )
        mau = count(
            "SELECT COUNT(DISTINCT user_id) AS n FROM messages "
            "WHERE role = 'user' AND created_at >= ?",
            (month_ts,),
        )

        usage = one(
            "SELECT COALESCE(SUM(input_tokens), 0) AS tokens_in, "
            "COALESCE(SUM(output_tokens), 0) AS tokens_out, "
            "COUNT(*) AS calls, "
            "COALESCE(SUM(CASE WHEN source = 'backfill' THEN 1 ELSE 0 END), 0) AS backfill_calls "
            "FROM usage_events WHERE created_at >= ? AND created_at < ?",
            (start_ts, end_ts),
        )
        tokens_in = int(usage.get("tokens_in") or 0)
        tokens_out = int(usage.get("tokens_out") or 0)
        tokens_total = tokens_in + tokens_out
        calls = int(usage.get("calls") or 0)
        backfill_calls = int(usage.get("backfill_calls") or 0)
        model_count = count(
            "SELECT COUNT(DISTINCT model) AS n FROM usage_events "
            "WHERE created_at >= ? AND created_at < ?",
            (start_ts, end_ts),
        )

        activity = {
            key: {"date": key, "active_users": 0, "new_users": 0, "sessions": 0, "turns": 0}
            for key in keys
        }
        for row in self._query(
            f"SELECT {msg_bucket} AS bucket, COUNT(DISTINCT user_id) AS active_users, "
            "COUNT(*) AS turns FROM messages "
            "WHERE role = 'user' AND created_at >= ? AND created_at < ? "
            "GROUP BY bucket",
            (start_ts, end_ts),
        ):
            item = activity.get(row["bucket"])
            if item:
                item["active_users"] = int(row["active_users"] or 0)
                item["turns"] = int(row["turns"] or 0)

        for row in self._query(
            f"SELECT {self._bucket_expr('created_at', bucket)} AS bucket, COUNT(*) AS new_users "
            "FROM users WHERE created_at >= ? AND created_at < ? GROUP BY bucket",
            (start_ts, end_ts),
        ):
            item = activity.get(row["bucket"])
            if item:
                item["new_users"] = int(row["new_users"] or 0)

        for row in self._query(
            f"SELECT {self._bucket_expr('created_at', bucket)} AS bucket, COUNT(*) AS sessions "
            "FROM sessions WHERE created_at >= ? AND created_at < ? GROUP BY bucket",
            (start_ts, end_ts),
        ):
            item = activity.get(row["bucket"])
            if item:
                item["sessions"] = int(row["sessions"] or 0)

        tokens = {
            key: {"date": key, "input": 0, "output": 0, "total": 0, "calls": 0}
            for key in keys
        }
        for row in self._query(
            f"SELECT {usage_bucket} AS bucket, "
            "COALESCE(SUM(input_tokens), 0) AS input, "
            "COALESCE(SUM(output_tokens), 0) AS output, "
            "COUNT(*) AS calls FROM usage_events "
            "WHERE created_at >= ? AND created_at < ? GROUP BY bucket",
            (start_ts, end_ts),
        ):
            item = tokens.get(row["bucket"])
            if item:
                item["input"] = int(row["input"] or 0)
                item["output"] = int(row["output"] or 0)
                item["total"] = item["input"] + item["output"]
                item["calls"] = int(row["calls"] or 0)

        models = []
        for row in self._query(
            "SELECT model, COUNT(*) AS calls, COUNT(DISTINCT uid) AS users, "
            "COALESCE(SUM(input_tokens), 0) AS input, "
            "COALESCE(SUM(output_tokens), 0) AS output, "
            "COALESCE(SUM(CASE WHEN source = 'backfill' THEN 1 ELSE 0 END), 0) AS backfill_calls "
            "FROM usage_events WHERE created_at >= ? AND created_at < ? "
            "GROUP BY model ORDER BY (SUM(input_tokens) + SUM(output_tokens)) DESC, model LIMIT ?",
            (start_ts, end_ts, limit),
        ):
            total = int(row["input"] or 0) + int(row["output"] or 0)
            models.append({
                "model": row["model"] or "",
                "provider": (row["model"] or "").split("/", 1)[0] if "/" in (row["model"] or "") else "",
                "calls": int(row["calls"] or 0),
                "users": int(row["users"] or 0),
                "input": int(row["input"] or 0),
                "output": int(row["output"] or 0),
                "total": total,
                "token_share": self._share(total, tokens_total),
                "call_share": self._share(int(row["calls"] or 0), calls),
                "backfill_calls": int(row["backfill_calls"] or 0),
            })

        users = self._query(
            "SELECT uid, username, is_admin, created_at FROM users "
            "ORDER BY created_at ASC, uid ASC"
        )
        users_by_uid = {row["uid"]: row for row in users}

        def grouped(sql: str, params: tuple = ()) -> dict:
            return {row["uid"]: row for row in self._query(sql, params)}

        turns_by_uid = grouped(
            "SELECT user_id AS uid, COUNT(*) AS turns, "
            f"COUNT(DISTINCT {self._bucket_expr('created_at', 'day')}) AS active_days "
            "FROM messages WHERE role = 'user' AND created_at >= ? AND created_at < ? "
            "GROUP BY user_id",
            (start_ts, end_ts),
        )
        last_by_uid = grouped(
            "SELECT user_id AS uid, MAX(created_at) AS last_active FROM messages GROUP BY user_id"
        )
        sessions_by_uid = grouped(
            "SELECT user_id AS uid, COUNT(*) AS sessions FROM sessions "
            "WHERE created_at >= ? AND created_at < ? GROUP BY user_id",
            (start_ts, end_ts),
        )
        usage_by_uid = grouped(
            "SELECT uid, COALESCE(SUM(input_tokens), 0) AS input, "
            "COALESCE(SUM(output_tokens), 0) AS output, COUNT(*) AS calls "
            "FROM usage_events WHERE created_at >= ? AND created_at < ? GROUP BY uid",
            (start_ts, end_ts),
        )
        top_model_by_uid = {}
        for row in self._query(
            "SELECT uid, model, SUM(input_tokens + output_tokens) AS total "
            "FROM usage_events WHERE created_at >= ? AND created_at < ? "
            "GROUP BY uid, model ORDER BY uid, total DESC, model",
            (start_ts, end_ts),
        ):
            top_model_by_uid.setdefault(row["uid"], row["model"] or "")

        all_uids = set(users_by_uid)
        all_uids.update(turns_by_uid)
        all_uids.update(sessions_by_uid)
        all_uids.update(usage_by_uid)
        user_rows = []
        for uid in all_uids:
            account = users_by_uid.get(uid) or {}
            turn_row = turns_by_uid.get(uid) or {}
            usage_row = usage_by_uid.get(uid) or {}
            input_tokens = int(usage_row.get("input") or 0)
            output_tokens = int(usage_row.get("output") or 0)
            total_tokens = input_tokens + output_tokens
            created_at = int(account.get("created_at") or 0)
            user_rows.append({
                "uid": uid,
                "username": account.get("username") or "",
                "is_admin": bool(account.get("is_admin") or 0),
                "is_anonymous": uid == DEFAULT_USER_ID,
                "created_at": created_at,
                "is_new": bool(created_at and created_at >= start_ts),
                "last_active": int((last_by_uid.get(uid) or {}).get("last_active") or 0),
                "active_days": int(turn_row.get("active_days") or 0),
                "sessions": int((sessions_by_uid.get(uid) or {}).get("sessions") or 0),
                "turns": int(turn_row.get("turns") or 0),
                "calls": int(usage_row.get("calls") or 0),
                "input": input_tokens,
                "output": output_tokens,
                "total": total_tokens,
                "token_share": self._share(total_tokens, tokens_total),
                "top_model": top_model_by_uid.get(uid, ""),
            })
        user_rows.sort(key=lambda item: (
            -item["total"], -item["turns"], item["username"] or "~", item["uid"],
        ))

        return {
            "range": {
                "days": days,
                "bucket": bucket,
                "from": start.strftime("%Y-%m-%d"),
                "to": today.strftime("%Y-%m-%d"),
            },
            "summary": {
                "active_users": active_users,
                "dau": dau,
                "wau": wau,
                "mau": mau,
                "new_users": new_users,
                "sessions": sessions,
                "turns": turns,
                "calls": calls,
                "backfill_calls": backfill_calls,
                "models": model_count,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tokens_total": tokens_total,
                "avg_tokens_per_turn": round(tokens_total / turns, 1) if turns else 0.0,
                "avg_tokens_per_user": round(tokens_total / active_users, 1) if active_users else 0.0,
            },
            "activity": [activity[key] for key in keys],
            "tokens": [tokens[key] for key in keys],
            "models": models,
            "users": user_rows[:limit],
        }

    def delete(self, session_id: str, user_id: str=DEFAULT_USER_ID) -> None:
        self.delete_wait_sync(session_id, user_id)

    def transfer_session(self, session_id: str, to_uid: str, from_uid: str = DEFAULT_USER_ID) -> int:
        """把会话及其消息从 from_uid 过户给 to_uid，返回受影响的会话行数（0表示会话不存在或归属不符）

        from_uid 校验防止仅凭 session_id 越权过户他人会话；消息行同样按 from_uid 收紧。
        """
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "UPDATE sessions SET user_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (to_uid, int(time.time()), session_id, from_uid)
            )
            transferred = cursor.rowcount
            if transferred:
                conn.execute(
                    "UPDATE messages SET user_id = ? WHERE session_id = ? AND user_id = ?",
                    (to_uid, session_id, from_uid)
                )
                if to_uid != from_uid:
                    # 项目归属只对原用户有意义：过户不该把 A 的项目边界带到 B 名下
                    conn.execute("UPDATE sessions SET project_id = NULL WHERE id = ?",
                                 (session_id,))
            conn.commit()

        if transferred and to_uid != from_uid:
            # 过户后旧账号的批准记录不能跟到新账号名下
            approvals.clear_session(from_uid, session_id)

        return transferred

    def delete_sessions_by_user(self, user_id: str) -> None:
        self.delete_user_sessions_wait_sync(user_id)
    # endregion

    # region 消息
    def append_message(self, session_id: str, role: str, content: str, think: str="", user_id: str=DEFAULT_USER_ID,
                       attachments: str="", meta: str="") -> Optional[int]:
        """落一条消息，返回新行 id（会话不可写入时返回 None）。

        `attachments` 是附件 JSON（见 service.attachment），正文里只留 token；
        `meta` 是系统行的结构化数据（如 compact 的前后 token），不对模型可见。
        返回 id 是为了让记录器在收尾时回填本轮统计（见 SessionRecorder.finish）。
        """
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "INSERT INTO messages (session_id, user_id, role, content, think, attachments, meta, created_at) "
                "SELECT ?, ?, ?, ?, ?, ?, ?, ? WHERE EXISTS "
                "(SELECT 1 FROM sessions WHERE id = ? AND user_id = ?)",
                (session_id, user_id, role, content, think, attachments, meta, int(time.time()), session_id, user_id)
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ? AND user_id = ?",
                         (int(time.time()), session_id, user_id))
            conn.commit()
            return cursor.lastrowid if cursor.rowcount else None

    def get_messages(self, session_id: str, limit: int=30, user_id: str=DEFAULT_USER_ID,
                     before_id: Optional[int]=None) -> list[dict]:
        """按落库顺序返回一页消息；`before_id` 给定时只取该 id 之前的（上滑分页）。

        用 id 做 keyset 分页而不是 OFFSET：会话还在追加新消息，OFFSET 会随新行漂移，
        翻页时既可能重播最近的、又可能跳过更早的。
        """
        sql = "SELECT id, role, content, think, attachments, meta, created_at FROM messages WHERE session_id = ? AND user_id = ?"
        params: list = [session_id, user_id]
        if before_id is not None:
            sql += " AND id < ?"
            params.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        return list(reversed(self._query(sql, tuple(params))))

    def count_messages(self, session_id: str, user_id: str=DEFAULT_USER_ID,
                       before_id: Optional[int]=None) -> int:
        sql = "SELECT COUNT(*) AS n FROM messages WHERE session_id = ? AND user_id = ?"
        params: list = [session_id, user_id]
        if before_id is not None:
            sql += " AND id < ?"
            params.append(before_id)

        rows = self._query(sql, tuple(params))
        return int(rows[0]["n"]) if rows else 0

    def attachment_referenced(self, session_id: str, user_id: str, file_id: str) -> bool:
        """已进入历史消息的附件不能作为"未发送草稿"删除，否则重开会话变成断链。"""
        rows = self._query(
            "SELECT 1 FROM messages WHERE session_id = ? AND user_id = ? AND attachments LIKE ? LIMIT 1",
            (session_id, user_id, f"%{file_id}%")
        )
        return bool(rows)

    # 供上下文回灌：仅user/assistant正文（排除tool记录行）。limit=None 表示全部
    def get_history(self, session_id: str, limit: Optional[int]=20, user_id: str=DEFAULT_USER_ID) -> list[dict]:
        if limit is None:
            rows = self._query(
                "SELECT role, content FROM messages WHERE session_id = ? AND user_id = ? "
                "AND role IN ('user', 'assistant') ORDER BY id",
                (session_id, user_id)
            )
        else:
            rows = self._query(
                "SELECT role, content FROM messages WHERE session_id = ? AND user_id = ? "
                "AND role IN ('user', 'assistant') ORDER BY id DESC LIMIT ?",
                (session_id, user_id, limit)
            )
            rows = list(reversed(rows))
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    # endregion

    @staticmethod
    def _encode_thinking(thinking: Optional[bool]) -> Optional[int]:
        if thinking is None:
            return None
        return 1 if thinking else 0

    @staticmethod
    def _decode_thinking(value) -> Optional[bool]:
        if value is None:
            return None
        return bool(value)

session_service = SessionService()
