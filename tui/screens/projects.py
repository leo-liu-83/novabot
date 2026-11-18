# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : projects.py
# @Description : TUI 项目：当前目录创建项目、自动生成 AGENTS.md、只允许改名

"""TUI 的项目管理保持最小面：

- 项目目录固定为启动 NovaBot 时所在的目录，不由用户选择；
- 创建项目只填名称，不填描述/技能；
- 创建成功后按目录现状自动写一版 AGENTS.md，已有文件不覆盖；
- 可以重命名，也可以删除（删除会一并清掉目录里的 .novabot 与 AGENTS.md，先确认）；
- 不能改目录或改技能：目录由启动位置决定，改目录等于删掉重建。
"""

import os
import shutil

from service.project.agents import build_agents_from_scan, write_agents_draft
from service.project.errors import ProjectError
from service.project.project_service import project_service


def current_directory() -> str:
    """项目目录固定为当前工作目录，规范化成与服务层一致的 realpath。"""
    return os.path.realpath(os.getcwd())


def visible_project_commands(can_projects: bool) -> list[tuple[str, str]]:
    if not can_projects:
        return []
    return [("/project", "创建、改名或删除项目（目录固定为当前目录）")]


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


def project_files_missing(project: dict) -> list[str]:
    """项目目录里缺哪些项目文件：notes 目录与 AGENTS.md。

    目录被删掉重建、或项目是旧版本登记的，这两种东西都可能不在——
    `/project` 据此给出“补齐”入口，而不是假装已经建好了。
    """
    root = str(project.get("path") or "")
    missing: list[str] = []
    if not os.path.isdir(os.path.join(root, ".novabot", "notes")):
        missing.append(".novabot/notes")
    if not os.path.isfile(os.path.join(root, "AGENTS.md")):
        missing.append("AGENTS.md")
    return missing


def repair_project_files(project: dict) -> list[str]:
    """补齐缺失的项目文件，返回补了哪些；已有的绝不覆盖。"""
    root = str(project.get("path") or "")
    missing = project_files_missing(project)
    if not missing:
        return []

    if ".novabot/notes" in missing:
        os.makedirs(os.path.join(root, ".novabot", "notes"), exist_ok=True)
    if "AGENTS.md" in missing:
        write_agents_draft(project, build_agents_from_scan(project))
    return missing


def delete_current_project(uid: str, project_id: str, *, remove_files: bool) -> dict:
    """删除项目登记；`remove_files=True` 时再清掉目录里的 .novabot 与 AGENTS.md。

    文件清理必须由调用方显式选择：AGENTS.md 里可能是用户自己写的内容，
    删登记只是整理归属，不该默认连带删文件。

    先删登记（会话自动解绑），再删文件：文件删不掉时登记已经是没了，
    调用方据 `removed` 提示即可，不能反过来留下"项目还在但文件没了"。
    """
    row = project_service.delete_project(uid, project_id)
    removed: list[str] = []
    row["removed"] = removed
    if not remove_files:
        return row

    root = str(row.get("path") or "")
    meta = os.path.join(root, ".novabot")
    if root and os.path.isdir(meta):
        shutil.rmtree(meta, ignore_errors=True)
        if not os.path.exists(meta):
            removed.append(".novabot")

    agents = os.path.join(root, "AGENTS.md")
    if root and os.path.isfile(agents):
        try:
            os.unlink(agents)
            removed.append("AGENTS.md")
        except OSError:
            pass

    return row


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
