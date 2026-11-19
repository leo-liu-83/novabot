# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : app.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 15:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 主应用模块

import asyncio
import json
import os

from textual.app import App
from textual.reactive import reactive

from config import config
from logger import logger, silence_console
from core.agent import persistence
from .screens import WelcomeScreen, ChatScreen
from .screens.admin_setup_screen import AdminSetupScreen
from .screens.auth import needs_admin_setup, persist_login, restore_login, switch_identity
from .screens.sessions import create_session
from .widgets import CustomTextArea
from .windows_ime import apply_windows_ime_patch
from . import terminal_title
from service.attachment.draft_cleanup import cleanup_stale_drafts
from service.user.user_service import UserCtx, user_service

# Windows 中文输入法兼容：必须在 Textual 启动输入线程前安装
apply_windows_ime_patch()

UI_CONFIG_FILE = f"{config.APP_DATA_PATH}/ui.json"


def load_config() -> dict:
    """加载配置文件"""
    if os.path.exists(UI_CONFIG_FILE):
        try:
            with open(UI_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(config_data: dict) -> None:
    """保存配置文件（合并而非覆盖：主题与语言各存各的，不能互相冲掉）"""
    data = load_config()
    data.update(config_data)
    os.makedirs(config.APP_DATA_PATH, exist_ok=True)
    with open(UI_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


class NovaBotApp(App):
    """NovaBot TUI 应用"""

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+t", "cycle_theme", "Theme"),
    ]

    show_chat = reactive(False)

    def run(self, *args, **kwargs):
        """进入全屏前先摘掉 loguru 的 stderr sink。

        否则每条日志都会在边框外漏出一段彩色文字，并打断 Textual 的整屏重绘。
        放在 run() 而不是模块导入时：测试与 REPL import 这个模块不该被静音。
        """
        silence_console()
        return super().run(*args, **kwargs)

    async def on_mount(self) -> None:
        """应用启动时显示欢迎页面"""
        # checkpoint持久化绑定当前事件循环，必须先于任何ChatService构建
        await persistence.startup()
        # 到期凭证清扫：remember_login=false 时每次登录都留一张没人吊销的票（见 purge_expired_tokens）
        try:
            user_service.purge_expired_tokens()
        except Exception as exc:  # noqa: BLE001 - 清理失败不能拦住启动
            logger.warning(f"清理过期登录凭证失败（不影响使用）：{exc}")
        try:
            await asyncio.to_thread(cleanup_stale_drafts)
        except Exception as exc:  # noqa: BLE001 - 清理失败不能拦住启动
            logger.warning(f"清理过期附件草稿失败（不影响使用）：{exc}")

        self.themes = list(self.available_themes.keys())
        self.current_theme_index = 0

        switch_identity(restore_login())
        # 终端窗口标题：Textual 不管这个，先落到应用名，进会话后再由 ChatScreen 补上标题
        self.set_terminal_title()

        # 多用户模式但本机还没有管理员：必须先完成建管理员才能使用NovaBot。
        # 取消输入框只会退回这个页面，不放行——首个admin决定谁有权提权，不能靠"不填"跳过。
        if needs_admin_setup():
            self.push_screen(AdminSetupScreen(self._finish_admin_setup))
            self.title = config.APP_NAME
            self._load_theme()
            return

        self.push_screen(WelcomeScreen())
        self.title = config.APP_NAME
        self._load_theme()

    async def on_unmount(self) -> None:
        """退出时显式关掉checkpoint连接：atexit只是兜底，不能保证WAL落盘。"""
        await persistence.shutdown()
        # 别把窗口标题留在「NovaBot · 某会话」上：交还终端自己管
        terminal_title.restore(self)

    def set_terminal_title(self, session_title: str | None = None) -> None:
        """设置终端窗口标题。没有会话时只显示应用名。"""
        terminal_title.apply(self, terminal_title.window_title(session_title, config.APP_NAME))

    def _finish_admin_setup(self, ctx: UserCtx, token: str) -> None:
        """建管理员完成：落登录态、切换身份，再进正常流程"""
        persist_login(token)
        switch_identity(ctx)
        self.pop_screen()
        self.push_screen(WelcomeScreen())

    def _load_theme(self) -> None:
        """加载保存的theme"""
        saved_theme = load_config().get("theme", "textual-dark")
        if saved_theme in self.themes:
            self.theme = saved_theme
            self.current_theme_index = self.themes.index(saved_theme)

    def _save_theme(self) -> None:
        """保存当前theme"""
        save_config({"theme": self.theme})

    def action_cycle_theme(self) -> None:
        """切换theme"""
        self.current_theme_index = (self.current_theme_index + 1) % len(self.themes)
        self.theme = self.themes[self.current_theme_index]
        self._save_theme()

    def on_custom_text_area_submitted(self, event: CustomTextArea.Submitted) -> None:
        """处理 CustomTextArea 提交事件 - Enter 直接提交"""
        text_to_send = event.text.strip()

        if not text_to_send:
            return

        # 检查事件来源：只处理来自欢迎页输入框的事件，聊天页由ChatScreen自己处理
        if event.text_area.id == "main-input":
            if not self.show_chat:
                self.show_chat = True
                event.text_area.text = ""
                event.text_area.styles.height = 2
                # 携带欢迎页当前的模型/思考默认值开新会话
                welcome = self.screen
                self.push_screen(ChatScreen(
                    first_message=text_to_send,
                    session=create_session(model=getattr(welcome, "model", None),
                                          thinking_level=getattr(welcome, "thinking_level", None)),
                ))
