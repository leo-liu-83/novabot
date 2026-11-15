# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : server.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : uvicorn入口

from api import create_app

app = create_app()
