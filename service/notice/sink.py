# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : sink.py
# @Description : 端点故障提示的收集器（同一端点每会话只报一次）

from dataclasses import dataclass

# 进程级共享状态：session_id -> 已报过的端点键。
# 收集器每轮agent重建，但去重必须跨轮存活，否则MCP装配失败会每轮都报一遍。
_REPORTED: dict[str, set[str]] = {}

# 保留最近多少个会话的去重记录，超出丢最旧的
MAX_TRACKED_SESSIONS = 200


@dataclass(frozen=True)
class Notice:
    key: str
    text: str


class NoticeSink:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._pending: list[Notice] = []

    def add(self, key: str, text: str) -> None:
        """收一条提示。同键只收第一条——同一端点每会话只报一次。"""
        seen = _REPORTED.setdefault(self.session_id, set())
        if key in seen:
            return

        seen.add(key)
        self._pending.append(Notice(key=key, text=text))
        self._evict()

    def drain(self) -> list[Notice]:
        items = self._pending
        self._pending = []
        return items

    @staticmethod
    def _evict() -> None:
        while len(_REPORTED) > MAX_TRACKED_SESSIONS:
            oldest = next(iter(_REPORTED))
            del _REPORTED[oldest]
