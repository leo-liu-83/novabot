# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : schema.py
# @Description : projects 表与索引的 DDL（会话层与项目层共用）

"""projects 表与索引。项目层与会话层各自执行一次。

`sessions` 与 `projects` 会互相 JOIN（会话行带项目名、项目行带会话计数），
所以谁先连库都不能缺对方那张表：TUI 只跑会话层，不会 import 项目 API。
本模块刻意不 import 任何东西——会话层要 import 它，避免成环。
"""

PROJECTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL,
    skills      TEXT NOT NULL DEFAULT '[]',
    archived_at INTEGER,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

-- 同一用户的活动项目：名称唯一、路径唯一；归档后允许重新使用同名/同路径
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_user_name
    ON projects(user_id, name) WHERE archived_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_user_path
    ON projects(user_id, path) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_projects_user_updated
    ON projects(user_id, updated_at);
"""

# CREATE TABLE IF NOT EXISTS 不会给存量表补列：新增字段都在这里登记，
# 项目层与会话层谁先连库都走同一个迁移入口。
PROJECT_COLUMNS = (
    ("description", "TEXT NOT NULL DEFAULT ''"),
)


def ensure_projects_schema(conn) -> None:
    """建表 + 补齐缺失列；两个服务共用，避免只改一边导致 TUI 先连库时缺列。"""
    conn.executescript(PROJECTS_SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    for column, declaration in PROJECT_COLUMNS:
        if column not in columns:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {declaration}")
