# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : terminal_title.py
# @Software    : VSCode
# @Datetime    : 2026/11/19
# @Author      : leo liu
# @Version     : 1.0
# @Description : 终端窗口标题（OSC 2）

"""终端窗口标题。

Textual 只维护应用内的 Header 标题，不会去改终端窗口标题。这里直接往终端写
OSC 2 序列（`ESC ] 2 ; 标题 BEL`），走的是 driver 的写线程——和整屏刷新共用一个
输出通道，不会插进别的转义序列中间。

说明：

- 终端不支持时序列会被忽略，不需要探测；
- 写失败一律吞掉：标题只是锦上添花，不能因为它让 TUI 启动不了；
- 退出时恢复成空标题，把控制权交还终端/ shell 自己的 title 逻辑。
"""

from typing import Any

_BEL = "\x07"
MAX_LENGTH = 120      # 标题过长会被终端截断，超长内容也没有阅读价值


def title_sequence(text: str) -> str:
    """拼 OSC 2 序列。

    控制字符必须剔掉：标题里混进 ESC 会提前结束序列、甚至改动终端状态，
    而这些内容来自用户/模型的会话标题，不能直接信任。
    """
    clean = "".join(ch for ch in text if ch.isprintable()).strip()
    return f"\x1b]2;{clean[:MAX_LENGTH]}{_BEL}"


def window_title(session_title: str | None = None, app_name: str = "NovaBot") -> str:
    """窗口标题：应用名；有会话标题时带上，方便多开时区分窗口。"""
    title = (session_title or "").strip()
    return f"{app_name} · {title}" if title else app_name


def _driver(app: Any) -> Any:
    """取输出驱动。

    Textual 8 没有公开的 `App.driver`，实例上只有 `_driver`；老版本反过来。
    两个名字都试，哪个在就用哪个——拿不到时调用方静默跳过。
    """
    for attr in ("_driver", "driver"):
        driver = getattr(app, attr, None)
        if driver is not None and callable(getattr(driver, "write", None)):
            return driver
    return None


def apply(app: Any, text: str) -> None:
    """把标题写到终端；没有可写 driver 时静默跳过（测试用 headless driver）。"""
    driver = _driver(app)
    if driver is None:
        return
    try:
        driver.write(title_sequence(text))
    except Exception:  # noqa: BLE001 - 终端不支持/驱动已关闭都不该影响应用
        pass


def restore(app: Any) -> None:
    """退出时清空标题，避免标题栏停在「NovaBot · 某某会话」上。"""
    apply(app, "")
