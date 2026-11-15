# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : backfill.py
# @Software    : VSCode
# @Datetime    : 2026-10-31 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 无运行态时按窗口预算从对话史重建上下文

"""上下文来源的**第二级**。

第一级是 LangGraph 的 checkpoint（运行态：完整消息序列、工具调用配对、压缩边界）；
它不存在时，用会话库里的 `messages` 重建一段对话喂给模型。

"喂多少"由这里决定，不由客户端决定：条数在中文长会话下毫无意义
（30 条短问答约 3k token，30 条长回答可能 60k token，而窗口有 8k 也有 131k），
所以按 `count_tokens_cjk` 从最新往回累加到窗口预算为止——但**至少 10 条**，
哪怕超预算也保住：接错上下文比超预算更糟，超出的部分交给压缩中间件。
"""

from dataclasses import dataclass, field
from typing import Optional

from core.agent import persistence
from core.llm.context_budget import ContextLimits, history_budget
from core.llm.token_counter import count_tokens_cjk
from service.attachment.attachments import build_reference_message, from_json

from .session_service import DEFAULT_USER_ID, session_service

# 最少回灌这么多条：宁可超预算，也不能让模型只看到半截对话
MIN_MESSAGES = 10
# 每页取这么多行来判预算：历史可能几千条，不能一次读全；也是无预算时的硬上界
PAGE_SIZE = 200


@dataclass(frozen=True)
class Loaded:
    """`messages` 按时间正序；`floor_applied` 表示"为了凑满 MIN_MESSAGES 而超了预算"。"""

    messages: list = field(default_factory=list)
    floor_applied: bool = False


def load_history(session_id: str, *, user_id: str = DEFAULT_USER_ID,
                 limits: Optional[ContextLimits] = None, service=None,
                 base: Optional[int] = None, query_tokens: int = 0) -> Loaded:
    """有运行态就一条都不灌；没有就按预算从最新往回取。

    判据必须是 `persistence.has_state`（进程内活性 + 磁盘 checkpoint）双路：
    只看磁盘的话，checkpoint 降级时每轮都会重新灌一遍，而 LangGraph 的 input 是**追加**语义，
    模型于是看到同一段对话的第二份、第三份。
    """
    if not session_id or persistence.has_state(session_id):
        return Loaded()

    svc = service or session_service
    # 与闸门同源：扣掉基线/压缩保留/摘要余量/本轮输入后，剩下的才是能给历史的
    budget = history_budget(limits, base, query_tokens)

    picked: list[dict] = []
    tokens = 0
    floor = False
    before_id = None

    while True:
        page = svc.get_messages(session_id, PAGE_SIZE, user_id, before_id)
        if not page:
            break

        reached_budget = False
        # 页内是正序，判预算要从最新的一条往回走
        for row in reversed(page):
            if row["role"] not in ("user", "assistant"):
                continue

            # 历史轮次一定早于当前轮，必然会被"一轮后收起"收成引用：
            # 这里直接构造引用，不读图、不转文档，也不产生 image block。
            content = build_reference_message(
                row["content"], from_json(row.get("attachments") or "")
            )
            estimate = content
            cost = count_tokens_cjk([{"role": row["role"], "content": estimate}])

            if budget is not None and tokens + cost > budget:
                if len(picked) >= MIN_MESSAGES:
                    reached_budget = True
                    break
                # 还没凑够下限：带着超出的部分继续，并如实告诉调用方
                floor = True

            picked.append({"role": row["role"], "content": content})
            tokens += cost

        if reached_budget or len(page) < PAGE_SIZE:
            break
        before_id = page[0]["id"]

    picked.reverse()
    return Loaded(messages=picked, floor_applied=floor)
