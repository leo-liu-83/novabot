# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : image_compress.py
# @Software    : VSCode
# @Datetime    : 2026/11/03 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 附件发送前压图：长边 2000、base64 <= 2 MiB，带内容哈希缓存

"""把图片压到 provider 愿意接收的尺寸与体积，再作为 image block 发送。

为什么在发送前压、而不是上传时改源文件：附件可能是工作区里的原文件
（`mode=workspace/shell`），用户没同意我们改它。压缩结果按内容哈希缓存到
会话目录（`<artifact_dir>/media_cache`），重复展开与按需重读都走缓存。

参数取自 OpenCode 2.2.5（`packages/core/src/image.ts`）：长边 2000、JPEG 质量
80/70/55/40 递减。base64 上限按本项目的磁盘/请求体积目标压到 2 MiB，比 OpenCode 更严。
解不了的格式（HEIC/AVIF）原样交给 provider，
由既有的 422 兜底处理——这里不猜、不报错打断整轮。
"""

import base64
import hashlib
import io
import os

from logger import logger

MAX_EDGE = 2000
MAX_BASE64_BYTES = 2 * 1024 * 1024
QUALITIES = (80, 70, 55, 40)
MIN_EDGE = 512
SHRINK = 0.75

try:  # 极端环境允许缺 Pillow：降级为不压图
    from PIL import Image, ImageOps

    _PIL = Image
    _ImageOps = ImageOps
except ImportError:  # pragma: no cover - 只在未安装时走
    _PIL = None
    _ImageOps = None


def _cache_path(cache_dir: str | None, data: bytes) -> str | None:
    if not cache_dir:
        return None

    key = hashlib.sha256(data).hexdigest()[:16]
    # 文件名必须带上目标参数：只改上限而不改键，旧缓存里的 5 MiB 图会继续命中
    return os.path.join(cache_dir, f"{key}-{MAX_EDGE}-{MAX_BASE64_BYTES}.jpg")


def _flatten(img):
    """带 alpha 的图合成白底：JPEG 没有透明通道，直接转 RGB 会把透明处变黑。"""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = _PIL.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[3])
        return background
    return img if img.mode == "RGB" else img.convert("RGB")


def _resize(img, edge: int):
    width, height = img.size
    if max(width, height) <= edge:
        return img

    copy = img.copy()
    copy.thumbnail((edge, edge), _PIL.LANCZOS)
    return copy


def _encode(data: bytes, mime: str) -> tuple[bytes, str]:
    """解码、摆正方向、缩放并编码。任何异常都退化为原样返回。"""
    try:
        img = _PIL.open(io.BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001 - 解析器异常类型各异，统一视为"压不了"
        return data, mime

    try:
        img = _ImageOps.exif_transpose(img)
    except Exception:  # noqa: BLE001 - 坏 EXIF 不该让整张图发不出去
        pass

    if max(img.size) <= MAX_EDGE and len(base64.b64encode(data)) <= MAX_BASE64_BYTES:
        return data, mime

    img = _flatten(img)
    edge = min(max(img.size), MAX_EDGE)

    while edge >= MIN_EDGE:
        candidate = _resize(img, edge)
        for quality in QUALITIES:
            buf = io.BytesIO()
            candidate.save(buf, format="JPEG", quality=quality, optimize=True)
            payload = buf.getvalue()
            if len(base64.b64encode(payload)) <= MAX_BASE64_BYTES:
                # 压完反而更大就保留原图：目标是省字节，不是"必须转 JPEG"
                return (data, mime) if len(payload) >= len(data) else (payload, "image/jpeg")
        edge = int(edge * SHRINK)

    return data, mime


def compress_image(data: bytes, mime: str, *, cache_dir: str | None = None) -> tuple[bytes, str]:
    """返回 (压缩后字节, mime)；压不了、用不上或没有 Pillow 时原样返回。"""
    if _PIL is None or not data:
        return data, mime

    cached = _cache_path(cache_dir, data)
    if cached and os.path.isfile(cached):
        try:
            with open(cached, "rb") as handle:
                return handle.read(), "image/jpeg"
        except OSError as exc:
            logger.warning(f"压图缓存读取失败，重新编码：{exc}")

    payload, out_mime = _encode(data, mime)
    if cached and payload is not data:
        try:
            os.makedirs(os.path.dirname(cached), exist_ok=True)
            with open(cached, "wb") as handle:
                handle.write(payload)
        except OSError as exc:
            # 缓存失败只影响下次解码，不影响这次发送
            logger.warning(f"压图缓存写入失败：{exc}")

    return payload, out_mime
