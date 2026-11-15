# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Description : 人格页只读数据：SOUL.md 与长期记忆文件。

import os
import re
from typing import Optional

from service.user.user_service import (
    MEMORY_CATEGORIES,
    MEMORY_CORE_FILE,
    UserCtx,
)

MAX_TEXT_BYTES = 256 * 1024
TRUNCATION_NOTE = "\n\n…（内容超过 256 KB，已截断）"
_MEMORY_NAME = re.compile(r"^[A-Za-z0-9_-]+\.md$")


def has_soul(ctx: UserCtx) -> bool:
    """只看 SOUL.md 是否存在；清空内容不改变页面可用性。"""
    return bool(ctx.soul_path) and os.path.isfile(ctx.soul_path)


def _read_text(path: str) -> tuple[str, bool]:
    with open(path, "rb") as file:
        raw = file.read(MAX_TEXT_BYTES + 1)
    truncated = len(raw) > MAX_TEXT_BYTES
    text = raw[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += TRUNCATION_NOTE
    return text, truncated


def read_soul(ctx: UserCtx) -> str:
    if not ctx.soul_path or not os.path.isfile(ctx.soul_path):
        return ""
    try:
        return _read_text(ctx.soul_path)[0]
    except OSError:
        return ""


def _memory_root(ctx: UserCtx) -> Optional[str]:
    if not ctx.memory_path:
        return None
    root = os.path.realpath(ctx.memory_path)
    return root if os.path.isdir(root) else None


def _memory_path(ctx: UserCtx, name: str) -> Optional[str]:
    """校验文件名并返回真实路径；任何越界/非法名都返回 None。"""
    if not _MEMORY_NAME.match(name or ""):
        return None
    root = _memory_root(ctx)
    if not root:
        return None
    path = os.path.realpath(os.path.join(root, name))
    if path != root and not path.startswith(root + os.sep):
        return None
    if not os.path.isfile(path):
        return None
    return path


def read_memory(ctx: UserCtx, name: str) -> Optional[str]:
    path = _memory_path(ctx, name)
    if not path:
        return None
    try:
        return _read_text(path)[0]
    except OSError:
        return None


def memory_snapshot(ctx: UserCtx) -> tuple[list[dict], str]:
    """列目录里实际存在的 .md 文件，并返回 core.md 正文。

    清单顺序：core.md → MEMORY_CATEGORIES 顺序 → 其余按文件名升序。
    每个文件只读一次，计数用同一份内容；不递归、不跟随目录外符号链接。
    """
    root = _memory_root(ctx)
    if not root:
        return [], ""

    try:
        names = sorted(os.listdir(root))
    except OSError:
        return [], ""

    preferred = [MEMORY_CORE_FILE, *(name for name, _ in MEMORY_CATEGORIES)]
    rank = {name: index for index, name in enumerate(preferred)}
    candidates = [
        name for name in names
        if not name.startswith(".")
        and _MEMORY_NAME.match(name)
        and _memory_path(ctx, name)
    ]
    candidates.sort(key=lambda name: (rank.get(name, len(preferred)), name))

    entries: list[dict] = []
    core_content = ""
    for name in candidates:
        path = _memory_path(ctx, name)
        if not path:
            continue
        try:
            stat = os.stat(path)
            text, _ = _read_text(path)
        except OSError:
            continue
        entries.append({
            "name": name,
            "lines": text.count("\n"),
            "size": stat.st_size,
            "updated_at": int(stat.st_mtime),
        })
        if name == MEMORY_CORE_FILE:
            core_content = text

    return entries, core_content
