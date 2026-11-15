"""会话、历史与附件 API：Web/TUI 共用同一套服务端会话数据。"""

import json
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from api.resp import response_code
from api.resp.errors import api_error
from api.v1.auth import resolve_user_ctx
from config import config
from core.agent import persistence
from core.agent.deep_agents.exec_policy import (
    ExecLevel,
    effective_ceiling,
    parse_level,
    resolve_level,
    set_session_override,
)
from service.project.project_service import project_service
from service.project.errors import ProjectError
from service.session import approvals
from service.session.session_service import session_service
from service.user.user_service import UserCtx
from service.attachment.attachments import (
    AttachmentError, from_json, max_file_bytes, stage_bytes,
)

router = APIRouter(prefix="/sessions")


class RenameSessionSchema(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class CreateSessionSchema(BaseModel):
    model: Optional[str] = None
    thinking: Optional[bool] = None
    # 空 = 无项目会话，行为与引入项目前一致
    project_id: Optional[str] = None


class SessionLevelSchema(BaseModel):
    # None = 回到身份默认档位；T0..T4 = 本会话覆盖
    level: Optional[str] = None


_LEVELS = {f"T{int(level)}": level for level in ExecLevel}


def _as_level(level: ExecLevel) -> str:
    return f"T{int(level)}"


def _with_exec_level(row: Optional[dict], ctx: UserCtx) -> Optional[dict]:
    """会话行带上当前生效档位；覆盖按 (uid, session_id) 存进程内，和 TUI /exec 同源。"""
    if row is None:
        return None
    out = dict(row)
    out["exec_level"] = _as_level(resolve_level(config.EXEC_LEVEL, ctx, out["id"]))
    return out


@router.get("", summary="列出当前用户的会话")
async def list_sessions(
    limit: int = Query(100, ge=1, le=200),
    project_id: Optional[str] = Query(None),
    ctx: UserCtx = Depends(resolve_user_ctx),
) -> Any:
    rows = session_service.list_sessions(limit=limit, user_id=ctx.uid,
                                         project_id=project_id)
    return response_code.resp_200([_with_exec_level(row, ctx) for row in rows])


@router.post("", summary="创建会话")
async def create_session(input: CreateSessionSchema, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    if input.project_id:
        if not ctx.can_projects:
            raise api_error("project.disabled")
        try:
            project_service.require_usable(ctx.uid, input.project_id)
        except ProjectError as exc:
            raise api_error(exc.code, **exc.vars) from exc
    try:
        session_id = session_service.create(model=input.model, thinking=input.thinking,
                                            user_id=ctx.uid, project_id=input.project_id)
    except ValueError as exc:
        raise api_error("session.create_blocked", status_code=status.HTTP_409_CONFLICT, message=str(exc)) from exc
    return response_code.resp_200(_with_exec_level(session_service.get_session(session_id, ctx.uid), ctx))


class TransferProjectSchema(BaseModel):
    # None = 取消项目归属；已有会话允许换项目或解除
    project_id: Optional[str] = Field(default=None, min_length=1)


@router.patch("/{session_id}/project", summary="设置或取消会话的项目归属")
async def transfer_to_project(session_id: str, input: TransferProjectSchema,
                              ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    if input.project_id:
        if not ctx.can_projects:
            raise api_error("project.disabled")
        try:
            project_service.require_usable(ctx.uid, input.project_id)
        except ProjectError as exc:
            raise api_error(exc.code, **exc.vars) from exc

    result = session_service.set_session_project(session_id, input.project_id, ctx.uid)
    if result == "not_found":
        raise api_error("session.not_found")
    if result == "busy":
        raise api_error("session.project_transfer_busy")

    # 项目变了：旧上下文批准过的路径不能带到新项目/无项目会话
    if result == "ok":
        approvals.clear_session(ctx.uid, session_id)

    return response_code.resp_200(_with_exec_level(
        session_service.get_session(session_id, ctx.uid), ctx))


@router.get("/usage", summary="当前账号近 N 天 token 用量")
async def usage(
    days: int = Query(7, ge=1, le=30),
    ctx: UserCtx = Depends(resolve_user_ctx),
) -> Any:
    return response_code.resp_200(session_service.usage_daily(days=days, user_id=ctx.uid))


@router.get("/stats", summary="当前账号的会话数与累计 token 用量")
async def account_stats(ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    return response_code.resp_200(session_service.account_stats(user_id=ctx.uid))


@router.get("/{session_id}", summary="读取会话信息")
async def get_session(session_id: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    row = session_service.get_session(session_id, ctx.uid)
    if row is None:
        raise api_error("session.not_found")
    return response_code.resp_200(_with_exec_level(row, ctx))


@router.patch("/{session_id}/level", summary="调整当前会话的执行档位")
async def set_session_level(
    session_id: str,
    input: SessionLevelSchema,
    ctx: UserCtx = Depends(resolve_user_ctx),
) -> Any:
    """只改这个会话的覆盖，不动账号默认档位，也不写配置文件。

    覆盖仍会在 `resolve_level` 里被身份天花板与服务封顶收敛；返回值给出实际生效值。
    """
    if session_service.get_session(session_id, ctx.uid) is None:
        raise api_error("session.not_found")

    if input.level is None:
        requested = None
    else:
        raw = input.level.strip().upper()
        if raw not in _LEVELS:
            raise api_error("session.invalid_level")
        requested = parse_level(raw)

    set_session_override(ctx.uid, session_id, requested)
    effective = resolve_level(config.EXEC_LEVEL, ctx, session_id)
    return response_code.resp_200({
        "level": _as_level(effective),
        "requested": input.level.strip().upper() if input.level else None,
        "ceiling": _as_level(effective_ceiling(ctx, config.EXEC_LEVEL)),
        "default_level": _as_level(ctx.default_level),
    })


@router.get("/{session_id}/messages", summary="分页读取会话消息")
async def get_messages(
    session_id: str,
    limit: int = Query(30, ge=1, le=100),
    before_id: Optional[int] = Query(None, ge=1),
    ctx: UserCtx = Depends(resolve_user_ctx),
) -> Any:
    if session_service.get_session(session_id, ctx.uid) is None:
        raise api_error("session.not_found")

    rows = session_service.get_messages(session_id, limit, ctx.uid, before_id)
    # `messages.attachments` is internal model/runtime metadata and may contain the server's
    # absolute path for TUI T2+ references. Never serialize it to the browser.
    for row in rows:
        attachments = from_json(row.get("attachments") or "")
        row["attachments"] = [{
            "id": os.path.basename(a.model_path.removeprefix("/attachments/"))
                  if a.copied and a.model_path.startswith("/attachments/") else None,
            "name": a.name,
            "mime": a.mime,
            "size": a.size,
            "kind": a.kind,
            "copied": a.copied,
        } for a in attachments]

        # 系统行（compact）、每轮统计（assistant.meta.turn）与工具卡片（tool.meta）
        # 的结构化数据可以给前端，但归档路径是服务器绝对路径，不能出站。
        # 其它 role 的 meta 不对外承诺，一律清空。
        raw_meta = row.pop("meta", "") or ""
        meta: dict = {}
        if row.get("role") in ("compact", "user", "assistant", "tool") and raw_meta:
            try:
                parsed = json.loads(raw_meta)
                meta = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                meta = {}
        if row.get("role") == "compact":
            meta.pop("file", None)
        elif row.get("role") in ("user", "assistant"):
            # 只放行已知字段：改由服务端决定哪些落库元数据算"可出站"。
            # user 行也要放行 turn——整轮没有 assistant 正文时用量挂在那里。
            turn = meta.get("turn")
            meta = {"turn": turn} if isinstance(turn, dict) else {}
        elif row.get("role") == "tool":
            # 卡片展开区要工具名、参数与结果文本；只放行这三个已知字符串字段
            meta = {
                key: meta[key]
                for key in ("tool", "args", "output")
                if isinstance(meta.get(key), str)
            }
        else:
            meta = {}
        row["meta"] = meta
    next_before_id = rows[0]["id"] if rows else None
    has_more = bool(next_before_id and session_service.count_messages(
        session_id, ctx.uid, before_id=next_before_id
    ))
    return response_code.resp_200({
        "messages": rows,
        "limit": limit,
        "has_more": has_more,
        "next_before_id": next_before_id if has_more else None,
    })


@router.patch("/{session_id}", summary="重命名会话")
async def rename_session(
    session_id: str,
    input: RenameSessionSchema,
    ctx: UserCtx = Depends(resolve_user_ctx),
) -> Any:
    if not session_service.rename(session_id, input.title, ctx.uid):
        raise api_error("session.deleting")
    return response_code.resp_200(_with_exec_level(session_service.get_session(session_id, ctx.uid), ctx))


@router.delete("/{session_id}", summary="安全删除会话")
async def delete_session(session_id: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    # delete_wait 先立删除 tombstone 阻止新 turn，再等待已有请求释放 DB 租约，
    # 最后才删除 messages/checkpoint/附件目录，防止在途请求在清理后把数据写回来。
    deleted = await session_service.delete_wait(session_id, ctx.uid)
    if not deleted:
        raise api_error("session.not_found")
    return response_code.resp_200({"session_id": session_id, "deleted": True})


@router.post("/{session_id}/attachments", summary="上传会话附件")
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    ctx: UserCtx = Depends(resolve_user_ctx),
) -> Any:
    lease_id = session_service.acquire_lease(session_id, ctx.uid)
    if lease_id is None:
        raise api_error("session.deleting")
    try:
        privileged = ctx.is_admin or (ctx.is_default and not config.MULTI_USER)
        limit_bytes = max_file_bytes(privileged)
        # 普通用户最多读上限+1，不能先 file.read() 无界吃满进程内存；
        # 管理员按配置不受限，直接读完（管理员上传属于信任操作）。
        data = await file.read(limit_bytes + 1 if limit_bytes is not None else -1)
        if limit_bytes is not None and len(data) > limit_bytes:
            raise api_error("attachment.upload_limit", mb=limit_bytes // (1024 * 1024))
        try:
            attachment = stage_bytes(
                file.filename or "upload", data, thread_id=session_id,
                declared_mime=file.content_type, is_admin=privileged,
            )
        except AttachmentError as exc:
            code_status = (status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                           if exc.code == "attachment.too_large"
                           else status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
            raise api_error(exc.code or "attachment.invalid", status_code=code_status,
                            message=str(exc), **exc.vars) from exc
        file_id = attachment.model_path.removeprefix("/attachments/")
        return response_code.resp_200({
            "id": file_id,
            "name": attachment.name,
            "mime": attachment.mime,
            "size": attachment.size,
            "kind": attachment.kind,
        })
    finally:
        await file.close()
        session_service.release_lease(lease_id)


@router.delete("/{session_id}/attachments/{file_id}", summary="删除未发送的会话附件")
async def delete_attachment(session_id: str, file_id: str, ctx: UserCtx = Depends(resolve_user_ctx)) -> Any:
    lease_id = session_service.acquire_lease(session_id, ctx.uid)
    if lease_id is None:
        raise api_error("session.deleting")
    try:
        # 文件 ID 只能是目录内的 basename；realpath 再校验，避免 ../ 与软链接越界。
        if os.path.basename(file_id) != file_id or file_id in (".", ".."):
            raise api_error("attachment.missing")
        root = os.path.realpath(persistence.attachment_dir(session_id))
        target = os.path.realpath(os.path.join(root, file_id))
        if not target.startswith(root + os.sep) or not os.path.isfile(target):
            raise api_error("attachment.missing")
        if session_service.attachment_referenced(session_id, ctx.uid, file_id):
            raise api_error("attachment.referenced")
        os.remove(target)
        return response_code.resp_200({"deleted": True, "id": file_id})
    finally:
        session_service.release_lease(lease_id)
