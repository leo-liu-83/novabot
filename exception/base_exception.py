# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : base_exception.py
# @Software    : VSCode
# @Datetime    : 2025/10/29 08:57:20
# @Author      : leo liu
# @Version     : 1.0
# @Description : 基础异常定义

from typing import Optional

from const.messages import msg

# 路由异常
class ViewError(Exception):
    def __init__(self, err_desc: str=msg.MSG_VIEW_ERROR):
        self.err_desc = err_desc

# 业务异常
class ServiceError(Exception):
    def __init__(self, err_desc: str=msg.MSG_SERVICE_ERROR, code: Optional[str]=None):
        self.err_desc = err_desc
        # 固定错误提示的稳定 code；原始异常文本不设 code（前端不做翻译）
        self.code = code

# 核心异常
class CoreError(Exception):
    def __init__(self, err_desc: str=msg.MSG_CORE_ERROR):
        self.err_desc = err_desc

# 插件异常
class PluginsError(Exception):
    def __init__(self, err_desc: str=msg.MSG_PLUGINS_ERROR):
        self.err_desc = err_desc

# 公共组建异常
class UtilsError(Exception):
    def __init__(self, err_desc: str=msg.MSG_UTILS_ERROR):
        self.err_desc = err_desc