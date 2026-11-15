# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : skill_catalog.py
# @Software    : VSCode
# @Author      : leo liu
# @Version     : 1.0
# @Description : 技能可见性与权限目录：按"角色×层"决定谁能看/扫描/批准/拒绝哪些技能

import os
from dataclasses import dataclass
from typing import Optional

from config import config
from service.user.user_service import (
    DEFAULT_USER_ID,
    UserCtx,
    shared_skills_path,
    system_skills_path,
    user_service,
    user_workspace_path,
)

from .skill_review_service import skill_review_service
from .skill_scan import SKIP_DIRS, is_executable_file

LAYER_SYSTEM = "system"
LAYER_SHARED = "shared"
LAYER_PRIVATE = "private"

# 单用户机主等同admin但作用域仅共享；多用户匿名只能动共享且无拒绝
ROLE_ANONYMOUS = "anonymous"
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_SINGLE = "single"


@dataclass(frozen=True)
class Caps:
    view: bool
    scan: bool
    approve: bool
    reject: bool
    approve_needs_safe: bool


NO_CAPS = Caps(False, False, False, False, True)


def has_scripts(skill_dir: str) -> bool:
    """是否含可执行面：scripts/下存在任何文件。

    不只认.py——.sh/.ps1/.js这些同样能跑（agent在T2+可用execute调它们，
    T1技能脚本通道虽只认.py，内容照样能被读进上下文）。口径必须与扫描面、
    哈希面一致，否则"没扫到"的东西照样能加载。
    """
    scripts = os.path.join(skill_dir, "scripts")
    if not os.path.isdir(scripts):
        return False

    for root, dirs, files in os.walk(scripts):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            if is_executable_file(name):
                return True

    return False


def is_skill_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "SKILL.md"))


def is_skill_hidden(skill_dir: str) -> bool:
    """该技能目录当前对agent是否不可见。

    加载门（skill_backend）与执行门（exec层拦未放行技能路径）共用这一判据——
    两边各写一份必然漂移，出现"文件工具看不见、shell却能读"这种不一致。
    """
    state = skill_review_service.get_state(skill_dir)
    if not has_scripts(skill_dir):
        # 纯提示词技能默认放行，仅被显式拒绝才隐藏
        return state.rejected

    # 脚本技能须已放行且内容未变更
    return not state.runnable


def skill_roots_on_disk() -> list[str]:
    """本机全部技能根（共享 + 各用户私有），按真实路径去重。

    shell以OS用户身份运行，能摸到整盘——所以执行层的隐藏集合必须覆盖全机：
    只算当前身份的技能根，别人私有的未放行技能照样能被cat到。
    """
    roots = [shared_skills_path()]
    for row in user_service.list_users():
        roots.append(f"{user_workspace_path(row['uid'])}/skills")

    out: list[str] = []
    seen: set[str] = set()
    for root in roots:
        path = os.path.abspath(root)
        if path in seen:
            continue
        seen.add(path)
        out.append(path)

    return out


def hidden_skill_dirs() -> list[str]:
    """全机尚未放行的技能目录（真实路径）——执行层要挡的就是这些。"""
    hidden: list[str] = []
    for root in skill_roots_on_disk():
        if not os.path.isdir(root):
            continue
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            skill_dir = os.path.join(root, name)
            if not os.path.isdir(skill_dir) or not is_skill_dir(skill_dir):
                continue
            if is_skill_hidden(skill_dir):
                hidden.append(os.path.abspath(skill_dir))

    return hidden


def resolve_role(ctx: UserCtx) -> str:
    if ctx.uid == DEFAULT_USER_ID:
        return ROLE_SINGLE if not config.MULTI_USER else ROLE_ANONYMOUS

    return ROLE_ADMIN if ctx.is_admin else ROLE_USER


def _under(child: str, parent: str) -> bool:
    child, parent = os.path.abspath(child), os.path.abspath(parent)
    return child == parent or child.startswith(parent + os.sep)


def classify_skill(skill_dir: str) -> tuple[str, Optional[str]]:
    """返回(层, 归属uid)。uid为归属用户的私有层时给出；共享/系统层为None。"""
    if _under(skill_dir, system_skills_path()):
        return LAYER_SYSTEM, None
    if _under(skill_dir, shared_skills_path()):
        return LAYER_SHARED, None

    for row in user_service.list_users():
        if _under(skill_dir, f"{user_workspace_path(row['uid'])}/skills"):
            return LAYER_PRIVATE, row["uid"]

    return LAYER_PRIVATE, None  # 不在任何登记用户下的私有目录


def capabilities(role: str, layer: str, owner_uid: Optional[str], ctx: UserCtx) -> Caps:
    """唯一的能力真源：UI弹窗与命令入口都走它，谁也不能凭参数绕过。

    非admin一律 approve_needs_safe=True（危险不可放行）；只有admin/单用户机主能放行危险技能。
    拒绝只给admin/单用户机主；匿名与普通用户无拒绝动作。
    """
    if layer == LAYER_SYSTEM:
        # 系统技能由仓库维护，Web 只允许查看，不能扫描/放行/拒绝
        return Caps(True, False, False, False, True)

    if role == ROLE_SINGLE:
        # 单用户机主=admin能力，但作用域锁共享（无他人私有）
        if layer != LAYER_SHARED:
            return NO_CAPS
        return Caps(True, True, True, True, False)

    if role == ROLE_ADMIN:
        return Caps(True, True, True, True, False)

    if role == ROLE_ANONYMOUS:
        # 匿名只在多用户下存在，仅能动共享，且无拒绝、只放安全的
        if layer != LAYER_SHARED:
            return NO_CAPS
        return Caps(True, True, True, False, True)

    # ROLE_USER：共享只读；自己私有可扫描+放行安全的；他人私有不可见
    if layer == LAYER_SHARED:
        return Caps(True, False, False, False, True)
    if layer == LAYER_PRIVATE and owner_uid == ctx.uid:
        return Caps(True, True, True, False, True)

    return NO_CAPS


@dataclass
class SkillEntry:
    name: str
    skill_dir: str
    layer: str
    owner_label: str
    caps: Caps
    # 私有层归属账号；共享/系统为 None。同名私有技能必须靠它区分
    owner_uid: Optional[str] = None


def _owner_label(layer: str, owner_uid: Optional[str], names: dict) -> str:
    if layer == LAYER_SYSTEM:
        return "系统"
    if layer == LAYER_SHARED:
        return "共享"
    if owner_uid and owner_uid in names:
        return f"@{names[owner_uid]}"
    return "私有"


def visible_skills(ctx: UserCtx, *, include_system: bool = False) -> list[SkillEntry]:
    """当前身份"有权查看"的全量技能（含纯提示词技能）。

    include_system=True 时把系统层也列出来（Web 技能页需要三类齐全）；
    TUI 默认不含系统层，保持原有审查范围。
    """
    role = resolve_role(ctx)
    names = {row["uid"]: row["username"] for row in user_service.list_users()}

    roots: list[tuple[str, Optional[str]]] = []
    if include_system:
        roots.append((system_skills_path(), None))
    if role in (ROLE_ADMIN,):
        # admin：所有用户私有 + 共享
        for uid in names:
            roots.append((f"{user_workspace_path(uid)}/skills", uid))
    if role in (ROLE_ADMIN, ROLE_USER):
        if role == ROLE_USER:
            roots.append((f"{user_workspace_path(ctx.uid)}/skills", ctx.uid))
    if role in (ROLE_ADMIN, ROLE_USER, ROLE_ANONYMOUS, ROLE_SINGLE):
        roots.append((shared_skills_path(), None))

    seen: set[str] = set()
    entries: list[SkillEntry] = []
    for root, owner_uid in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            skill_dir = os.path.join(root, name)
            ab = os.path.abspath(skill_dir)
            if ab in seen or not is_skill_dir(skill_dir):
                continue
            seen.add(ab)

            layer, cls_uid = classify_skill(skill_dir)
            uid = owner_uid if owner_uid is not None else cls_uid
            caps = capabilities(role, layer, uid, ctx)
            if not caps.view:
                continue
            entries.append(SkillEntry(
                name=name, skill_dir=ab, layer=layer,
                owner_label=_owner_label(layer, uid, names), caps=caps,
                owner_uid=uid if layer == LAYER_PRIVATE else None,
            ))

    return entries


def review_state(skill_dir: str):
    return skill_review_service.get_state(skill_dir)
