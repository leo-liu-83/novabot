# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : skill_review_service.py
# @Software    : VSCode
# @Datetime    : 2026-10-13 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 技能审查状态：扫描结论 + 放行决定，供加载/执行闸门与TUI查询

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

from config import config
from service.db_base import SqliteService
from .skill_scan import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    SKIP_DIRS,
    ScanResult,
    is_executable_file,
    scan_skill,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_reviews (
    skill_path  TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    approved    INTEGER NOT NULL,
    findings    TEXT NOT NULL DEFAULT '[]',
    reviewed_at INTEGER NOT NULL,
    status      TEXT,
    scan_verdict TEXT
);
"""

# 加列迁移：老库升级时按 approved 回填 status，approved=0 一律按最保守的"未放行"处理
MIGRATIONS = [
    ("skill_reviews", "status", "TEXT"),
    ("skill_reviews", "scan_verdict", "TEXT"),
]

# 人工决定：只有这两种会落库，NULL=从未决定（新技能/仅扫描过）
STATUS_RELEASED = "released"   # 已放行
STATUS_REJECTED = "rejected"   # 已拒绝
# 扫描结论：与"放行"正交，扫描动作本身不改放行位
VERDICT_SAFE = "safe"
VERDICT_DANGEROUS = "dangerous"

# 危险阈值：出现 high 或 medium 即判危险；仅 low 或零发现为安全。
# base64/动态加载等 low 规则本就声明"混淆载荷看不见"，用 low 判危险会大面积误伤
_DANGEROUS_SEVERITIES = {SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM}

# 哈希覆盖范围：可执行面（SKILL.md + scripts/下的.py）。references/assets是只读材料，
# 不参与执行，纳入哈希只会让无关编辑把审批打回未审查。
_HASH_MAX_BYTES = 1024 * 1024


@dataclass
class ReviewState:
    status: Optional[str]          # 'released' / 'rejected' / None
    scan_verdict: Optional[str]    # 'safe' / 'dangerous' / None
    content_hash: str
    stale: bool
    findings: list
    reviewed_at: int

    @property
    def released(self) -> bool:
        return self.status == STATUS_RELEASED

    @property
    def rejected(self) -> bool:
        return self.status == STATUS_REJECTED

    @property
    def unscanned(self) -> bool:
        return self.scan_verdict is None

    @property
    def is_dangerous(self) -> bool:
        return self.scan_verdict == VERDICT_DANGEROUS

    # 兼容旧消费者：approved 即放行位的镜像
    @property
    def approved(self) -> bool:
        return self.released

    # 加载/执行闸门的统一判据：脚本技能必须"已放行且内容未变更"
    @property
    def runnable(self) -> bool:
        return self.released and not self.stale


def content_hash(skill_dir: str) -> str:
    """技能可执行面的SHA-256。任一字节变化都会改变结果。

    覆盖scripts/下的所有文件（不只.py）：扫描面、审批面、哈希面三者口径必须一致，
    否则放行后把脚本换成另一个文件，放行位照样有效。
    """
    digest = hashlib.sha256()
    skill_md = os.path.join(skill_dir, "SKILL.md")

    if os.path.isfile(skill_md):
        _feed(digest, skill_md, "SKILL.md")

    scripts_dir = os.path.join(skill_dir, "scripts")
    if os.path.isdir(scripts_dir):
        for root, dirs, files in os.walk(scripts_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in sorted(files):
                if name.startswith("."):
                    continue
                if not is_executable_file(name):
                    continue
                full = os.path.join(root, name)
                _feed(digest, full, os.path.relpath(full, skill_dir))

    return digest.hexdigest()


def _feed(digest, path: str, rel: str) -> None:
    digest.update(rel.encode("utf-8"))
    digest.update(b"\x00")
    try:
        with open(path, "rb") as f:
            data = f.read(_HASH_MAX_BYTES)
    except OSError:
        digest.update(b"<unreadable>")
        return

    digest.update(str(len(data)).encode("ascii"))
    digest.update(data)


def verdict_of(result: ScanResult) -> str:
    """扫描结果 → 安全/危险。只看是否命中 high/medium。"""
    if any(f.severity in _DANGEROUS_SEVERITIES for f in result.findings):
        return VERDICT_DANGEROUS

    return VERDICT_SAFE


class SkillReviewService(SqliteService):
    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _on_new_connection(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
        for table, column, declaration in MIGRATIONS:
            if column in self._table_columns(conn, table):
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        # 老行按 approved 回填 status；approved=0（含旧的"扫一下"写入）保守当作未放行
        conn.execute(
            "UPDATE skill_reviews SET status = CASE WHEN approved = 1 "
            "THEN ? ELSE ? END WHERE status IS NULL",
            (STATUS_RELEASED, STATUS_REJECTED),
        )
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

    def get_state(self, skill_dir: str) -> ReviewState:
        """当前审查状态。stale=True表示放行/扫描后内容变过，脚本技能据此收回可执行性。"""
        path = os.path.abspath(skill_dir)
        current = content_hash(path)
        rows = self._query("SELECT * FROM skill_reviews WHERE skill_path = ?", (path,))

        if not rows:
            return ReviewState(None, None, current, False, [], 0)

        row = rows[0]
        try:
            findings = json.loads(row["findings"])
        except (ValueError, TypeError):
            findings = []

        stored = row["content_hash"] or ""
        return ReviewState(
            status=row["status"],
            scan_verdict=row["scan_verdict"],
            content_hash=stored,
            stale=bool(stored) and stored != current,
            findings=findings,
            reviewed_at=int(row["reviewed_at"] or 0),
        )

    def _upsert(self, path: str, digest: str, findings_json: str, status: Optional[str],
                verdict: Optional[str], keep_status: bool = False, keep_verdict: bool = False) -> None:
        """写一行；keep_*为True时保留原值（扫描不动放行位、放行/拒绝不动扫描结论）。"""
        now = int(time.time())
        approved = 1 if status == STATUS_RELEASED else 0
        cols = "skill_path, content_hash, approved, findings, reviewed_at, status, scan_verdict"
        vals = (path, digest, approved, findings_json, now, status, verdict)
        update = ("content_hash=excluded.content_hash, reviewed_at=excluded.reviewed_at, "
                  "approved=excluded.approved, findings=excluded.findings")
        if not keep_status:
            update += ", status=excluded.status"
        if not keep_verdict:
            update += ", scan_verdict=excluded.scan_verdict"
        self._execute(
            f"INSERT INTO skill_reviews ({cols}) VALUES (?, ?, ?, ?, ?, ?, ?) "
            f"ON CONFLICT(skill_path) DO UPDATE SET {update}",
            vals,
        )

    def _findings_verdict(self, path: str) -> tuple[str, Optional[str]]:
        row = self._query("SELECT findings, scan_verdict FROM skill_reviews WHERE skill_path = ?", (path,))
        if not row:
            return "[]", None

        return row[0]["findings"], row[0]["scan_verdict"]

    def scan(self, skill_dir: str) -> tuple[ReviewState, ScanResult]:
        """只扫描：算结论、存发现与哈希，不改放行位。"""
        path = os.path.abspath(skill_dir)
        result = scan_skill(path)
        findings_json = json.dumps([f.to_dict() for f in result.findings], ensure_ascii=False)
        self._upsert(path, content_hash(path), findings_json, status=None,
                     verdict=verdict_of(result), keep_status=True)

        return self.get_state(path), result

    def release(self, skill_dir: str) -> ReviewState:
        """批准：置已放行，并把哈希钉到当前内容（此后改动即stale）。"""
        path = os.path.abspath(skill_dir)
        findings_json, verdict = self._findings_verdict(path)
        self._upsert(path, content_hash(path), findings_json, status=STATUS_RELEASED,
                     verdict=verdict, keep_verdict=True)

        return self.get_state(path)

    def reject(self, skill_dir: str) -> ReviewState:
        """拒绝：置未放行，保留已有扫描结论供回看。"""
        path = os.path.abspath(skill_dir)
        findings_json, verdict = self._findings_verdict(path)
        self._upsert(path, content_hash(path), findings_json, status=STATUS_REJECTED,
                     verdict=verdict, keep_verdict=True)

        return self.get_state(path)

    def forget(self, skill_dir: str) -> None:
        self._execute("DELETE FROM skill_reviews WHERE skill_path = ?", (os.path.abspath(skill_dir),))

    def all_states(self) -> list[dict]:
        return self._query("SELECT skill_path, status, scan_verdict, approved, content_hash, reviewed_at "
                           "FROM skill_reviews ORDER BY skill_path")


skill_review_service = SkillReviewService()
