# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : context.py
# @Description : 项目会话上下文：项目说明、笔记索引与推荐技能

"""项目上下文装配。

项目知识放在项目目录内（spec §9.3）：`AGENTS.md` 常驻注入（截断），
`.novabot/notes/*.md` 只给文件名索引，正文由 agent 按需读取。
推荐技能只是软优先级，不改变技能可见性与审核门。
"""

import os
from dataclasses import dataclass
from typing import Optional

from const.prompts import ProjectSection
from service.project.errors import ProjectError
from service.project.paths import project_dir_state
from service.project.project_service import project_service

AGENTS_MAX_BYTES = 12 * 1024
NOTES_MAX_FILES = 200
NOTES_INDEX_MAX_BYTES = 8 * 1024


@dataclass(frozen=True)
class ProjectContext:
    """一个项目会话需要的稳定信息；不包含会话行。"""

    id: str
    name: str
    path: str
    archived: bool
    skills: tuple[dict, ...] = ()


def load_project_context(project_id: str, user_id: str) -> ProjectContext:
    """按会话归属加载项目；归档不阻止已有会话，目录失效才拒绝。"""
    row = project_service.get_project(user_id, project_id, include_archived=True)
    if row is None:
        raise ProjectError("project.not_found")

    path = row["path"]
    if project_dir_state(path) != "ok" or os.path.normcase(os.path.realpath(path)) != path:
        raise ProjectError("project.path_unavailable", path=path)

    return ProjectContext(
        id=row["id"],
        name=row["name"],
        path=path,
        archived=bool(row["archived_at"]),
        skills=tuple(row.get("skills") or ()),
    )


def _read_capped(path: str, limit: int) -> tuple[Optional[str], bool]:
    """按字节上限读取文本，截断落在 UTF-8 字符边界上。"""
    try:
        with open(path, "rb") as file:
            data = file.read(limit + 1)
    except OSError:
        return None, False

    if len(data) <= limit:
        try:
            return data.decode("utf-8").strip() or None, False
        except UnicodeDecodeError:
            return None, False

    cut = data[:limit]
    # 回退到完整 UTF-8 字符：最多回退 3 个字节
    for back in range(0, 4):
        try:
            text = cut[: len(cut) - back].decode("utf-8").strip()
            return text or None, True
        except UnicodeDecodeError:
            continue

    return None, True


def _note_index(notes_dir: str) -> tuple[tuple[str, ...], bool]:
    """只列 `.novabot/notes/*.md` 的文件名，最多 200 个、渲染后最多 8 KB。"""
    try:
        names = sorted(
            name for name in os.listdir(notes_dir)
            if name.endswith(".md") and os.path.isfile(os.path.join(notes_dir, name))
        )
    except OSError:
        return (), False

    truncated = len(names) > NOTES_MAX_FILES
    names = names[:NOTES_MAX_FILES]

    kept: list[str] = []
    used = 0
    for name in names:
        cost = len(f"- {name}\n".encode("utf-8"))
        if used + cost > NOTES_INDEX_MAX_BYTES:
            truncated = True
            break
        kept.append(name)
        used += cost

    return tuple(kept), truncated


def _recommended_skills(project: ProjectContext, ctx) -> tuple[str, ...]:
    """按当前身份可见且未被审核门隐藏的技能过滤推荐列表。"""
    if not project.skills:
        return ()

    from service.skill.skill_catalog import is_skill_hidden, visible_skills

    entries = visible_skills(ctx, include_system=True)
    picked: list[str] = []
    for ref in project.skills:
        if not isinstance(ref, dict):
            continue
        name = str(ref.get("name") or "")
        layer = str(ref.get("layer") or "")
        owner_uid = ref.get("owner_uid")
        for entry in entries:
            if entry.name != name or entry.layer != layer:
                continue
            if (entry.owner_uid or None) != (owner_uid or None):
                continue
            if is_skill_hidden(entry.skill_dir):
                break
            if name not in picked:
                picked.append(name)
            break

    return tuple(picked)


def build_project_section(project: ProjectContext, ctx) -> ProjectSection:
    """读取项目知识并组装提示段；每次建 agent 时调用，文件改动下一轮生效。"""
    brief, brief_truncated = _read_capped(
        os.path.join(project.path, "AGENTS.md"), AGENTS_MAX_BYTES)

    notes, notes_truncated = _note_index(os.path.join(project.path, ".novabot", "notes"))

    return ProjectSection(
        name=project.name,
        path=project.path,
        brief=brief,
        brief_truncated=brief_truncated,
        notes=notes,
        notes_truncated=notes_truncated,
        skills=_recommended_skills(project, ctx),
    )
