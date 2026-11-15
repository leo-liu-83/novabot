# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : base_config.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 15:32:41
# @Author      : leo liu
# @Version     : 1.0
# @Description :

import os
import sys
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings

from service.config.config_service import ConfigService
from service.config.dtos import *
from core.agent.deep_agents.exec_policy import ExecLevel, parse_level
from service.web.web_policy import parse_permission

# 库名曾叫 sessions.db，但库里早已不止会话：users / auth_tokens / skill_reviews /
# web_quotas / web_usage / user_web_providers 都在同一文件里。改名与 novabot.json 对齐。
DB_FILE_NAME = "novabot.db"
LEGACY_DB_FILE_NAME = "sessions.db"


def resolve_db_file_path(data_dir: str) -> str:
    """定位应用数据库，并把存量 sessions.db 原子改名为 novabot.db。

    改名而非只改常量：直接换名会让老用户的库变成孤儿文件，表现为"数据全没了"。
    改名失败时回落到旧路径，宁可名字不好看也不能让数据孤儿化。
    """
    target = f"{data_dir}/{DB_FILE_NAME}"
    legacy = f"{data_dir}/{LEGACY_DB_FILE_NAME}"

    if os.path.exists(target) or not os.path.exists(legacy):
        return target

    try:
        os.replace(legacy, target)
    except OSError as err:
        # 配置加载早于日志系统就绪，只能打stderr
        print(f"[CONFIG] 数据库改名失败，继续使用旧文件 {legacy}: {err}", file=sys.stderr)
        return legacy

    return target


class BaseConfig(BaseSettings):
    # 应用基础信息
    APP_ENV: str = "dev"  # 环境变量
    APP_NAME: str = "NovaBot"  # 应用名称
    APP_DESC: str = "Nova智能体"  # 应用描述
    APP_VERSION: str = "0.1.0"  # 应用版本
    APP_LOG_PATH: str = ""  # 应用日志目录
    APP_LOG_NAME: str = "nova_bot"  # 应用日志文件名
    APP_LOG_ROTATION: str = "5"  # 应用日志文件切换大小
    APP_LOG_RETENTION: str = "7 days"  # 应用日志存储天数
    APP_LOG_LEVEL: str = "INFO"  # 应用日志等级
    APP_DATA_PATH: str = f"{Path.home()}/.novabot"  # 应用数据目录

    def db_dir(self) -> str:
        """应用库目录——实时算，不在import时定死。

        早先这里是 `APP_DB_PATH: str = f"{APP_DATA_PATH}/data"` 这样的类属性：
        import时算一次就冻住了，之后改 APP_DATA_PATH 不会跟着变。
        单例服务若在构造时把路径钉死，就永远指向那一个库——测试会写进真库。
        """
        return f"{self.APP_DATA_PATH}/data"

    def db_file_path(self) -> str:
        """应用库全路径——会话/账号/技能审查/网络配置同库，各service统一取这里。"""
        return resolve_db_file_path(self.db_dir())

    # 用户数据
    USER_WORKSPACE_PATH: str = "workspace"  # 用户数据目录
    USER_CONFIG_FILE: str = "novabot.json"  # 用户配置文件
    USER_PROMPT_FILE: str = "prompt.md"  # 用户自定义提示词（可选，嵌入核心提示词槽位）

    # 配置数据
    CONFIG_DATA: Optional[ConfigDto] = ConfigService(user_config_path=f"{APP_DATA_PATH}/{USER_CONFIG_FILE}").load()

    # 多用户相关配置：用户配置未显式给出时取默认值（单用户、记住登录）
    MULTI_USER: bool = bool(CONFIG_DATA.multi_user) if CONFIG_DATA else False
    REMEMBER_LOGIN: bool = (
        CONFIG_DATA.remember_login
        if CONFIG_DATA and CONFIG_DATA.remember_login is not None
        else True
    )

    # 模型相关配置
    MODEL_CHAT_SUFFIX: str = "/chat/completions"
    # 模型未配置num_ctx时的默认最大输入token数。
    # 默认值固化在config/config.json的llm.max_input_tokens，缺失时用同一安全值兜底。
    LLM_MAX_INPUT_TOKENS: int = (
        CONFIG_DATA.llm.max_input_tokens
        if CONFIG_DATA and CONFIG_DATA.llm and CONFIG_DATA.llm.max_input_tokens is not None
        else 131072
    )

    # 附件限制：默认值固化在 config/config.json 的 attachments 段
    ATTACHMENT_MAX_PER_MESSAGE: int = (
        CONFIG_DATA.attachments.max_per_message
        if CONFIG_DATA and CONFIG_DATA.attachments
        and CONFIG_DATA.attachments.max_per_message is not None
        else 5
    )
    ATTACHMENT_MAX_FILE_MB: int = (
        CONFIG_DATA.attachments.max_file_mb
        if CONFIG_DATA and CONFIG_DATA.attachments
        and CONFIG_DATA.attachments.max_file_mb is not None
        else 10
    )
    ATTACHMENT_MAX_FILE_BYTES: int = ATTACHMENT_MAX_FILE_MB * 1024 * 1024
    ATTACHMENT_ADMIN_UNLIMITED: bool = (
        CONFIG_DATA.attachments.admin_unlimited
        if CONFIG_DATA and CONFIG_DATA.attachments
        and CONFIG_DATA.attachments.admin_unlimited is not None
        else True
    )
    ATTACHMENT_DRAFT_TTL_DAYS: int = (
        CONFIG_DATA.attachments.draft_ttl_days
        if CONFIG_DATA and CONFIG_DATA.attachments
        and CONFIG_DATA.attachments.draft_ttl_days is not None
        else 7
    )

    # Agent相关配置
    AGENT_WORKSPACE_PATH: str = f"{APP_DATA_PATH}/{USER_WORKSPACE_PATH}"  # default档Agent工作目录（backend根目录）
    AGENT_USER_PROMPT_PATH: str = f"{APP_DATA_PATH}/{USER_PROMPT_FILE}"  # 公共自定义指令文件绝对路径
    AGENT_SYSTEM_SKILLS_PATH: str = "skills"  # 系统技能目录（相对项目根）
    AGENT_INTERRUPT_TOOLS: dict = {"write_file": True, "edit_file": True, "delete": True}  # 需要人工确认的工具

    # agent单次运行的模型调用轮数上限（None=不限）。到顶时摘掉工具逼模型用文本收尾
    AGENT_MAX_STEPS: Optional[int] = (
        CONFIG_DATA.agent.max_steps if CONFIG_DATA and CONFIG_DATA.agent else None
    )

    # 脚本执行档位：**服务级封顶**——本部署最高允许到哪一档。
    # 各身份的默认档位（单用户T1 / admin T3 / 普通用户T0）与天花板都要过它收敛；
    # `exec.level=T3` 的部署里，admin 最多也只能授予 T3。
    # 未显式配置时取 T4（不额外压顶）；解析失败按最严（OFF）。
    # 实际生效档位见 core/agent/deep_agents/exec_policy.py
    EXEC_LEVEL: ExecLevel = parse_level(
        CONFIG_DATA.exec.level
        if CONFIG_DATA and CONFIG_DATA.exec and CONFIG_DATA.exec.level is not None
        else "T4"
    )

    # MCP（E9）：系统层server全局共享、默认启动；用户层在DB里、默认关闭（见service/mcp/registry）
    MCP_SYSTEM_SERVERS: list = (
        [{"name": s.name, "url": s.url,
          "enabled": True if s.enabled is None else bool(s.enabled),
          "headers": s.headers or {}}
         for s in (CONFIG_DATA.mcp.servers or []) if s.name and s.url]
        if CONFIG_DATA and CONFIG_DATA.mcp else []
    )

    # 网络能力（E8）：默认搜索开、抓取ask。配额是运行时用户数据（web_quotas），不在配置文件里
    WEB_SEARCH_ENABLED: bool = (
        CONFIG_DATA.web.search.enabled
        if CONFIG_DATA and CONFIG_DATA.web and CONFIG_DATA.web.search and CONFIG_DATA.web.search.enabled is not None
        else True
    )
    WEB_FETCH_PERMISSION: str = parse_permission(
        CONFIG_DATA.web.fetch.permission if CONFIG_DATA and CONFIG_DATA.web and CONFIG_DATA.web.fetch else None
    )
    WEB_FETCH_ALLOWLIST: tuple = tuple(
        (CONFIG_DATA.web.fetch.url_allowlist or ())
        if CONFIG_DATA and CONFIG_DATA.web and CONFIG_DATA.web.fetch
        else ()
    )

    # 旧"全局第三方搜索接口"档（来自用户novabot.json的web.search.providers）。
    # 语义已变：用户自己的第三方接口改进数据库（user_web_providers），这一档待迁移到
    # 数据库的global scope，故先原样保留。空provider名在配置期就被丢掉，不进环——
    # 否则每搜一次静默降级一档。
    WEB_SEARCH_PROVIDERS: tuple = tuple(
        {
            "provider": (p.provider or "").strip().lower(),
            "api_key": p.key or None,
            "endpoint": p.endpoint or None,
            "extra": {},
        }
        for p in (
            (CONFIG_DATA.web.search.user_providers or ())
            if CONFIG_DATA and CONFIG_DATA.web and CONFIG_DATA.web.search
            and CONFIG_DATA.web.search.user_providers else ()
        )
        if (p.provider or "").strip()
    )

config = BaseConfig()
