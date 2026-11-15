"""项目 API：登记已有目录、改名、归档。"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth import resolve_user_ctx
from service.project.errors import ProjectError
from service.project.agents import (
    build_agents_draft, delete_agents_file, generate_agents_with_model,
    read_agents_file, write_agents_draft,
)
from service.project.project_service import project_service
from service.user.user_service import UserCtx

router = APIRouter(prefix="/projects")


class CreateProjectSchema(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1)
    # 长度由服务层校验：pydantic 的 422 文案没有 error code，前端翻译不了
    description: str = ""
    skills: list[dict] = Field(default_factory=list)


class UpdateProjectSchema(BaseModel):
    # 缺省=不改；显式给值时校验长度，null 与省略等价（路径不可改）
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = None
    skills: Optional[list[dict]] = None


class WriteAgentsSchema(BaseModel):
    # 内容长度由服务层校验：pydantic 422 没有可翻译的 error code
    content: str = ""


def _require_projects(ctx: UserCtx) -> None:
    if not ctx.can_projects:
        raise api_error("project.disabled")


def _translate(exc: ProjectError) -> HTTPException:
    return api_error(exc.code, **exc.vars)


@router.get("", summary="列出当前用户的活动项目")
async def list_projects(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    return response_code.resp_200(project_service.list_projects(ctx.uid))


@router.post("", summary="登记一个已有目录作为项目")
async def create_project(input: CreateProjectSchema,
                         ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    try:
        row = project_service.create(ctx.uid, input.name, input.path, input.skills,
                                     input.description)
    except ProjectError as exc:
        raise _translate(exc) from exc
    return response_code.resp_200(row)


@router.get("/browse", summary="浏览本机目录（选择项目目录用）")
async def browse_projects(path: str = "", ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    try:
        return response_code.resp_200(project_service.browse(ctx.uid, path))
    except ProjectError as exc:
        raise _translate(exc) from exc


@router.get("/{project_id}", summary="项目详情")
async def get_project(project_id: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    row = project_service.get_project(ctx.uid, project_id)
    if row is None:
        raise api_error("project.not_found")
    return response_code.resp_200(row)


@router.patch("/{project_id}", summary="改名或调整项目技能")
async def update_project(project_id: str, input: UpdateProjectSchema,
                         ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    try:
        row = project_service.update_project(
            ctx.uid, project_id, name=input.name,
            description=input.description, skills=input.skills)
    except ProjectError as exc:
        raise _translate(exc) from exc
    return response_code.resp_200(row)


@router.post("/{project_id}/agents-draft", summary="生成 AGENTS.md 草稿（不落盘）")
async def draft_project_agents(project_id: str,
                               ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    row = project_service.get_project(ctx.uid, project_id)
    if row is None:
        raise api_error("project.not_found")
    draft = build_agents_draft(row)
    return response_code.resp_200({
        "eligible": draft.eligible, "reason": draft.reason,
        "content": draft.content, "sources": list(draft.sources),
    })


@router.post("/{project_id}/agents", summary="写入用户确认过的 AGENTS.md 草稿")
async def write_project_agents(project_id: str, input: WriteAgentsSchema,
                               ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    row = project_service.get_project(ctx.uid, project_id)
    if row is None:
        raise api_error("project.not_found")
    try:
        result = write_agents_draft(row, input.content)
    except ProjectError as exc:
        raise _translate(exc) from exc
    return response_code.resp_200(result)


@router.get("/{project_id}/agents", summary="读取项目根 AGENTS.md")
async def read_project_agents(project_id: str,
                              ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    row = project_service.get_project(ctx.uid, project_id)
    if row is None:
        raise api_error("project.not_found")
    try:
        return response_code.resp_200(read_agents_file(row))
    except ProjectError as exc:
        raise _translate(exc) from exc


@router.put("/{project_id}/agents", summary="保存项目根 AGENTS.md（编辑或手工创建）")
async def update_project_agents(project_id: str, input: WriteAgentsSchema,
                                ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    row = project_service.get_project(ctx.uid, project_id)
    if row is None:
        raise api_error("project.not_found")
    try:
        result = write_agents_draft(row, input.content, overwrite=True)
    except ProjectError as exc:
        raise _translate(exc) from exc
    return response_code.resp_200(result)


@router.delete("/{project_id}/agents", summary="删除项目根 AGENTS.md")
async def delete_project_agents(project_id: str,
                                ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    row = project_service.get_project(ctx.uid, project_id)
    if row is None:
        raise api_error("project.not_found")
    try:
        return response_code.resp_200(delete_agents_file(row))
    except ProjectError as exc:
        raise _translate(exc) from exc


@router.post("/{project_id}/agents-generate", summary="让模型根据项目现状生成 AGENTS.md 草稿")
async def generate_project_agents(project_id: str,
                                  ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    row = project_service.get_project(ctx.uid, project_id)
    if row is None:
        raise api_error("project.not_found")
    try:
        result = await generate_agents_with_model(row)
    except ProjectError as exc:
        raise _translate(exc) from exc
    return response_code.resp_200(result)


@router.delete("/{project_id}", summary="归档项目（不删目录）")
async def archive_project(project_id: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    _require_projects(ctx)
    if not project_service.archive_project(ctx.uid, project_id):
        raise api_error("project.not_found")
    return response_code.resp_200({"project_id": project_id, "archived": True})
