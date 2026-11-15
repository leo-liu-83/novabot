# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : config_exception.py
# @Software    : VSCode
# @Datetime    : 2026/04/20 21:03:32
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from const.messages import msg
from .base_exception import ServiceError

# 配置文件异常
class ConfigFilesError(ServiceError):
    def __init__(self, err_desc: str=msg.MSG_CONFIG_FILE_ERROR):
        self.err_desc = err_desc