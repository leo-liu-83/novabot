# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : base.py
# @Software    : VSCode
# @Datetime    : 2026/04/20 20:47:19
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from pathlib import Path

class BaseUtil:
    def __init__(self):
        self.root_path = self._get_root_path()

    def _get_root_path(self) -> Path:
        # util的上一级即项目根目录
        return Path(__file__).resolve().parent.parent

base_util = BaseUtil()