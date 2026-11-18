# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : subagents.py
# @Description : 子代理角色的对外元数据（API/Web/TUI 共用）

"""子代理角色的元数据。

放在 `const/` 是为了让 API 与前端不必导入 `core.agent.deep_agents` 的重依赖
（deepagents、llm_manager）；角色行为在 `core/agent/deep_agents/subagents.py`。
"""

EXPLORE = "explore"
GENERAL = "general"

ROLES = (EXPLORE, GENERAL)

# deepagents 用 general-purpose 这个槽位判定"已有 general"，从而不再追加它自带的默认角色
SPEC_NAME = {EXPLORE: "explore", GENERAL: "general-purpose"}

TITLES = {EXPLORE: "探索", GENERAL: "通用"}

DESCRIPTIONS = {
    EXPLORE: "只读查资料、跨文件定位",
    GENERAL: "改代码、跑命令、多步骤执行",
}
