# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : model_exception.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 20:55:56
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from const.messages import msg
from .base_exception import CoreError

class ModelPropError(CoreError):
    def __init__(self, err_desc = msg.MSG_MODEL_PROP_ERROR):
        self.err_desc = err_desc

class ModelPromptError(CoreError):
    def __init__(self, err_desc = msg.MSG_PROMPT_ERROR):
        self.err_desc = err_desc

class ModelCallError(CoreError):
    def __init__(self, err_desc = msg.MSG_MODEL_CALL_ERROR):
        self.err_desc = err_desc


class ModelUnsupportedContentError(ModelCallError):
    """模型端点拒绝了内容（典型是纯文本模型收到图片块，HTTP 422）。"""

    def __init__(self, err_desc: str = "当前模型不支持读取该内容（如图片）。"
                 "请换用支持相应能力的模型，或移除该内容后重试。"):
        self.err_desc = err_desc