# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : agent_enum.py
# @Software    : VSCode
# @Datetime    : 2026/04/27 15:40:05
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from enum import IntEnum

class AgentBackendTypeEnum(IntEnum):
    # 未知类型
    UNKNOWN = 0
    # StateBackend
    STATE = 1
    # FilesystemBackend
    FILESYSTEM = 2
    # LocalShellBackend
    LOCAL_SHELL = 3
    # StoreBackend
    STORE = 4
    # CompositeBackend
    COMPOSITE = 5
    # Sandbox
    SANDBOX = 6