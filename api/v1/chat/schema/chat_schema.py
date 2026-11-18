# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : chat_schema.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from typing import Optional, Literal

from pydantic import BaseModel, Field, field_validator

from core.llm.thinking import LEVELS, normalize_level

class ChatInputSchema(BaseModel):
    query: str = Field(..., description="用户问题")
    session_id: Optional[str] = Field(default=None, description="会话ID，缺省自动生成")
    model: Optional[str] = Field(default=None, description="provider/model，缺省取配置默认模型")
    thinking_level: Optional[str] = Field(
        default=None, description="思考档位：off/low/medium/high；缺省跟随模型默认")
    enable_thinking: Optional[bool] = Field(
        default=None, description="旧字段：等价于 thinking_level=medium(True)/off(False)")
    show_thinking: bool = Field(default=True, description="是否下发思考过程事件/字段（思考关闭时前端强制屏蔽）")
    streamable: bool = Field(default=True, description="是否流式返回")
    attachments: list[dict[str, str]] = Field(default_factory=list,
        description="Web 已上传附件的服务端 id/name；不接受物理路径，TUI 附件由本地表现层传入")

    @field_validator("thinking_level")
    def check_level(cls, value):
        if value is None or value == "":
            return None
        return normalize_level(value)

    @property
    def resolved_thinking(self) -> Optional[str]:
        """档位优先；老字段按 true=medium / false=off 折算。"""
        return normalize_level(self.thinking_level) or normalize_level(self.enable_thinking)

class ChatResumeSchema(BaseModel):
    session_id: str = Field(..., description="待恢复的会话ID")
    decision: Literal["approve", "reject", "always"] = Field(
        ..., description="对interrupt的决策；always = 批准并在本会话内记住这个具体对象")
    # 恢复时需与原请求一致，否则模型会回退到默认配置（thread上下文属于原调用）
    model: Optional[str] = Field(default=None, description="provider/model，与首次请求保持一致")
    thinking_level: Optional[str] = Field(default=None, description="与首次请求保持一致")
    enable_thinking: Optional[bool] = Field(default=None, description="旧字段：与首次请求保持一致")
    show_thinking: bool = Field(default=True, description="与首次请求保持一致；关闭思考后恢复也不应回显思考")
    # 不传=对所有未决interrupt生效；传了=只处理这一个（多个待确认时逐个确认用）
    interrupt_id: Optional[str] = Field(default=None, description="只恢复指定的interrupt，不传则全部恢复")

    @field_validator("thinking_level")
    def check_level(cls, value):
        if value is None or value == "":
            return None
        return normalize_level(value)

    @property
    def resolved_thinking(self) -> Optional[str]:
        return normalize_level(self.thinking_level) or normalize_level(self.enable_thinking)
