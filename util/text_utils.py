# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : text_utils.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 14:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from typing import Literal
import re

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

Channel = Literal["think", "answer"]

def get_text_width(text: str) -> int:
    """终端显示宽度：中文/emoji占2列，制表符占4列，其余1列"""
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    emoji_chars = len(re.findall(r'[\U0001F300-\U0001F9FF]', text))
    tab_chars = text.count('\t')

    return chinese_chars * 2 + emoji_chars * 2 + tab_chars * 4 + (len(text) - chinese_chars - emoji_chars - tab_chars)

class ThinkTagSplitter:
    """
    流式think标签拆分器。

    输入token流中think段由成对标签界定，标签可能被任意切分在多个chunk中，
    状态机保证跨chunk识别，输出(通道,文本)片段序列，通道为think或answer。
    """

    def __init__(self):
        self.in_think = False
        self.pending = ""

    def feed(self, text: str) -> list[tuple[Channel, str]]:
        buf = self.pending + text
        self.pending = ""

        segments: list[tuple[Channel, str]] = []
        i = 0

        while True:
            tag = THINK_CLOSE if self.in_think else THINK_OPEN
            idx = buf.find(tag, i)

            if idx < 0:
                # 末尾可能是被切开的半个标签，扣住等待下个chunk
                hold = self._partial_tag_len(buf[i:], tag)
                emit = buf[i: len(buf) - hold] if hold else buf[i:]
                if hold:
                    self.pending = buf[len(buf) - hold:]
                if emit:
                    segments.append(self._channel(emit))
                break

            if idx > i:
                segments.append(self._channel(buf[i:idx]))

            self.in_think = not self.in_think
            i = idx + len(tag)

        return segments

    # 流结束时把未定型的残留文本吐出（未闭合的think按原通道输出）
    def flush(self) -> list[tuple[Channel, str]]:
        segments = []
        if self.pending:
            segments.append(self._channel(self.pending))
            self.pending = ""
        return segments

    def _channel(self, text: str) -> tuple[Channel, str]:
        return ("think" if self.in_think else "answer"), text

    # 计算s尾部作为tag前缀的最大长度
    def _partial_tag_len(self, s: str, tag: str) -> int:
        for n in range(min(len(tag) - 1, len(s)), 0, -1):
            if s.endswith(tag[:n]):
                return n
        return 0
