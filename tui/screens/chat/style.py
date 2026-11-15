# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : style.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 15:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 聊天页面样式

CSS = """
ChatScreen {
    background: $background;
}

#chat-container {
    width: 100%;
    height: 100%;
    padding: 1 2;
    layout: vertical;
}

#chat-header-area {
    width: 100%;
    height: auto;
    padding: 0 0;
    border-left: solid $panel;
}

#chat-header {
    width: 100%;
    height: auto;
    padding: 1 1;
    background: $surface;
}

.chat-title {
    color: $foreground;
    text-style: bold;
    width: 1fr;
}

.chat-stats {
    content-align: right middle;
    padding-right: 2;
}

/* think状态置于顶部n msgs右侧，固定宽度避免on/off/auto切换抖动 */
.chat-think {
    width: 12;
    height: 1;
    content-align: right middle;
}

/* 身份指示：user:<name> 或 匿名，固定宽度防登录/登出时标题抖动 */
.chat-identity {
    width: 14;
    height: 1;
    padding-right: 2;
    content-align: right middle;
    color: $foreground-muted;
}

#messages-scroll {
    width: 100%;
    height: 1fr;
    margin-bottom: 1;
}

#messages-list {
    width: 100%;
    height: 100%;
    padding: 0 0;
}

.notice-line {
    width: 100%;
    height: auto;
    padding: 0 1;
    color: $foreground-muted;
    text-style: italic;
}

/* 连续两条命令提示之间空一行：各占一行会黏成一片，看不出是几条 */
.notice-sep {
    margin-top: 1;
}

/* 每轮结束的 token/耗时小计：与 Markdown 正文的 padding 0 2 对齐 */
.turn-total {
    width: 100%;
    height: auto;
    padding: 0 2;
    margin-top: 0;
    margin-bottom: 1;
    color: $foreground-muted;
}

.user-message-area {
    width: 100%;
    height: auto;
    padding: 0 0;
    margin-top: 1;
    border-left: heavy $primary;
}

.user-message-wrapper {
    width: 100%;
    height: auto;
    min-height: 3;
    background: $surface;
}

/* 提问行：重发按钮在正文右侧、纵向与第一行对齐（align的第二个值是垂直方向） */
.user-message-row {
    width: 100%;
    height: auto;
    padding: 1 1;
    align: left top;
}

.user-message-row > .user-message {
    width: 1fr;
    height: auto;
}

.user-message {
    color: $foreground;
    width: 100%;
    height: auto;
}

.thinking-message-area {
    width: 100%;
    height: auto;
    padding: 0 0;
    margin-top: 1;
    border-left: solid $panel;
}

.thinking-message-wrapper {
    width: 100%;
    height: auto;
    padding: 0 1;
}

.thinking-message {
    width: 100%;
    height: auto;
    color: $foreground-muted;
}

.thinking-prefix {
    color: $warning;
    text-style: italic;
}

.assistant-message {
    color: $foreground;
    width: 100%;
    margin-top: 1;
    height: auto;
}

/* 正文输出块：复制按钮占满首行、内容右对齐（整行可点，hover提示） */
.answer-block {
    width: 100%;
    height: auto;
    margin-top: 1;
}

.answer-block .assistant-message {
    margin-top: 0;
}

.answer-block .copy-btn {
    width: 100%;
    content-align: right middle;
}

/* 代码块内Label默认width:auto会把长行撑出横向滚动区，约束宽度让text-wrap生效（正文与思考区通用） */
MarkdownFence > Label {
    width: 100%;
}

/* 工具调用状态条：输入框上方，仅执行中显示，spinner动画提示进行中
   padding-left:1 使内容起始x与下方chat-tags-box（container 2 + box 1）对齐 */
#tool-status {
    width: 100%;
    height: 1;
    padding: 0 1 0 1;
    layout: horizontal;
}

#tool-spinner {
    width: 2;
    height: 1;
    color: $primary;
}

#tool-text {
    width: auto;
    height: 1;
    color: $foreground-muted;
    content-align: left middle;
}

/* 工具调用完成记录：常驻消息流的单行说明 */
.tool-call-line {
    width: 100%;
    height: auto;
    padding: 0 1;
    margin-top: 1;
}

#confirm-bar {
    width: 100%;
    height: auto;
    padding: 0 0;
    margin-top: 1;
    border-left: heavy $warning;
}

#confirm-wrapper {
    width: 100%;
    height: auto;
    padding: 1 2;
    layout: vertical;
    background: $boost;
}

#confirm-detail {
    width: 100%;
    height: auto;
    max-height: 10;
    padding: 0;
    border: none;
    background: transparent;
}

#confirm-buttons {
    width: 100%;
    height: auto;
    padding-top: 1;
}

/* 文本tab式按钮：选中项橙底黑字，参考opencode permission面板 */
#confirm-buttons Button {
    border: none;
    background: transparent;
    color: $foreground-muted;
    padding: 0 1;
    margin-right: 1;
}

#confirm-buttons Button.-confirm-sel {
    background: $warning;
    color: $background;
    text-style: bold;
}

#confirm-keys {
    width: 1fr;
    color: $foreground-muted;
    content-align: right middle;
}

#command-popup {
    width: 100%;
    height: auto;
    max-height: 18;
    padding: 0 0;
    margin-top: 1;
    border-left: heavy $primary;
    background: $surface;
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

/* 模型行：输入框上方常显。padding-left 2 让起始 x 与输入框文字（border 1 + padding 1）对齐 */
#chat-model-line {
    width: 100%;
    height: 1;
    padding: 0 0 0 2;
}

#chat-model {
    width: auto;
    height: 1;
    color: $foreground-muted;
    content-align: left middle;
}

#chat-input-area {
    width: 100%;
    height: auto;
    padding: 0 0;
    border-left: heavy $primary;
    layout: vertical;
}

#chat-input-container {
    width: 100%;
    height: auto;
    min-height: 2;
    max-height: 8;
    padding: 1 1 1 1;
    background: $surface;
}

#chat-input {
    width: 100%;
    height: 2;
    border: none;
    background: transparent;
    color: $foreground-muted;
    padding: 0;
    min-height: 2;
    max-height: 5;
}

#chat-input:focus {
    border: none;
}

#chat-input--placeholder {
    color: $foreground-muted;
}

/* spinner+项目信息占位容器：始终1fr，内部切换不影响右侧快捷键位置 */
#chat-tags-box {
    width: 1fr;
    height: 1;
    padding-left: 1;
    layout: horizontal;
}

#chat-project {
    width: auto;
    height: 1;
    color: $foreground;
    content-align: left middle;
}

/* agent运行指示：盲文点旋转图标，颜色跟随主题primary */
#chat-running {
    width: 2;
    height: 1;
    color: $primary;
}

#chat-interrupt {
    width: auto;
    height: 1;
    margin-left: 1;
    content-align: left middle;
}

#input-hints {
    width: 100%;
    height: auto;
    margin-top: 0;
    padding-right: 0;
    dock: bottom;
}

#hints-row {
    width: 100%;
    height: auto;
}

#chat-shortcuts {
    width: auto;
    content-align: right middle;
}
"""
