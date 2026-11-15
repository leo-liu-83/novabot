# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : exec_middleware.py
# @Software    : VSCode
# @Datetime    : 2026/10/13 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 脚本执行的权威判定层：按档位过滤模型可见工具，并在工具调用边界兜底拒绝

from typing import Any, Awaitable, Callable, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from .exec_policy import (
    ExecDecision,
    ExecLevel,
    command_touches_hidden_dirs,
    decide,
    is_hard_denied,
    is_read_only_path_escape,
    is_shell_enabled,
)
from service.skill.skill_catalog import hidden_skill_dirs
from service.user.user_service import UserCtx

EXECUTE_TOOL = "execute"
_DENY_MESSAGE = (
    f"[脚本执行] 当前档位不允许执行命令，`{EXECUTE_TOOL}` 已被禁用。"
    "请改用文件读写工具完成任务；若确需执行命令，请调高 exec.level 后重试。"
)
_NOT_ALLOWED_MESSAGE = (
    "[脚本执行] 当前档位（T2 只读命令）只放行这些命令："
    "ls、cat、grep、wc、head、tail、pwd、git status、git diff、git log。"
    "不接受管道、链接、重定向、子shell，也不接受 find 与各类解释器。"
    "请改用文件读写工具，或调高 exec.level。"
)
_HARD_DENY_MESSAGE = (
    "[脚本执行] 该命令命中硬黑名单（不可逆破坏系统的操作），任何档位都不放行。"
    "请改用更安全的做法；如果确实需要，请由你本人手动执行。"
)

_HIDDEN_SKILL_MESSAGE = (
    "[脚本执行] 该路径属于尚未放行的技能，未放行的技能不能被读取或执行。"
    "请让管理员用 /skill-review 审查并放行后再试。"
)

_PATH_OUTSIDE_MESSAGE = (
    "[脚本执行] T2 只放行工作区内的只读路径；绝对路径、~ 展开或跳出工作区（含软链接）"
    "都不放行。工作区外的文件请改用文件工具，或由你本人手动查看。"
)
_PROJECT_PATH_OUTSIDE_MESSAGE = (
    "[脚本执行] T2 只放行项目目录内的只读路径；绝对路径、~ 展开或跳出项目目录（含软链接）"
    "都不放行。项目外的文件请改用文件工具，或由你本人手动查看。"
)


class ExecToolPolicyMiddleware(AgentMiddleware[Any, Any, Any]):
    """把生效档位落到execute工具层。

    T0/T1：execute整个藏掉——T1走的是无shell的run_skill_script，本就不该看见execute。
    T2：execute可见，但逐条走白名单判定（exec_policy.decide）。
    T3/T4：留给E5的逐条确认与硬地板。

    deepagents在FILESYSTEM后端下已会自行过滤execute，但它的源码注明那"不是安全表面"，
    只保证宣称与执行一致；且T2起切到LocalShellBackend后库会正常宣告execute。
    因此本中间件作为本项目自己的判定层：模型看不到不该有的工具，
    即便模型硬发出调用也在边界拒绝。
    """

    def __init__(self, level: ExecLevel, ctx: Optional[UserCtx] = None,
                 project_root: Optional[str] = None) -> None:
        self.level = level
        # 身份用于定位工作区（shell的cwd）与未放行技能集合；无ctx时不做路径判定
        self.ctx = ctx
        # 项目会话的 shell cwd 与边界根；None 时与以前完全一致
        self.project_root = project_root

    @property
    def enabled(self) -> bool:
        return is_shell_enabled(self.level)

    @staticmethod
    def _tool_name(tool: Any) -> str:
        if isinstance(tool, dict):
            return str(tool.get("name") or tool.get("function", {}).get("name") or "")

        return str(getattr(tool, "name", "") or "")

    def _strip_execute(self, tools: list[Any]) -> list[Any]:
        return [t for t in tools if self._tool_name(t) != EXECUTE_TOOL]

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if not self.enabled:
            request = request.override(tools=self._strip_execute(request.tools))

        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        if not self.enabled:
            request = request.override(tools=self._strip_execute(request.tools))

        return await handler(request)

    @staticmethod
    def _called_name(request: Any) -> str:
        # 边界判定以tool_call里的名字为准：ToolCallRequest.tool在工具未注册时为None，
        # 而tool_call始终带着模型实际发出的工具名
        tool_call = getattr(request, "tool_call", None) or {}
        return str(tool_call.get("name", "") or "")

    def _verdict(self, request: Any) -> Optional[str]:
        """对execute调用做档位判定。

        返回None表示放行。ASK也返回None——它交给deepagents的interrupt机制弹确认，
        由用户在TUI/API侧决定放行还是中止；本层只负责硬黑名单与T2白名单这两类硬判定。
        """
        if self._called_name(request) != EXECUTE_TOOL:
            return None

        if not self.enabled:
            return _DENY_MESSAGE

        args = getattr(request, "tool_call", None) or {}
        command = str((args.get("args") or {}).get("command", ""))
        hidden = self._hidden_dirs()
        cwd = self._shell_cwd()

        # 路径判定单独算一次：既是拒绝依据，也要用来选提示语
        touches_hidden = command_touches_hidden_dirs(command, hidden, cwd)
        decision = decide(self.level, command, hidden, cwd, project_root=self.project_root)

        if decision == ExecDecision.DENY:
            if self.level == ExecLevel.READ_ONLY and is_read_only_path_escape(command, cwd):
                # 命令结构合法、只是读的范围越界：用边界文案，别让人以为命令写错了
                return (_PROJECT_PATH_OUTSIDE_MESSAGE if self.project_root
                        else _PATH_OUTSIDE_MESSAGE)
            return self._deny_reason(command, touches_hidden)

        return None

    def _hidden_dirs(self) -> list:
        """全机未放行的技能目录。shell以OS用户身份运行，能摸到整盘，故取全机而非当前身份。"""
        if self.ctx is None:
            return []

        try:
            return hidden_skill_dirs()
        except Exception:
            # 取不到就退化为"不拦路径"，不能因探测失败把agent锁死
            return []

    def _shell_cwd(self) -> str:
        if self.project_root:
            return self.project_root

        return self.ctx.workspace_path if self.ctx is not None else "."

    @staticmethod
    def _deny_reason(command: str, touches_hidden: bool = False) -> str:
        if touches_hidden:
            return _HIDDEN_SKILL_MESSAGE

        if is_hard_denied(command):
            return _HARD_DENY_MESSAGE

        return _NOT_ALLOWED_MESSAGE

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], ToolMessage | Command[Any]]) -> ToolMessage | Command[Any]:
        refusal = self._verdict(request)
        if refusal:
            return ToolMessage(content=refusal, tool_call_id=request.tool_call["id"], status="error")

        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[ToolMessage | Command[Any]]]) -> ToolMessage | Command[Any]:
        refusal = self._verdict(request)
        if refusal:
            return ToolMessage(content=refusal, tool_call_id=request.tool_call["id"], status="error")

        return await handler(request)