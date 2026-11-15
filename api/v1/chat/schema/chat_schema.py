# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : chat_schema.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from typing import Optional, Literal

from pydantic import BaseModel, Field

class ChatInputSchema(BaseModel):
    query: str = Field(..., description="用户问题")
    session_id: Optional[str] = Field(default=None, description="会话ID，缺省自动生成")
    model: Optional[str] = Field(default=None, description="provider/model，缺省取配置默认模型")
    enable_thinking: Optional[bool] = Field(default=None, description="前端控制深度思考开关，缺省取模型定义配置")
    show_thinking: bool = Field(default=True, description="是否下发思考过程事件/字段（部分模型不遵守enable_thinking时由前端强制屏蔽）")
    streamable: bool = Field(default=True, description="是否流式返回")
    attachments: list[dict[str, str]] = Field(default_factory=list,
        description="Web 已上传附件的服务端 id/name；不接受物理路径，TUI 附件由本地表现层传入")

class ChatResumeSchema(BaseModel):
    session_id: str = Field(..., description="待恢复的会话ID")
    decision: Literal["approve", "reject", "always"] = Field(
        ..., description="对interrupt的决策；always = 批准并在本会话内记住这个具体对象")
    # 恢复时需与原请求一致，否则模型会回退到默认配置（thread上下文属于原调用）
    model: Optional[str] = Field(default=None, description="provider/model，与首次请求保持一致")
    enable_thinking: Optional[bool] = Field(default=None, description="与首次请求保持一致")
    show_thinking: bool = Field(default=True, description="与首次请求保持一致；关闭思考后恢复也不应回显思考")
    # 不传=对所有未决interrupt生效；传了=只处理这一个（多个待确认时逐个确认用）
    interrupt_id: Optional[str] = Field(default=None, description="只恢复指定的interrupt，不传则全部恢复")
