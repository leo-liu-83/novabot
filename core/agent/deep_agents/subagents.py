# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : subagents.py
# @Description : 子代理角色：定义、模型解析与 spec 构造

"""子代理角色。

角色定义与"怎么跑"解耦：这里只产出 deepagents 的 SubAgent spec。
今天的同步 `task` 工具直接用它们；将来做后台任务时复用同一份 spec，只换执行方式。
"""

from typing import Callable, Optional

from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.summarization import create_summarization_middleware

from config import config
from const.prompts import build_subagent_prompt
from core.llm.token_counter import count_tokens_cjk
from core.manager.llm_manager import llm_manager
from service.model_catalog import is_usable as _is_model_usable
from service.notice.sink import NoticeSink
from .exec_middleware import ExecToolPolicyMiddleware
from .exec_policy import ExecLevel
from .mcp_middleware import McpToolPolicyMiddleware
from .project_backend import ProjectPathMiddleware
from .step_limit import StepLimitMiddleware
from .web_middleware import WebFetchPolicyMiddleware

from const.subagents import DESCRIPTIONS, EXPLORE, GENERAL, ROLES, SPEC_NAME, TITLES

# explore 只拿只读文件工具（FilesystemMiddleware 的 FsToolName）
EXPLORE_FS_TOOLS = ("ls", "read_file", "glob", "grep")
# explore 允许的自定义工具：搜索/抓取/文档转换。run_skill_script、附件、MCP 一律不给
EXPLORE_CUSTOM_TOOLS = frozenset({"web_search", "web_fetch", "convert_document"})


def is_role(value: str) -> bool:
    return value in ROLES


def title_of(role: str) -> str:
    return TITLES.get(role, role)


def configured_model(role: str) -> str:
    """用户配置里该角色显式写的模型；空串表示跟随会话。"""
    data = config.CONFIG_DATA
    agent = data.agent if data else None
    subagents = agent.subagents if agent else None
    entry = (subagents or {}).get(role)
    return (entry.model if entry else "") or ""


def model_usable(model: str) -> bool:
    """不联网的可用性判断；实现放在 model_catalog，API 与组装层共用一份。"""
    return _is_model_usable(model)


def resolve_role_model(role: str, session_model: str,
                       notices: Optional[NoticeSink]=None) -> str:
    """角色模型：未配置或不可用都回退到会话模型；回退时每会话只提示一次。"""
    configured = configured_model(role)
    if not configured:
        return session_model
    if model_usable(configured):
        return configured
    if notices is not None:
        notices.add(
            f"subagent-model:{role}",
            f"子代理「{title_of(role)}」配置的模型不可用（{configured}），已回退到会话模型",
        )
    return session_model




def build_subagent_specs(*, session_model: str, ctx, backend, base_tools: list,
                         explore_tools: list, skills: Optional[list],
                         exec_level: ExecLevel, interrupt_on: dict, permissions: list,
                         project, session_id: str, project_block: str = "",
                         notices: Optional[NoticeSink]=None,
                         mcp_modes: Optional[dict]=None,
                         usage_sink: Optional[Callable[[str, str, int, int, float], None]]=None) -> list[dict]:
    """构造两个子代理的 spec；返回可直接交给 create_deep_agent 的 subagents。"""
    specs: list[dict] = []

    for role in ROLES:
        model_id = resolve_role_model(role, session_model, notices)
        llm = llm_manager.get_llm(model_id, session_id=session_id)
        if usage_sink is not None:
            # 子代理走的是嵌套 ainvoke，不会产生主会话的 usage 事件；由模型层回调记账
            llm.usage_hook = (
                lambda model, tokens_in, tokens_out, elapsed, _role=role:
                usage_sink(_role, model, tokens_in, tokens_out, elapsed)
            )

        middleware: list = []
        if project is not None:
            middleware.append(ProjectPathMiddleware(project.path))
        middleware.append(StepLimitMiddleware(config.AGENT_MAX_STEPS))
        if role == GENERAL:
            middleware.append(ExecToolPolicyMiddleware(
                exec_level, ctx, project_root=project.path if project is not None else None))
            middleware.append(McpToolPolicyMiddleware(mcp_modes or {}))
        middleware.append(WebFetchPolicyMiddleware(
            config.WEB_FETCH_PERMISSION, config.WEB_FETCH_ALLOWLIST))
        if role == EXPLORE:
            middleware.append(FilesystemMiddleware(
                backend=backend, tools=list(EXPLORE_FS_TOOLS), _permissions=permissions))
        # 摘要中间件必须按子代理自己的模型建：上下文窗口与归档都跟着它走
        middleware.append(create_summarization_middleware(
            llm, backend, token_counter=count_tokens_cjk))

        spec = {
            "name": SPEC_NAME[role],
            "description": ROLE_DESCRIPTIONS[role],
            "system_prompt": build_subagent_prompt(role, project_block),
            # general 继承主 agent 的工具；explore 用调用方给的只读子集
            "tools": list(base_tools) if role == GENERAL else list(explore_tools),
            "model": llm,
            "middleware": middleware,
        }
        if role == GENERAL:
            spec["skills"] = skills
            spec["interrupt_on"] = interrupt_on
            spec["permissions"] = permissions
        specs.append(spec)

    return specs


# 主模型看到的角色说明：写"什么时候用我/别用我"，比自我介绍更能降低误派
ROLE_DESCRIPTIONS = {
    EXPLORE: (
        f"{DESCRIPTIONS[EXPLORE]}。需要「先搞清楚代码在哪、怎么写的」时用它；"
        "它不能改文件、不能执行命令，需要修改或运行请交给 general。"
    ),
    GENERAL: (
        f"{DESCRIPTIONS[GENERAL]}。需要「已经知道要改什么」时用它；"
        "还不知道代码在哪就先派 explore。它不能再分派子代理。"
    ),
}
