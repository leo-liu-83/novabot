# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : reasoning.py
# @Software    : VSCode
# @Datetime    : 2026/11/05 23:30:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 响应侧思考内容归一化

"""把不同端点返回的思考内容统一成一个规范字段。

请求侧用 `thinking_style` 配置（见 openai.py），响应侧没有配置项，只能按字段名探测。
已知的三种返回形态：

1. 独立字段（主流）：`reasoning_content` / `reasoning` / `thinking`
2. 明细数组：OpenRouter 的 `reasoning_details: [{"text": ...}]`
3. content 块数组：Anthropic 风格的 `{"type": "thinking", "text": ...}`

探测顺序固定，只取第一个非空值，不做厂商/模型分支：同一份响应里不会同时出现
两个非空的思考字段，固定顺序足够；真要区分协议，应在 core/llm/ 下新增适配器。

为什么必须探测而不是只认一个名字：字段名对不上时端点不报错，只会静默返回空，
用户看到的是"这个模型好像没有思考过程"——与 `enable_thinking` 被忽略是同一类假象。
"""

from typing import Any

from langchain_core.messages import AIMessageChunk

from logger import logger

# 优先专用字段，其次明细数组，最后 content 块
FIELD_CANDIDATES = ("reasoning_content", "reasoning", "thinking", "reasoning_details")
# 块/明细里真正装文本的键（Anthropic 原生块用 `thinking` 作为正文键）
_TEXT_KEYS = ("text", "summary", "thinking", "content")
_THINKING_BLOCK_TYPES = frozenset({"thinking", "reasoning"})


def _text_from(value: Any) -> str:
    """尽量从字符串/明细字典/数组里取出文本，取不到返回空串。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in _TEXT_KEYS:
            text = _text_from(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        return "".join(_text_from(item) for item in value)
    return ""


def _from_content_blocks(payload: dict) -> str:
    """Anthropic 风格：思考混在 content 块数组里。"""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""

    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in _THINKING_BLOCK_TYPES:
            text = _text_from(block)
            if text:
                parts.append(text)
    return "".join(parts)


def extract_reasoning(payload: Any) -> str:
    """从流式 delta 或非流式 message 里取出思考正文。

    取不到返回空串，绝不抛异常：正文里的 think 标签由 ThinkTagSplitter 兜底，
    这里只负责"独立字段"这一种形态。
    """
    if not isinstance(payload, dict):
        return ""

    for field in FIELD_CANDIDATES:
        text = _text_from(payload.get(field))
        if text:
            return text

    return _from_content_blocks(payload)


def reasoning_tokens(usage: Any) -> int:
    """usage 里报告的思考 token 数；各端点字段名不同，逐个探测。"""
    if not isinstance(usage, dict):
        return 0

    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    for value in (
        details.get("reasoning_tokens") if isinstance(details, dict) else None,
        details.get("reasoning") if isinstance(details, dict) else None,
        usage.get("reasoning_tokens"),
    ):
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count
    return 0


def warn_missing_reasoning(model: str, usage: Any) -> None:
    """usage 说思考过、正文却是空的：多半是响应字段名没适配。

    只告警不报错——用户仍然拿到了答案，但我们要能在日志里发现这个差异，
    而不是像 `enable_thinking` 那样被静默吞掉。
    """
    count = reasoning_tokens(usage)
    if count <= 0:
        return
    logger.warning(
        f"模型 {model} 报告了 {count} 个思考 token，但响应里没有可识别的思考正文；"
        "可能是响应字段名未适配（候选见 core/llm/http/reasoning.py）"
    )


class ReasoningWatch:
    """流式场景的同一诊断：思考正文在增量帧，usage 在最后一帧，必须跨帧累计。"""

    def __init__(self, model: str = "") -> None:
        self.model = model
        self.saw_text = False
        self.tokens = 0

    def observe(self, generation_chunk: Any) -> None:
        message = getattr(generation_chunk, "message", None)
        if not isinstance(message, AIMessageChunk):
            return

        if (message.additional_kwargs or {}).get("reasoning_content"):
            self.saw_text = True

        usage = message.usage_metadata or {}
        details = usage.get("output_token_details") or {}
        try:
            self.tokens = max(self.tokens, int(details.get("reasoning") or 0))
        except (TypeError, ValueError):
            pass

    def finish(self) -> None:
        if self.tokens > 0 and not self.saw_text:
            warn_missing_reasoning(self.model, {
                "completion_tokens_details": {"reasoning_tokens": self.tokens},
            })
