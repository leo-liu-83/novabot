# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : custom_text_area.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 15:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 自定义TextArea，支持Enter提交，Ctrl+N换行，自动扩展高度

import re
import time
import inspect
from typing import Callable, Optional, Union

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widgets import TextArea

from util.text_utils import get_text_width

ATTACHMENT_TOKEN_RE = re.compile(r"\[(?:File|Image|Video|Audio) \d+\]")
_ATTACHMENT_TOKEN_STYLE = Style(bgcolor="#d79b00", color="#141414", bold=True)


class CustomTextArea(TextArea):
    """自定义 TextArea，支持 Enter 提交，Ctrl+N 换行，自动扩展高度"""

    # 按键拦截钩子：返回True（或返回awaitable且结果为True）表示该键已被外部消费
    key_interceptor: Optional[Callable[[str], Union[bool, "object"]]] = None
    # 粘贴钩子：入参是剪贴板原文，返回 None 表示"宿主不接管，照常插入"；
    # 返回字符串表示宿主处理过（附件已被摘走），插入返回的剩余文本
    paste_handler: Optional[Callable[[str], Optional[str]]] = None

    class Submitted(Message):
        """提交事件"""

        def __init__(self, text_area: "CustomTextArea", text: str) -> None:
            super().__init__()
            self.text_area = text_area
            self.text = text

    def __init__(self, **kwargs):
        self.max_lines = 6
        self.min_lines = 2
        self._last_paste_time = 0
        super().__init__(**kwargs)

    def _render_line(self, y: int):
        """给内联附件 token 上色（OpenCode 风格），其余渲染沿用 TextArea。

        Textual 8.x 的 TextArea 实际调用的是 `_render_line`，不是公开的 render_line。
        """
        strip = super()._render_line(y)
        text = strip.text
        spans = []
        for match in ATTACHMENT_TOKEN_RE.finditer(text):
            start = get_text_width(text[:match.start()])
            end = start + get_text_width(match.group(0))
            if start < end:
                spans.append((start, end))
        if not spans:
            return strip

        def styled(start: int, end: int) -> Strip:
            part = strip.crop(start, end)
            # Rich 的 Segment.apply_style 让原有样式优先，覆盖不了 TextArea 主题色；
            # 这里显式把 token 样式叠在原有样式后面（后者胜出）。
            segments = [Segment(seg.text, seg.style + _ATTACHMENT_TOKEN_STYLE)
                        for seg in part._segments]
            return Strip(segments, part.cell_length)

        parts = []
        pos = 0
        for start, end in spans:
            if start > pos:
                parts.append(strip.crop(pos, start))
            parts.append(styled(start, end))
            pos = end
        if pos < strip.cell_length:
            parts.append(strip.crop(pos))
        return Strip.join(parts)

    async def _on_key(self, event: events.Key) -> None:
        """处理键盘事件"""
        if time.time() - self._last_paste_time < 0.1:
            return

        if self.key_interceptor:
            consumed = self.key_interceptor(event.key)
            if inspect.isawaitable(consumed):
                consumed = await consumed
            if consumed:
                event.stop()
                event.prevent_default()
                return

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text.strip()
            if text:
                # 不在这里清空：聊天页要读输入框里的附件 token 与待发送计划，
                # 清空/还原由宿主在发送受理或拒绝后统一处理。
                self.post_message(self.Submitted(self, text))
        elif event.key == "ctrl+n":
            event.stop()
            event.prevent_default()
            cursor = self.cursor_location
            self.insert("\n", cursor)
            self.cursor_location = self.cursor_location
        elif event.key == "ctrl+a":
            event.stop()
            event.prevent_default()
            self.select_all()
        else:
            await super()._on_key(event)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """文本变化时调整高度"""
        self._adjust_height()

    async def _on_paste(self, event: events.Paste) -> None:
        """处理粘贴事件"""
        event.stop()
        event.prevent_default()
        self._last_paste_time = time.time()

        # 附件识别（E10.4）：粘贴是刻意动作，只在这时扫描路径——
        # 平时打字不扫，否则用户敲到一半的 `/home/user/` 会被吞进附件条
        if self.paste_handler is not None and event.text:
            try:
                remainder = self.paste_handler(event.text)
            except Exception:
                remainder = None
            if remainder is not None:
                if remainder:
                    self.insert(remainder)
                    self._adjust_height()
                return

        await super()._on_paste(event)

    def _adjust_height(self) -> None:
        """根据实际显示行数调整高度"""
        if not self.text or not self.text.strip():
            new_height = self.min_lines
        else:
            max_chars = max(1, self.size.width - 2)

            total_lines = 0
            for line in self.text.split('\n'):
                line_width = get_text_width(line)
                total_lines += max(1, (line_width + max_chars - 1) // max_chars)

            new_height = min(total_lines, self.max_lines)

        if self.styles.height != new_height:
            self.styles.height = new_height
