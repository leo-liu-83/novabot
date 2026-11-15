# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : errors.py
# @Description : 项目领域的可预期失败


class ProjectError(Exception):
    """项目领域的可预期失败；code 与 api/resp/errors.py 的键一一对应。

    服务层不抛 HTTPException：视图层拿到 code 与 vars 后再翻译成 HTTP 错误，
    免得项目服务依赖 Web 层，也让 TUI 能复用同一套失败语义。
    """

    def __init__(self, code: str, **vars) -> None:
        super().__init__(code)
        self.code = code
        self.vars = vars
