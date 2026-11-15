# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : user_exception.py
# @Software    : VSCode
# @Datetime    : 2026/10/10 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from typing import Optional

from const.messages import msg
from .base_exception import ServiceError

class AuthError(ServiceError):
    def __init__(self, err_desc: str = msg.MSG_AUTH_ERROR, code: Optional[str] = None):
        self.err_desc = err_desc
        self.code = code