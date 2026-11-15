# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : token_counter.py
# @Software    : VSCode
# @Datetime    : 2026/10/26 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : CJK 感知的 token 估算，压缩阈值与界面占用共用同一把尺子

import json
import math
from typing import Any, Iterator, Optional, Sequence

from langchain_core.messages.utils import convert_to_messages, count_tokens_approximately

# 2026-11-06 用 deepseek-flash 对三类真实语料（技术叙述/文学叙述/Markdown 文档）
# 各测一遍 prompt_tokens，中文实测 0.66–0.74 token/字符。上游近似对中文只给
# 0.25–0.30，差额靠这一项补：0.45 时三类样本的估算落在实测的 0.96–1.07 倍。
#
# 早先的 0.75 是按"1 字 = 1 token"的直觉定的，实测偏保守约 1.4 倍：压缩会在
# 真实占用 ~70% 时触发，85%/95% 预警几乎看不到。标定样本不要用"上下文"这类
# 高频词重复拼接——它在 BPE 里接近 1 词 3 字，会算出 0.34 的假密度。
NON_ASCII_WEIGHT = 0.45


def _text_parts(message: Any) -> list[str]:
    """只取消息里的文本：图片/文件块不计字符，避免 base64 把估算撑爆。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return [content]

    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return parts


def _tool_call_texts(message: Any) -> Iterator[str]:
    """write_file/edit_file 的中文正文在 tool_calls.args 里，不在 content 里。"""
    for call in getattr(message, "tool_calls", None) or []:
        args = call.get("args") if isinstance(call, dict) else None
        if args is not None:
            yield json.dumps(args, ensure_ascii=False, default=str)


def _non_ascii(text: str) -> int:
    return sum(1 for char in text if ord(char) > 127)


def non_ascii_chars(messages: Sequence[Any]) -> int:
    """统计消息文本与工具调用参数中的非 ASCII 字符数（CJK 为主）。"""
    total = 0
    for message in convert_to_messages(messages):
        for part in _text_parts(message):
            total += _non_ascii(part)
        for text in _tool_call_texts(message):
            total += _non_ascii(text)
    return total


def tools_non_ascii_chars(tools: Optional[Sequence[Any]]) -> int:
    """工具 schema（名称/描述/参数说明）里的非 ASCII 字符数。"""
    if not tools:
        return 0

    total = 0
    for tool in tools:
        try:
            if isinstance(tool, dict):
                data: Any = tool
            else:
                schema = getattr(tool, "tool_call_schema", None)
                data = dict(schema) if isinstance(schema, dict) else None
            if data is not None:
                total += _non_ascii(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            # schema 结构异常不该让计数崩掉；这一项退回上游近似
            continue
    return total


def count_tokens_cjk(messages: Sequence[Any], *, tools: Optional[list] = None, **kwargs) -> int:
    """在上游近似值上按非 ASCII 字符数补足，返回 int。

    签名与 `count_tokens_approximately` 对齐（含 `tools=`），可直接注入
    deepagents 的 `SummarizationMiddleware`。
    """
    base = count_tokens_approximately(messages, tools=tools, **kwargs)
    correction = non_ascii_chars(messages) + tools_non_ascii_chars(tools)
    return math.ceil(base + NON_ASCII_WEIGHT * correction)
