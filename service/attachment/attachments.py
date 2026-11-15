# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : attachments.py
# @Software    : VSCode
# @Datetime    : 2026/10/31 17:10:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 附件（E10.4）：路径识别、可读性判定、落盘与消息标记

"""把用户给的本地文件变成 agent 能读的东西。

三条不可混用的姿态，全在这一个模块里定清：

- **工作区内**：文件本来就在 agent 的虚拟根里 → 直接引用虚拟路径，不需要同意
- **工作区外且 T3+**：`execute` 能以 OS 身份读到（T3 每条读取都要确认）→ 引用**绝对物理路径**，不复制
- **工作区外且 T0–T2**：文件工具读不到、T2 的只读命令也收敛到工作区 → **复制到会话附件目录**，需要用户同意

为什么按"能不能读到"分而不是"一律复制"：复制一份等于把原文件的完整副本落到会话目录里，
用户在 T3+ 下本来就能让 agent 直接读原文件，复制只是白占盘、还制造两份真相。
T2 的"有 shell"不等于"能读工作区外"——路径参数受工作区约束（决策 27），所以必须复制。

界面上附件是 `[File 1]` / `[Image 1]` / `[Video 1]` / `[Audio 1]` 这样的 token：
DB 与输入框里只存 token，发给模型时才展开成"token = 路径 (mime, 大小) + 可读性说明"。
这样重放、上下文计数、界面展示都不会被一长串 base64/路径污染。
"""

import base64
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.parse import unquote, urlparse

from config import config
from core.agent import persistence
from logger import logger
from service.attachment.image_compress import compress_image

# 单条消息的附件数上限：再多基本是"把整个目录塞进上下文"
MAX_PER_MESSAGE = 5

# 内联进消息的上限：太长的文本/文档会撑爆上下文，截断并明确告知
TEXT_INLINE_MAX_CHARS = 50_000
DOC_INLINE_MAX_CHARS = 20_000          # 文本/源码/配置/日志
# 文档转换的兜底上限；附件上传/发送的实际限制由 config.attachments 决定
BINARY_MAX_BYTES = 20 * 1024 * 1024

TIER_TEXT = "text"            # read_file 直接可读
TIER_CONVERTIBLE = "convertible"  # 二进制容器：要 convert_document 转 Markdown
TIER_MEDIA = "media"          # 图片/音视频：当前模型看不见内容，只给路径
TIER_DENIED = "denied"        # 内容不可见，不收


def max_per_message(is_admin: bool = False) -> Optional[int]:
    """单条消息附件数上限；管理员按配置可不受限（None）。"""
    if is_admin and config.ATTACHMENT_ADMIN_UNLIMITED:
        return None
    return config.ATTACHMENT_MAX_PER_MESSAGE or MAX_PER_MESSAGE


def max_file_bytes(is_admin: bool = False) -> Optional[int]:
    """单文件大小上限；管理员按配置可不受限（None）。"""
    if is_admin and config.ATTACHMENT_ADMIN_UNLIMITED:
        return None
    return config.ATTACHMENT_MAX_FILE_BYTES or BINARY_MAX_BYTES


KIND_FILE = "file"
KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"

# 附件内容与用户原文的分界：收起旧附件时按它切开，只保留用户原话。
# 只由 build_model_message() 写入，读的时候按第一次出现切分。
ATTACH_END = "[/附件]"


class PendingImages:
    """本轮要随请求注入、但不进入 state 的图片块。

    为什么单独一个容器：图片只在"这次模型调用"里存在，state 里只留文本引用。
    中间件与 `read_attachment` 共享同一个实例，422 重试时服务层清空它，
    两侧下一次调用都看不到图片。`key` 用于同一轮内重复读取去重。
    """

    def __init__(self, blocks: list[dict] | None = None) -> None:
        self._blocks = list(blocks or [])
        self._keys: set[str] = set()

    def add(self, block: dict, *, key: str) -> bool:
        if key in self._keys:
            return False
        self._keys.add(key)
        self._blocks.append(block)
        return True

    def extend(self, blocks: list[dict]) -> None:
        self._blocks.extend(blocks)

    def clear(self) -> None:
        self._blocks.clear()
        self._keys.clear()

    def __iter__(self):
        return iter(self._blocks)

    def __len__(self) -> int:
        return len(self._blocks)

    def __bool__(self) -> bool:
        return bool(self._blocks)

_KIND_LABELS = {KIND_FILE: "File", KIND_IMAGE: "Image", KIND_VIDEO: "Video", KIND_AUDIO: "Audio"}

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".ico", ".heic"}
_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv", ".flv", ".m4v", ".mpeg", ".mpg"}
_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff"}

# anydoc 覆盖的文档容器：内容由专用解析器才拿得到（TODO E10.4b）
_CONVERTIBLE_EXT = {
    ".pdf", ".doc", ".docx", ".docm", ".odt", ".rtf", ".epub",
    ".ppt", ".pps", ".pot", ".pptx", ".pptm", ".ppsx", ".ppsm", ".odp",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods",
}

# 内容看不见或不是"给模型读"的东西：与技能扫描同一姿态（E8.18）
_DENIED_EXT = {
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".exe", ".dll", ".so", ".dylib", ".msi", ".dmg", ".app",
    ".pyc", ".pyo", ".class", ".o", ".a", ".lib", ".obj", ".wasm",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".db", ".sqlite", ".sqlite3", ".bin", ".iso", ".img",
}

# 只认绝对路径与 file:// —— 相对路径的"当前目录"语义不该由界面猜，
# 而 `src/main.py` 这类串在正常技术讨论里高频出现，认相对路径必然误判。
# 允许空格：终端粘贴的带空格路径总是裹着引号，存在性校验才是最终判据
_ABS_PATH_RE = re.compile(r"^(?:~[/\\]?.*|/.*|[A-Za-z]:[\\/].*)$")


class AttachmentError(ValueError):
    """拒绝理由，消息直接给用户看（不是给模型的）。

    code/vars 供 Web 按语言翻译固定提示；原始异常（如 OSError）不带 code，保持原文。
    """

    def __init__(self, message: str, code: str = "", **vars):
        super().__init__(message)
        self.code = code
        self.vars = vars


@dataclass(frozen=True)
class Attachment:
    label: str
    kind: str
    name: str            # 展示用的原名（复制后会重命名，原名不能丢）
    model_path: str      # 发给模型的路径：虚拟路径或绝对物理路径
    source_path: str
    mime: str
    size: int
    tier: str
    copied: bool
    mode: str            # workspace | shell | copied —— 决定展开文本里的可读性说明

    def as_dict(self) -> dict:
        return asdict(self)


def doc_convert_available() -> bool:
    """文档转换能力是否装了（E10.4b）。探测而不是导入：缺依赖不该影响附件本身。"""
    return importlib.util.find_spec("anydoc") is not None


def classify(path: str) -> tuple[str, str]:
    """返回 (kind, tier)。认不出的一律按 file/text 处理——宁可让模型试读，不要瞎猜成媒体。"""
    ext = os.path.splitext(path)[1].lower()

    if ext in _DENIED_EXT:
        return KIND_FILE, TIER_DENIED
    if ext in _IMAGE_EXT:
        return KIND_IMAGE, TIER_MEDIA
    if ext in _VIDEO_EXT:
        return KIND_VIDEO, TIER_MEDIA
    if ext in _AUDIO_EXT:
        return KIND_AUDIO, TIER_MEDIA
    if ext in _CONVERTIBLE_EXT:
        return KIND_FILE, TIER_CONVERTIBLE

    return KIND_FILE, TIER_TEXT


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, "File")


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}MB"
    if size >= 1024:
        return f"{size / 1024:.0f}KB"
    return f"{size}B"


def _resolve_source(raw: str) -> str:
    """把用户给的写法变成真实绝对路径；认不出来或读不到就抛 AttachmentError。"""
    text = (raw or "").strip().strip('"').strip("'").rstrip(",;。)")

    if text.startswith("file://"):
        parsed = urlparse(text)
        text = unquote(parsed.path)
        if parsed.netloc and parsed.netloc not in ("", "localhost"):
            raise AttachmentError(f"不支持远程 file:// 主机：{text}")

    path = os.path.realpath(os.path.expanduser(text))
    if not path or path in (os.sep, os.path.expanduser("~")):
        raise AttachmentError("路径太宽泛，请给具体文件")
    if not os.path.exists(path):
        raise AttachmentError(f"文件不存在：{raw}")
    if not os.path.isfile(path):
        raise AttachmentError(f"只支持文件，不支持目录：{path}")
    if not os.access(path, os.R_OK):
        raise AttachmentError(f"当前用户读不到该文件：{path}")

    return path


def _inside_workspace(path: str, workspace_path: str) -> bool:
    root = os.path.realpath(workspace_path)
    return path == root or path.startswith(root + os.sep)


def decide(raw_path: str, *, workspace_path: str, shell_enabled: bool,
           is_admin: bool = False) -> dict:
    """判定这个路径该怎么给 agent，不做任何复制。

    返回 `{"path","kind","tier","size","name","mime","mode","model_path"}`，`mode` 是：
    - `workspace`：已在工作区虚拟根内，直接引用虚拟路径
    - `shell`：工作区外但 T3+（每次读取都经确认），引用绝对物理路径（靠 execute 读）
    - `copy`：工作区外且 T0–T2，必须复制进会话附件目录（需用户同意）
    """
    path = _resolve_source(raw_path)
    kind, tier = classify(path)
    size = os.path.getsize(path)

    if tier == TIER_DENIED:
        raise AttachmentError(
            f"不支持的类型：{os.path.basename(path)}（{os.path.splitext(path)[1] or '未知扩展名'}）。"
            "压缩包/可执行文件/字节码这类内容看不见，收进来也只是骗自己——请解包后贴具体文件。"
        )

    limit = max_file_bytes(is_admin)
    if limit is not None and size > limit:
        raise AttachmentError(f"文件过大：{human_size(size)}，上限 {human_size(limit)}")

    root = os.path.realpath(workspace_path)
    if _inside_workspace(path, workspace_path):
        mode = "workspace"
        # 工作区内给虚拟根路径：物理前缀属于本机布局，既不该抄进模型上下文，
        # 文件工具也只认虚拟根写法
        model_path = "/" + os.path.relpath(path, root).replace(os.sep, "/")
    elif shell_enabled:
        mode = "shell"
        model_path = path
    else:
        mode = "copy"
        model_path = ""

    # 音视频不复制：一份几十 MB 的录像塞进会话目录既救不了"模型看不见"的问题，
    # 也只是在磁盘上多养一份真相
    if mode == "copy" and kind in (KIND_VIDEO, KIND_AUDIO):
        raise AttachmentError(
            f"{kind_label(kind).title()} 不复制进会话目录。请把文件移进工作区，"
            "或把执行档位提到 T3+ 后直接引用绝对路径"
        )

    return {
        "path": path,
        "name": os.path.basename(path),
        "kind": kind,
        "tier": tier,
        "size": size,
        "mime": mimetypes.guess_type(path)[0] or "application/octet-stream",
        "mode": mode,
        "model_path": model_path,
    }


def _stored_name(source_path: str) -> str:
    """原名 + 内容短哈希：同一会话反复贴同名文件不互相覆盖，也不丢原名的可读性。"""
    stem = os.path.basename(source_path)
    root, ext = os.path.splitext(stem)
    safe_root = re.sub(r"[^\w.\-]+", "_", root)[:60] or "file"
    digest = hashlib.sha1(source_path.encode("utf-8")).hexdigest()[:6]
    return f"{safe_root}-{digest}{ext.lower()}"


def stage(plans: list[dict], *, thread_id: str, copy_allowed: bool,
          is_admin: bool = False) -> list[Attachment]:
    """按判定结果生成附件。需要复制的条目在 `copy_allowed=False` 时整体拒绝。

    `copy_allowed` 由界面的三态同意决定：拒绝→不调用本函数；本次同意/本会话同意→True。
    """
    limit_count = max_per_message(is_admin)
    if limit_count is not None and len(plans) > limit_count:
        raise AttachmentError(f"一条消息最多 {limit_count} 个附件", code="attachment.too_many", limit=limit_count)

    limit_bytes = max_file_bytes(is_admin)
    if limit_bytes is not None:
        for plan in plans:
            if plan["size"] > limit_bytes:
                raise AttachmentError(
                    f"文件过大：{human_size(plan['size'])}，上限 {human_size(limit_bytes)}",
                    code="attachment.too_large", size=human_size(plan["size"]),
                    limit=human_size(limit_bytes),
                )

    if not copy_allowed and any(p["mode"] == "copy" for p in plans):
        raise AttachmentError("未同意复制，附件未加入")

    counters: dict[str, int] = {}
    staged: list[Attachment] = []

    for plan in plans:
        kind = plan["kind"]
        counters[kind] = counters.get(kind, 0) + 1
        # 表现层可能已经给附件定了 token（删除其他附件后编号会保留空洞）；
        # 优先用它，保证输入框里的 [Image 2] 与 Attachment.label 一致。
        label = plan.get("label") or f"[{kind_label(kind)} {counters[kind]}]"

        if plan["mode"] == "copy":
            attachment_dir = persistence.attachment_dir(thread_id)
            target = os.path.join(attachment_dir, _stored_name(plan["path"]))
            _copy_into(plan["path"], target, attachment_dir)
            model_path = f"/attachments/{os.path.basename(target)}"
            size = os.path.getsize(target)
        else:
            model_path = plan["model_path"]
            size = plan["size"]

        staged.append(Attachment(
            label=label,
            kind=kind,
            name=plan["name"],
            model_path=model_path,
            source_path=plan["path"],
            mime=plan["mime"],
            size=size,
            tier=plan["tier"],
            copied=plan["mode"] == "copy",
            mode=plan["mode"],
        ))

    return staged


def stage_bytes(name: str, data: bytes, *, thread_id: str,
                declared_mime: Optional[str]=None, is_admin: bool=False) -> Attachment:
    """把 Web 上传的字节安全落到该会话 `/attachments/`，返回服务端生成的路径元数据。

    HTTP 客户端只拿到随机 `id`/name/mime/size，不能提交物理路径；chat 请求再用 id 在
    此会话附件目录里解析。上传本身就是用户明确的引入动作，因此不再弹 TUI 的复制同意框。
    """
    original = os.path.basename((name or "upload").replace("\\", "/")).strip() or "upload"
    kind, tier = classify(original)
    if tier == TIER_DENIED:
        raise AttachmentError("不支持该附件类型：压缩包、可执行文件、字节码、字体与数据库文件不接收", code="attachment.type_denied")
    if not data:
        raise AttachmentError("空文件不能作为附件", code="attachment.empty")

    limit = max_file_bytes(is_admin)
    if limit is not None and len(data) > limit:
        raise AttachmentError(f"文件过大：{human_size(len(data))}，上限 {human_size(limit)}", code="attachment.too_large", size=human_size(len(data)), limit=human_size(limit))

    mime = mimetypes.guess_type(original)[0] or "application/octet-stream"
    declared = (declared_mime or "").split(";", 1)[0].strip().lower()
    # MIME 仅作第二道一致性校验，不作为内容判型依据；通用 octet-stream 允许，
    # 真正的类型仍由扩展名 + 文件签名/文本解码共同确定。
    if declared and declared not in (mime.lower(), "application/octet-stream", "application/zip"):
        raise AttachmentError(f"上传 MIME 与文件扩展名不匹配：{declared} / {mime}", code="attachment.mime_mismatch", declared=declared, expected=mime)
    _validate_uploaded_bytes(original, data, tier, kind)

    attachment_root = persistence.attachment_dir(thread_id)
    stem, ext = os.path.splitext(original)
    safe_stem = re.sub(r"[^\w.\-]+", "_", stem)[:60] or "upload"
    digest = hashlib.sha256(data).hexdigest()[:10]
    # 内容hash防止不同文件覆盖；随机后缀防同内容重复上传时覆盖仍在历史里引用的附件
    stored_name = f"{safe_stem}-{digest}-{secrets.token_hex(3)}{ext.lower()}"
    os.makedirs(attachment_root, exist_ok=True)
    target = os.path.join(attachment_root, stored_name)
    try:
        with open(target, "xb") as handle:
            handle.write(data)
    except OSError as exc:
        raise AttachmentError(f"附件保存失败：{exc}") from exc

    label = f"[{kind_label(kind)} 1]"
    return Attachment(
        label=label, kind=kind, name=original, model_path=f"/attachments/{stored_name}",
        source_path=target, mime=mime, size=len(data), tier=tier, copied=True, mode="copy",
    )


def resolve_uploaded_ids(ids: list, *, thread_id: str,
                         is_admin: bool = False) -> list[Attachment]:
    """把 Web 上传返回的 opaque 文件 id 解析成模型附件；路径只能落在当前会话目录。"""
    limit_count = max_per_message(is_admin)
    if limit_count is not None and len(ids) > limit_count:
        raise AttachmentError(f"一条消息最多 {limit_count} 个附件", code="attachment.too_many", limit=limit_count)
    limit_bytes = max_file_bytes(is_admin)
    root = os.path.realpath(persistence.attachment_dir(thread_id))
    counters: dict[str, int] = {}
    resolved: list[Attachment] = []
    for raw_item in ids:
        if isinstance(raw_item, dict):
            raw_id = raw_item.get("id")
            display_name = str(raw_item.get("name") or "")[:180]
        else:
            raw_id = raw_item
            display_name = ""
        file_id = os.path.basename(str(raw_id or ""))
        if not file_id or file_id != raw_id or file_id in (".", ".."):
            raise AttachmentError("附件 ID 无效", code="attachment.id_invalid")
        path = os.path.realpath(os.path.join(root, file_id))
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            raise AttachmentError("附件不存在或不属于当前会话", code="attachment.not_found")
        size = os.path.getsize(path)
        if limit_bytes is not None and size > limit_bytes:
            raise AttachmentError(
                f"文件过大：{human_size(size)}，上限 {human_size(limit_bytes)}",
                code="attachment.too_large", size=human_size(size),
                limit=human_size(limit_bytes),
            )
        kind, tier = classify(path)
        if tier == TIER_DENIED:
            raise AttachmentError("该附件类型不允许发送", code="attachment.type_denied")
        counters[kind] = counters.get(kind, 0) + 1
        resolved.append(Attachment(
            label=f"[{kind_label(kind)} {counters[kind]}]",
            kind=kind,
            name=display_name or _original_name_from_stored(path),
            model_path=f"/attachments/{file_id}",
            source_path=path,
            mime=mimetypes.guess_type(path)[0] or "application/octet-stream",
            size=size,
            tier=tier,
            copied=True,
            mode="copy",
        ))
    return resolved


def _original_name_from_stored(path: str) -> str:
    """Uploaded filenames are stored with hash suffix; API also persists display name separately.

    This fallback is only used if a client submits a valid stored id but omitted display metadata.
    The ordinary Web route passes original names on the ChatInput schema.
    """
    return os.path.basename(path)


def _validate_uploaded_bytes(name: str, data: bytes, tier: str, kind: str) -> None:
    """扩展名与内容签名双检。拒绝伪装成文本的二进制/脚本容器。"""
    ext = os.path.splitext(name)[1].lower()
    signatures = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
        ".gif": data.startswith((b"GIF87a", b"GIF89a")),
        ".webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
        ".pdf": data.startswith(b"%PDF-"),
        ".docx": data.startswith(b"PK\x03\x04"),
        ".docm": data.startswith(b"PK\x03\x04"),
        ".xlsx": data.startswith(b"PK\x03\x04"),
        ".xlsm": data.startswith(b"PK\x03\x04"),
        ".pptx": data.startswith(b"PK\x03\x04"),
        ".pptm": data.startswith(b"PK\x03\x04"),
        ".odt": data.startswith(b"PK\x03\x04"),
        ".ods": data.startswith(b"PK\x03\x04"),
        ".odp": data.startswith(b"PK\x03\x04"),
        ".epub": data.startswith(b"PK\x03\x04"),
        ".doc": data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ".xls": data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ".ppt": data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ".rtf": data.lstrip().startswith(b"{\\rtf"),
    }
    if ext in signatures and not signatures[ext]:
        raise AttachmentError(f"文件内容与扩展名不匹配：{name}", code="attachment.signature_mismatch", name=name)
    if tier == TIER_TEXT:
        if b"\x00" in data:
            raise AttachmentError(f"文件看起来是二进制内容，不作为文本附件接收：{name}", code="attachment.text_binary", name=name)
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AttachmentError(f"文本附件必须是 UTF-8 编码：{name}", code="attachment.text_encoding", name=name) from exc


def _copy_into(source: str, target: str, attachment_dir: str) -> None:
    os.makedirs(attachment_dir, exist_ok=True)
    try:
        shutil.copyfile(source, target)
    except OSError as exc:
        raise AttachmentError(f"复制失败：{exc}") from exc

    # 复制完再校验一次落盘大小，别让半写成功的文件当成成功
    if os.path.getsize(target) != os.path.getsize(source):
        os.remove(target)
        raise AttachmentError("复制不完整，附件未加入")


def _readability_note(att: Attachment) -> str:
    if att.mode == "shell":
        shell_note = "靠 execute 读取，T3 每条命令都要确认"
    elif att.mode == "copy":
        shell_note = "已复制到会话附件目录"
    else:
        shell_note = "已在工作区内"

    if att.tier == TIER_CONVERTIBLE:
        tool_note = ("二进制文档容器；可用 convert_document 转成 Markdown 后读取"
                     if doc_convert_available() else
                     "二进制文档容器；本机未安装文档转换能力")
    elif att.kind == KIND_IMAGE:
        tool_note = "图片文件；是否支持读取图片内容取决于模型能力"
    elif att.kind in (KIND_VIDEO, KIND_AUDIO):
        tool_note = "音视频文件；是否支持读取取决于模型能力"
    else:
        tool_note = "文本文件，可直接 read_file/grep"

    return f"{shell_note}；{tool_note}"


def expand_for_model(attachments: list[Attachment]) -> str:
    """把 token 展开成给模型的附件清单。没有附件时返回空串。"""
    if not attachments:
        return ""

    lines = ["[附件]"]
    for att in attachments:
        lines.append(
            f"{att.label} = {att.model_path} ({att.mime}, {human_size(att.size)}) — {_readability_note(att)}"
        )
    lines.append("引用附件时用上面的 token；读取请用 model_path 给出的路径。")
    return "\n".join(lines)


def compose_query(text: str, attachments: list[Attachment]) -> str:
    """发给模型的实际内容：附件清单 + 用户原文（原文里保留 token）。"""
    block = expand_for_model(attachments)
    return f"{block}\n\n{text}" if block else text


def build_model_message(
    text: str, attachments: list[Attachment], *, thread_id: str | None = None
) -> tuple[str, str, list[dict]]:
    """把附件硬编码展开成模型输入（不再让模型自己 read_file）。

    返回 (content, text_for_estimate, image_blocks)：
    - 图片：压图后只返回 image_url block，**不拼进 content**；由本轮中间件注入请求，
      state 里只留文本引用（见 2026-11-03-附件磁盘治理-B-A-design.md）
    - PDF/Office：用 anydoc 转 Markdown 后作为文本内联
    - 文本：直接内联（超长截断并说明）
    - 音视频：无法内联，只给路径与说明

    文本末尾在用户原文前插入 `ATTACH_END`：后续轮次靠它把附件内容收成引用。
    """
    if not attachments:
        return text, text, 0

    cache_dir = os.path.join(persistence.artifact_dir(thread_id), "media_cache") if thread_id else None
    lines = ["[附件]"]
    images: list[dict] = []

    for att in attachments:
        lines.append(f"{att.label} = {att.model_path} ({att.mime}, {human_size(att.size)})")
        if att.kind == KIND_IMAGE:
            try:
                with open(att.source_path, "rb") as handle:
                    payload = handle.read()
            except OSError as exc:
                lines.append(f"（图片读取失败：{exc}）")
                continue
            payload, mime = compress_image(payload, att.mime, cache_dir=cache_dir)
            encoded = base64.b64encode(payload).decode("ascii")
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            })
            lines.append("图片内容随本轮请求提供；当前模型不支持图片时只保留路径。")
        elif att.tier == TIER_CONVERTIBLE:
            lines.append("内容（已用 anydoc 转为 Markdown）：\n" + _inline_document(att))
        elif att.tier == TIER_TEXT:
            lines.append("内容：\n" + _inline_text(att))
        else:
            lines.append("此类媒体无法内联为模型输入，仅提供路径")

    lines.append("附件内容已经随本条消息提供；不要用 read_file 读取 /attachments/ 下的文件。")
    text_part = "\n".join(lines) + f"\n{ATTACH_END}\n\n" + text

    return text_part, text_part, images


_COLLAPSED_HEAD = "[附件]（以下内容已收起，不再随消息发送）"
_REHYDRATE_HINT = '需要时用 read_attachment("{path}") 重新读取；不要凭记忆描述或引用已收起的内容。'
REF_LINE_RE = re.compile(r"^(\[(?:File|Image|Video|Audio) \d+\]) = (.+?) \((.*)\)$")


def collapse_text(text: str) -> str:
    """把一条含附件的消息收成"引用 + 用户原文"；没有标记则原样返回。

    标记之前的正文（base64 说明、文档 Markdown、文本内联）全部丢掉，
    只保留每个附件的 label/路径行与一句重读提示。
    """
    if ATTACH_END not in text:
        return text

    head, tail = text.split(ATTACH_END, 1)
    refs = [m for m in (REF_LINE_RE.match(line.strip()) for line in head.splitlines()) if m]
    if not refs:
        return text

    lines = [_COLLAPSED_HEAD]
    for ref in refs:
        lines.append(f"{ref.group(1)} = {ref.group(2)} ({ref.group(3)})")
    lines.append(_REHYDRATE_HINT.format(path=refs[-1].group(2)))
    lines.append(ATTACH_END)
    return "\n".join(lines) + "\n" + tail.lstrip("\n")


def build_reference_message(text: str, attachments: list[Attachment]) -> str:
    """不读盘直接构造收起形状：回灌历史用。

    历史上的轮次一定早于当前轮，逐张读图/转文档再被收起纯属浪费——
    这里只输出 label、路径与重读指引。
    """
    if not attachments:
        return text

    lines = [_COLLAPSED_HEAD]
    lines += [f"{a.label} = {a.model_path} ({a.mime}, {human_size(a.size)})" for a in attachments]
    for att in attachments:
        if att.mode == "shell":
            lines.append(
                f"{att.label} 不在会话附件目录或工作区，无法重读；"
                "需要时请用户重新附图或先复制进工作区。"
            )
        else:
            lines.append(_REHYDRATE_HINT.format(path=att.model_path))
    lines.append(ATTACH_END)
    return "\n".join(lines) + "\n" + text


def _inline_text(att: Attachment) -> str:
    try:
        with open(att.source_path, "r", encoding="utf-8-sig", errors="replace") as handle:
            data = handle.read(TEXT_INLINE_MAX_CHARS + 1)
    except OSError as exc:
        return f"（文本读取失败：{exc}；路径 {att.model_path}）"

    if len(data) > TEXT_INLINE_MAX_CHARS:
        return data[:TEXT_INLINE_MAX_CHARS] + f"\n...[已截断，完整文件见 {att.model_path}]"
    return data


def _inline_document(att: Attachment) -> str:
    try:
        import anydoc
    except ImportError:
        return f"（本机未安装文档转换能力，读不到内容；路径 {att.model_path}）"

    try:
        with open(att.source_path, "rb") as handle:
            markdown = anydoc.to_markdown_bytes(handle.read())
    except Exception as exc:  # noqa: BLE001 - 解析器异常类型各异，统一翻成可读原因
        name = type(exc).__name__
        if name == "NeedsOcrError":
            reason = "扫描件/图片页需要 OCR，本项目不做 OCR"
        elif name == "EncryptedError":
            reason = "文档已加密或有密码保护"
        elif name == "UnsupportedError":
            reason = "文档类型不受支持"
        else:
            reason = str(exc)[:200]
        return f"（无法解析：{reason}；路径 {att.model_path}）"

    markdown = (markdown or "").strip()
    if not markdown:
        return f"（转换成功但没有文本内容；路径 {att.model_path}）"
    if len(markdown) > DOC_INLINE_MAX_CHARS:
        markdown = markdown[:DOC_INLINE_MAX_CHARS] + f"\n...[已截断，完整文件见 {att.model_path}]"
    return markdown


def to_json(attachments: list[Attachment]) -> str:
    return json.dumps([a.as_dict() for a in attachments], ensure_ascii=False) if attachments else ""


def from_json(raw: str) -> list[Attachment]:
    """DB 里的附件列转回对象；坏数据只丢附件不影响消息正文。"""
    if not raw:
        return []
    try:
        rows = json.loads(raw)
        return [Attachment(**row) for row in rows if isinstance(row, dict)]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(f"附件数据无法解析，按无附件处理：{exc}")
        return []


def expand_content(content: str, attachments: list[Attachment]) -> str:
    """重放开档时用：DB 存的是含 token 的原文，回灌给模型前要重新展开一次。"""
    return compose_query(content, attachments)


def find_paths(text: str) -> tuple[list[str], str]:
    """从一段文本里挑出"确实是本地文件"的 token，返回 (路径列表, 去掉路径后的文本)。

    按 shlex 切（能正确处理带空格与引号的路径），逐个独立校验；
    普通句子里的 token 因为不存在于磁盘而自然留下，不会把文字吞掉。
    """
    if not text or not text.strip():
        return [], text

    found: list[str] = []
    kept: list[str] = []

    for line in re.split(r"[\n]+", text):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parts = shlex.split(stripped)
        except ValueError:
            parts = stripped.split()

        line_paths: list[str] = []
        line_kept: list[str] = []
        for part in parts:
            candidate = part.strip().strip('",;。')
            if not _looks_like_path(candidate):
                line_kept.append(part)
                continue
            try:
                _resolve_source(candidate)
            except AttachmentError:
                line_kept.append(part)
                continue
            line_paths.append(candidate)

        found.extend(line_paths)
        if not line_paths:
            # 文件管理器复制的路径常带空格且**不加引号**（`截图 2026-11-01.png`）。
            # shlex 会把它拆成多段、逐段都不存在，于是整条被当普通文本。
            # 这里按"绝对路径开头"重新截取，并从右往左收缩到真实存在的文件为止。
            unquoted = _extract_unquoted_path(line)
            if unquoted is not None:
                path, remainder = unquoted
                found.append(path)
                if remainder:
                    kept.append(remainder)
                continue
        if not line_paths:
            # 这一行没摘出任何路径：原文照留。按 shlex 重拼会吞掉引号，
            # 粘进来的代码片段/带引号的短语会被改写，那是文本粘贴不该有的副作用
            kept.append(line)
        elif line_kept:
            kept.append(" ".join(line_kept))

    return found, "\n".join(kept)


def remove_paths(text: str) -> str:
    """去掉路径 token 后的剩余文本（用于粘贴与提交时回写输入框）。"""
    return find_paths(text)[1].strip()


def _extract_unquoted_path(line: str) -> Optional[tuple[str, str]]:
    """从未加引号的一行里截出"带空格的绝对路径"。

    返回 (路径, 去掉路径后的剩余文本)；找不到返回 None。
    只在 shlex 逐 token 全失败后调用：先按空格从右往左收缩，命中第一个真实存在的文件。
    """
    for match in re.finditer(r"file://|~/|/", line):
        start = match.start()
        if start > 0 and line[start - 1] not in " \t\"'（(：:，,":
            continue

        tail = line[start:].rstrip()
        if not tail:
            continue

        words = re.split(r"\s+", tail)
        for end in range(len(words), 0, -1):
            candidate = " ".join(words[:end]).strip().strip('",;。')
            if not _looks_like_path(candidate):
                continue
            try:
                _resolve_source(candidate)
            except AttachmentError:
                continue

            prefix = line[:start].strip()
            suffix = " ".join(words[end:]).strip()
            remainder = " ".join(part for part in (prefix, suffix) if part).strip()
            return candidate, remainder

    return None


def _looks_like_path(token: str) -> bool:
    """粗筛：只要求它"像绝对路径"，存在性由 _resolve_source 判。

    带空格的 token 不预先排除——终端粘贴的带空格路径总是裹着引号，shlex 拆完就含空格；
    而一句英文里以 `/` 开头的 token 极少，真有也会被 isfile 校验挡掉。
    """
    if not token:
        return False
    if token.startswith("file://"):
        return True
    return bool(_ABS_PATH_RE.match(token))
