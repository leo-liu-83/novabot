# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : welcome_screen.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 15:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 欢迎页面

from typing import Optional

from textual.app import ComposeResult
from textual.timer import Timer
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Label

from core.agent import persistence
from config import config
from ..auth import identity_label
from ..commands import CommandMixin
from ..sessions import SessionEntry
from service.user.user_service import get_current_ctx
from ...widgets import CustomTextArea, PixelLogo
from service.attachment.attachments import find_paths
from . import style
from tui.i18n import t


class WelcomeScreen(CommandMixin, Screen):
    """欢迎页面：Logo + 输入框，支持/命令面板，提交后进入聊天页"""

    CSS = style.CSS

    # 提示语覆盖状态行后，保留这么久再恢复身份/模型/思考开关那行
    NOTICE_RESTORE_SECONDS = 5.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cmd_input_id = "main-input"
        # 首页选择的模型/思考作为新会话默认值
        self.model: Optional[str] = None
        self.enable_thinking: Optional[bool] = None
        self._notice_timer: Optional[Timer] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-container"):
            yield PixelLogo()
            with Horizontal(id="input-wrapper"):
                with Container(id="input-area"):
                    with Container(id="input-container"):
                        yield CustomTextArea(
                            placeholder=t(t(t("Ask anything...  (输入 / 唤起命令)"))),
                            id="main-input"
                        )
            with Container(id="shortcuts-wrapper"):
                yield Label("[dim]Ctrl+N[/dim] newline  [dim]Enter[/dim] submit  [dim]Ctrl+Q[/dim] quit", id="shortcut-hints")

        with Container(id="bottom-bar"):
            with Horizontal(id="bottom-row"):
                yield Label("", id="tags")
                yield Label(config.APP_VERSION, id="version")

        # 命令弹窗停靠在底部而非塞进居中容器：放在居中块里会随命令数变高，
        # 把整个居中内容顶出屏幕底部（终端矮于30行时末尾命令不可见）
        yield from self.compose_command_popup()

    def on_mount(self) -> None:
        self.query_one("#command-popup", Container).display = False
        self.bind_command_input()
        self._refresh_cmd_status()
        main_input = self.query_one("#main-input", CustomTextArea)
        main_input.paste_handler = self._handle_paste
        main_input.focus()

    def _handle_paste(self, pasted: str) -> Optional[str]:
        """首页粘贴文件路径：直接开一个会话并把路径交给聊天页的附件流程。

        首页没有附件条可展示，路径留在输入框里只会被当成普通文本（若以 / 开头
        还会被误当命令）。这里吞掉原文，转交聊天页处理复制/同意。
        """
        paths, remainder = find_paths(pasted)
        if not paths:
            return None

        self.run_worker(self._open_chat_with_paths(paths, remainder), group="paste-attach")
        return ""

    async def _open_chat_with_paths(self, paths: list[str], remainder: str) -> None:
        from ..chat import ChatScreen
        from ..sessions import create_session

        chat = ChatScreen(session=create_session(model=self.model, thinking=self.enable_thinking))
        await self.app.push_screen(chat)
        chat.receive_pasted_paths(paths, remainder)

    # 首页状态体现在快捷键行左侧：身份 + 模型 + 思考开关（+ 降级标记）
    def _refresh_cmd_status(self) -> None:
        state = {True: "on", False: "off", None: "auto"}[self.enable_thinking]
        who = identity_label(get_current_ctx())
        # 降级标记在首页也要看得见：用户还没进会话就该知道"重启会丢对话"
        degraded = "" if persistence.is_persistent() else t("  [red]⚠ 不持久化[/]")
        self.query_one("#tags", Label).update(
            f" [dodgerblue]Build[/dodgerblue]  {who}  {self.current_model()}  think:{state}{degraded}")

    # 首页无当前会话
    def _current_session_id(self) -> Optional[str]:
        return None

    # /new与/session在首页：带着当前默认值进入聊天页
    async def do_new_session(self) -> None:
        from ..chat import ChatScreen
        from ..sessions import create_session
        self.app.push_screen(ChatScreen(session=create_session(model=self.model, thinking=self.enable_thinking)))

    async def do_switch_session(self, entry: SessionEntry) -> None:
        from ..chat import ChatScreen
        self.app.push_screen(ChatScreen(session=entry))

    async def mount_notice(self, text: str) -> None:
        """首页只有一行状态位，提示语只能覆盖它；几秒后恢复身份/模型/思考开关那行。"""
        self.query_one("#tags", Label).update(f" [yellow]{text}[/yellow]")
        if self._notice_timer is not None:
            self._notice_timer.stop()
        self._notice_timer = self.set_timer(self.NOTICE_RESTORE_SECONDS, self._restore_cmd_status)

    def _restore_cmd_status(self) -> None:
        self._notice_timer = None
        self._refresh_cmd_status()

    # /help与/memory在首页：跳到聊天页并在那里展示内容
    # （首页只有一行状态位，放不下命令列表与记忆正文）
    async def mount_help(self) -> None:
        from ..chat import ChatScreen
        from ..sessions import create_session

        chat = ChatScreen(session=create_session(title=t("命令列表")))
        await self.app.push_screen(chat)
        await chat.mount_help()

    async def mount_memory(self, text: str) -> None:
        from ..chat import ChatScreen
        from ..sessions import create_session

        chat = ChatScreen(session=create_session(title=t(t(t("长期记忆")))))
        await self.app.push_screen(chat)
        await chat.mount_memory(text)

    # 首页发消息：带该消息开新会话进聊天页
    async def send_message(self, text: str) -> None:
        from ..chat import ChatScreen
        self.app.push_screen(ChatScreen(first_message=text))

    # 首页无当前会话，登录/登出后重画状态行即可（#tags兼作提示位，不额外弹提示以免覆盖身份显示）
    async def do_identity_changed(self, ctx, keep_session: bool) -> None:
        if self._notice_timer is not None:
            self._notice_timer.stop()
            self._notice_timer = None
        self._refresh_cmd_status()
