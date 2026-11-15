# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : admin_setup_screen.py
# @Software    : VSCode
# @Datetime    : 2026/10/13 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 建管理员强制页：多用户模式下本机尚无admin时挡住整个应用

from typing import Callable

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Input, Label

from exception.user_exception import AuthError
from service.user.user_service import UserCtx
from .auth import ADMIN_SETUP_STEPS, ADMIN_SETUP_TITLE, setup_admin
from tui.i18n import t


class AdminSetupScreen(Screen):
    """创建首个管理员账号。取消输入框只会退回本页——不填没法跳过。

    为什么必须强制：管理员决定谁有权获得脚本执行能力。若允许跳过，
    攻击者可以先建普通账号再想办法绕过，而"第一个建admin的人是谁"
    一旦可被抢占，后面所有提权授权都失去意义。
    """

    CSS = """
    #admin-center {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #admin-panel {
        width: auto;
        min-width: 52;
        max-width: 80%;
        height: auto;
        padding: 2 3;
        background: $surface;
        border: round $primary;
    }

    #admin-title {
        width: 100%;
        height: auto;
        text-style: bold;
        color: $foreground;
    }

    #admin-desc {
        width: 100%;
        height: auto;
        margin: 1 0;
        color: $foreground-muted;
    }

    #admin-label {
        width: 100%;
        height: auto;
        color: $foreground;
    }

    #admin-field {
        width: 100%;
        height: auto;
        margin-top: 1;
        background: $surface;
    }

    #admin-field:focus {
        border: none;
    }

    #admin-error {
        width: 100%;
        height: auto;
        color: $error;
    }

    #admin-hint {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $foreground-muted;
        content-align: right middle;
    }
    """

    def __init__(self, on_done: Callable[[UserCtx, str], None], **kwargs):
        super().__init__(**kwargs)
        self._on_done = on_done
        self._index = 0
        self._values: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Container(id="admin-center"):
            with Container(id="admin-panel"):
                yield Label("", id="admin-title")
                yield Label(t(ADMIN_SETUP_TITLE), id="admin-desc")
                yield Label("", id="admin-label")
                yield Input(id="admin-field")
                yield Label("", id="admin-error")
                yield Label(t("[dim]Enter 继续    Esc 取消[/dim]"), id="admin-hint")

    def on_mount(self) -> None:
        self._sync_step()

    def _sync_step(self) -> None:
        key, label, is_password = ADMIN_SETUP_STEPS[self._index]
        total = len(ADMIN_SETUP_STEPS)

        self.query_one("#admin-title", Label).update(t(t('创建管理员账号（{}/{}）'), self._index + 1, total))
        self.query_one("#admin-label", Label).update(t(label))

        field = self.query_one("#admin-field", Input)
        field.password = is_password
        field.value = ""
        field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value

        if not value.strip():
            return

        self._values[ADMIN_SETUP_STEPS[self._index][0]] = value
        self._index += 1

        if self._index >= len(ADMIN_SETUP_STEPS):
            self._submit()
            return

        self._sync_step()

    def _submit(self) -> None:
        try:
            ctx, token = setup_admin(self._values)
        except AuthError as ex:
            self._values.clear()
            self._index = 0
            self.query_one("#admin-error", Label).update(t(t('创建失败：{}'), ex.err_desc))
            self._sync_step()
            return

        self._on_done(ctx, token)

    def action_cancel(self) -> None:
        """Esc 不放行：清空当前输入留在本页。这是有意为之，不是待办。"""
        self._values.clear()
        self._index = 0
        self.query_one("#admin-error", Label).update("")
        self._sync_step()