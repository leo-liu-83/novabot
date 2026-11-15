# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : model_enum.py
# @Software    : VSCode
# @Datetime    : 2026/04/28 10:27:06
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from enum import Enum

class ModelApiTypeEnum(Enum):
    # 未知类型
    UNKNOWN = "none"
    # OpenAI
    OPENAI = "openai-completions"
    # Anthropic
    ANTHROPIC = "anthropic"