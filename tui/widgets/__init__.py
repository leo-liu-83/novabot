# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : __init__.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 15:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from textual.widgets import Static
from rich.text import Text

from .custom_text_area import ATTACHMENT_TOKEN_RE, CustomTextArea
from .dot_spinner import DotSpinner
from .copy_button import CopyButton
from .retry_button import RetryButton


class PixelLogo(Static):
    """NOVABOT艺术字Logo"""

    def render(self):
        logo_lines = [
            "███╗   ██╗ ██████╗ ██╗   ██╗ █████╗ ██████╗  ██████╗ ████████╗",
            "████╗  ██║██╔═══██╗██║   ██║██╔══██╗██╔══██╗██╔═══██╗╚══██╔══╝",
            "██╔██╗ ██║██║   ██║██║   ██║███████║██████╔╝██║   ██║   ██║",
            "██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══██║██╔══██╗██║   ██║   ██║",
            "██║ ╚████║╚██████╔╝ ╚████╔╝ ██║  ██║██████╔╝╚██████╔╝   ██║",
            "╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝╚═════╝  ╚═════╝    ╚═╝",
        ]

        theme = self.app.available_themes[self.app.theme]
        part1_color = "#757575" if theme.dark else "#888888"
        part2_color = "#FFFFFF" if theme.dark else "#303030"

        styled_text = Text()
        for i, line in enumerate(logo_lines):
            part1 = line[:36]
            part2 = line[36:]

            styled_text.append(part1, style=part1_color)
            styled_text.append(part2, style=part2_color)

            if i < len(logo_lines) - 1:
                styled_text.append("\n")

        return styled_text
