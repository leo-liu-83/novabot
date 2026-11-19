# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : agent.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 21:39:07
# @Author      : leo liu
# @Version     : 1.0
# @Description :

import os
from typing import TYPE_CHECKING, Optional
from langchain.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.summarization import create_summarization_middleware

from enums import AgentBackendTypeEnum
from config import config
from const.prompts import build_system_prompt, render_project_block
from core.agent import persistence
from core.llm.token_counter import count_tokens_cjk
from core.llm.thinking import normalize_level
from core.manager.llm_manager import llm_manager
from logger import logger
from service.attachment.attachments import PendingImages
from service.notice.sink import NoticeSink
from service.user.user_service import UserCtx, get_current_ctx
from .exec_middleware import ExecToolPolicyMiddleware
from .attachment_middleware import AttachmentLifecycleMiddleware
from .attachment_tool import build_read_attachment_tool
from .document_tool import build_convert_document_tool
from .exec_policy import (
    ExecLevel,
    backend_for_level,
    is_skill_script_enabled,
    resolve_level,
)
from .mcp_tools import build_mcp_tools
from .mcp_middleware import McpToolPolicyMiddleware
from .project_backend import (
    ProjectFilesystemBackend, ProjectPathMiddleware, ProjectShellBackend,
)
from .skill_backend import (
    AttachmentGuardBackend,
    SkillGateBackend,
    SkillGateShellBackend,
    SkillUsageBackend,
)
from .step_limit import StepLimitMiddleware
from .subagent_gate import SubagentGateMiddleware
from .subagents import build_subagent_specs
from .skill_script import build_run_skill_script_tool
from .web_fetch import build_web_fetch_tool
from .web_middleware import WebFetchPolicyMiddleware
from .web_search import build_web_search_tool

if TYPE_CHECKING:
    from service.project.context import ProjectContext

# 只读挂载层：系统技能与共享技能对agent不可写；/skills/为各身份自有层，可写
READ_ONLY_SOURCES = ("/system_skills/", "/shared_skills/")
# 用户给的附件（E10.4）：一律只读——它是输入，不是agent的产出区
ATTACHMENTS_MOUNT = "/attachments/"
# system技能仓库自带、可信，不做审查过滤；其余层（shared/private）过滤未approve的技能
SYSTEM_SKILLS_MOUNT = "/system_skills/"


def skill_sources(ctx: UserCtx, backend) -> Optional[list[str]]:
    """技能发现只认身份的技能挂载，不能把会话产物目录当成技能来源。

    backend.routes 里还挂着 /conversation_history/ 与 /large_tool_results/，
    它们是模型可写的普通文件目录；若混进 skills，deepagents 会把里面的
    SKILL.md 当技能加载，绕过技能门。
    """
    if not isinstance(backend, CompositeBackend):
        return None

    return [mount for mount, _ in ctx.skill_mounts]


class Agent:
    """
    基于langchain DeepAgents实现
    """
    # 进程级共享checkpointer，保证同thread_id的会话及interrupt-resume可持续
    _shared_checkpointer = MemorySaver()

    def __init__(self, model: Optional[str]=None, thinking_level: Optional[str]=None,
                 streamable: bool=False,
                 backend_type: AgentBackendTypeEnum=AgentBackendTypeEnum.FILESYSTEM,
                 enable_thinking: Optional[bool]=None):
        # 未指定模型时取配置中的默认Agent模型
        self.model = model or (config.CONFIG_DATA.agent.model if config.CONFIG_DATA and config.CONFIG_DATA.agent else "")
        # 档位优先；老调用方传的布尔按 true=medium / false=off 折算
        self.thinking_level = normalize_level(thinking_level) or normalize_level(enable_thinking)
        self.streamable = streamable
        self.backend_type = backend_type
        # 构建后可读：服务层用它推导上下文预算，避免再解析一遍模型配置
        self.max_input_tokens: Optional[int] = None
        self.max_output_tokens: int = 0
        # 手动压缩（/compact）要复用同一个摘要引擎：模型、backend、历史归档路径都挂在它上面
        self.summarization = None

    def get_agent(self, prompt: Optional[str]=None, tools: list[BaseTool]=[], request: Optional[dict]=None,
                  ctx: Optional[UserCtx]=None, session_id: Optional[str]=None,
                  notices: Optional[NoticeSink] = None, thread_id: Optional[str]=None,
                  pending_images: Optional[PendingImages]=None,
                  project: "Optional[ProjectContext]"=None,
                  usage_sink=None):
        # API侧thread_id带uid前缀、TUI就是session_id；产物目录按thread_id派生，必须与checkpoint同名
        thread_id = thread_id or session_id
        # API并发场景下身份不能取全局单例（多请求会互相覆盖），故ctx由调用方显式传入
        ctx = ctx or get_current_ctx()
        # 图片只活在本次请求：服务层持有同一个实例，422 时清空、read_attachment 追加
        pending = pending_images if pending_images is not None else PendingImages()
        llm = llm_manager.get_llm(self.model, self.thinking_level, session_id=session_id)
        self.max_input_tokens = getattr(llm, "max_input_tokens", None)
        self.max_output_tokens = getattr(llm, "max_tokens", 0) or 0
        # 能力使用统计的落库回调：技能（SKILL.md/脚本）、MCP 调用、网络搜索各算一次。
        # 统计失败只记日志，绝不影响对话本身
        skill_usage = self._skill_usage_sink(ctx, session_id)
        tool_usage = self._tool_usage_sink(ctx, session_id)
        backend = self._get_backend(ctx, session_id, thread_id, project,
                                    on_skill_used=skill_usage)
        exec_level = resolve_level(config.EXEC_LEVEL, ctx, session_id,
                                   project_mode=project is not None)

        # T1才挂run_skill_script；T2+走execute通道，两条路互斥（见exec_policy）
        tools = list(tools)
        if is_skill_script_enabled(exec_level):
            tools.append(build_run_skill_script_tool(ctx, on_skill_used=skill_usage))

        # 网络能力不参与执行档位（E8）：搜索全身份可用，配额在服务层按uid判
        if config.WEB_SEARCH_ENABLED:
            tools.append(build_web_search_tool(ctx, notices,
                                               on_used=lambda source: tool_usage("search", source)))
        if config.WEB_FETCH_PERMISSION != "deny":
            tools.append(build_web_fetch_tool())

        # 文档转换（E10.4b）：PDF/Office 转 Markdown。能力本身不分档位（它是"读文件内容的
        # 另一种方式"，不是执行能力），但绝对路径要跟着档位走：
        # 绝对路径转换是"静默读取任意本机文件"，本工具没有逐次确认，
        # 所以只在 T4（明示全责）放开；T0–T3 只认附件与工作区，否则这里就是绕过虚拟根的后门。
        # 没装 firecrawl-anydoc 时工具仍在，返回如实的"未安装"，不让模型对读不到的格式瞎猜。
        tools.append(build_convert_document_tool(
            ctx, thread_id, allow_absolute=(exec_level == ExecLevel.ALLOW_ALL)
        ))
        # 附件收起后的重读通道：只认 /attachments/ 与工作区，绝对路径不放开
        tools.append(build_read_attachment_tool(ctx, thread_id, pending))

        # MCP工具：连接server、发现工具。逐工具的 allow/ask/deny 在调用点判（见 mcp_middleware）。
        # 与执行档位无关——MCP是独立的"远端能力"轴（见TODO E9.2）
        mcp_tools, mcp_modes, _mcp_notes = build_mcp_tools(
            ctx, notices, on_used=lambda server, tool: tool_usage("mcp", server, tool))
        tools.extend(mcp_tools)

        # 从backend实际挂载的技能路由推导sources，插入顺序即优先级：system在前，user可覆盖同名系统技能
        skills = skill_sources(ctx, backend)

        # 与界面占用共用 CJK 计数器：中文按 1 token/字估，避免压缩在 80% 前不触发。
        # 实例留存给 /compact：手动压缩必须与自动压缩共用同一个事件键与归档目录，否则会互相覆盖
        summarization = create_summarization_middleware(llm, backend, token_counter=count_tokens_cjk)
        self.summarization = summarization

        project_section = None
        if project is not None:
            # 延迟 import：context 模块会持有 project_service 单例，不能在 Agent 模块导入时就建库
            from service.project.context import build_project_section

            project_section = build_project_section(project, ctx)

        permissions = self._get_permissions(ctx, project=project, uid=ctx.uid,
                                            session_id=session_id)
        interrupt_on = self._interrupt_tools(exec_level, ctx, session_id, project)

        # 探索子代理自己一份只读工具集：文档转换不允许绝对路径（那是 T4 才放开的能力，
        # 只读角色不该因为主会话是 T4 就跟着获得）
        explore_tools: list = []
        if config.WEB_SEARCH_ENABLED:
            # 子代理的搜索同样计入本会话：报表看的是"会话用了什么能力"，不分主/子
            explore_tools.append(build_web_search_tool(
                ctx, notices, on_used=lambda source: tool_usage("search", source)))
        if config.WEB_FETCH_PERMISSION != "deny":
            explore_tools.append(build_web_fetch_tool())
        explore_tools.append(build_convert_document_tool(ctx, thread_id, allow_absolute=False))

        subagents = build_subagent_specs(
            session_model=self.model,
            ctx=ctx,
            backend=backend,
            base_tools=tools,
            explore_tools=explore_tools,
            skills=skills,
            exec_level=exec_level,
            interrupt_on=interrupt_on,
            permissions=permissions,
            project=project,
            session_id=session_id or "",
            project_block=render_project_block(project_section) if project_section is not None else "",
            notices=notices,
            mcp_modes=mcp_modes,
            usage_sink=usage_sink,
        )

        return create_deep_agent(
            model=llm,
            tools=tools,
            system_prompt=prompt or build_system_prompt(
                user_prompt=self._read_text_file(ctx.resolve_prompt_path()),
                soul=self._read_text_file(ctx.soul_path),
                profile=self._read_text_file(ctx.user_profile_path),
                enable_persona=ctx.can_persona,
                enable_memory=ctx.can_memory,
                memory_core=self._read_text_file(ctx.memory_core_path),
                project=project_section,
            ),
            skills=skills,
            backend=backend,
            permissions=permissions,
            subagents=subagents,
            middleware=[
                # 项目路径归一必须在 HITL 与工具执行之前
                *([ProjectPathMiddleware(project.path)] if project is not None else []),
                # 轮数上限放最外层：它摘工具时要把内层中间件的工具也一并摘掉
                StepLimitMiddleware(config.AGENT_MAX_STEPS),
                # 子代理闸门：并发、单轮预算、超时（只认 task 工具，其余放行）
                SubagentGateMiddleware(session_id or ""),
                ExecToolPolicyMiddleware(exec_level, ctx,
                                         project_root=project.path if project is not None else None),
                WebFetchPolicyMiddleware(config.WEB_FETCH_PERMISSION, config.WEB_FETCH_ALLOWLIST),
                McpToolPolicyMiddleware(mcp_modes),
                # 附件只在一轮内内联：更早的收起为引用；本轮图片只在请求副本里注入
                AttachmentLifecycleMiddleware(pending_images=pending),
                # 与界面占用共用 CJK 计数器：中文按 1 token/字估，避免压缩在 80% 前不触发
                summarization,
            ],
            checkpointer=persistence.get_checkpointer() or self._shared_checkpointer,
            interrupt_on=interrupt_on
        )

    # T3逐条确认：execute进interrupt_on，由TUI/API的确认栏决定放行还是中止。
    # 用相等而非>=：T4按定义不确认任何命令，加了execute打断会让"免确认"变成谎话。
    # 硬黑名单与inline-eval仍由中间件把关，两者都不过interrupt。
    @staticmethod
    def _interrupt_tools(exec_level: ExecLevel, ctx: Optional[UserCtx]=None,
                         session_id: Optional[str]=None,
                         project: "Optional[ProjectContext]"=None) -> dict:
        if project is not None and ctx is not None and session_id:
            from .project_policy import build_project_interrupt_on

            return build_project_interrupt_on(exec_level, ctx, session_id, project)

        if exec_level == ExecLevel.CONFIRM_ALL:
            return {**config.AGENT_INTERRUPT_TOOLS, "execute": True}

        return dict(config.AGENT_INTERRUPT_TOOLS)

    # 个性化文件（prompt.md/SOUL.md/USER.md）每次构建时读取，改文件即时生效；缺失或为空则省略对应段
    @staticmethod
    def _read_text_file(path: Optional[str]) -> Optional[str]:
        if not path:
            return None

        try:
            if not os.path.exists(path):
                return None

            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    # default档无persona能力：soul/user_profile为None，onboarding段因此不会被注入
    @staticmethod
    def _get_permissions(ctx: UserCtx, project: "Optional[ProjectContext]"=None,
                         uid: Optional[str]=None,
                         session_id: Optional[str]=None) -> list[FilesystemPermission]:
        if project is not None:
            from .project_policy import build_project_permissions

            return build_project_permissions(ctx, project, uid or ctx.uid, session_id or "")

        denied = [f"{mount}**" for mount in ctx.skills_sources if mount in READ_ONLY_SOURCES]
        # 附件只读（E10.4）：那是用户给模型的输入。模型往附件目录写，会把"用户给的"与
        # "agent 产出的"混在一个目录里，删除语义与"这份内容是谁写的"都再也说不清
        denied.append(f"{ATTACHMENTS_MOUNT}**")

        return [FilesystemPermission(operations=["write"], paths=denied, mode="deny")] if denied else []

    @staticmethod
    def _tool_usage_sink(ctx: UserCtx, session_id: Optional[str]):
        """MCP / 网络搜索的写库回调。

        name 语义由 kind 决定：mcp 是 server 名，search 是实际生效的档位:provider
        （user:exa / nova-global:parallel / 内置名）。
        """
        uid = ctx.uid
        sid = session_id or ""

        def record(kind: str, name: str, detail: str = "") -> None:
            if not sid or not name:
                return
            try:
                from service.session.session_service import session_service

                session_service.record_tool_usage(
                    kind=kind, name=name, detail=detail, uid=uid, session_id=sid,
                )
            except Exception as exc:  # noqa: BLE001 - 统计失败不影响对话
                logger.warning(f"能力使用统计写入失败（{kind}:{name}）：{exc}")

        return record

    @staticmethod
    def _skill_usage_sink(ctx: UserCtx, session_id: Optional[str]):
        """技能使用写库回调。

        签名与两个调用点对齐：backend 只传技能名（read），run_skill_script 额外传
        action="script"。session_id 缺失（未落到会话）时不记——报表按会话/用户聚合，
        没有会话的行只会污染口径。
        """
        uid = ctx.uid
        sid = session_id or ""

        def record(skill: str, action: str = "read") -> None:
            if not sid:
                return
            try:
                # 延迟 import：agent 模块在服务层构建之前就会被导入（TUI/API 共用）
                from service.session.session_service import session_service

                session_service.record_skill_usage(
                    skill=skill, uid=uid, session_id=sid, action=action,
                )
            except Exception as exc:  # noqa: BLE001 - 统计失败不影响对话
                logger.warning(f"技能使用统计写入失败（{skill}）：{exc}")

        return record

    # 按当前身份ctx构建Backend：挂载表与只读策略全部来自ctx，不读模块级路径常量
    def _get_backend(self, ctx: UserCtx, session_id: Optional[str]=None,
                     thread_id: Optional[str]=None,
                     project: "Optional[ProjectContext]"=None,
                     on_skill_used=None):
        project_root = project.path if project is not None else None
        if project_root is None:
            os.makedirs(ctx.workspace_path, exist_ok=True)

        # 档位封顶：显式请求LOCAL_SHELL时，实际用哪个后端由生效档位决定；
        # 档位未开启执行则降级回FILESYSTEM（该后端无execute能力）
        backend_type = self.backend_type
        if backend_type == AgentBackendTypeEnum.LOCAL_SHELL:
            # 必须与中间件用同一个resolve_level，否则会话覆盖会让后端与判定层档位不一致
            backend_type = backend_for_level(resolve_level(config.EXEC_LEVEL, ctx, session_id))

        if backend_type == AgentBackendTypeEnum.STATE:
            return StateBackend()

        if project_root is not None:
            # 项目模式：绝对路径按真实文件系统解析（virtual_mode=False），**不 makedirs 项目目录**
            if backend_type == AgentBackendTypeEnum.FILESYSTEM:
                default = ProjectFilesystemBackend(root_dir=project_root, virtual_mode=False,
                                                   skill_roots=(), project_root=project_root)
            elif backend_type == AgentBackendTypeEnum.LOCAL_SHELL:
                default = ProjectShellBackend(root_dir=project_root, virtual_mode=False,
                                              skill_roots=(), project_root=project_root)
            else:
                raise ValueError(f"暂不支持的Backend类型: {backend_type}")
        elif backend_type == AgentBackendTypeEnum.FILESYSTEM:
            # 默认backend的根就是工作区，而技能目录物理上在工作区之下（workspace/skills），
            # 它也必须挂门——否则从工作区根 glob/grep 就能绕过路由直达未放行技能
            default = SkillGateBackend(root_dir=ctx.workspace_path, virtual_mode=True,
                                       skill_roots=(f"{ctx.workspace_path}/skills",),
                                       on_skill_used=on_skill_used)
        elif backend_type == AgentBackendTypeEnum.LOCAL_SHELL:
            default = SkillGateShellBackend(root_dir=ctx.workspace_path, virtual_mode=True,
                                            skill_roots=(f"{ctx.workspace_path}/skills",),
                                            on_skill_used=on_skill_used)
        else:
            raise ValueError(f"暂不支持的Backend类型: {backend_type}")

        routes = {}
        for mount, path in ctx.skill_mounts:
            os.makedirs(path, exist_ok=True)
            if mount == SYSTEM_SKILLS_MOUNT:
                # 系统技能全部放行，只记使用；套放行门会让未审查的脚本技能凭空消失
                routes[mount] = SkillUsageBackend(root_dir=path, virtual_mode=True,
                                                  skill_roots=(path,),
                                                  on_skill_used=on_skill_used)
            else:
                routes[mount] = SkillGateBackend(root_dir=path, virtual_mode=True,
                                                 skill_roots=(path,),
                                                 on_skill_used=on_skill_used)

        # 压缩历史/媒体/大工具结果按会话隔离：虚拟路径不变，物理落到会话目录，
        # 删除与过户只需认thread_id，不必再维护"会话→文件"的映射
        if thread_id:
            try:
                mounted = {}
                for mount, path in persistence.artifact_routes(thread_id).items():
                    os.makedirs(path, exist_ok=True)
                    backend_cls = AttachmentGuardBackend if mount == ATTACHMENTS_MOUNT else FilesystemBackend
                    mounted[mount] = backend_cls(root_dir=path, virtual_mode=True)
                routes.update(mounted)
            except (OSError, ValueError) as exc:
                # 存储不可写/路径非法只该让产物目录落空，不能把整轮对话打成500
                logger.warning(f"会话产物目录不可用，跳过挂载（thread={thread_id}）：{exc}")

        return CompositeBackend(default=default, routes=routes)
