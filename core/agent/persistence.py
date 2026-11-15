# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : persistence.py
# @Software    : VSCode
# @Datetime    : 2026/10/26 12:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 会话级持久化：LangGraph checkpoint + artifact 目录
"""把 agent 状态与会话产物绑到同一个 thread_id 上。

设计要点（见 docs/superpowers/specs/2026-10-26-会话压缩历史与agent状态持久化-design.md）：

- checkpoint 写进应用库（checkpoints/writes 是官方 saver 的表），不再用进程内 MemorySaver；
- 压缩历史、媒体、大工具结果落在 ``APP_DATA/sessions/<thread_key>/``，按 thread 隔离；
- 删除/过户只认 thread_id，因此不需要"会话 → 文件"的映射表；
- 第三方持久化包只在本文件出现，业务侧只用这里的封装。
"""

import atexit
import asyncio
import hashlib
import os
import re
import shutil
import sqlite3
import threading
from collections import OrderedDict
from typing import Any, Optional

from config import config
from logger import logger

# thread_id 里的 ':' 等字符不能直接做目录名；超长名字还会撞 NAME_MAX
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_MAX_KEY = 64

# 依赖缺失时本模块仍要可导入：startup() 会降级而不是让整个进程起不来
try:
    import aiosqlite
except ImportError:  # pragma: no cover - 依赖缺失只在降级场景出现
    aiosqlite = None  # type: ignore[assignment]

_saver: Any = None
_mode: str = "memory"
_saver_db_path: Optional[str] = None
_saver_loop: Optional[asyncio.AbstractEventLoop] = None

# 本进程激活过的 thread。checkpoint 降级（内存模式）时磁盘上什么都没有，
# 只有这个标记能告诉服务层"这段对话正在聊，别再回灌一遍"——LangGraph 的 input
# 是追加语义，重复回灌会让模型看到同一段对话的第二份。
MAX_LIVE_THREADS = 500
_LIVE: "OrderedDict[str, None]" = OrderedDict()
_live_lock = threading.Lock()


def mark_live(thread_id: str) -> None:
    """登记"这个 thread 在本进程正在用"。LRU 到 `MAX_LIVE_THREADS`，与占用登记同姿态。"""
    if not thread_id:
        return

    with _live_lock:
        _LIVE[thread_id] = None
        _LIVE.move_to_end(thread_id)
        while len(_LIVE) > MAX_LIVE_THREADS:
            _LIVE.popitem(last=False)


def is_live(thread_id: str) -> bool:
    with _live_lock:
        return thread_id in _LIVE


def has_state(thread_id: str, db_path: Optional[str] = None) -> bool:
    """这个 thread 有没有可续的上下文：本进程在用，或磁盘上有 checkpoint。"""
    return is_live(thread_id) or thread_exists(thread_id, db_path)


def reset_live_threads() -> None:
    """清空活性登记（测试用）。"""
    with _live_lock:
        _LIVE.clear()


def _stop_at_exit() -> None:
    """退出兜底：aiosqlite 的工作线程是非 daemon，不停止会让解释器等它。"""
    conn = getattr(_saver, "conn", None) if _saver is not None else None
    if conn is None:
        return

    try:
        conn.stop()
    except Exception:
        pass


atexit.register(_stop_at_exit)


def thread_key(thread_id: str) -> str:
    """把 thread_id 变成可做目录名的稳定键。

    发生字符替换、名字是 '.'/'..'、或长度超限时追加内容哈希，
    既避免 'u1:s' 与 'u1_s' 撞名，也避免路径穿越。
    """
    if not thread_id:
        raise ValueError("thread_id 不能为空")

    safe = _SAFE_CHARS.sub("_", thread_id)
    needs_hash = safe != thread_id or safe in (".", "..") or len(safe) > _MAX_KEY
    if not needs_hash:
        return safe

    digest = hashlib.sha1(thread_id.encode("utf-8")).hexdigest()[:8]
    prefix = safe[:_MAX_KEY].strip(".") or "thread"
    return f"{prefix}-{digest}"


def artifact_dir(thread_id: str) -> str:
    """会话产物根目录（压缩历史、媒体、大工具结果）。"""
    key = thread_key(thread_id)
    base = os.path.realpath(f"{config.APP_DATA_PATH}/sessions")
    target = os.path.realpath(os.path.join(base, key))
    if os.path.commonpath([base, target]) != base:
        raise ValueError(f"非法的 thread_id：{thread_id!r}")

    return target


def artifact_routes(thread_id: str) -> dict[str, str]:
    """虚拟路径 → 物理目录，交给 CompositeBackend 挂载。

    虚拟路径与 deepagents 的默认值保持一致，模型看到的路径不变。
    """
    root = artifact_dir(thread_id)
    return {
        "/conversation_history/": f"{root}/conversation_history",
        "/large_tool_results/": f"{root}/large_tool_results",
        # 用户给的文件（E10.4）：只读挂载。模型往附件目录写会让"用户给的输入"与
        # "agent 产出的东西"混成一堆，删除语义也就脏了（deny 见 Agent._get_permissions）
        "/attachments/": f"{root}/attachments",
    }


def attachment_dir(thread_id: str) -> str:
    """会话附件目录（物理路径）。随 purge_thread 一起删，不需要单独的清理策略。"""
    return f"{artifact_dir(thread_id)}/attachments"


async def startup(db_path: Optional[str] = None) -> bool:
    """在事件循环里初始化持久化 saver；失败返回 False 且不抛异常。

    AsyncSqliteSaver 构造时会抓当前 loop，所以必须在启动钩子里建一次并复用。
    """
    global _saver, _saver_db_path, _saver_loop, _mode

    path = db_path or config.db_file_path()
    loop = asyncio.get_running_loop()
    if _saver is not None and _saver_db_path == path and _saver_loop is loop:
        return True

    # 旧 saver 绑在旧 loop/旧库上，不能复用；先关掉，避免连接和线程泄漏
    if _saver is not None:
        await shutdown()

    if aiosqlite is None:
        _mode = "memory"
        logger.warning("aiosqlite 未安装，agent 状态退回内存模式（重启会丢上下文）")
        return False

    conn = None
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # aiosqlite 的工作线程默认非 daemon，且 connect() 一 await 就启动线程；
        # 解释器退出时会 join 它，而 TUI/CLI 的退出路径没人 await close()，进程就会挂住。
        # 因此必须在 await 之前把线程标成 daemon（pytest 收尾同理）。
        conn = aiosqlite.connect(path)
        worker = getattr(conn, "_thread", None)
        if worker is not None and not worker.is_alive():
            worker.daemon = True
        await conn

        saver = AsyncSqliteSaver(conn)
        await saver.setup()
    except Exception as exc:
        _mode = "memory"
        logger.warning(f"agent 状态持久化初始化失败，退回内存模式：{exc}")
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
        return False

    _saver, _saver_db_path, _saver_loop, _mode = saver, path, loop, "persistent"
    # 旧键形态的线程只可能来自升级前：库一通就清一次，之后不再有这种键
    purge_legacy_threads(path)
    return True


async def shutdown() -> None:
    """关闭 saver 连接；未初始化时是 no-op。"""
    global _saver, _saver_db_path, _saver_loop, _mode

    saver, _saver, _saver_db_path, _saver_loop, _mode = _saver, None, None, None, "memory"
    if saver is None:
        return

    try:
        await saver.conn.close()
    except Exception as exc:
        logger.warning(f"关闭 checkpoint 连接失败：{exc}")


def mode() -> str:
    """`"persistent"`（checkpoint 落库）或 `"memory"`（进程内，重启即丢）。

    降级本身是有意为之——库打不开时能聊比不能聊重要；不可接受的是**无声**。
    """
    return _mode


def is_persistent() -> bool:
    return _mode == "persistent"


def get_checkpointer() -> Any:
    """当前可用的 saver；未初始化（或已降级）时返回 None。"""
    return _saver


def thread_exists(thread_id: str, db_path: Optional[str] = None) -> bool:
    """该 thread 是否已有持久化 checkpoint。

    读失败一律当作"没有"，调用方据此回灌 DB 历史，不能因 IO 故障让会话打不开。
    """
    if not thread_id:
        return False

    path = db_path or config.db_file_path()
    if not os.path.exists(path):
        return False

    row = None
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (thread_id,)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False

    return row is not None


def _dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def purge_thread(thread_id: str, db_path: Optional[str] = None) -> int:
    """删除该 thread 的 checkpoint/writes 与产物目录，返回释放的字节数。

    DB 失败只记日志：会话行此时已经删了，不能因为清理失败把它"复活"。
    """
    if not thread_id:
        return 0

    with _live_lock:
        _LIVE.pop(thread_id, None)

    path = db_path or config.db_file_path()
    if os.path.exists(path):
        try:
            conn = sqlite3.connect(path)
            try:
                with conn:
                    conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
                    conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning(f"清理 checkpoint 失败（thread={thread_id}）：{exc}")

    target = artifact_dir(thread_id)
    freed = _dir_bytes(target)
    shutil.rmtree(target, ignore_errors=True)

    return freed


def purge_legacy_threads(db_path: Optional[str] = None) -> int:
    """清掉旧 `uid:session_id` 形态的 API 线程。

    会话键改成服务端签发的 uuid 之后，这些线程不再可达、也再没人能删：
    它们从没进过 `sessions` 表，注销账号覆盖不到。判据只有"键里含冒号"一条，
    新键是裸 uuid，因此天然幂等，也不会误伤本轮之后建立的会话。
    """
    path = db_path or config.db_file_path()
    if not os.path.exists(path):
        return 0

    try:
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE '%:%'"
            ).fetchall()
            with conn:
                conn.execute("DELETE FROM writes WHERE thread_id LIKE '%:%'")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning(f"旧线程扫描失败，跳过清理：{exc}")
        return 0

    freed = 0
    for (thread_id,) in rows:
        if thread_id and ":" in thread_id:
            freed += purge_thread(thread_id, db_path=path)

    if rows:
        logger.info(f"已清理旧 uid:* 线程 {len(rows)} 个，释放 {freed / 1024 / 1024:.1f} MB")

    return len(rows)
