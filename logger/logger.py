# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : logger.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 15:39:34
# @Author      : leo liu
# @Version     : 1.0
# @Description :

import os
from loguru import logger
from config import config

# 定位到log日志文件
base_path = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(base_path, "logs")
if not os.path.exists(log_path):
    os.mkdir(log_path)

log_level = config.APP_LOG_LEVEL if hasattr(config, "APP_LOG_LEVEL") and config.APP_LOG_LEVEL else "DEBUG"
log_rotation = config.APP_LOG_ROTATION if hasattr(config, "APP_LOG_ROTATION") and config.APP_LOG_ROTATION else "1"
log_retention = config.APP_LOG_RETENTION if hasattr(config, "APP_LOG_RETENTION") and config.APP_LOG_RETENTION else "5 days"
log_path = config.APP_LOG_PATH if hasattr(config, "APP_LOG_PATH") and config.APP_LOG_PATH else log_path
log_file = f"{config.APP_LOG_NAME}.log" if hasattr(config, "APP_LOG_NAME") and config.APP_LOG_NAME else "novabot.log"
log_enqueue = True

log_full_path = os.path.join(log_path, log_file)
# level 必须传：loguru 的 file sink 默认是 DEBUG，配置里的 APP_LOG_LEVEL 就成了摆设
logger.add(log_full_path, rotation=log_rotation, retention=log_retention,
           level=log_level, enqueue=log_enqueue)

# loguru 导入时会自带一个写 stderr 的彩色 sink。TUI 全屏接管终端后它仍在，
# 每条日志都会在边框两侧漏出一小段彩色文字，还会打断 Textual 的整屏重绘（看着像卡顿）。
CONSOLE_SINK_ID = 0


def silence_console() -> bool:
    """摘掉 loguru 自带的 stderr sink；全屏 TUI 启动前调用。

    返回是否真的摘掉了。重复调用安全（第二次起返回 False）。
    文件 sink 不受影响——日志只是不再往终端吐。
    """
    try:
        logger.remove(CONSOLE_SINK_ID)
        return True
    except ValueError:
        return False


__all__ = ["logger", "silence_console", "CONSOLE_SINK_ID"]
