# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : main.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 15:26:32
# @Author      : leo liu
# @Version     : 1.0
# @Description : 入口定义

import argparse
import asyncio
import sys

# 安装脚本已经校验过解释器版本；这里兜底，防止有人绕过安装器直接用旧解释器启动。
# 那一步如果不拦，用户看到的会是 deepagents 的语法错误堆栈，而不是一句"版本不够"。
if sys.version_info < (3, 12):
    raise SystemExit(
        "NovaBot 需要 Python 3.12 或更高版本，当前是 %d.%d.%d。\n"
        "请改用 3.12+ 解释器启动，或重新运行安装脚本。"
        % sys.version_info[:3]
    )


def run_tui():
    from tui import NovaBotApp
    NovaBotApp().run()


def run_repl():
    from tui import run_repl as repl_loop
    asyncio.run(repl_loop())


def run_web(host: str, port: int):
    import uvicorn
    uvicorn.run(app="api.server:app", host=host, port=port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NovaBot")
    parser.add_argument("entry", nargs="?", choices=["tui", "repl", "web"], default="tui",
                        help="启动入口：tui=Textual界面(默认) repl=终端快速会话 web=Web 控制台(HTTP服务)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)

    args = parser.parse_args()

    if args.entry == "web":
        run_web(args.host, args.port)
    elif args.entry == "repl":
        run_repl()
    else:
        run_tui()
