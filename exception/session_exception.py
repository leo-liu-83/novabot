# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : session_exception.py
# @Software    : VSCode
# @Datetime    : 2026-10-31 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 会话归属异常

from const.messages import msg
from .base_exception import ServiceError

class SessionAccessError(ServiceError):
    """会话键不是服务端签发给当前身份的：拒绝，不静默新建。"""

    def __init__(self, err_desc: str = msg.MSG_SESSION_NOT_OWNED,
                 code: str = "session.access_denied"):
        self.err_desc = err_desc
        self.code = code
