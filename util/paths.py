# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : paths.py
# @Description : 应用数据目录与数据库文件定位（CLI 与 config 共用）

"""库文件定位。

`config/base_config.py` 在 import 时就要用它，而 `util/secret_cli.py` 需要在不加载
整套配置的前提下找到同一个库，所以单独放一层，两边共用一个实现。
"""

import os
import sys

# 库名曾叫 sessions.db，但库里早已不止会话：users / auth_tokens / skill_reviews /
# web_quotas / web_usage / user_web_providers 都在同一文件里。改名与 novabot.json 对齐。
DB_FILE_NAME = "novabot.db"
LEGACY_DB_FILE_NAME = "sessions.db"


def resolve_db_file_path(data_dir: str) -> str:
    """定位应用数据库，并把存量 sessions.db 原子改名为 novabot.db。

    改名而非只改常量：直接换名会让老用户的库变成孤儿文件，表现为"数据全没了"。
    改名失败时回落到旧路径，宁可名字不好看也不能让数据孤儿化。
    """
    target = f"{data_dir}/{DB_FILE_NAME}"
    legacy = f"{data_dir}/{LEGACY_DB_FILE_NAME}"

    if os.path.exists(target) or not os.path.exists(legacy):
        return target

    try:
        os.replace(legacy, target)
    except OSError as err:
        # 配置加载早于日志系统就绪，只能打 stderr
        print(f"[CONFIG] 数据库改名失败，继续使用旧文件 {legacy}: {err}", file=sys.stderr)
        return legacy

    return target


def default_data_dir() -> str:
    """~/.novabot/data，与 BaseConfig.db_dir() 同口径（不 import config，避免循环）。"""
    return os.path.join(os.path.expanduser("~"), ".novabot", "data")


def db_path_from(data_dir: str | None = None) -> str:
    return resolve_db_file_path(data_dir or default_data_dir())
