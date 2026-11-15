# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : skill_backend.py
# @Software    : VSCode
# @Author      : leo liu
# @Version     : 1.0
# @Description : 技能闸门：未放行的技能，其目录/正文/glob/grep 命中一律不可见

import logging
import os
from typing import Optional

from deepagents.backends import FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import (
    FileDownloadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
)

from service.skill.skill_catalog import is_skill_hidden

logger = logging.getLogger(__name__)


class SkillGateBackend(FilesystemBackend):
    """执行"放行门"：未放行的技能，其目录、正文、glob/grep 命中一律不可见。

    必须同时守五个入口。只挡 `ls` 是不够的——实测（E8.11 漏洞）三条漏径：
    `glob` 与 `grep` 直接遍历文件系统，能绕过 ls 挖出技能名；
    `read` 能直接读到 SKILL.md 正文，那正是提示词注入面本身。
    少守一个入口，门就等于没关。

    skill_roots 声明"本backend下哪些目录是技能根"（真实路径）。
    默认backend的根就是工作区，而技能目录物理上在工作区之下（workspace/skills），
    所以它也必须挂门，否则从工作区根遍历就能绕过路由直达技能目录。

    注意：`execute`（T2+ 的 shell）不在本门覆盖范围内——它跑任意命令，
    路径解析与判定属执行权限层（exec_policy），那里拿不到身份上下文。
    """

    def __init__(self, root_dir, skill_roots=(), virtual_mode: bool = True, **kwargs):
        super().__init__(root_dir, virtual_mode=virtual_mode, **kwargs)
        self._skill_roots = [os.path.abspath(str(r)) for r in skill_roots]

    # ── 门判定 ──────────────────────────────────────────────

    def _vprefix(self, root: str) -> Optional[str]:
        """技能根相对本backend的虚拟路径前缀；根不在backend内则返回None（无从守卫）。"""
        rel = os.path.relpath(root, os.path.abspath(str(self.cwd)))
        if rel == ".":
            return ""
        if rel == ".." or rel.startswith(".." + os.sep):
            return None
        return "/" + rel.replace(os.sep, "/")

    def _skill_is_hidden(self, skill_dir: str) -> bool:
        # 判据与执行门共用（skill_catalog.is_skill_hidden）：两边各写一份必然漂移，
        # 会出现"文件工具看不见、shell却能读"这种不一致
        return is_skill_hidden(skill_dir)

    def _hidden_vpaths(self) -> list[str]:
        hidden: list[str] = []
        for root in self._skill_roots:
            if not os.path.isdir(root):
                continue
            prefix = self._vprefix(root)
            if prefix is None:
                continue
            try:
                names = sorted(os.listdir(root))
            except OSError:
                continue
            for name in names:
                skill_dir = os.path.join(root, name)
                if not os.path.isdir(skill_dir):
                    continue
                if not os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
                    continue
                if self._skill_is_hidden(skill_dir):
                    hidden.append(f"{prefix}/{name}")
        return hidden

    @staticmethod
    def _canon(vpath: str) -> str:
        return (vpath or "").strip("/")

    def _is_hidden_path(self, vpath: str, hidden: list[str]) -> bool:
        canon = self._canon(vpath)
        for h in hidden:
            hc = self._canon(h)
            if canon == hc or canon.startswith(hc + "/"):
                return True
        return False

    # ── 五个入口 ────────────────────────────────────────────

    def ls(self, path: str) -> LsResult:
        result = super().ls(path)
        hidden = self._hidden_vpaths()
        if not hidden:
            return result

        # 请求的目录本身就在未放行技能内：当作不存在（空列表，不回显错误）
        if self._is_hidden_path(path, hidden):
            return LsResult(error=None, entries=[])

        entries = result.entries
        if not entries:
            return result

        kept = [e for e in entries if not self._is_hidden_path(str(e.get("path", "")), hidden)]
        return LsResult(error=result.error, entries=kept)

    def glob(self, pattern: str, path: Optional[str] = None) -> GlobResult:
        result = super().glob(pattern, path)
        hidden = self._hidden_vpaths()
        if not hidden:
            return result

        if path and self._is_hidden_path(path, hidden):
            return GlobResult(matches=[], truncated=result.truncated,
                              truncation_reason=result.truncation_reason)

        matches = result.matches
        if not matches:
            return result

        kept = [m for m in matches if not self._is_hidden_path(str(m.get("path", "")), hidden)]
        return GlobResult(matches=kept, truncated=result.truncated,
                          truncation_reason=result.truncation_reason)

    def grep(self, pattern: str, path: Optional[str] = None, glob: Optional[str] = None, *,
             max_count: Optional[int] = None, context_lines: int = 0) -> GrepResult:
        result = super().grep(pattern, path, glob, max_count=max_count,
                              context_lines=context_lines)
        hidden = self._hidden_vpaths()
        if not hidden:
            return result

        if path and self._is_hidden_path(path, hidden):
            return GrepResult(matches=[], truncated=result.truncated)

        matches = result.matches
        if not matches:
            return result

        kept = [m for m in matches if not self._is_hidden_path(str(m.get("path", "")), hidden)]
        return GrepResult(error=result.error, matches=kept, truncated=result.truncated)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        hidden = self._hidden_vpaths()
        if hidden and self._is_hidden_path(file_path, hidden):
            return ReadResult(error=f"Path '{file_path}': path_not_found")

        return super().read(file_path, offset=offset, limit=limit)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        hidden = self._hidden_vpaths()
        if not hidden:
            return super().download_files(paths)

        responses: list[FileDownloadResponse] = []
        for path in paths:
            if self._is_hidden_path(path, hidden):
                # 一进一出一一对应：skills中间件按位置zip，多缺一个就整体错位
                responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
            else:
                responses.append(super().download_files([path])[0])
        return responses


class AttachmentGuardBackend(FilesystemBackend):
    """附件目录不提供工具浏览/读取：内容在发送时已硬编码内联。

    图片已变成 image content block，文档已转成文本；再让模型 ls/read_file 只会
    重复读取、触发 422 或浪费上下文。物理路径仍可用于 execute / convert_document。
    """

    _READ_HINT = (
        "附件目录不提供 read_file：内容已在发送时内联到对应消息"
        "（图片为图片块、文档为文本）。如果当前消息没有该附件，"
        "说明它是历史附件，请让用户重新附加后再分析。"
    )

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return ReadResult(error=self._READ_HINT)

    def ls(self, path: str) -> LsResult:
        # 不让模型从目录里“发现”历史附件路径再尝试读取
        return LsResult(entries=[])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(matches=[])

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None,
             *, max_count: int | None = None, context_lines: int = 0) -> GrepResult:
        return GrepResult(matches=[])


class SkillGateShellBackend(SkillGateBackend, LocalShellBackend):
    """LOCAL_SHELL 默认backend（T2+）：同样带放行门。

    LocalShellBackend继承FilesystemBackend且只覆写execute（shell命令），
    故ls/glob/grep/read/download_files走同一套门的覆写。
    execute不在门的覆盖范围内——见类说明。
    """
