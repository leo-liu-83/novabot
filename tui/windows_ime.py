# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
"""Windows 中文输入法兼容补丁。

Textual 8.2.x 的 Win32 驱动会丢弃 `dwControlKeyState` 非空且 `VK=0` 的按键
事件，但中文输入法提交全角标点等字符时正好会发这种事件，表现为输入法打字
丢字/标点打不出来。

上游修复（Textualize/textual#6667）尚未合并。这里不改 site-packages，而是在
启动时包一层 ReadConsoleInputW：保留真实 Unicode 字符的事件，只把它们的
修饰键状态清零，避开 Textual 的误判。仅 Windows 生效，且只在检测到旧版
驱动实现时安装。
"""

import ctypes
import inspect
import logging
import sys

log = logging.getLogger(__name__)

KEY_EVENT = 0x0001


def _is_ime_commit_event(virtual_key: int, char: str) -> bool:
    """VK=0 但带真实 Unicode 字符：IME 提交字符，不应被当成裸修饰键丢掉。"""
    return virtual_key == 0 and bool(char) and char != "\x00"


def _event_count(win32, count_ptr) -> int:
    try:
        return int(count_ptr._obj.value)
    except AttributeError:
        try:
            return int(
                ctypes.cast(count_ptr, ctypes.POINTER(win32.wintypes.DWORD)).contents.value
            )
        except Exception:
            return 0


def _clear_ime_modifier_noise(win32, buffer_ptr, count_ptr) -> None:
    """把 IME 提交字符事件上的修饰键状态清零，Textual 就不会丢弃它。"""
    count = _event_count(win32, count_ptr)
    if count <= 0:
        return

    records = ctypes.cast(buffer_ptr, ctypes.POINTER(win32.INPUT_RECORD))
    for index in range(count):
        record = records[index]
        if record.EventType != KEY_EVENT:
            continue
        key_event = record.Event.KeyEvent
        if _is_ime_commit_event(
            key_event.wVirtualKeyCode, key_event.uChar.UnicodeChar
        ):
            key_event.dwControlKeyState = 0


def _needs_patch(win32) -> bool:
    """只处理仍带有旧过滤条件的 Textual 版本；上游修好后自动跳过。"""
    try:
        source = inspect.getsource(win32.EventMonitor.run)
    except Exception:
        return False
    return "dwControlKeyState" in source and "not key" not in source


def apply_windows_ime_patch() -> bool:
    """安装 Windows IME 兼容补丁；返回是否已安装（或本来就不需要）。"""
    if sys.platform != "win32":
        return False

    try:
        from textual.drivers import win32
    except Exception:
        return False

    if getattr(win32, "_novabot_ime_patch", False):
        return True
    if not hasattr(win32, "KERNEL32") or not hasattr(win32.KERNEL32, "ReadConsoleInputW"):
        return False
    if not _needs_patch(win32):
        return False

    original = win32.KERNEL32.ReadConsoleInputW

    def patched(hConsoleInput, lpBuffer, nLength, lpNumberOfEventsRead):
        result = original(hConsoleInput, lpBuffer, nLength, lpNumberOfEventsRead)
        try:
            _clear_ime_modifier_noise(win32, lpBuffer, lpNumberOfEventsRead)
        except Exception as exc:  # 补丁失败不能影响 TUI 启动
            log.debug("Windows IME patch skipped: %s", exc)
        return result

    win32.KERNEL32.ReadConsoleInputW = patched
    win32._novabot_ime_patch = True
    log.info("已应用 Windows 中文输入法兼容补丁")
    return True
