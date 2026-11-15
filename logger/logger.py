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
logger.add(log_full_path, rotation=log_rotation, retention=log_retention, enqueue=log_enqueue)

__all__ = ["logger"]