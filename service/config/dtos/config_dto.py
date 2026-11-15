# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : config_dto.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 22:36:37
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from decimal import Decimal
from dataclasses import dataclass
from typing import Optional

@dataclass
class CostDto:
    """单位金额，用于计算token消耗费用"""
    input: Decimal  # 输入单位金额
    output: Decimal  # 输出单位金额
    cache_read: Decimal = Decimal(0)  # 读Cache单位金额
    cache_write: Decimal = Decimal(0)  # 写Cache单位金额

@dataclass
class ModelDto:
    """模型定义"""
    code: str  # 模型Code
    name: str  # 模型名称
    stream: bool = True  # 是否流式
    enable_thinking: bool = False  # 是否思考
    num_ctx: int = 0  # 上下文窗口
    # 端点的真实输出上限；不配时预算按 4096 预留（保守）。仅在端点确实更大时填
    max_output_tokens: Optional[int] = None
    cost: Optional[CostDto] = None  # 单位金额
    # 关闭思考的请求体写法（None=按URL猜）。网关改名转发时用它显式指定：
    #   enable_thinking | thinking_type | template_kwargs | 以及用 + 组合
    thinking_style: Optional[str] = None
    # 该模型额外的请求头；同名头覆盖 provider.headers
    headers: Optional[dict] = None

@dataclass
class ProviderDto:
    """供应商定义"""
    code: str  # 供应商Code
    name: str  # 供应商名称
    base_url: str  # 接口地址
    api_key: str  # 接口key
    api: str  # 接口类型
    models: list[ModelDto]  # 可用模型列表
    host: str = ""  # 网关Host头（内网代理场景）
    # 该供应商所有模型的额外请求头；模型自己的 headers 优先
    headers: Optional[dict] = None
    # 动态模型发现：从 OpenAI 兼容的 /models 接口拉取可用模型
    dynamic_models: bool = False
    # 模型发现地址；留空按 base_url + /models。OpenCode 这类平台路径不同需显式配置
    models_url: str = ""

@dataclass
class LlmDto:
    """LLM运行时默认参数"""
    max_input_tokens: Optional[int] = None  # 模型未配置num_ctx时的默认最大输入token数

@dataclass
class AgentDto:
    """代理定义"""
    model: str
    # 单次运行的模型调用轮数上限（LLM→工具→LLM 往返）。None/0=不限。
    # 到顶时摘掉工具逼模型用文本收尾（学OpenCode的maxSteps）
    max_steps: Optional[int] = None

@dataclass
class ExecDto:
    """脚本执行档位配置（详见 TODO.md §9）

    只解析level：rules等细粒度授权随E5落地，过早解析会造成"配了却不生效"的误导。
    """
    level: Optional[str] = None  # T0~T4；服务级封顶。None=未显式配置，取T4（不压顶）

@dataclass
class WebSearchProviderDto:
    """全局第三方搜索接口（E8.6）。与models同处novabot.json，key明文同信任边界"""
    provider: str
    key: Optional[str] = None
    endpoint: Optional[str] = None

@dataclass
class WebSearchDto:
    """NovaBot自带搜索（E8）。enabled=None表示未显式配置，取默认开"""
    enabled: Optional[bool] = None
    # 系统内置的免key原生能力（定义在config/config.json，随发行版走），列表顺序即降级优先级
    providers: Optional[list] = None
    # 旧"全局第三方接口"档（来自用户novabot.json）。语义已变：用户自己的第三方接口改进数据库，
    # 这一档待迁移到数据库的global scope，故单独存着、不与系统内置混为一谈
    user_providers: Optional[list] = None

@dataclass
class WebFetchDto:
    """抓取权限三态（E8.3）：ask每次确认（白名单域名免确认）/ allow直接抓 / deny不注册工具"""
    permission: Optional[str] = None  # ask|allow|deny，无法识别按ask（fail-closed同exec.level按T0的思路）
    url_allowlist: Optional[list] = None  # 免确认域名，如 ["docs.python.org", "github.com"]

@dataclass
class WebDto:
    search: Optional[WebSearchDto] = None
    fetch: Optional[WebFetchDto] = None

@dataclass
class McpServerDto:
    """系统层MCP server（全局共享，默认启动）。与web.search.providers同属"全局档"：机主配、所有人共享"""
    name: str
    url: str
    enabled: Optional[bool] = None  # None=默认启动
    headers: Optional[dict] = None


@dataclass
class McpDto:
    """MCP配置（E9）。系统层server默认启动；用户层server由各用户自己配、默认关闭（见service/mcp/registry）"""
    servers: Optional[list] = None
    max_user_servers: Optional[int] = None  # 每用户可连接的server数量上限


@dataclass
class AttachmentDto:
    """附件与上传限制。None=未显式配置，取内置默认值。"""
    max_per_message: Optional[int] = None  # 单条消息附件数上限
    max_file_mb: Optional[int] = None  # 单文件大小上限（MB）
    admin_unlimited: Optional[bool] = None  # 管理员是否不受上述限制
    draft_ttl_days: Optional[int] = None  # 未发送草稿附件的保留天数


@dataclass
class ConfigDto:
    """配置定义"""
    models: list[ProviderDto]  # 供应商列表
    llm: Optional[LlmDto] = None  # LLM运行时默认参数
    agent: Optional[AgentDto] = None  # 代理
    exec: Optional[ExecDto] = None  # 脚本执行档位（None=未显式配置，服务封顶取T4）
    web: Optional[WebDto] = None  # 网络能力（None=全默认：搜索开、抓取ask）
    mcp: Optional[McpDto] = None  # MCP能力（None=无系统层server）
    attachments: Optional[AttachmentDto] = None  # 附件限制与草稿保留
    multi_user: Optional[bool] = None  # 是否启用多用户（None=未显式配置，取默认）
    remember_login: Optional[bool] = None  # 登录态是否跨启动保留
