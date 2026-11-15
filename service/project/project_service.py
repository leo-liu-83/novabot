# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : project_service.py
# @Description : 项目登记与查询

import json
import os
import sqlite3
import time
import uuid
from typing import Optional, Sequence

from service.db_base import SqliteService
from service.project.errors import ProjectError
from service.project.paths import browse_dirs, project_dir_state, validate_project_path
from service.project.schema import ensure_projects_schema

NAME_MAX = 80
DESCRIPTION_MAX = 500

_SELECT = (
    "SELECT p.*, COUNT(s.id) AS session_count, MAX(s.updated_at) AS last_active "
    "FROM projects p LEFT JOIN sessions s ON s.project_id = p.id "
)


def _row(raw: dict) -> dict:
    """把行里的 skills JSON 解成列表；调用方拿到的是可直接下发的 dict。"""
    out = dict(raw)
    try:
        out["skills"] = json.loads(raw.get("skills") or "[]")
    except ValueError:
        out["skills"] = []
    out["description"] = raw.get("description") or ""
    out["session_count"] = int(raw.get("session_count") or 0)
    out["last_active"] = raw.get("last_active")
    # 项目页据此提示补 AGENTS.md；只查存在性，不读内容。
    out["has_agents"] = os.path.isfile(os.path.join(out.get("path") or "", "AGENTS.md"))
    return out


def _validate_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > NAME_MAX:
        raise ProjectError("project.name_invalid", max=NAME_MAX)
    return cleaned


def _validate_description(description: Optional[str]) -> str:
    cleaned = (description or "").strip()
    if len(cleaned) > DESCRIPTION_MAX:
        raise ProjectError("project.description_invalid", max=DESCRIPTION_MAX)
    return cleaned


def _translate_integrity(exc: sqlite3.IntegrityError) -> ProjectError:
    """SQLite 只报列名，不报索引名：`UNIQUE constraint failed: projects.user_id, projects.name`。"""
    text = str(exc)
    if "projects.name" in text:
        return ProjectError("project.name_taken")
    if "projects.path" in text:
        return ProjectError("project.path_nested")
    return ProjectError("project.name_taken")


class ProjectService(SqliteService):
    """项目登记；与会话共用一个库，建表时顺带保证 projects 表存在。"""

    def _on_new_connection(self, conn: sqlite3.Connection) -> None:
        ensure_projects_schema(conn)
        conn.commit()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute(sql, params)
            conn.commit()

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            conn = self._connection()
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def create(self, user_id: str, name: str, raw_path: str,
               skills: Sequence[dict] | None = None,
               description: str = "") -> dict:
        """登记一个已有目录；不创建 AGENTS.md，只补 .novabot/notes/ 目录。"""
        name = _validate_name(name)
        description = _validate_description(description)
        if self._query(
            "SELECT 1 FROM projects WHERE user_id = ? AND name = ? AND archived_at IS NULL",
            (user_id, name),
        ):
            raise ProjectError("project.name_taken")

        path = validate_project_path(
            raw_path, uid=user_id, existing_paths=self.active_paths(user_id)
        )
        os.makedirs(os.path.join(path, ".novabot", "notes"), exist_ok=True)

        now = int(time.time())
        project_id = str(uuid.uuid4())
        try:
            self._execute(
                "INSERT INTO projects (id, user_id, name, description, path, skills,"
                " archived_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (project_id, user_id, name, description, path,
                 json.dumps(list(skills or ()), ensure_ascii=False), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise _translate_integrity(exc) from exc

        return _row({
            "id": project_id, "user_id": user_id, "name": name,
            "description": description, "path": path,
            "skills": json.dumps(list(skills or ()), ensure_ascii=False),
            "archived_at": None, "created_at": now, "updated_at": now,
            "session_count": 0, "last_active": None,
        })

    def list_projects(self, user_id: str, *, include_archived: bool = False) -> list[dict]:
        sql = _SELECT + "WHERE p.user_id = ?"
        if not include_archived:
            sql += " AND p.archived_at IS NULL"
        sql += " GROUP BY p.id ORDER BY p.updated_at DESC"
        return [_row(r) for r in self._query(sql, (user_id,))]

    def get_project(self, user_id: str, project_id: str, *,
                    include_archived: bool = False) -> Optional[dict]:
        sql = _SELECT + "WHERE p.user_id = ? AND p.id = ?"
        if not include_archived:
            sql += " AND p.archived_at IS NULL"
        rows = self._query(sql + " GROUP BY p.id", (user_id, project_id))
        return _row(rows[0]) if rows else None

    def browse(self, user_id: str, raw_path: str = "") -> dict:
        """选择项目目录用的目录列表；只列目录名，不读文件内容。"""
        return browse_dirs(raw_path, uid=user_id)

    def active_paths(self, user_id: str) -> list[str]:
        rows = self._query(
            "SELECT path FROM projects WHERE user_id = ? AND archived_at IS NULL",
            (user_id,),
        )
        return [r["path"] for r in rows]

    def update_project(self, user_id: str, project_id: str, *,
                       name: Optional[str] = None,
                       description: Optional[str] = None,
                       skills: Optional[Sequence[dict]] = None) -> dict:
        row = self.get_project(user_id, project_id, include_archived=True)
        if row is None:
            raise ProjectError("project.not_found")

        sets, params = ["updated_at = ?"], [int(time.time())]
        if name is not None:
            name = _validate_name(name)
            if self._query(
                "SELECT 1 FROM projects WHERE user_id = ? AND name = ?"
                " AND archived_at IS NULL AND id != ?",
                (user_id, name, project_id),
            ):
                raise ProjectError("project.name_taken")
            sets.append("name = ?")
            params.append(name)
        if description is not None:
            sets.append("description = ?")
            params.append(_validate_description(description))
        if skills is not None:
            sets.append("skills = ?")
            params.append(json.dumps(list(skills), ensure_ascii=False))
        params.extend([project_id, user_id])
        try:
            self._execute(
                f"UPDATE projects SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
                tuple(params),
            )
        except sqlite3.IntegrityError as exc:
            raise _translate_integrity(exc) from exc

        return self.get_project(user_id, project_id, include_archived=True) or row

    def archive_project(self, user_id: str, project_id: str) -> bool:
        now = int(time.time())
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "UPDATE projects SET archived_at = ?, updated_at = ?"
                " WHERE id = ? AND user_id = ? AND archived_at IS NULL",
                (now, now, project_id, user_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    def require_usable(self, user_id: str, project_id: str) -> dict:
        """会话装配前的校验：归属、未归档、目录仍在原址且可写。"""
        row = self.get_project(user_id, project_id, include_archived=True)
        if row is None:
            raise ProjectError("project.not_found")
        if row["archived_at"]:
            raise ProjectError("project.archived")
        path = row["path"]
        # realpath 与存库值不一致 = 目录被换成了符号链接（或 relocated），不能再当作同一个项目
        if project_dir_state(path) != "ok" or os.path.normcase(os.path.realpath(path)) != path:
            raise ProjectError("project.path_unavailable", path=path)
        return row

    def delete_projects_by_user(self, user_id: str) -> None:
        self._execute("DELETE FROM projects WHERE user_id = ?", (user_id,))


project_service = ProjectService()
