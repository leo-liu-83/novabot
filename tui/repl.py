# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : chat.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : TUI会话入口

import asyncio

from rich.console import Console

from api.v1.chat.schema import ChatInputSchema
from api.v1.chat.service import ChatService
from tui.i18n import t

console = Console()

async def run():
    # 与TUI/API同规则：先建持久化saver，再让ChatService构建agent
    from core.agent import persistence
    await persistence.startup()
    try:
        await _chat_loop()
    finally:
        # 显式关闭：atexit只是兜底，交互式退出也要让WAL落盘
        await persistence.shutdown()


async def _chat_loop():
    console.print(t("[bold]NovaBot[/bold] TUI，输入 /exit 退出，/new 开启新会话，/think on|off|auto 控制深度思考\n"))

    session_id = None
    enable_thinking: bool | None = None

    while True:
        query = (await asyncio.to_thread(console.input, t(t(t("[cyan]你 > [/cyan]"))))).strip()
        if not query:
            continue
        if query == "/exit":
            break
        if query == "/new":
            session_id = None
            console.print(t(t(t("[dim]已开启新会话[/dim]\n"))))
            continue
        if query.startswith("/think"):
            enable_thinking = _toggle_thinking(query, enable_thinking)
            continue

        input = ChatInputSchema(query=query, session_id=session_id, enable_thinking=enable_thinking)
        chat_service = ChatService(input=input)
        session_id = chat_service.session_id

        console.print("bot > ", end="")
        await _print_events(chat_service.astream(), chat_service)

        console.print("\n")

# 解析/think命令，on/off为前端强制控制，auto恢复模型定义默认
def _toggle_thinking(query: str, current: bool | None) -> bool | None:
    parts = query.split()
    if len(parts) == 1:
        # 与TUI一致：/think不带参数是"查看当前"，不是隐式开启
        state = t(t(t("强制开启"))) if current is True else (t(t(t("强制关闭"))) if current is False else t(t(t("跟随模型定义"))))
        console.print(t(t(t('[dim]深度思考：{}（用法：/think on|off|auto）[/dim]\n')), state))
        return current

    arg = parts[1].lower()
    if arg == "on":
        console.print(t(t(t("[dim]深度思考：前端强制开启[/dim]\n"))))
        return True
    if arg == "off":
        console.print(t(t(t("[dim]深度思考：前端强制关闭[/dim]\n"))))
        return False
    if arg == "auto":
        console.print(t(t(t("[dim]深度思考：跟随模型定义[/dim]\n"))))
        return None

    console.print(t("[dim]未知的思考模式，可用：on / off / auto[/dim]\n"))
    return current

# 打印会话事件，interrupt时进入确认交互并恢复
async def _print_events(events, chat_service: ChatService):
    async for event in events:
        if event["event"] == "answer":
            console.print(event["answer"], end="", markup=False)
        elif event["event"] == "think":
            console.print(event["answer"], end="", style="dim italic", markup=False)
        elif event["event"] == "error":
            console.print(f"\n[red]{event['answer']}[/red]", markup=False)
        elif event["event"] == "interrupt":
            console.print(t(t(t('\n[yellow]等待确认: {}[/yellow]')), event['answer']), markup=False)
            decision = await _ask_decision()

            await _print_events(chat_service.aresume(decision), chat_service)
            return

async def _ask_decision() -> str:
    while True:
        answer = (await asyncio.to_thread(console.input, t(t(t("[yellow]允许执行? (y/n) [/yellow]"))))).strip().lower()
        if answer in ("y", "n"):
            return "approve" if answer == "y" else "reject"
