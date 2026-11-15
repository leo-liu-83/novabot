# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : compaction.py
# @Software    : VSCode
# @Datetime    : 2026/10/31 15:30:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 手动压缩当前会话上下文（/compact）

"""把当前线程的有效消息交给 deepagents 的摘要引擎，压掉保留区之外的部分。

与自动压缩共用同一个 `_summarization_event` 状态键与历史归档目录，所以：

- 手动压过的会话，下一轮自动压缩不会重复压同一段；
- 归档文件仍落在会话目录的 `/conversation_history/`，模型侧路径不变；
- 压缩只新增事件、不删消息（deepagents 的 `messages` 是 DeltaChannel，删旧链会丢上下文）。

这里刻意复用中间件的私有方法而不是另写一套摘要逻辑：口径、摘要提示词、工具调用配对
的裁剪规则都必须与自动压缩一致，否则手动压完下一轮会自动再压一次。
"""

from dataclasses import dataclass
from typing import Any, Optional

from deepagents.middleware.summarization import (
    SUMMARIZATION_EVENT_KEY,
    SUMMARIZATION_SESSION_ID_KEY,
)

from core.agent.deep_agents.attachment_middleware import degrade_message
from core.llm.context_budget import BASE_FLOOR, base_tokens, last_context, record_context
from core.llm.token_counter import count_tokens_cjk
from logger import logger


@dataclass(frozen=True)
class CompactOutcome:
    """一次手动压缩的结果。

    ok=False 时分两种：`reason` 表示"没必要压"（没有历史/仍在保留区），是正常反馈；
    `error` 表示"压失败了"，历史未改动，调用方应给出可读告警。
    """

    ok: bool
    before: int = 0
    after: int = 0
    messages: int = 0
    summary: str = ""
    file: Optional[str] = None
    error: str = ""
    reason: str = ""


def _base(thread_id: str) -> int:
    """system prompt + 工具定义的估算；样本不足时用下界，宁可少算也不错报占用。"""
    return base_tokens(thread_id) or BASE_FLOOR


async def compact_thread(agent: Any, summarization: Any, config: dict, thread_id: str) -> CompactOutcome:
    """对 `thread_id` 当前状态执行一次压缩。不调用主模型，只调摘要模型。

    Args:
        agent: 已编译的 agent（需要 `aget_state` / `aupdate_state`）。
        summarization: `create_summarization_middleware` 返回的中间件实例。
        config: 带 `thread_id` 的 RunnableConfig。
        thread_id: 占用登记与历史归档用的线程标识。
    """
    state = await agent.aget_state(config)
    values = dict(getattr(state, "values", None) or {})
    messages = list(values.get("messages") or [])
    if not messages:
        return CompactOutcome(ok=False, reason="当前会话还没有可压缩的内容")

    event = values.get(SUMMARIZATION_EVENT_KEY)
    effective = summarization._apply_event_to_messages(messages, event)
    # 与自动压缩同一个保留区（窗口的 10%）：整段都在保留区内时没有可压的旧消息
    cutoff = summarization._determine_cutoff_index(effective)
    if cutoff <= 0:
        return CompactOutcome(ok=False, reason="当前上下文仍在保留区内，无需压缩")

    to_summarize, preserved = summarization._partition_messages(effective, cutoff)
    before = last_context(thread_id) or (count_tokens_cjk(effective) + _base(thread_id))
    session_id = summarization._get_session_id(values)

    try:
        # 摘要模型既看不懂图片，也不该为一个早已收起的附件再付一次大请求：
        # 只降级摘要输入，归档仍保留原始消息（排障要看"当时到底发了什么"）。
        summary_input = [degrade_message(message) for message in to_summarize]
        summary = await summarization._acreate_summary(summary_input)
        # 先归档再更新状态：归档失败也要能如实告诉用户"文件没写入"
        file_path = await summarization._aoffload_to_backend(
            summarization._backend, to_summarize, session_id
        )
    except Exception as ex:
        logger.exception("手动压缩失败")
        return CompactOutcome(ok=False, before=before, error=f"{type(ex).__name__}: {ex}")

    summary_message = summarization._build_new_messages_with_path(summary, file_path)[0]
    state_cutoff = summarization._compute_state_cutoff(event, cutoff)
    prior_cutoff = int(event.get("cutoff_index") or 0) if isinstance(event, dict) else 0
    removed = state_cutoff - prior_cutoff if state_cutoff >= prior_cutoff else state_cutoff

    await agent.aupdate_state(config, {
        SUMMARIZATION_EVENT_KEY: {
            "cutoff_index": state_cutoff,
            "summary_message": summary_message,
            "file_path": file_path,
        },
        SUMMARIZATION_SESSION_ID_KEY: session_id,
    })

    after = count_tokens_cjk([summary_message, *preserved]) + _base(thread_id)
    record_context(thread_id, after)

    return CompactOutcome(
        ok=True,
        before=before,
        after=after,
        messages=removed,
        summary=summary,
        file=file_path,
    )
