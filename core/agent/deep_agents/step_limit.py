# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : step_limit.py
# @Software    : VSCode
# @Author      : leo liu
# @Version     : 1.0
# @Description : agent轮数上限：到顶时摘掉工具，逼模型用文本收尾（学OpenCode的maxSteps）

from typing import Any, Callable, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

# 到顶时给模型的收尾指令。措辞参考OpenCode的maxSteps：明确"不能再调工具"并要求总结进展与下一步
STEP_LIMIT_NUDGE = (
    "【轮数上限】本轮的工具调用次数已达上限，现在不再提供任何工具。\n"
    "请立即用纯文本**总结**回复，包含三部分：\n"
    "1. 到目前为止已经完成的步骤；\n"
    "2. 已获得的关键结果（文件路径、命令输出、结论等）；\n"
    "3. 建议用户接着做的下一步。\n"
    "不要再尝试调用工具。"
)


class StepLimitMiddleware(AgentMiddleware):
    """限制一次运行内的模型调用轮数（LLM→工具→LLM 的往返）。

    到顶的那一轮：摘掉全部工具 + 追加收尾指令，逼模型用文本总结。
    摘工具而不是只禁"别调工具"是刻意的：模型若无视指令仍发出工具调用，
    会因"工具不存在"而失败（fail-closed），不会造成任何副作用。

    计数是**每次运行**的：中间件实例随agent构建，一个agent实例对应一次用户请求；
    中途的interrupt-resume复用同一实例，故预算不会因确认而重置。
    """

    def __init__(self, max_steps: Optional[int] = None) -> None:
        self.max_steps = max_steps if (max_steps or 0) > 0 else None
        self.calls = 0

    def _at_limit(self) -> bool:
        return self.max_steps is not None and self.calls >= self.max_steps

    def _prepare(self, request: Any) -> Any:
        """到顶时：摘工具 + 追加收尾指令（追加而非替换，核心提示词不能被冲掉）。

        不额外设tool_choice="none"：工具已摘空，模型看不到任何工具名，
        再传一个tool_choice在部分模型网关上会因"没有tools却指定了tool_choice"而报错。
        """
        self.calls += 1
        if not self._at_limit():
            return request

        current = request.system_prompt or ""
        request = request.override(tools=[])
        request = request.override(
            system_message=SystemMessage(content=f"{current}\n\n{STEP_LIMIT_NUDGE}"))

        return request

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._prepare(request))

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return await handler(self._prepare(request))
