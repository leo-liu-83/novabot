# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : attachment_tool.py
# @Software    : VSCode
# @Datetime    : 2026/11/03 11:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : read_attachment：附件收起后按路径重读（文本/文档内联，图片进本轮待注入队列）

"""为什么图片不返回 Command：把图片写进 messages 就会被 checkpoint 序列化，A 方案
的目标正是"state 只留文本"。图片块进入与中间件共享的待注入队列，下一次模型调用
由中间件追加成临时 user 消息。这样既避开"tool 消息带图片块"的 422 风险，也不落盘。

路径白名单与 `convert_document` 同口径：`/attachments/...` 与工作区虚拟路径。
本机绝对路径不放开——本工具没有逐次确认，放开就等于绕过执行档位。
"""

import base64
import mimetypes
import os

from langchain.tools import tool

from core.agent import persistence
from core.agent.deep_agents.document_tool import ConvertError, convert_document_text
from service.attachment.attachments import (
    KIND_AUDIO,
    KIND_IMAGE,
    KIND_VIDEO,
    PendingImages,
    TEXT_INLINE_MAX_CHARS,
    TIER_CONVERTIBLE,
    TIER_TEXT,
    classify,
)
from service.attachment.image_compress import compress_image
from service.user.user_service import UserCtx

_SOURCE_PREFIX = "[read_attachment]"
_ATTACHMENTS_PREFIX = "/attachments/"


class _ReadError(ValueError):
    """消息直接回给模型。"""


def _resolve(raw: str, *, workspace_path: str, thread_id: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise _ReadError("路径不能为空")

    if text.startswith(_ATTACHMENTS_PREFIX):
        root = os.path.realpath(persistence.attachment_dir(thread_id))
        candidate = os.path.join(root, text[len(_ATTACHMENTS_PREFIX):])
    elif text.startswith("/") and not text.startswith("//"):
        root = os.path.realpath(workspace_path)
        candidate = os.path.join(root, text.lstrip("/"))
    else:
        raise _ReadError(
            "只接受 /attachments/... 或工作区内的虚拟路径；"
            "本机绝对路径不放开（避免绕过执行档位）"
        )

    path = os.path.realpath(os.path.normpath(candidate))
    if not (path == root or path.startswith(root + os.sep)):
        raise _ReadError("路径越界：只允许会话附件目录或工作区内的文件")
    if not os.path.isfile(path):
        # 虚拟根写法与本机绝对路径在字符串上无法区分（都以 / 开头）。
        # 本机确实存在的绝对路径明确拒绝，不静默映射到工作区里一个同名位置。
        if text.startswith("/") and os.path.exists(text):
            raise _ReadError(
                "只接受 /attachments/... 或工作区内的虚拟路径；"
                "本机绝对路径不放开（避免绕过执行档位）"
            )
        raise _ReadError(f"文件不存在：{text}")

    return path


def _inline_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            data = handle.read(TEXT_INLINE_MAX_CHARS + 1)
    except OSError as exc:
        raise _ReadError(f"读取失败：{exc}") from exc

    if len(data) > TEXT_INLINE_MAX_CHARS:
        return data[:TEXT_INLINE_MAX_CHARS] + f"\n...[已截断，完整文件见 {path}]"
    return data


def _image_block(path: str, mime: str, thread_id: str) -> tuple[dict, str]:
    """返回 (image_url block, 给 ToolMessage 的一句话)。图片只进本轮待注入队列。"""
    try:
        with open(path, "rb") as handle:
            payload = handle.read()
    except OSError as exc:
        raise _ReadError(f"图片读取失败：{exc}") from exc

    cache_dir = os.path.join(persistence.artifact_dir(thread_id), "media_cache")
    payload, mime = compress_image(payload, mime, cache_dir=cache_dir)
    encoded = base64.b64encode(payload).decode("ascii")
    model_path = _virtual_path(path, thread_id)

    block = {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{encoded}"},
    }
    return block, f"{_SOURCE_PREFIX} 图片将在下一次模型调用中提供；路径：{model_path}"


def _virtual_path(path: str, thread_id: str) -> str:
    root = os.path.realpath(persistence.attachment_dir(thread_id))
    real = os.path.realpath(path)
    if real.startswith(root + os.sep):
        return _ATTACHMENTS_PREFIX + os.path.relpath(real, root)
    return "/" + os.path.basename(real)


def build_read_attachment_tool(
    ctx: UserCtx, thread_id: str, pending_images: PendingImages
):
    """构建 read_attachment 工具：附件收起后，凭引用里的路径重新读取内容。"""

    @tool
    def read_attachment(path: str) -> str:
        """Read an attachment again after its content was collapsed out of the conversation.

        Use the path printed in the collapsed attachment reference. Accepts
        /attachments/... (this session's attachments) or a workspace path.
        Host absolute paths outside the workspace are refused: this tool has no
        per-call confirmation and must not become a way around the exec level.
        Images are queued for injection on the next model call; text and
        documents come back inline.
        """
        try:
            resolved = _resolve(
                path, workspace_path=ctx.workspace_path, thread_id=thread_id
            )
            kind, tier = classify(resolved)
            if kind == KIND_IMAGE:
                block, notice = _image_block(
                    resolved, _mime_of(resolved), thread_id
                )
                pending_images.add(block, key=_virtual_path(resolved, thread_id))
                return notice
            if kind in (KIND_VIDEO, KIND_AUDIO):
                return f"{_SOURCE_PREFIX} 音视频无法内联为模型输入；文件仍在 {path}"
            if tier == TIER_CONVERTIBLE:
                return convert_document_text(
                    path, workspace_path=ctx.workspace_path, thread_id=thread_id,
                    allow_absolute=False,
                )
            if tier == TIER_TEXT:
                return _inline_text(resolved)
            return f"{_SOURCE_PREFIX} 该类型无法读取内容：{path}"
        except (_ReadError, ConvertError) as err:
            return f"{_SOURCE_PREFIX} {err}"

    return read_attachment


def _mime_of(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
