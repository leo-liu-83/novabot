# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : input_screen.py
# @Software    : VSCode
# @Datetime    : 2026/10/10 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 通用输入弹窗（ModalScreen），多步填写+密码掩码，样式对齐SelectScreen

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, Label
from tui.i18n import t


class InputScreen(ModalScreen[Optional[dict]]):
    """多步输入弹窗：Enter进入下一步/提交，Esc取消；密码步自动掩码；返回{字段: 值}或None"""

    CSS = """
    #input-center {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #input-panel {
        width: auto;
        min-width: 44;
        max-width: 80%;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }

    #input-title {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        text-style: bold;
        color: $foreground;
    }

    #input-label {
        width: 100%;
        height: auto;
        color: $foreground-muted;
    }

    #input-field {
        width: 100%;
        height: auto;
        margin-top: 1;
        background: $surface;
    }

    #input-field:focus {
        border: none;
    }

    #input-hint {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $foreground-muted;
        content-align: right middle;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, title: str, steps: list[tuple[str, str, bool]],
                 initial: Optional[dict[str, str]] = None, **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._steps = steps
        self._initial = initial or {}
        self._index = 0
        self._values: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Container(id="input-center"):
            with Container(id="input-panel"):
                yield Label(t(self._title), id="input-title")
                yield Label("", id="input-label")
                yield Input(id="input-field")
                yield Label(t("[dim]Enter 继续    Esc 取消[/dim]"), id="input-hint")

    def on_mount(self) -> None:
        self._sync_step()
        self.query_one("#input-field", Input).focus()

    def _sync_step(self) -> None:
        key, label, is_password = self._steps[self._index]
        progress = t("（{}/{}）", self._index + 1, len(self._steps)) if len(self._steps) > 1 else ""

        self.query_one("#input-title", Label).update(f"{t(self._title)}{progress}")
        self.query_one("#input-label", Label).update(t(label))

        field = self.query_one("#input-field", Input)
        field.password = is_password
        field.value = str(self._initial.get(key, ""))
        field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value
        key = self._steps[self._index][0]

        if not value.strip():
            return

        self._values[key] = value
        self._index += 1

        if self._index >= len(self._steps):
            self.dismiss(self._values)
            return

        self._sync_step()

    def action_cancel(self) -> None:
        self.dismiss(None)