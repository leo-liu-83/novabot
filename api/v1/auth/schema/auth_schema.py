# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : auth_schema.py
# @Software    : VSCode
# @Datetime    : 2026/10/13 00:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : HTTP认证请求体

from typing import Optional

from pydantic import BaseModel, Field


class CredentialsSchema(BaseModel):
    """登录与注册共用：形状一致，语义由端点区分"""

    username: str = Field(description="用户名，1-32字符，允许中文，大小写敏感")
    password: str = Field(description="明文密码，至少6字符")


class TokenOut(BaseModel):
    """认证成功返回体"""

    token: str = Field(description="opaque token，后续请求放 Authorization: Bearer <token>")
    uid: str
    username: Optional[str] = None