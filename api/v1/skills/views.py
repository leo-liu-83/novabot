"""技能列表与审查动作：能力判定复用 skill_catalog.capabilities，不在这里另写一套。

技能正文/脚本属于本机文件，接口只返回元数据与扫描发现，不回传全文。
"""

from typing import Any, Optional

import os
import re

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth import resolve_user_ctx
from service.skill.skill_catalog import (
    ROLE_ADMIN,
    ROLE_ANONYMOUS,
    ROLE_SINGLE,
    has_scripts,
    resolve_role,
    shared_skills_path,
    user_workspace_path,
    visible_skills,
)
from service.skill.skill_package import (
    MAX_UPLOAD_BYTES,
    SkillPackageError,
    install_skill_package,
    list_skill_files,
    read_skill_file,
)
from service.skill.skill_review_service import skill_review_service
from service.user.user_service import UserCtx

router = APIRouter(prefix="/skills")


def _summary(skill_dir: str) -> str:
    """只取 SKILL.md frontmatter 的 description，正文不返回给浏览器。"""
    try:
        with open(os.path.join(skill_dir, "SKILL.md"), "r", encoding="utf-8") as handle:
            head = handle.read(4096)
    except OSError:
        return ""

    match = re.search(r"^description:\s*(.+)$", head, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _entry_payload(entry) -> dict:
    state = skill_review_service.get_state(entry.skill_dir)
    caps = entry.caps
    return {
        "name": entry.name,
        "layer": entry.layer,
        "owner": entry.owner_label,
        "owner_uid": getattr(entry, "owner_uid", None),
        "has_scripts": has_scripts(entry.skill_dir),
        "summary": _summary(entry.skill_dir),
        "caps": {
            "scan": caps.scan, "approve": caps.approve, "reject": caps.reject,
            "approve_needs_safe": caps.approve_needs_safe,
        },
        "status": state.status,
        "scan_verdict": state.scan_verdict,
        "stale": state.stale,
        "reviewed_at": state.reviewed_at,
        "findings": state.findings,
    }


def _find(ctx: UserCtx, name: str, *, layer: Optional[str] = None,
          owner_uid: Optional[str] = None, include_system: bool = False):
    for entry in visible_skills(ctx, include_system=include_system):
        if entry.name != name or (layer is not None and entry.layer != layer):
            continue
        # 管理员能看到多个用户的同名私有技能，必须按归属账号定位
        if owner_uid is not None and getattr(entry, "owner_uid", None) != owner_uid:
            continue
        return entry

    raise api_error("skill.not_found")


def _upload_root(ctx: UserCtx, target: str) -> tuple[str, str]:
    """上传落点：普通用户只能私有，管理员可私有+共享，单用户机主只有共享。"""
    role = resolve_role(ctx)
    target = (target or "private").strip().lower()

    if role == ROLE_ANONYMOUS:
        raise api_error("skill.upload_forbidden")
    if target not in ("private", "shared"):
        raise api_error("skill.upload_forbidden")
    if role == ROLE_SINGLE:
        if target != "shared":
            raise api_error("skill.upload_forbidden")
        return shared_skills_path(), "shared"
    if role != ROLE_ADMIN and target != "private":
        raise api_error("skill.upload_forbidden")

    if target == "shared":
        return shared_skills_path(), "shared"

    return f"{user_workspace_path(ctx.uid)}/skills", "private"


@router.get("/{name}/files", summary="列出技能内的文件")
async def skill_files(name: str, layer: Optional[str] = Query(default=None),
                      owner_uid: Optional[str] = Query(default=None),
                      ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    entry = _find(ctx, name, layer=layer, owner_uid=owner_uid, include_system=True)
    files, truncated = await run_in_threadpool(list_skill_files, entry.skill_dir)
    return response_code.resp_200({"files": files, "truncated": truncated})


@router.get("/{name}/file", summary="读取技能内的单个文件")
async def skill_file(name: str, path: str = Query(...), layer: Optional[str] = Query(default=None),
                     owner_uid: Optional[str] = Query(default=None),
                     ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    entry = _find(ctx, name, layer=layer, owner_uid=owner_uid, include_system=True)
    try:
        data = await run_in_threadpool(read_skill_file, entry.skill_dir, path)
    except SkillPackageError as exc:
        raise api_error(exc.code) from exc

    return response_code.resp_200(data)


@router.post("/upload", summary="上传 zip/.skill 技能包")
async def upload_skill(file: UploadFile = File(...), target: str = Form("private"),
                       ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    root, effective = _upload_root(ctx, target)
    # 最多读上限+1，不能先无界 read 吃满进程内存
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        if len(data) > MAX_UPLOAD_BYTES:
            raise api_error("skill.package_too_large")
        name, dest = await run_in_threadpool(
            install_skill_package, data, file.filename or "", root)
    except SkillPackageError as exc:
        raise api_error(exc.code) from exc
    finally:
        await file.close()

    return response_code.resp_200({"name": name, "target": effective, "path": dest})


@router.get("", summary="列出当前身份可见的技能与审查状态")
async def list_skills(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    return response_code.resp_200({
        # Web 技能页要完整显示系统/共享/私有三类；系统层只读
        "skills": [_entry_payload(entry) for entry in visible_skills(ctx, include_system=True)],
    })


@router.post("/{name}/scan", summary="扫描技能并记录发现（不改放行位）")
async def scan_skill(name: str, layer: Optional[str] = Query(default=None),
                     owner_uid: Optional[str] = Query(default=None),
                     ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    entry = _find(ctx, name, layer=layer, owner_uid=owner_uid)
    if not entry.caps.scan:
        raise api_error("skill.scan_forbidden")

    await run_in_threadpool(skill_review_service.scan, entry.skill_dir)
    return response_code.resp_200(_entry_payload(entry))


@router.post("/{name}/release", summary="放行技能")
async def release_skill(name: str, layer: Optional[str] = Query(default=None),
                        owner_uid: Optional[str] = Query(default=None),
                        ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    entry = _find(ctx, name, layer=layer, owner_uid=owner_uid)
    if not entry.caps.approve:
        raise api_error("skill.release_forbidden")

    state = skill_review_service.get_state(entry.skill_dir)
    if state.is_dangerous and entry.caps.approve_needs_safe:
        # 非 admin 只能放行扫描结论为安全的技能；危险项必须由管理员自己看过后放行
        raise api_error("skill.dangerous")

    await run_in_threadpool(skill_review_service.release, entry.skill_dir)
    return response_code.resp_200(_entry_payload(entry))


@router.post("/{name}/reject", summary="拒绝技能")
async def reject_skill(name: str, layer: Optional[str] = Query(default=None),
                       owner_uid: Optional[str] = Query(default=None),
                       ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    entry = _find(ctx, name, layer=layer, owner_uid=owner_uid)
    if not entry.caps.reject:
        raise api_error("skill.reject_forbidden")

    await run_in_threadpool(skill_review_service.reject, entry.skill_dir)
    return response_code.resp_200(_entry_payload(entry))
