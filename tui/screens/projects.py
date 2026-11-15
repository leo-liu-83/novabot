# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : projects.py
# @Description : TUI 项目：当前目录创建项目、自动生成 AGENTS.md、只允许改名

"""TUI 的项目管理保持最小面：

- 项目目录固定为启动 NovaBot 时所在的目录，不由用户选择；
- 创建项目只填名称，不填描述/技能；
- 创建成功后按目录现状自动写一版 AGENTS.md，已有文件不覆盖；
- 只能重命名，不能删除、改目录或改技能。
"""

import os

from service.project.agents import build_agents_from_scan, write_agents_draft
from service.project.errors import ProjectError
from service.project.project_service import project_service


def current_directory() -> str:
    """项目目录固定为当前工作目录，规范化成与服务层一致的 realpath。"""
    return os.path.realpath(os.getcwd())


def visible_project_commands(can_projects: bool) -> list[tuple[str, str]]:
    if not can_projects:
        return []
    return [("/project", "创建或重命名项目（弹窗；目录固定为当前目录）")]


def list_projects(uid: str) -> list[dict]:
    return project_service.list_projects(uid)


def project_at_current(uid: str) -> dict | None:
    current = os.path.normcase(current_directory())
    for row in list_projects(uid):
        if os.path.normcase(str(row.get("path") or "")) == current:
            return row
    return None


def create_current_project(uid: str, name: str) -> tuple[dict, str | None]:
    """创建当前目录的项目并自动生成 AGENTS.md。

    返回 (项目行, AGENTS 状态)。AGENTS 状态为 None 表示已写入；"exists" 表示目录
    原本已有文件（保留用户内容）；其它字符串是写入失败的 error code。
    项目创建成功但 AGENTS 写入失败时仍返回项目行，由调用方提示。
    """
    row = project_service.create(uid, name, current_directory(), [], description="")
    target = os.path.join(str(row["path"]), "AGENTS.md")
    if os.path.exists(target):
        return row, "exists"
    try:
        write_agents_draft(row, build_agents_from_scan(row))
    except ProjectError as exc:
        return row, exc.code
    return row, None


def rename_project(uid: str, project_id: str, name: str) -> dict:
    return project_service.update_project(uid, project_id, name=name)


_PROJECT_ERRORS = {
    "project.name_taken": "已有同名项目",
    "project.name_invalid": "项目名称不能为空且不超过 {max} 个字符",
    "project.path_invalid": "项目路径不可用：{reason}",
    "project.path_forbidden": "不能把该目录登记为项目：{path}",
    "project.path_nested": "该目录与已有项目目录重叠",
    "project.path_unavailable": "项目目录已失效或被移动到别处：{path}",
    "project.archived": "项目已归档",
}


def project_error_text(exc: ProjectError) -> str:
    text = _PROJECT_ERRORS.get(exc.code)
    if not text:
        return exc.code
    try:
        return text.format(**exc.vars)
    except (KeyError, ValueError):
        return text
