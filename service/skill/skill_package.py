# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Description : 技能文件预览与技能包安装（zip/.skill）。

"""技能文件预览与技能包安装。

只操作技能目录内部：文件列表跳过缓存/隐藏目录，读取时做真实路径校验；
上传包先校验成员与体积，再在目标根目录内解压，最后剥掉多余的包装目录。
"""

import io
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import PurePosixPath

from service.skill.skill_scan import SKIP_DIRS

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_PACKAGE_FILES = 2000
MAX_PREVIEW_BYTES = 512 * 1024
MAX_PREVIEW_FILES = 500

_IGNORED_TOP = {"__MACOSX", ".DS_Store"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class SkillPackageError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _parts(raw: str) -> tuple[str, ...]:
    path = PurePosixPath(str(raw or "").replace("\\", "/"))
    if path.is_absolute() or any(part in ("", "..") for part in path.parts):
        raise SkillPackageError("skill.package_invalid", f"技能包包含非法路径: {raw!r}")

    return tuple(part for part in path.parts if part not in ("", "."))


def _ignored(parts: tuple[str, ...]) -> bool:
    return any(part in _IGNORED_TOP or part.startswith(".") for part in parts)


def _logical_entries(path: str) -> list[str]:
    try:
        names = os.listdir(path)
    except OSError:
        return []

    return [name for name in names if name not in _IGNORED_TOP and not name.startswith(".")]


def _strip_wrappers(root: str) -> str:
    """剥掉压缩工具/仓库自动套上的单层主目录。

    只在"当前目录只有一个子目录、且自己不是技能根"时下钻；出现多个顶层项
    就停下交给 SKILL.md 校验，避免把两个技能打包在一起时误取其一。
    """
    current = root
    while os.path.isfile(os.path.join(current, "SKILL.md")) is False:
        entries = _logical_entries(current)
        if len(entries) != 1:
            break
        child = os.path.join(current, entries[0])
        if not os.path.isdir(child) or os.path.islink(child):
            break
        current = child

    return current


def install_skill_package(data: bytes, filename: str, target_root: str) -> tuple[str, str]:
    """把 zip/.skill 安装到 target_root 下的技能目录，返回 (技能名, 目录)。"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise SkillPackageError("skill.package_too_large", "技能包超过 20 MB")

    lower = (filename or "").lower()
    if not lower.endswith((".zip", ".skill")):
        raise SkillPackageError("skill.package_invalid", "只支持 .zip 或 .skill 压缩包")

    os.makedirs(target_root, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="skill-upload-", dir=target_root)
    replace_tmp = False
    try:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise SkillPackageError("skill.package_invalid", "不是有效的 zip 压缩包") from exc

        with archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > MAX_PACKAGE_FILES:
                raise SkillPackageError("skill.package_too_large", "技能包文件数量过多")

            total = 0
            for info in infos:
                parts = _parts(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise SkillPackageError("skill.package_invalid", "技能包不能包含符号链接")
                total += info.file_size
                if total > MAX_UNPACKED_BYTES:
                    raise SkillPackageError("skill.package_too_large", "技能包解压后超过 50 MB")
                if _ignored(parts):
                    continue
                archive.extract(info, tmp)

        root = _strip_wrappers(tmp)
        if not os.path.isfile(os.path.join(root, "SKILL.md")):
            raise SkillPackageError("skill.package_invalid", "技能包根目录缺少 SKILL.md")

        if os.path.abspath(root) == os.path.abspath(tmp):
            # 技能文件直接躺在包根：目录名用上传文件名，避免临时目录名成为技能名
            name = PurePosixPath(str(filename or "").replace("\\", "/")).stem
        else:
            name = os.path.basename(root.rstrip(os.sep))
        if not _NAME_RE.match(name):
            raise SkillPackageError("skill.package_invalid", f"技能目录名不合法: {name!r}")

        dest = os.path.join(target_root, name)
        if os.path.exists(dest):
            raise SkillPackageError("skill.exists", f"技能已存在: {name}")

        if os.path.abspath(root) == os.path.abspath(tmp):
            # 整个临时目录就是技能根：直接改名，不能先 move 再删父目录
            os.replace(tmp, dest)
            replace_tmp = True
        else:
            shutil.move(root, dest)

        return name, dest
    finally:
        if not replace_tmp and os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)


def list_skill_files(skill_dir: str) -> tuple[list[dict], bool]:
    """返回技能内可预览文件（相对路径 + 大小），以及是否被截断。"""
    files: list[dict] = []
    for root, dirs, names in os.walk(skill_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            if name.startswith("."):
                continue
            full = os.path.join(root, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, skill_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            files.append({"path": rel, "size": size})
            if len(files) >= MAX_PREVIEW_FILES:
                return files, True

    return files, False


def read_skill_file(skill_dir: str, raw_path: str) -> dict:
    """读取技能内文件；二进制或超大文件只返回元信息，不返回内容。"""
    try:
        parts = _parts(raw_path)
    except SkillPackageError as exc:
        raise SkillPackageError("skill.file_not_found", "文件不存在") from exc
    if not parts or any(part in SKIP_DIRS or part.startswith(".") for part in parts):
        raise SkillPackageError("skill.file_not_found", "文件不存在")

    root = os.path.realpath(skill_dir)
    target = os.path.realpath(os.path.join(root, *parts))
    if target != root and not target.startswith(root + os.sep):
        raise SkillPackageError("skill.file_not_found", "文件不存在")
    if not os.path.isfile(target) or os.path.islink(target):
        raise SkillPackageError("skill.file_not_found", "文件不存在")

    rel = os.path.relpath(target, root).replace(os.sep, "/")
    try:
        size = os.path.getsize(target)
        with open(target, "rb") as handle:
            raw = handle.read(MAX_PREVIEW_BYTES + 1)
    except OSError as exc:
        raise SkillPackageError("skill.file_not_found", "文件不存在") from exc

    if size > MAX_PREVIEW_BYTES:
        return {"path": rel, "size": size, "too_large": True, "binary": False}

    if b"\x00" in raw:
        return {"path": rel, "size": size, "binary": True, "too_large": False}

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": rel, "size": size, "binary": True, "too_large": False}

    return {"path": rel, "size": size, "binary": False, "too_large": False, "content": content}
