# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : recorder.py
# @Software    : VSCode
# @Datetime    : 2026-10-31 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 会话第一层（对话史）的唯一写入者

"""把 `ChatService` 的事件流落成 `messages`/`sessions`。

放在服务层而不是表现层，是因为"什么算一条消息""工具行存不存""附件 JSON 怎么落"
属于会话本身，不属于某个界面：TUI 与 API 走同一份代码，才不会出现两套口径。

本模块只依赖 `service.session` 与 `service.attachment`，不得 import 任何 `tui.*`。
"""

import json
import threading
import time
from typing import Optional

from service.attachment.attachments import to_json

from .session_service import DEFAULT_USER_ID, session_service

DEFAULT_TITLE = "新对话"
TITLE_MAX = 30       # 会话标题字数上限（汉字按字符计）
THINK_FAIL = "fail"  # 工具行的失败标记，与历史重放的判据一致


def _amount(value) -> float:
    """把已有 meta 里的数字读成可用于累加的值：坏数据当 0，不能让收尾写入抛异常。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value if value == value and abs(value) != float("inf") else 0


class SessionRecorder:
    """一轮对话的落库动作。随 `ChatService` 实例走：HTTP 的 resume 是另一个实例，
    它只会落下自己那一段，不会改写前一个实例已落的行。"""

    def __init__(self, *, session_id: str, user_id: str = DEFAULT_USER_ID,
                 model: Optional[str] = None, thinking: Optional[bool] = None,
                 service=None):
        self.session_id = session_id
        self.user_id = user_id
        self.model = model
        self.thinking = thinking
        self.service = service or session_service
        # 落库在事件循环外的线程里执行（锁等待不能堵住 loop）；取消时主流程可能
        # 在线程还没返回时就走到 finish，所以缓冲区也要串行保护。
        self._lock = threading.Lock()

        # call 段号 -> {"think":…, "answer":…}；段号来自事件，与界面分段展示同源
        self._segments: dict[int, dict[str, str]] = {}
        # 每个段已落库的字数：同一 call 可能被 tool 边界切成两条（段号只在 usage 块递增，
        # 一次 call 里出现多次 tool 时它会一直不变），只写增量才不会把后半段丢掉
        self._written: dict[int, dict[str, int]] = {}
        # 本轮累计用量
        self._turn = {"input": 0, "output": 0, "elapsed": 0.0}
        # 本轮开始时间：报告按自然日聚合时用轮次开始，而不是收尾时间
        self._turn_started_at: Optional[int] = None
        # 最后一条 assistant 正文行：有正文时用量写这里
        self._turn_row: Optional[int] = None
        # 本轮的用户行：整轮没有 assistant 正文时（只调工具就被确认/取消打断），
        # 用量挂在这里，否则这笔账只在 sessions 里、消息级永远看不见
        self._turn_user_row: Optional[int] = None
        # 当前挂着 meta.turn 的那一行，以及该行不在本实例时已有的用量（resume 分段累加用）
        self._turn_anchor: Optional[int] = None
        self._turn_base: dict = {}

    # region 回合边界
    def record_turn_start(self, query: str, attachments: Optional[list]=None) -> None:
        """落用户行并维护会话行（标题/模型/思考档）。空提问（resume、/compact）不留行。"""
        plans = attachments or []
        if not query and not plans:
            return

        # 新回合清零：同一实例可能被复用（TUI 连发），上一轮的锚点不能带过来
        with self._lock:
            self._turn = {"input": 0, "output": 0, "elapsed": 0.0}
            self._turn_started_at = int(time.time())
            self._turn_row = None
            self._turn_anchor = None
            self._turn_base = {}
            self._save_session_row(query, plans)
            self._turn_user_row = self.service.append_message(
                self.session_id, "user", query, user_id=self.user_id, attachments=to_json(plans)
            )

    def finish(self) -> None:
        """流结束/异常/取消都要调：已生成的半段也必须落库，
        否则库里留下没有回答的提问，重开时模型看到两条并列的用户消息。

        收尾时把本轮累计用量回填到最后一轮 assistant 行：刷新页面/重开 TUI
        后「本轮 in/out」才还在，而不是只活在当前内存里。
        """
        with self._lock:
            self._flush_segments()
            self._sync_turn_meta()

    def _sync_turn_meta(self) -> None:
        """把本轮累计用量写到当前锚点行（最后一条 assistant 行，没有就写用户行）。

        每次 usage 到达就调一次，而不是只在收尾写：进程被杀、请求取消、确认打断
        都不会留下「sessions 有数、消息侧没有」的孤儿账。锚点前移时旧行必须清掉，
        否则同一轮会被计两次。
        """
        if not (self._turn["input"] or self._turn["output"]):
            return

        target = self._turn_row or self._turn_user_row
        if target is None:
            # resume 分段没有自己的用户行：挂到本轮（最近一条）用户行上
            target = self.service.last_user_message_id(self.session_id, self.user_id)
        if target is None:
            return

        if self._turn_anchor is not None and self._turn_anchor != target:
            # 还原旧锚点原本的内容（可能是空的，就彻底清掉），再搬到新锚点
            self.service.set_message_meta(
                self.session_id, self._turn_anchor,
                json.dumps({"turn": self._turn_base}, ensure_ascii=False)
                if any(self._turn_base.values()) else "",
                self.user_id,
            )
            self._turn_base = {}

        if self._turn_anchor != target:
            # resume 的另一段可能已经写在同一行上：以该行原值为基数累加，不覆盖
            self._turn_base = self.service.message_turn(target, self.user_id) or {}
            self._turn_anchor = target

        merged = {
            key: _amount(self._turn_base.get(key)) + self._turn[key]
            for key in ("input", "output", "elapsed")
        }
        self.service.set_message_meta(
            self.session_id, target,
            json.dumps({"turn": merged}, ensure_ascii=False), self.user_id,
        )

    def record_assistant(self, answer: str, think: str="") -> None:
        """非流式（`ainvoke`）没有事件流，直接把最终结果落成一段。"""
        if not ((answer or "").strip() or (think or "").strip()):
            return
        with self._lock:
            self._turn_row = self.service.append_message(
                self.session_id, "assistant", answer.strip(), think.strip(), self.user_id)
    # endregion

    # region 事件驱动
    def observe(self, event: dict) -> None:
        with self._lock:
            self._observe_event(event)

    def _observe_event(self, event: dict) -> None:
        kind = event.get("event")

        if kind in ("think", "answer"):
            self._buffer(event, kind)
        elif kind == "tool_end":
            # 先刷正文再落工具行：DB 顺序必须与界面看到的顺序一致
            self._flush_segments()
            stats = event.get("stats") or {}
            meta = {}
            if isinstance(event.get("tool"), str) and event["tool"]:
                meta["tool"] = event["tool"]
            if isinstance(stats.get("args"), str):
                meta["args"] = stats["args"]
            if isinstance(stats.get("output"), str) and stats["output"]:
                meta["output"] = stats["output"]
            self.service.append_message(
                self.session_id, "tool", event.get("answer") or "",
                "" if event.get("ok", True) else THINK_FAIL, self.user_id,
                # 重放时卡片要恢复工具名、参数与结果；content 仍留给 TUI 的一行摘要
                meta=json.dumps(meta, ensure_ascii=False) if meta else "",
            )
        elif kind == "usage":
            # 一次模型调用一行 assistant：必须在 usage 边界落库，不能等回合末尾一次性刷。
            # HTTP 的 resume 是另一个 ChatService 实例，等末尾刷会让中断前的半段丢失或被重写
            self._flush_segments()
            stats = event.get("stats") or {}
            tokens_in = stats.get("input") or 0
            tokens_out = stats.get("output") or 0
            elapsed = stats.get("elapsed") or 0.0
            self.service.save_tokens(self.session_id, tokens_in, tokens_out,
                                     stats.get("tps") or 0.0, self.user_id)
            # 累加成本轮值：事件是按"每次模型调用"发的，界面上的一轮包含多次调用
            self._turn["input"] += tokens_in
            self._turn["output"] += tokens_out
            self._turn["elapsed"] += elapsed
            # 立刻落位：不能等 finish，进程被杀/取消时这一笔就永久只剩 sessions 计数
            self._sync_turn_meta()
            # 模型明细同样按调用立即落库：与 save_tokens 同频，强杀时不丢已完成调用
            target = self._turn_user_row or self.service.last_user_message_id(
                self.session_id, self.user_id)
            if target is not None:
                started = self._turn_started_at
                if started is None:
                    started = self.service.message_created_at(target, self.user_id) or int(time.time())
                self.service.record_usage_event(
                    turn_id=str(target), uid=self.user_id, session_id=self.session_id,
                    model=self.model or "", input_tokens=tokens_in,
                    output_tokens=tokens_out, elapsed=elapsed, created_at=started,
                )
        elif kind == "context":
            stats = event.get("stats") or {}
            self.service.save_context(self.session_id, stats.get("tokens") or 0,
                                      stats.get("limit") or 0, self.user_id)
        elif kind == "compact":
            # 压缩是会话历史的一部分：只活在当前页面的 notice 刷新后就会消失。
            # 失败没有改动历史，也不再补一条误导性的"已压缩"；失败提示仍由 error 事件负责。
            stats = event.get("stats") or {}
            if not stats.get("ok"):
                return

            # 压缩是消息流里的一个位置：先刷出它之前的正文，顺序才和界面一致
            self._flush_segments()
            meta = {key: stats.get(key) for key in ("before", "after", "messages", "summary", "file")}
            self.service.append_message(
                self.session_id, "compact", event.get("answer") or "",
                user_id=self.user_id, meta=json.dumps(meta, ensure_ascii=False),
            )
    # endregion

    # region 内部
    def _buffer(self, event: dict, kind: str) -> None:
        call = event.get("call") or 0
        segment = self._segments.setdefault(call, {"think": "", "answer": ""})
        segment[kind] += event.get("answer") or ""

    def _flush_segments(self) -> None:
        for call in sorted(self._segments):
            segment = self._segments[call]
            written = self._written.setdefault(call, {"think": 0, "answer": 0})
            answer = segment["answer"][written["answer"]:].strip()
            think = segment["think"][written["think"]:].strip()

            if answer or think:
                # 记行 id：收尾回填 meta.turn 要用最后一条 assistant 行
                self._turn_row = self.service.append_message(
                    self.session_id, "assistant", answer, think, self.user_id)

            written["answer"] = len(segment["answer"])
            written["think"] = len(segment["think"])

    def _save_session_row(self, query: str, plans: list) -> None:
        """标题只在首次命名（或仍是默认名）时设置，一旦起好不再随消息改变。"""
        current = self.service.get_session(self.session_id, self.user_id) or {}
        has_history = self.service.count_messages(self.session_id, self.user_id) > 0
        seed = (query or "").strip() or (plans[0].name if plans else "")
        named = current.get("title") and current["title"] != DEFAULT_TITLE

        title = current.get("title") or DEFAULT_TITLE
        if seed and (not has_history or not named):
            title = seed[:TITLE_MAX]

        # 只更新已经签发且仍属当前用户的行；UPSERT 会让"删除已完成、旧请求迟到写入"复活会话。
        self.service.update_existing(self.session_id, title, self.model, self.thinking, self.user_id)
    # endregion
