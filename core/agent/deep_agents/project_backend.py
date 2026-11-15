# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : project_backend.py
# @Description : 项目会话的 backend：相对路径归一与敏感结果过滤

"""项目 backend。

两个必须在这里解决的问题：

1. `virtual_mode=False` 本身只影响“未带前导斜杠”的路径；deepagents 的文件工具
   会先 `validate_path()` 把 `a.txt` 归一成 `/a.txt`，backend 再按主机绝对路径
   解析——`write_file("a.txt")` 会写到 `/a.txt`。所以必须在 HITL 与工具执行前
   用 `ProjectPathMiddleware` 把相对参数折回项目根。
2. `ls`/`glob`/`grep` 不能逐条弹敏感文件确认；这里直接过滤结果，
   `read_file` 的确认由权限层的专用 `when` 负责。
"""

import os

from langchain.agents.middleware import AgentMiddleware
from deepagents.backends.protocol import GlobResult, GrepResult, LsResult

from service.project.sensitive import is_sensitive
from .skill_backend import SkillGateBackend, SkillGateShellBackend

# 文件工具的路径参数：文件名工具用 file_path，批量工具用 path
FS_PATH_ARGS: dict[str, str] = {
    "ls": "path",
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
    "glob": "path",
    "grep": "path",
}

# 这些工具的路径是“搜索根”，"/" 在非虚拟 backend 里等于主机根；
# 统一折成项目根，避免模型按虚拟路径习惯误扫整盘。
SEARCH_ROOT_TOOLS = frozenset({"ls", "glob", "grep"})


def _to_project_path(raw: str, project_root: str) -> str:
    """相对路径按项目根解析；绝对路径/`~` 保持其真实含义。"""
    text = os.path.expanduser(str(raw or ""))
    if not os.path.isabs(text):
        text = os.path.join(project_root, text)

    return os.path.normpath(text)


class ProjectPathMiddleware(AgentMiddleware):
    """把项目文件工具的相对路径参数归一成项目绝对路径。

    必须排在 HumanInTheLoop 之前（用户中间件天然在 tail 之前），
    这样中断事件的 `when` 谓词与工具执行看到的都是同一份路径。
    """

    def __init__(self, project_root: str) -> None:
        self.project_root = project_root

    def _rewrite(self, request):
        tool_call = getattr(request, "tool_call", None) or {}
        name = str(tool_call.get("name") or "")
        arg_name = FS_PATH_ARGS.get(name)
        if arg_name is None:
            return request

        args = dict(tool_call.get("args") or {})
        raw = args.get(arg_name)

        if name in SEARCH_ROOT_TOOLS and (raw is None or str(raw) in ("", ".", "/")):
            args[arg_name] = self.project_root
        elif isinstance(raw, str) and raw:
            args[arg_name] = _to_project_path(raw, self.project_root)
        elif name in SEARCH_ROOT_TOOLS:
            args[arg_name] = self.project_root

        return request.override(tool_call={**tool_call, "args": args})

    def wrap_tool_call(self, request, handler):
        return handler(self._rewrite(request))

    async def awrap_tool_call(self, request, handler):
        return await handler(self._rewrite(request))


class SensitivePathGuardMixin:
    """过滤批量工具结果里的敏感项；直接扫描敏感路径返回空结果。"""

    project_root: str = ""

    def __init__(self, *args, project_root: str = "", **kwargs) -> None:
        self.project_root = project_root
        super().__init__(*args, **kwargs)

    def _blocked(self, path: str) -> bool:
        return is_sensitive(path, self.project_root)

    def _keep(self, items, key: str = "path"):
        return [item for item in (items or ())
                if not self._blocked(str(item.get(key, "")))]

    def ls(self, path: str) -> LsResult:
        if self._blocked(path):
            return LsResult(error=None, entries=[])
        result = super().ls(path)
        return LsResult(error=result.error, entries=self._keep(result.entries))

    def glob(self, pattern: str, path=None) -> GlobResult:
        if path and self._blocked(path):
            return GlobResult(matches=[], truncated=False, truncation_reason=None)
        result = super().glob(pattern, path)
        return GlobResult(error=result.error, matches=self._keep(result.matches),
                          truncated=result.truncated,
                          truncation_reason=result.truncation_reason)

    def grep(self, pattern: str, path=None, glob=None, **kwargs) -> GrepResult:
        if path and self._blocked(path):
            return GrepResult(matches=[], truncated=False)
        result = super().grep(pattern, path, glob, **kwargs)
        return GrepResult(error=result.error, matches=self._keep(result.matches),
                          truncated=result.truncated)


class ProjectFilesystemBackend(SensitivePathGuardMixin, SkillGateBackend):
    """项目文件 backend：根 = 项目目录，绝对路径按真实路径解析。"""


class ProjectShellBackend(SensitivePathGuardMixin, SkillGateShellBackend):
    """项目 shell backend：cwd = 项目目录。"""
