# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : style.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 15:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 欢迎页面样式

CSS = """
WelcomeScreen {
    background: $background;
}

#welcome-container {
    width: 100%;
    height: 100%;
    align: center middle;
    padding: 2;
}

PixelLogo {
    width: 100%;
    height: auto;
    content-align: center middle;
    margin-bottom: 3;
}

#input-wrapper {
    width: 100%;
    height: auto;
    align: center middle;
}

#input-area {
    width: 80;
    max-width: 80;
    height: auto;
    border-left: heavy $primary;
    layout: vertical;
}

#input-container {
    width: 100%;
    height: auto;
    min-height: 2;
    max-height: 8;
    padding: 1 1 1 1;
    background: $surface;
}

#command-popup {
    width: 100%;
    height: auto;
    max-height: 10;
    padding: 0 0;
    margin-top: 1;
    border-left: solid $primary;
    background: $surface;
    dock: bottom;
}

#command-options {
    width: 100%;
    height: auto;
    max-height: 10;
    padding: 0 1;
    background: $surface;
    border: none;
}

#command-options > .option-list--option {
    padding: 0 1;
}

#main-input {
    width: 100%;
    height: 2;
    border: none;
    background: transparent;
    color: $foreground;
    padding: 0;
    min-height: 2;
    max-height: 7;
}

#main-input--placeholder {
    color: $foreground-muted;
}

#shortcuts-wrapper {
    width: 100%;
    max-width: 100%;
    height: auto;
    align: center middle;
}

#shortcut-hints {
    width: 80;
    padding: 0 1;
    content-align: right middle;
}

#bottom-bar {
    width: 100%;
    height: auto;
    dock: bottom;
    layout: vertical;
}

#bottom-row {
    width: 100%;
    height: auto;
    padding-top: 1;
}

#tags {
    width: 1fr;
    height: 1;
    color: $foreground-muted;
    content-align: center middle;
}

#version {
    width: auto;
    padding: 0 2 0 1;
    color: $foreground-muted;
}
"""
