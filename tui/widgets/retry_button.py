# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : retry_button.py
# @Software    : VSCode
# @Datetime    : 2026-10-15 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 提问行右侧的「重问」按钮：把该条提问原文再发一次

from typing import Callable, Optional

from textual.message import Message
from textual.widgets import Static

from tui.i18n import t


class RetryButton(Static):
    """点击后不自行发送，只抛Submitted消息交由ChatScreen处理。

    这样控件不依赖screen的私有方法：谁挂载它、怎么处理重发由容器决定。
    """

    LABEL = "↻ 重问"

    DEFAULT_CSS = """
    RetryButton {
        width: auto;
        height: 1;
        padding: 0 1;
        color: $foreground-muted;
    }

    RetryButton:hover {
        color: $foreground;
        text-style: bold;
    }
    """

    class Submitted(Message):
        def __init__(self, query: str, attachments: Optional[list]=None) -> None:
            super().__init__()
            self.query = query
            # 带附件的提问重问时要连附件一起带上，否则模型只看到 [File 1] 却不知道是什么
            self.attachments = attachments or []

    def __init__(self, get_query: Callable[[], str], attachments: Optional[list]=None, **kwargs):
        super().__init__(t(self.LABEL), **kwargs)
        self._get_query = get_query
        self._attachments = attachments or []

    def on_click(self) -> None:
        query = (self._get_query() or "").strip()
        if query:
            self.post_message(self.Submitted(query, self._attachments))
