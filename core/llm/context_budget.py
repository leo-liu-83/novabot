# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : context_budget.py
# @Software    : VSCode
# @Datetime    : 2026/10/26 10:10:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 上下文预算、发送前闸门与进程内占用登记

import statistics
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Optional, Sequence

from .token_counter import count_tokens_cjk

# 摘要本身要占的输出预算；与窗口无关，固定值
SUMMARY_RESERVE = 2048
# 系统提示词 + 工具 schema 的下限。真实基线由 usage 反推，拿不到时用它保守估
BASE_FLOOR = 4096
# 每线程登记表容量：只裁剪缓存，不影响会话本身
MAX_TRACKED_THREADS = 500
# 基线取最近几次采样，取中位数抵抗首轮工具很多/切换模型的抖动
BASE_SAMPLES = 3


@dataclass(frozen=True)
class ContextLimits:
    """由模型窗口推导出的三个阈值；三者随窗口同比缩放。"""

    window: int
    trigger: int
    budget: int
    keep: int

    @classmethod
    def from_window(cls, window: int, max_output: int) -> Optional["ContextLimits"]:
        if not window or window <= 0:
            return None

        return cls(
            window=int(window),
            trigger=int(window * 0.85),
            budget=max(0, int(window * 0.95) - int(max_output or 0)),
            keep=int(window * 0.10),
        )


@dataclass(frozen=True)
class Verdict:
    ok: bool
    tokens: int
    budget: int
    reserve: int
    message: str


# thread_id -> 最近一次模型调用的精确输入 token 数（usage_metadata.input_tokens）
_CONTEXT: "OrderedDict[str, int]" = OrderedDict()
# thread_id -> 最近几次"非消息部分"的 token 数（系统提示词+工具 schema）
_BASE: "OrderedDict[str, deque[int]]" = OrderedDict()
_lock = threading.Lock()


def _touch(registry: "OrderedDict", key: str) -> None:
    registry.move_to_end(key)
    while len(registry) > MAX_TRACKED_THREADS:
        registry.popitem(last=False)


def record_context(thread_id: str, tokens: int) -> None:
    if not thread_id:
        return

    with _lock:
        _CONTEXT[thread_id] = int(tokens)
        _touch(_CONTEXT, thread_id)


def last_context(thread_id: str) -> Optional[int]:
    with _lock:
        return _CONTEXT.get(thread_id)


def record_base(thread_id: str, value: int) -> None:
    if not thread_id or value <= 0:
        return

    with _lock:
        samples = _BASE.get(thread_id)
        if samples is None:
            samples = _BASE[thread_id] = deque(maxlen=BASE_SAMPLES)
        samples.append(int(value))
        _touch(_BASE, thread_id)


def base_tokens(thread_id: str) -> Optional[int]:
    """取最后三次采样的中位数；不足三次时返回 None（闸门改用 BASE_FLOOR）。"""
    with _lock:
        samples = tuple(_BASE.get(thread_id) or ())

    if len(samples) < BASE_SAMPLES:
        return None

    return int(statistics.median(samples))


def history_budget(limits: Optional[ContextLimits], base: Optional[int] = None,
                   query_tokens: int = 0) -> Optional[int]:
    """回灌历史可用的 token 上限：窗口预算减去基线、压缩保留、摘要余量与本轮输入。

    必须与 `preflight` 用同一把尺。回灌按 `budget` 灌满、闸门按 `budget - reserve` 判的话，
    落在中间那一段的历史会"既灌得进、又必然被拒"：被拒的一轮不落库也不标记活性，
    下一轮算出同样的回灌，于是永远发不出去。
    """
    if limits is None:
        return None

    reserve = (BASE_FLOOR if base is None else base) + limits.keep + SUMMARY_RESERVE
    return max(0, limits.budget - reserve - max(0, query_tokens))


def preflight(text: str, *, limits: Optional[ContextLimits], base: Optional[int] = None,
              history: Sequence = (), extra_tokens: int = 0) -> Verdict:
    """判断这条输入在压缩之后是否仍放得下。

    只拦"压完还必然超预算"的情况；普通超阈值交给 deepagents 压缩。
    没有窗口信息时闸门关闭（前端只显示绝对值）。
    """
    if limits is None:
        return Verdict(True, 0, 0, 0, "")

    messages = list(history) + [{"role": "user", "content": text}]
    tokens = count_tokens_cjk(messages) + max(0, int(extra_tokens))
    reserve = (BASE_FLOOR if base is None else base) + limits.keep + SUMMARY_RESERVE
    if limits.budget <= reserve:
        # 窗口小到连基线与摘要余量都放不下：闸门永远不可能通过，关掉它（交给端点报错）
        return Verdict(True, tokens, limits.budget, reserve, "")

    ok = reserve + tokens <= limits.budget
    message = "" if ok else (
        f"本次输入约 {tokens / 1000:.1f}k tok，压缩后仍需约 {(reserve + tokens) / 1000:.1f}k，"
        f"超过可用预算 {limits.budget / 1000:.1f}k。"
        "可以拆分成两次发送，或保存为文件让我按路径读取。"
    )
    return Verdict(ok, tokens, limits.budget, reserve, message)


def limits_for_model(model: Optional[str]) -> Optional[ContextLimits]:
    """按模型读取窗口与输出上限；拿不到（模型不存在/无 profile）时返回 None。"""
    try:
        from core.manager.llm_manager import llm_manager

        llm = llm_manager.get_llm(model)
        window = getattr(llm, "max_input_tokens", None) or 0
        output = getattr(llm, "max_tokens", 0) or 0
        return ContextLimits.from_window(window, output)
    except Exception:
        return None


def reset() -> None:
    """清空进程内登记（测试用）。"""
    with _lock:
        _CONTEXT.clear()
        _BASE.clear()
