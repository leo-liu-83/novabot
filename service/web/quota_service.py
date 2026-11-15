# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : quota_service.py
# @Software    : VSCode
# @Datetime    : 2026/10/14 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : NovaBot 自带网络能力的每用户配额（E8.1）。用户自备 provider 不经此表，不受限

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from config import config
from service.db_base import SqliteService

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_quotas (
    uid         TEXT PRIMARY KEY,
    enabled     INTEGER NOT NULL DEFAULT 1,
    daily_limit INTEGER NOT NULL DEFAULT 50,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS web_usage (
    uid  TEXT NOT NULL,
    day  TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, day)
);
"""

DEFAULT_DAILY_LIMIT = 50
UNLIMITED = -1

_QUOTA_RESET = "default"


def parse_quota_spec(value: str) -> Optional[tuple[bool, int]]:
    """解析 /web-limit 的取值。返回 None 表示"回到默认"，即删除该用户的覆盖行。

    非法输入抛 ValueError 而不是静默回落——配错次数若悄悄变成 50，
    管理员会以为限制已生效。
    """
    text = (value or "").strip().lower()

    if text in (_QUOTA_RESET, "reset"):
        return None
    if text in ("off", "disable", "禁用"):
        return False, DEFAULT_DAILY_LIMIT
    if text in ("unlimited", "无限"):
        return True, UNLIMITED

    try:
        limit = int(text)
    except (ValueError, TypeError):
        raise ValueError(f"无法识别的配额取值: {value!r}") from None

    if limit < 0:
        raise ValueError("次数不能为负数；不限量请用 unlimited")

    return True, limit


def today() -> str:
    return time.strftime("%Y-%m-%d")


def next_reset_at() -> int:
    """下一次本地零点（配额按服务器本地日期重置），epoch 秒。"""
    now = datetime.now()
    return int(datetime.combine(now.date() + timedelta(days=1), datetime.min.time()).timestamp())


@dataclass(frozen=True)
class QuotaState:
    enabled: bool
    daily_limit: int
    used: int

    @property
    def unlimited(self) -> bool:
        return self.daily_limit == UNLIMITED

    @property
    def remaining(self) -> int:
        if self.unlimited:
            return UNLIMITED

        return max(0, self.daily_limit - self.used)

    @property
    def exhausted(self) -> bool:
        return not self.enabled or (not self.unlimited and self.used >= self.daily_limit)

    @property
    def overridden(self) -> bool:
        return self.daily_limit != DEFAULT_DAILY_LIMIT or not self.enabled


class WebQuotaService(SqliteService):
    def _on_new_connection(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
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

    def _limits(self, uid: str) -> tuple[bool, int]:
        rows = self._query("SELECT enabled, daily_limit FROM web_quotas WHERE uid = ?", (uid,))
        if not rows:
            return True, DEFAULT_DAILY_LIMIT

        return bool(rows[0]["enabled"]), int(rows[0]["daily_limit"])

    def used_today(self, uid: str) -> int:
        rows = self._query("SELECT used FROM web_usage WHERE uid = ? AND day = ?", (uid, today()))
        return int(rows[0]["used"]) if rows else 0

    def state(self, uid: str) -> QuotaState:
        enabled, limit = self._limits(uid)
        return QuotaState(enabled, limit, self.used_today(uid))

    def refund(self, uid: str) -> None:
        """provider全线失败时退还本次扣减——「只在成功时扣」的承诺要靠它兑现。"""
        with self._lock:
            conn = self._connection()
            conn.execute(
                "UPDATE web_usage SET used = used - 1 WHERE uid = ? AND day = ? AND used > 0",
                (uid, today()),
            )
            conn.commit()

    def consume(self, uid: str) -> tuple[bool, QuotaState]:
        """原子地检查并扣减一次。返回是否放行及扣减后的状态。

        调用方必须以本方法的返回值作为闸门，不要先用 state() 判断再调用——
        两次调用之间存在竞态，会让超限用户穿透。state() 只用于展示。
        """
        with self._lock:
            conn = self._connection()
            rows = conn.execute(
                "SELECT enabled, daily_limit FROM web_quotas WHERE uid = ?", (uid,)
            ).fetchall()

            if rows:
                enabled, limit = bool(rows[0]["enabled"]), int(rows[0]["daily_limit"])
            else:
                enabled, limit = True, DEFAULT_DAILY_LIMIT

            day = today()
            used_rows = conn.execute(
                "SELECT used FROM web_usage WHERE uid = ? AND day = ?", (uid, day)
            ).fetchall()
            used = int(used_rows[0]["used"]) if used_rows else 0

            blocked = not enabled or (limit != UNLIMITED and used >= limit)
            if not blocked:
                conn.execute(
                    "INSERT INTO web_usage (uid, day, used) VALUES (?, ?, 1) "
                    "ON CONFLICT(uid, day) DO UPDATE SET used = used + 1",
                    (uid, day),
                )
                used += 1

            conn.commit()

        return not blocked, QuotaState(enabled, limit, used)

    def set_quota(self, uid: str, enabled: bool, daily_limit: int) -> None:
        self._execute(
            "INSERT INTO web_quotas (uid, enabled, daily_limit, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET enabled=excluded.enabled, "
            "daily_limit=excluded.daily_limit, updated_at=excluded.updated_at",
            (uid, 1 if enabled else 0, int(daily_limit), int(time.time())),
        )

    def clear_quota(self, uid: str) -> None:
        self._execute("DELETE FROM web_quotas WHERE uid = ?", (uid,))

    def list_quotas(self) -> list[dict]:
        return self._query(
            "SELECT uid, enabled, daily_limit, updated_at FROM web_quotas ORDER BY uid"
        )

    def purge_day(self, day: Optional[str] = None) -> None:
        self._execute("DELETE FROM web_usage WHERE day < ?", (day or today(),))


web_quota_service = WebQuotaService()