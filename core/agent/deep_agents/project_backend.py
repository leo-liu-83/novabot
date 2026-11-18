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
import sys
from typing import Optional

from langchain.agents.middleware import AgentMiddleware
from deepagents.backends.protocol import ExecuteResponse, GlobResult, GrepResult, LsResult

from service.project.sensitive import is_sensitive
from .project_venv import (build_env, ensure_venv, find_venv, installs_packages,
                           is_venv_bootstrap)
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
    """项目 shell backend：cwd = 项目目录，并自动使用项目自己的 Python 环境。

    写代码不需要激活环境；只有跑命令（`execute`）时才把项目 venv 前置进 PATH。
    没有 venv 的 Python 工程会在第一次需要 Python 的命令里建一个 `.venv`
    （由 uv/poetry/pdm 托管的项目除外）。
    """

    def execute(self, command: str, *, timeout: Optional[int] = None):
        # 每次按当前状态算：agent 可能刚跑完 `uv sync`，venv 是之后才出现的
        venv, _ = ensure_venv(self.project_root, command=command)
        if not venv and is_venv_bootstrap(command):
            # 空项目里没有 python 可执行文件（PATH 已剔除所有环境）：
            # 显式建 venv 时把 NovaBot 的基础解释器目录临时加回 PATH，
            # 让它能执行 `python -m venv`，但这条命令本身不创建任何东西
            self._env = build_env(None)
            self._env["PATH"] = (os.path.dirname(sys.executable)
                                 + os.pathsep + self._env["PATH"])
            return super().execute(command, timeout=timeout)
        if not venv and installs_packages(command):
            # 没有项目环境时 pip 会命中 NovaBot 自己的环境或系统 Python：
            # 宁可直接拒绝，也不让一次 install 污染全局
            return ExecuteResponse(
                output=(
                    "拒绝执行：项目里没有独立的虚拟环境，`pip install` 会装进 NovaBot 自己的环境"
                    "或系统 Python。请先创建项目环境（`python -m venv .venv`），"
                    "或改用项目所用的 uv / poetry / pdm 命令。"
                ),
                exit_code=1,
            )
        self._env = build_env(find_venv(self.project_root))
        return super().execute(command, timeout=timeout)
