# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : chat_enum.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 20:50:25
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from enum import IntEnum, Enum

class ThinkStatusEnum(IntEnum):
    # 未知类型
    UNKNOWN = 0
    # 开始思考
    THINK_START = 1
    # 思考中
    THINKING = 2
    # 思考结束
    THINK_END = 3
    # 正文
    CONTENT = 4

class EventTypeEnum(Enum):
    ANSWER = "answer"
    THINK = "think"
    TOOL_CALL = "tool_call"
    # 工具参数还在生成（write_file 的整份内容要逐块产出）：只报路径与规模，让卡片有进度
    TOOL_ARGS = "tool_args"
    TOOL_END = "tool_end"
    PROGRESS = "progress"
    USAGE = "usage"
    CONTEXT = "context"
    COMPACT = "compact"
    INTERRUPT = "interrupt"
    WARNING = "warning"
    ERROR = "error"

class EventFromTypeEnum(Enum):
    HOST = "host"
    TOOL = "tool"