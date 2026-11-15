# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : copy_button.py
# @Software    : VSCode
# @Datetime    : 2026/10/04 09:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 正文块右上角复制按钮：优先系统剪贴板工具（wl-copy/xclip/xsel/clip.exe），OSC52兜底

import os
import shutil
import subprocess
from typing import Callable

from textual.widgets import Static

from tui.i18n import t


def copy_to_clipboard(app, text: str) -> str:
    """复制文本到系统剪贴板；返回实际使用的通道 system/osc52"""
    cmds = []
    if os.environ.get("WAYLAND_DISPLAY"):
        cmds.append(["wl-copy"])
    if os.environ.get("DISPLAY"):
        if shutil.which("xclip"):
            cmds.append(["xclip", "-selection", "clipboard"])
        elif shutil.which("xsel"):
            cmds.append(["xsel", "-b", "-i"])
    if "WSL_DISTRO_NAME" in os.environ and shutil.which("clip.exe"):
        cmds.append(["clip.exe"])

    for cmd in cmds:
        if not shutil.which(cmd[0]):
            continue
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "system"
        except Exception:
            continue

    app.copy_to_clipboard(text)
    return "osc52"


class CopyButton(Static):
    LABEL = "⧉ 复制"
    DONE = "[green]✓ 已复制[/green]"
    OSC_WARN = "[yellow]已复制(仅应用内)[/yellow]"

    DEFAULT_CSS = """
    CopyButton {
        width: auto;
        height: 1;
        padding: 0 1;
        color: $foreground-muted;
        text-align: right;
    }

    CopyButton:hover {
        color: $foreground;
        text-style: bold;
    }
    """

    def __init__(self, get_text: Callable[[], str], **kwargs):
        super().__init__(t(self.LABEL), **kwargs)
        self._get_text = get_text

    def on_click(self, event) -> None:
        text = (self._get_text() or "").strip()
        if not text:
            return

        channel = copy_to_clipboard(self.app, text)
        self.update(t(self.DONE) if channel == "system" else t(self.OSC_WARN))
        self.set_timer(1.5, lambda: self.update(t(self.LABEL)))
