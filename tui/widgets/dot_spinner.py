# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : dot_spinner.py
# @Software    : VSCode
# @Datetime    : 2026/10/03 23:30:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 盲文点旋转运行指示器，颜色跟随主题

from rich.text import Text
from textual.widget import Widget

FRAMES = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]
TICK = 0.1


class DotSpinner(Widget):
    DEFAULT_CSS = """
    DotSpinner {
        width: 2;
        height: 1;
        content-align: center middle;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._frame = 0

    def on_mount(self) -> None:
        self._timer = self.set_interval(TICK, self._tick)

    def on_unmount(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(FRAMES)
        self.refresh()

    def render(self) -> Text:
        return Text(FRAMES[self._frame], style=f"bold {self.styles.color.hex if self.styles.color else 'white'}")
