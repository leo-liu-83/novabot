# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : commands.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 18:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : / 命令面板混入，WelcomeScreen与ChatScreen共用

import asyncio
import os
from functools import partial
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from config import config
from exception.user_exception import AuthError
from core.llm.thinking import normalize_level
from service.model_catalog import model_catalog, thinking_options
from ..widgets import CustomTextArea
from .auth import (
    DELETE_ACCOUNT_STEPS,
    LOGIN_STEPS,
    PASSWD_STEPS,
    REGISTER_STEPS,
    authenticate,
    build_remember_instruction,
    change_password,
    async_delete_account,
    logout_current,
    persist_login,
    remember_hint,
    should_offer_handover,
    switch_identity,
    transfer_current_session,
    verify_account,
    visible_account_commands,
    visible_auth_commands,
    visible_memory_commands,
)
from .input_screen import InputScreen
from .select_screen import ConfirmScreen, SelectScreen
from util.text_utils import get_text_width
from .sessions import SessionEntry, delete_session_wait, join_project
from service.session.approvals import clear_session
from .projects import (
    create_current_project,
    current_directory,
    delete_current_project,
    project_at_current,
    project_error_text,
    project_files_missing,
    rename_project,
    repair_project_files,
    visible_project_commands,
)
from service.project.errors import ProjectError
from service.user.user_service import get_current_ctx, list_memory_files, memory_file, user_service
from service.skill.skill_review_service import skill_review_service
from service.skill.skill_catalog import (
    SkillEntry,
    classify_skill,
    has_scripts,
    review_state,
    resolve_role,
    capabilities,
    visible_skills,
)
from service.mcp.registry import mcp_registry, MCP_MODES, resolve_tool_mode
from service.mcp.mcp_client import MCPError
from service.mcp.tools import session_for
from service.web.provider_service import (
    GLOBAL_SCOPE,
    PROVIDER_KINDS,
    ProviderConfigError,
    user_web_provider_service,
)
from service.web.quota_service import (
    DEFAULT_DAILY_LIMIT,
    UNLIMITED,
    parse_quota_spec,
    web_quota_service,
)
from core.agent.deep_agents.exec_policy import (
    ExecLevel,
    RISK_TEXT,
    effective_ceiling,
    get_session_override,
    parse_level,
    resolve_level,
    set_session_override,
)
from tui.i18n import get_language, t


def _format_findings(result) -> str:
    """扫描结果按严重度排序展示。空的也要说清楚，否则用户无法区分"没发现"和"没扫到"。"""
    if result.errors and not result.findings:
        return (t(t(t("扫描过程有异常，结果不完整：\n"))) + "\n".join(result.errors)
                + _fold_note(result))

    if not result.findings:
        return t("未发现已知危险模式。") + _fold_note(result)

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    lines = []
    for f in sorted(result.findings, key=lambda x: (order.get(x.severity, len(order)), x.file, x.line)):
        loc = f"{f.file}:{f.line}" if f.line else f.file
        snippet = f"  `{f.snippet}`" if f.snippet else ""
        lines.append(f"- **{f.severity}** `{f.rule_id}` {loc} — {f.message}{snippet}")

    tail = t(t(t("\n\n扫描过程有异常：\n"))) + "\n".join(result.errors) if result.errors else ""
    return "\n".join(lines) + _fold_note(result) + tail


def _fold_note(result) -> str:
    """被抑制与被折叠的条数要说出来——不然用户以为"没命中"，其实是被压掉了。"""
    parts = []
    suppressed = getattr(result, "suppressed", 0)
    duplicates = getattr(result, "duplicates", 0)
    if suppressed:
        parts.append(t(t(t('{} 条命中被规则的误报抑制压掉（多为引号内提及）')), suppressed))
    if duplicates:
        parts.append(t(t(t('{} 条与已有发现属同一构造，已折叠')), duplicates))

    return t(t(t('\n\n另有 {}。')), '；'.join(parts)) if parts else ""


def _format_finding_dicts(findings: list) -> str:
    """渲染已存库的发现（dict 形态，来自 get_state().findings），与 _format_findings 同序。"""
    if not findings:
        return t("未发现已知危险模式。")

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    lines = []
    for f in sorted(findings, key=lambda x: (order.get(x.get("severity", ""), 3),
                                             x.get("file", ""), x.get("line", 0))):
        loc = f"{f['file']}:{f['line']}" if f.get("line") else f.get("file", "")
        snippet = f"  `{f['snippet']}`" if f.get("snippet") else ""
        lines.append(f"- **{f.get('severity','')}** `{f.get('rule_id','')}` {loc} — {f.get('message','')}{snippet}")

    return "\n".join(lines)


def list_reviewable_skills() -> list:
    """当前身份有权查看的全量技能（含纯提示词），委托 skill_catalog。"""
    return [(e.name, e.skill_dir) for e in visible_skills(get_current_ctx())]


def _format_time(ts: int) -> str:
    if not ts:
        return ""
    import time as _time
    st = _time.localtime(ts)
    if st.tm_year == _time.localtime().tm_year:
        return _time.strftime("%m-%d %H:%M", st)
    return _time.strftime("%Y-%m-%d", st)


# 常用命令置前：新建/切换会话、模型、思考开关是高频操作
COMMON_COMMANDS: list[tuple[str, str]] = [
    ("/new", "开启新会话"),
    ("/session", "切换会话（弹窗选择）"),
    ("/model", "切换模型（弹窗选择）"),
    ("/think", "思考档位（弹窗选择：off/low/medium/high/auto）"),
    ("/compact", "手动压缩上下文（摘要旧消息）"),
    ("/lang", "切换界面语言（中文 / English）"),
]

# 思考档位：off/low/medium/high 是统一语义，具体请求字段由 core/llm/thinking.py 翻译；
# auto = 不覆盖，跟着模型定义的默认档位
THINK_MODES: list[tuple[str, str]] = [
    ("off", "关闭 · 不请求思考"),
    ("low", "低 · 轻量推理"),
    ("medium", "中 · 常规任务"),
    ("high", "高 · 复杂推理"),
    ("auto", "跟随模型默认"),
]
THINK_LABELS = dict(THINK_MODES)

# 脚本执行档位：配置为默认 + 会话内临时调整（不写配置文件）
# 技能安全审查：统一成一条命令，选中后弹窗逐级操作（见_open_skill_review）
SKILL_REVIEW_COMMANDS: list[tuple[str, str]] = [
    ("/skill-review", "查看并审查技能安全状态（弹窗选择）"),
]


# 无权限的命令一律不显示：与其给一个永远无效的旋钮、或一个批准后不会生效的开关，
# 不如让这些身份根本看不到。门控按身份天花板（能力上限），不按当前生效档位——
# 后者会随配置变化让命令忽隐忽现。
def visible_exec_commands(is_anonymous: bool, ceiling: ExecLevel) -> list[tuple[str, str]]:
    """只列一条：档位选择交给弹窗，不为每档各占一行命令。

    is_anonymous指多用户模式下未登录的那档——它不需要/exec。
    单用户模式（multi_user=false）坐在机器前的是机主本人，不按匿名处理。
    天花板T0（被收回授权的账号，或服务封顶=T0）同样不给：弹窗里只有T0一档，没有可选项。
    """
    if is_anonymous or ceiling == ExecLevel.OFF:
        return []

    return [("/exec", t(t(t("查看并调整脚本执行档位"))))]


# 弹窗里每档一行：能力标签 + 风险。RISK_TEXT给的是完整句子，太长放不进单行选择项
LEVEL_PICKER_DESC = {
    ExecLevel.OFF: "关闭 · 不执行任何命令",
    ExecLevel.SKILL_ONLY: "仅技能脚本 · 只跑已装技能 scripts/ 下的 .py（无 shell）",
    ExecLevel.READ_ONLY: "只读命令 · 可 ls/cat/grep/git status，改不了任何东西",
    ExecLevel.CONFIRM_ALL: "任意命令 · 逐条确认后才执行",
    ExecLevel.ALLOW_ALL: "任意命令免确认 · 等于把用户权限交给 agent",
}


def visible_skill_review_commands() -> list[tuple[str, str]]:
    """全身份可见。审查是"管理/知情"能力，与能否执行脚本（档位）解耦：
    即便天花板T0、跑不了任何脚本，也能查看/扫描自己有权触达的技能状态。
    具体能扫描/批准/拒绝哪些，由 skill_catalog.capabilities 按"角色×层"逐技能决定。"""
    return [(name, t(desc)) for name, desc in SKILL_REVIEW_COMMANDS]


# 自备搜索接口配置（E8.4）：不受/web-limit配额限制；key不回显。
# 统一成一条命令，弹窗逐级操作（见_open_web_config）
WEB_CONFIG_COMMANDS: list[tuple[str, str]] = [
    ("/web-config", "配置我自备的搜索接口（弹窗选择）"),
]

# MCP（E9）：用户层server默认关闭、需管理员放行；系统层server在config/config.json
MCP_COMMANDS: list[tuple[str, str]] = [
    ("/mcp", "管理我的 MCP server（添加/开关/删除，管理员可放行与设上限）"),
]

# 执行档位授权：仅管理员。授予值即对方天花板，不得越过服务封顶（config.exec.level）；
# 改天花板不动对方当前档位，只限制他能自提到的上限
def visible_exec_limit_commands(is_admin: bool) -> list[tuple[str, str]]:
    if not is_admin:
        return []

    return [("/exec-limit", t(t(t("为各账号设置脚本执行档位上限"))))]


# 网络搜索配额：仅管理员。普通用户与匿名者看不到这条命令——
# 配额是管理员掌管的开关，不给无权者一个只能看的旋钮
def visible_web_limit_commands(is_admin: bool) -> list[tuple[str, str]]:
    if not is_admin:
        return []

    return [("/web-limit", t(t(t("查看并设置各账号的网络搜索配额"))))]


def visible_project_limit_commands(is_admin: bool) -> list[tuple[str, str]]:
    if not is_admin:
        return []

    return [("/project-limit", t(t(t("为各账号开通/收回项目功能"))))]

# 收尾命令置后：退出程序与帮助都不是高频操作
TAIL_COMMANDS: list[tuple[str, str]] = [
    ("/exit", "退出 NovaBot"),
    ("/help", "显示全部命令"),
]

def parse_level_spec(raw: str) -> ExecLevel:
    """严格解析档位输入，认不出就报错而不是回落OFF。

    parse_level的fail-closed回落用在"读配置"上是对的，用在"管理员下指令"上会出事：
    /exec-limit bob T9 会被当成T0从而收回授权，打错字却显示"已收回"。
    """
    text = (raw or "").strip().upper()
    if text.startswith("T") and text[1:].isdigit() and int(text[1:]) <= int(ExecLevel.ALLOW_ALL):
        return ExecLevel(int(text[1:]))
    for level in ExecLevel:
        if level.name == text:
            return level

    raise ValueError(t(t(t('无法识别的档位: {!r}')), raw))


REMEMBER_MAX = 500
_EXEC_RESET = "__reset__"
_EXEC_GRANT_PREFIX = "grant:"
_EXEC_GRANT_ALL = "grant:*"
_PROJECT_GRANT_PREFIX = "project:"
_PROJECT_GRANT_ALL = "project:*"
# /web-limit 与 /web-config 弹窗的取值：全部账号行、添加provider行
_WEB_LIMIT_ALL = "all"
_WEB_CONFIG_ADD = "__add__"
_WEB_CONFIG_GLOBAL = "__global__"
# /mcp 弹窗取值：添加、管理他人、限额设置
_MCP_ADD = "__mcp_add__"
_MCP_ADMIN = "__mcp_admin__"
_MCP_LIMIT = "__mcp_limit__"
_MCP_TOGGLE_GRANT = "__mcp_grant__"
_MCP_ADMIN_ROW = "__mcp_row__"
# 工具级权限弹窗：清除全部覆盖、单工具回到继承
_MCP_TOOLS_CLEAR = "__mcp_tools_clear__"
_MCP_TOOL_INHERIT = "__mcp_tool_inherit__"

# /mcp 的输入字段。InputScreen不接受空值（空输入不推进），故字段都必须填
MCP_SERVER_STEPS = [("name", "名称（唯一标识）", False), ("url", "Endpoint URL", False)]
MCP_LIMIT_STEPS = [("limit", "可连接数量上限（整数）", False)]


def _mcp_fetch_tool_names(uid: str, row: dict) -> list[str]:
    """连一次 server 拿工具名。连不上会抛 MCPError，由调用方转成提示。"""
    session = session_for(uid, {"name": row["name"], "url": row["url"],
                                "headers": row.get("headers") or {}})

    return [str(t.get("name")) for t in session.list_tools()
            if isinstance(t, dict) and t.get("name")]


def _mcp_tool_mode_items(server_mode: str) -> list:
    mode_text = {"ask": t(t(t("每次确认"))), "allow": t(t(t("信任·免确认"))), "deny": t(t(t("不用")))}
    descriptions = {
        "ask": t(t(t("每次调用都要你确认"))),
        "allow": t(t(t("信任它，免确认"))),
        "deny": t(t(t("不注册给模型，也调不到"))),
    }
    items = [(_MCP_TOOL_INHERIT,
              Text.assemble((t(t(t("继承 server 模式"))), "bold"),
                            (t(t(t('  当前 server 为 {}')), mode_text.get(server_mode, server_mode)), "dim")))]
    for mode in MCP_MODES:
        items.append((mode, Text.assemble((mode, "bold"), (f"  {descriptions[mode]}", "dim"))))

    return items

# 需要自建服务的provider填endpoint而非key；custom_mcp还要额外的工具名
_ENDPOINT_PROVIDERS = ("searxng", "custom_mcp")


def _provider_input_steps(provider: str, include_tool: bool = False) -> list:
    """provider的录入字段。key按密码态录入，endpoint不是秘密故明文。"""
    if provider in _ENDPOINT_PROVIDERS:
        steps = [("endpoint", t(t(t("Endpoint 地址"))), False)]
        if provider == "custom_mcp" and include_tool:
            steps.append(("tool", t(t(t("MCP 工具名"))), False))
        return steps

    return [("key", "API Key", True)]


def _read_memory(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _provider_usable(provider) -> bool:
    """有密钥或本地服务（llama-server等免key）视为可用"""
    if provider.api_key:
        return True
    base = (provider.base_url or "").lower()
    return base.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]", "unix://"))


def models_by_provider() -> list[tuple[str, str, list[tuple[str, bool]]]]:
    """按provider分组：[(code, 显示名, [(provider/code, 是否有密钥可用)])]；同code的provider与模型合并，后配置覆盖前配置"""
    if not (config.CONFIG_DATA and config.CONFIG_DATA.models):
        return []

    groups: dict[str, dict] = {}
    order = []
    for provider in model_catalog.providers():
        if provider.code not in groups:
            groups[provider.code] = {"name": provider.name or provider.code, "models": {}}
            order.append(provider.code)
        if provider.name:
            groups[provider.code]["name"] = provider.name
        usable = _provider_usable(provider)
        for m in provider.models or []:
            groups[provider.code]["models"][f"{provider.code}/{m.code}"] = usable

    return [(code, groups[code]["name"], list(groups[code]["models"].items())) for code in order]


def all_models() -> list[tuple[str, bool]]:
    return [item for _, _, models in models_by_provider() for item in models]


class CommandMixin(Screen):
    """
    /命令面板：输入/唤出过滤列表，up/down选择、enter确认、esc关闭。

    宿主Screen需提供：
      self.model / self.thinking_level 状态，_cmd_input_id 输入框id，
      compose中yield self.compose_command_popup()，on_mount中调用 self.bind_command_input()，
      并实现 current_model / do_new_session / do_switch_session / mount_notice / mount_help
    """

    _cmd_input_id: str = "chat-input"

    # region 面板UI
    def compose_command_popup(self) -> ComposeResult:
        with Container(id="command-popup"):
            yield OptionList(id="command-options")

    def bind_command_input(self) -> None:
        input = self.query_one(f"#{self._cmd_input_id}", CustomTextArea)
        input.key_interceptor = self._intercept_key
        options = self.query_one("#command-options", OptionList)
        options.can_focus = False

    def _popup_visible(self) -> bool:
        return self.query_one("#command-popup", Container).display

    def _hide_popup(self) -> None:
        self.query_one("#command-popup", Container).display = False

    def _show_popup(self, typed: str, show_all: bool=False) -> None:
        entries = self._command_table()
        matches = entries if show_all else [(n, d) for n, d in entries if n.startswith(typed)]

        popup = self.query_one("#command-popup", Container)
        if not matches:
            self._hide_popup()
            return

        options = self.query_one("#command-options", OptionList)
        options.set_options([
            Option(Text.assemble((n, "bold"), (f"  {d}", "dim")), id=n)
            for n, d in matches
        ])
        options.highlighted = 0
        popup.display = True

    def on_text_area_changed(self, event) -> None:
        if getattr(event.text_area, "id", None) != self._cmd_input_id:
            return

        hook = getattr(self, "_on_input_text_changed", None)
        if hook is not None:
            hook(event)

        text = event.text_area.text.strip()
        if not text.startswith("/"):
            if self._popup_visible():
                self._hide_popup()
            return

        self._show_popup(text)

    # 弹出面板可见时接管方向键/回车/Esc（async：消费键后可能触发UI变更需await）
    async def _intercept_key(self, key: str) -> bool:
        if not self._popup_visible():
            return False

        options = self.query_one("#command-options", OptionList)

        if key == "up":
            options.action_cursor_up()
            return True
        if key == "down":
            options.action_cursor_down()
            return True
        if key == "escape":
            self._hide_popup()
            return True
        if key == "enter":
            # 8.2.8的highlighted_option返回Option对象而非id
            selected = options.highlighted_option
            if selected is not None and selected.id:
                await self._confirm_command(str(selected.id))
            return True

        return False

    async def _confirm_command(self, command: str) -> None:
        input = self.query_one(f"#{self._cmd_input_id}", CustomTextArea)
        input.text = ""
        self._hide_popup()

        if command == "/help":
            await self.mount_help()
            return

        await self._execute_command(command)
    # endregion

    # region 命令表与执行
    def _command_table(self) -> list[tuple[str, str]]:
        """命令按性质分组排序：会话/模型 → 记忆 → 执行/网络/技能 → 身份 → 收尾。

        身份组（登录/登出/改密/注册/注销）排在最后：它们是低频操作，
        且彼此互斥可见（未登录只出/login与/register，已登录只出其余三个）。
        """
        ctx = get_current_ctx()

        table = (
            COMMON_COMMANDS
            + visible_project_commands(ctx.can_projects)
            + visible_memory_commands(ctx.is_default)
            + visible_exec_commands(ctx.is_default and config.MULTI_USER,
                                    effective_ceiling(ctx, config.EXEC_LEVEL))
            + visible_exec_limit_commands(ctx.is_admin)
            + visible_web_limit_commands(ctx.is_admin)
            + visible_project_limit_commands(ctx.is_admin)
            + WEB_CONFIG_COMMANDS
            + MCP_COMMANDS
            + visible_skill_review_commands()
            + visible_auth_commands(config.MULTI_USER, ctx.is_default)
            + visible_account_commands(ctx.is_default)
            + TAIL_COMMANDS
        )
        # 这些表是模块级常量（不能在 import 时 t()，否则语言被冻结在启动值）
        return [(name, t(desc)) for name, desc in table]

    # 命令执行，返回是否消费（防止被当作query发送）
    async def _execute_command(self, text: str) -> bool:
        parts = text.split()
        cmd = parts[0] if parts else ""

        if cmd == "/exit":
            self.app.exit()
        elif cmd == "/new":
            await self.do_new_session()
        elif cmd == "/think":
            # 无参开弹窗，带参直接设（与/model同形）；认不出的取值报错而不是静默变auto
            if len(parts) > 1:
                arg = parts[1].lower()
                try:
                    level = normalize_level(arg)
                except ValueError:
                    await self.mount_notice(t(t(t(
                        '未知的思考档位 `{}`。可用：off / low / medium / high / auto')), arg))
                    return True

                self.thinking_level = level
                self._refresh_cmd_status()
            else:
                self._open_think_picker()
        elif cmd == "/model":
            if len(parts) > 1:
                # 带参数直接切换（兼容精确指定），否则弹窗
                usable = dict(all_models())
                if parts[1] not in usable:
                    await self.mount_notice(t(t(t('未找到模型: {}')), parts[1]))
                elif not usable[parts[1]]:
                    await self.mount_notice(t(t(t('模型 {} 未配置密钥，请在用户配置中补充key')), parts[1]))
                else:
                    self.model = parts[1]
            else:
                self._open_model_picker()
        elif cmd == "/compact":
            await self.do_compact()
        elif cmd == "/lang":
            self._open_language()
        elif cmd == "/session":
            self._open_session_picker()
        elif cmd == "/project":
            # push_screen_wait 必须在 worker 里跑；命令回调本身不是 worker
            self.run_worker(self._open_project(), group="project")
        elif cmd == "/login":
            self._start_auth(register=False)
        elif cmd == "/register":
            self._start_auth(register=True)
        elif cmd == "/logout":
            self._start_logout()
        elif cmd == "/help":
            # 必须有显式分支：否则落到下面的startswith("/")兜底——按前缀过滤命令表，
            # 没有命令以"/help"开头，弹窗会直接隐藏，表现为"敲了没反应"
            await self.mount_help()
        elif cmd == "/memory":
            await self._open_memory(parts[1] if len(parts) > 1 else None)
        elif cmd == "/remember":
            await self._execute_remember(" ".join(parts[1:]))
        elif cmd == "/exec":
            await self._open_exec(parts[1:] if len(parts) > 1 else [])
        elif cmd == "/skill-review":
            await self._open_skill_review(parts[1:])
        elif cmd == "/exec-limit":
            await self._open_exec_limit(parts[1:])
        elif cmd == "/web-limit":
            self._open_web_limit()
        elif cmd == "/project-limit":
            self._open_project_limit()
        elif cmd == "/web-config":
            self._open_web_config()
        elif cmd == "/mcp":
            self._open_mcp()
        elif cmd == "/passwd":
            self._start_passwd()
        elif cmd == "/delete-account":
            self._start_delete_account()
        elif cmd.startswith("/"):
            self._hide_popup()
            self._show_popup("/", show_all=True)
            return True
        else:
            return False

        self._refresh_cmd_status()
        return True

    # 状态栏刷新钩子，宿主没有额外状态栏时默认无操作
    def _refresh_cmd_status(self) -> None:
        pass

    # 界面语言：切换后重建当前屏幕，静态标签才会用新语言重新渲染
    def _open_language(self) -> None:
        current = get_language()
        items: list = []
        for code, label in (("zh", "中文"), ("en", "English")):
            # 与 /model、/think 同一形态：当前档位显式标出来，否则两行长得一样
            if code == current:
                parts = [(label, "bold"), (t("   ←  当前"), "green")]
            else:
                parts = [(label, "bold")]
            items.append((code, Text.assemble(*parts)))

        self.app.push_screen(
            SelectScreen(t("界面语言"), items, selected=current), self._on_language_picked)

    def _on_language_picked(self, value: Optional[str]) -> None:
        if not value:
            return
        from tui.i18n import set_language

        set_language(value)
        # 不能在弹窗的回调里 await switch_screen：当前屏幕（弹窗）正在等这个回调返回，
        # 而切换又要等它移除，会死锁。交给 App 级 worker，回调立即返回。
        self.app.run_worker(self._reload_for_language(), group="language")

    async def _reload_for_language(self) -> None:
        if getattr(self, "_agent_running", False):
            await self.mount_notice(t("正在生成，语言将在下一屏生效"))
            return

        # ChatScreen 复用当前会话（历史从库里重放）；WelcomeScreen 直接重建
        screen = type(self)(session=self._session) if hasattr(self, "_session") else type(self)()
        await self.app.switch_screen(screen)
        try:
            await screen.mount_notice(t("界面语言已切换"))
        except Exception:
            # 新屏幕尚未挂载出消息区时不值得为一句提示把切换流程打崩
            pass

    # 手动压缩钩子：欢迎页没有会话上下文，聊天页覆盖为真正的 worker
    async def do_compact(self) -> None:
        await self.mount_notice(t(t(t("当前页面没有会话上下文，进入会话后再 /compact"))))

    def current_model(self) -> str:
        return self.model or (config.CONFIG_DATA.agent.model if config.CONFIG_DATA and config.CONFIG_DATA.agent else t(t(t("未配置"))))

    # 打开/切换会话时沿用其绑定模型；模型不可用（已移除/未配密钥）则回退默认模型，返回提示文案
    def _resolve_session_model(self, model: Optional[str]) -> str:
        if not model:
            self.model = None
            return ""

        usable = dict(all_models())
        if usable.get(model):
            self.model = model
            return ""

        self.model = None
        if model not in usable:
            return t(t(t('会话模型 {} 已不存在，使用默认模型')), model)
        return t(t(t('会话模型 {} 未配置密钥，使用默认模型')), model)
    # endregion

    # region 选择弹窗
    def _open_model_picker(self) -> None:
        current = self.current_model()
        groups = models_by_provider()
        # 模型多时（聚合平台动辄几百个）才挂搜索框，少模型时不占一行
        filterable = sum(len(models) for _, _, models in groups) > 8
        items: list = []
        for idx, (code, name, models) in enumerate(groups):
            # 8.2.8 OptionList无分组能力：组标题用disabled行（游标自动跳过），provider之间插空行
            if idx > 0:
                items.append(Option(Text(" "), disabled=True))
            # provider 顶格、模型缩进两格：标题是分组框，模型才是可选内容
            items.append(Option(Text(f"{name} ({code})", style="bold cyan"), disabled=True))
            for m, usable in models:
                # 无密钥的模型可见但禁用（游标导航自动跳过disabled项）
                if m == current:
                    parts = [(f"  {m}", "bold"), (t(t(t("   ←  当前"))), "green")]
                elif usable:
                    parts = [(f"  {m}", "bold")]
                else:
                    parts = [(f"  {m}", "dim"), (t(t(t("   未配置密钥"))), "red")]
                # 搜索词同时匹配模型 id 与 provider 显示名，输入 “百炼” 也能找到
                items.append((m, Text.assemble(*parts), not usable, f"{m} {name}"))

        hint = t(t(t("↑↓ 选择    Enter 确认    Esc 取消")))
        if filterable:
            hint += t(t(t("    输入以搜索")))
        self.app.push_screen(SelectScreen(
            t(t(t("选择模型"))), items, filterable=filterable, hint=hint,
            empty_text=t(t(t("没有匹配的模型"))) if filterable else "",
        ), self._on_model_picked)

    def _on_model_picked(self, value: Optional[str]) -> None:
        if value:
            self.model = value
            self._refresh_cmd_status()

    # 思考档位弹窗：按当前模型能表达的档位裁剪，避免给出三个看起来一样的选项
    def _open_think_picker(self) -> None:
        current = self.thinking_level or "auto"
        options = thinking_options(self.current_model())["levels"]

        items: list = []
        for mode, desc in THINK_MODES:
            if mode != "auto" and mode not in options:
                continue
            # 当前标记放在描述之后：档位名与说明先读，标记只做收尾，避免中文行被挤换行
            parts = [(f"{mode:<6} ", "bold green" if mode == current else "bold"),
                     (t(desc), "dim")]
            if mode == current:
                parts.append((f"  {t('← 当前')}", "green"))
            items.append((mode, Text.assemble(*parts)))

        self.app.push_screen(SelectScreen(t(t(t("思考档位"))), items), self._on_think_picked)

    def _on_think_picked(self, value: Optional[str]) -> None:
        if value:
            self.thinking_level = None if value == "auto" else value
            self._refresh_cmd_status()

    # 会话按最后活动时间倒序，value直接用session_id
    def _sorted_sessions(self) -> list[SessionEntry]:
        from .sessions import list_sessions
        return sorted(list_sessions(), key=lambda e: e.get("updated_at", 0), reverse=True)

    def _current_session_id(self) -> Optional[str]:
        return None

    # 会话按最后活动时间倒序，value直接用session_id
    # 三列布局：● 标记列 / 标题(省略号) / 时间(右对齐)；当前会话独立背景色带，普通行斑马纹作视觉间隔
    def _open_session_picker(self) -> None:
        current_id = self._current_session_id()

        def build_items(ctx: dict) -> list:
            # 轻量Text行（不建Table）；高亮背景由OptionList widget层CSS负责，光标移动零重建
            avail = ctx.get("avail", 62)
            items = []
            for entry in self._sorted_sessions():
                is_current = current_id is not None and entry["id"] == current_id
                title_style = "bold" if is_current else "dim"
                title = t(entry["title"])
                if entry.get("project_name"):
                    title = f"[{entry['project_name']}] {title}"
                # 项目标签会占宽度：有项目时略放宽，仍给右侧时间留足空间
                limit = 14 if entry.get("project_name") else 10
                shown = title if len(title) <= limit else title[:limit] + "…"
                head = f"{'●' if is_current else ' '} {shown}"
                stamp = _format_time(entry.get("updated_at", 0))
                # 标题左对齐、时间右对齐：中间用空格填充到可用宽度
                pad = max(2, avail - get_text_width(head) - len(stamp))
                text = Text.assemble((head, title_style), (" " * pad), (stamp, "dim"))
                items.append((entry["id"], text, False, title))
            return items

        picker = SelectScreen(
            t(t(t("选择会话"))), [],
            hint=t(t(t("↑↓ 选择    Enter 切换    Esc 取消    Ctrl+D 删除"))),
            on_delete=self._request_session_delete,
            filterable=True,
            items_factory=build_items,
            content_highlight=False,
        )
        self._session_picker = picker
        self.app.push_screen(picker, self._on_session_picked)

    def _on_session_picked(self, value: Optional[str]) -> None:
        self._session_picker = None
        if not value:
            return

        for entry in self._sorted_sessions():
            if entry["id"] == value:
                if self._current_session_id() != value:
                    self.run_worker(self.do_switch_session(entry))
                return

    def _bind_current_session_to(self, project: dict) -> bool:
        """把当前会话转入项目（无项目 → 项目）；首页没有会话时什么都不做。

        返回是否真的改了，调用方据此在提示里说明。转移失败（会话正忙/已删除）
        只影响这一行提示，不能把命令流程打崩。
        """
        session = getattr(self, "_session", None)
        if not self._current_session_id() or not isinstance(session, dict):
            return False
        # 只做「无项目 → 项目」：已属于其它项目的会话不因为创建新项目就被搬走
        if session.get("project_id"):
            return False
        changed = join_project(session, project)
        if changed:
            refresh = getattr(self, "_refresh_cmd_status", None)
            if callable(refresh):
                refresh()
        return changed

    async def _open_project(self) -> None:
        """项目弹窗：当前目录没项目就直接填名字创建；已有项目时 Enter 改名、Ctrl+D 删除。"""
        ctx = get_current_ctx()
        if not ctx.can_projects:
            await self.mount_notice(t("项目功能未开通"))
            return

        existing = project_at_current(ctx.uid)
        if existing is None:
            await self._create_project_dialog(ctx, current_directory())
            return

        while True:
            current = current_directory()
            existing = project_at_current(ctx.uid)
            if existing is None:
                # 上一轮把它删了：不要再弹一个空列表
                return

            # 只展示当前目录的项目：其它目录的项目不应出现在这里
            items: list = [(
                existing["id"],
                Text.assemble(
                    (existing["name"], "bold"),
                    (f"  {existing.get('path') or ''}", "dim"),
                ),
                False,
                f"{existing['name']} {existing.get('path') or ''}",
            )]
            # 目录被删掉重建、或项目登记得比这套功能早时，.novabot 与 AGENTS.md 可能不在：
            # 不给入口的话，用户只会看到“项目存在”却怎么都找不到这两个东西。
            missing = project_files_missing(existing)
            if missing:
                items.append((
                    "__repair__",
                    Text.assemble(
                        (t("＋ 补齐项目文件"), "bold"),
                        (f"  {'、'.join(missing)}", "dim"),
                    ),
                    False,
                    f"{t('补齐项目文件')} {' '.join(missing)}",
                ))

            picker: SelectScreen

            def on_delete(value: str) -> None:
                """Ctrl+D：把删除请求带回本轮循环处理，确认框与主流程排在同一条线上。"""
                if value != existing["id"]:
                    picker.set_hint_error(t("只有当前项目可以删除"))
                    return
                picker.dismiss("__delete__")

            picker = SelectScreen(
                t("项目"), items,
                selected=existing["id"],
                hint=t("↑↓ 选择    Enter 重命名    Ctrl+D 删除    Esc 取消"),
                on_delete=on_delete,
            )
            value = await self.app.push_screen_wait(picker)
            if not value:
                return

            if value == "__delete__":
                # 删登记与删文件分开问：AGENTS.md 可能是用户手写的，不能默认一起删
                choice = await self.app.push_screen_wait(SelectScreen(
                    t("删除项目「{}」", existing["name"]),
                    [
                        ("record", Text(t("仅删除项目登记（目录里的文件保留）")), False,
                         t("仅删除项目登记")),
                        ("files", Text(t("同时清理 .novabot 与 AGENTS.md")), False,
                         t("同时清理项目文件")),
                    ],
                    selected="record",
                    hint=t("↑↓ 选择    Enter 确认    Esc 取消"),
                ))
                if not choice:
                    continue
                try:
                    row = delete_current_project(
                        ctx.uid, existing["id"], remove_files=(choice == "files"))
                except ProjectError as exc:
                    await self.mount_notice(t(project_error_text(exc)))
                    continue
                self._forget_deleted_project(ctx.uid, row["id"])
                removed = row.get("removed") or []
                if choice != "files":
                    await self.mount_notice(t("已删除项目 {}（目录里的文件保留）", row["name"]))
                elif removed:
                    await self.mount_notice(t("已删除项目 {}；清理了 {}", row["name"], "、".join(removed)))
                else:
                    await self.mount_notice(t("已删除项目 {}（目录里没有可清理的项目文件）", row["name"]))
                return

            if value == "__repair__":
                try:
                    done = repair_project_files(existing)
                except ProjectError as exc:
                    await self.mount_notice(t(project_error_text(exc)))
                    continue
                await self.mount_notice(
                    t("已补齐项目文件：{}", "、".join(done)) if done else t("项目文件已齐全"))
                continue

            values = await self.app.push_screen_wait(InputScreen(
                t("重命名项目"),
                [("name", t("项目名称"), False)],
                initial={"name": existing["name"]},
            ))
            if not values:
                continue
            new_name = values["name"].strip()
            if new_name == existing["name"]:
                # 原样回车是最常见的误操作：别报“已重命名”，那会让人以为已经重建了项目
                await self.mount_notice(t("项目名称未变更"))
                continue
            try:
                row = rename_project(ctx.uid, existing["id"], new_name)
            except ProjectError as exc:
                await self.mount_notice(t(project_error_text(exc)))
                continue
            await self.mount_notice(t("项目已重命名为 {}", row["name"]))

    @staticmethod
    def _shorten_path(path: str, limit: int = 48) -> str:
        """弹窗标题里的路径：太长就中段省略，保留首尾（根与最后一级目录都看得见）。"""
        if len(path) <= limit:
            return path
        head = (limit - 1) // 2
        return f"{path[:head]}…{path[-(limit - 1 - head):]}"

    async def _create_project_dialog(self, ctx, current: str) -> None:
        """没有项目时直接给输入框：目录就是当前目录，名称默认取目录名。"""
        default_name = os.path.basename(current.rstrip(os.sep)) or t("新项目")
        values = await self.app.push_screen_wait(InputScreen(
            t("创建项目（{}）", self._shorten_path(current)),
            [("name", t("项目名称"), False)],
            initial={"name": default_name},
        ))
        if not values:
            return
        try:
            row, agents_state = create_current_project(ctx.uid, values["name"].strip())
        except ProjectError as exc:
            await self.mount_notice(t(project_error_text(exc)))
            return

        joined = self._bind_current_session_to(row)
        if agents_state is None:
            message = t("已创建项目 {}，并生成 AGENTS.md", row["name"])
        elif agents_state == "exists":
            message = t("已创建项目 {}；目录已有 AGENTS.md，未覆盖", row["name"])
        else:
            message = t("已创建项目 {}，但生成 AGENTS.md 失败", row["name"])
        if joined:
            message += t("；当前会话已转入项目")
        await self.mount_notice(message)

    def _forget_deleted_project(self, uid: str, project_id: str) -> None:
        """项目删掉后，当前会话的缓存还挂着它：不清就会继续显示已删项目的目录。"""
        session = getattr(self, "_session", None)
        if not isinstance(session, dict) or session.get("project_id") != project_id:
            return
        session["project_id"] = None
        session["project_name"] = None
        session["project_path"] = None
        clear_session(uid, session.get("id") or "")
        refresh = getattr(self, "_refresh_cmd_status", None)
        if callable(refresh):
            refresh()

    # Ctrl+D：当前会话拒绝删除（弹窗内红字提示），其余弹确认框
    def _request_session_delete(self, session_id: str) -> None:
        picker = getattr(self, "_session_picker", None)
        entry = next((e for e in self._sorted_sessions() if e["id"] == session_id), None)
        if entry is None:
            return

        if self._current_session_id() == session_id:
            if picker is not None:
                picker.set_hint_error(t(t(t("当前会话不可删除"))))
            return

        def on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return

            self.run_worker(self._delete_session_wait(entry, picker), group="session-delete")

        self.app.push_screen(ConfirmScreen(t(t(t('删除会话「{}」？此操作不可恢复')), entry['title'])), on_confirm)

    async def _delete_session_wait(self, entry: SessionEntry, picker) -> None:
        """删除前等待该会话所有在途 turn 收尾，避免 checkpoint/消息在清理后写回来。"""
        deleted = await delete_session_wait(entry)
        if not deleted:
            if picker is not None:
                picker.set_hint_error(t(t(t("会话已不存在或归属已变化"))))
            return

        # 原地刷新当前弹窗列表；对非栈顶弹窗dismiss不会生效，关窗重开会叠加残留旧窗
        if picker is not None:
            if self._sorted_sessions():
                picker.refresh_items()
            else:
                picker.dismiss(None)
    # endregion

    # region 认证流程（登录/注册/登出）
    # push_screen_wait只能在worker上下文调用（消息处理器里会抛NoActiveWorker），
    # 故命令分发只启动worker，弹窗交互与后续切换都在worker内完成
    def _start_auth(self, register: bool) -> None:
        self.run_worker(self._auth_flow(register), group="auth")

    def _start_logout(self) -> None:
        self.run_worker(self._logout_flow(), group="auth")

    def _start_passwd(self) -> None:
        self.run_worker(self._passwd_flow(), group="auth")

    def _start_delete_account(self) -> None:
        self.run_worker(self._delete_account_flow(), group="auth")

    async def _passwd_flow(self) -> None:
        ctx = get_current_ctx()
        if ctx.is_default:
            return

        values = await self.app.push_screen_wait(InputScreen(t(t(t("修改密码"))), PASSWD_STEPS))
        if not values:
            return

        try:
            change_password(ctx.uid, values["old"], values["new"], values["confirm"])
        except AuthError as ex:
            await self.mount_notice(t(t(t('改密失败：{}')), ex.err_desc))
            return

        self._refresh_cmd_status()
        await self.mount_notice(t(t(t("密码已修改，其他设备需重新登录"))))

    async def _delete_account_flow(self) -> None:
        ctx = get_current_ctx()
        if ctx.is_default:
            return

        values = await self.app.push_screen_wait(InputScreen(t(t(t("注销账号"))), DELETE_ACCOUNT_STEPS))
        if not values:
            return

        # 密码先行校验并在此拦截，避免用户白走两轮确认弹窗
        try:
            verify_account(ctx.uid, values["password"])
        except AuthError as ex:
            await self.mount_notice(t(t(t('注销失败：{}')), ex.err_desc))
            return

        if not await self.app.push_screen_wait(ConfirmScreen(t(t(t('确定注销账号「{}」？该操作不可恢复')), ctx.username))):
            return

        purge_files = await self._confirm_purge_files()
        if purge_files is None:
            return

        await async_delete_account(ctx.uid, values["password"], purge_files=purge_files)
        await self.do_identity_changed(get_current_ctx(), keep_session=False)

    async def _confirm_purge_files(self) -> Optional[bool]:
        """返回True=连工作区文件一起删，False=只删账号数据，None=用户取消"""
        items = [
            ("no", Text(t(t(t("保留工作区文件（仅删除账号与会话）"))))),
            ("yes", Text(t(t(t("同时删除工作区文件（不可恢复）"))), style="red")),
        ]
        choice = await self.app.push_screen_wait(SelectScreen(t(t(t("是否同时删除工作区文件？"))), items))

        return None if choice is None else choice == "yes"

    async def _auth_flow(self, register: bool) -> None:
        title = t(t(t("注册新账号"))) if register else t(t(t("登录")))
        steps = REGISTER_STEPS if register else LOGIN_STEPS

        values = await self.app.push_screen_wait(InputScreen(title, steps))
        if not values:
            return

        try:
            ctx, token = authenticate(register, values["username"], values["password"], values.get("password2"))
        except AuthError as ex:
            await self.mount_notice(t(t(t('{}失败：{}')), title, ex.err_desc))
            return

        # 过户询问必须在切换身份之前：此时当前会话仍属default，切换后就无从判断
        handover = False
        if should_offer_handover(self._session_message_count()):
            handover = await self.app.push_screen_wait(
                ConfirmScreen(t(t(t('是否将当前会话过户给「{}」？')), ctx.username))
            )

        if handover:
            transfer_current_session(self._current_session_id(), ctx)

        persist_login(token)
        switch_identity(ctx)
        await self.do_identity_changed(ctx, keep_session=handover)

    async def _logout_flow(self) -> None:
        if not await self.app.push_screen_wait(ConfirmScreen(t(t(t("退出登录？当前会话将回到匿名身份下"))))):
            return

        await self.do_identity_changed(logout_current(), keep_session=False)

    def _session_message_count(self) -> int:
        return 0

    def _caps_now(self, skill_dir: str):
        """按当前身份现算能力，手打与弹窗共用同一条闸，杜绝凭参数绕过。"""
        ctx = get_current_ctx()
        layer, uid = classify_skill(skill_dir)
        return capabilities(resolve_role(ctx), layer, uid, ctx)

    def _find_visible_skill(self, name: str):
        for e in visible_skills(get_current_ctx()):
            if e.name == name:
                return e
        return None

    async def _open_skill_review(self, args: list) -> None:
        """技能审查：无参走两级弹窗；带参保留手打进阶入口，同样过 _caps_now 校验。"""
        if not args:
            self._open_skill_review_picker()
            return

        name = args[0]
        action = (args[1] if len(args) > 1 else "scan").lower()
        entry = self._find_visible_skill(name)
        if entry is None:
            await self.mount_notice(t(t(t('没有你有权查看的技能「{}」')), name))
            return

        if action == "scan":
            await self._skill_scan(entry)
        elif action == "approve":
            await self._skill_approve(entry)
        elif action == "reject":
            await self._skill_reject(entry)
        else:
            await self._skill_detail(entry)

    def _skill_row_text(self, entry: SkillEntry, state) -> Text:
        if state.released:
            decision, style = t(t(t("已放行"))), "green"
        elif state.rejected:
            decision, style = t(t(t("已拒绝"))), "red"
        elif not has_scripts(entry.skill_dir):
            decision, style = t(t(t("默认放行"))), "cyan"      # 纯提示词无决定行
        else:
            decision, style = t(t(t("未放行"))), "yellow"

        verdict = t(t(t("未扫描"))) if state.unscanned else (t(t(t("危险"))) if state.is_dangerous else t(t(t("安全"))))
        tail = t(t(t(" · 需重审"))) if state.stale else ""
        return Text.assemble(
            (entry.name, "bold"),
            (f"  [{entry.owner_label}]", "dim"),
            (f"  {decision}", style),
            (t(t(t('  | 扫描:{}{}')), verdict, tail), "red" if state.is_dangerous else "dim"),
        )

    def _open_skill_review_picker(self) -> None:
        entries = visible_skills(get_current_ctx())
        if not entries:
            self.run_worker(self.mount_notice(t(t(t("当前身份没有可查看的技能")))))
            return

        by_dir = {e.skill_dir: e for e in entries}
        items = [(e.skill_dir, self._skill_row_text(e, review_state(e.skill_dir)), False, e.name)
                 for e in entries]
        self.app.push_screen(
            SelectScreen(t(t(t('技能安全审查（{} 个）')), len(entries)), items,
                         filterable=len(entries) > 8,
                         hint=t(t(t("↑↓ 选择    Enter 查看/操作    Esc 取消")))
                              + (t(t(t("    输入以搜索"))) if len(entries) > 8 else "")),
            partial(self._on_skill_review_picked, by_dir),
        )

    def _on_skill_review_picked(self, by_dir: dict, value: Optional[str]) -> None:
        entry = by_dir.get(value or "")
        if entry is None:
            return

        # 纯只读身份（如普通用户看共享）直接看详情；有动作能力者给二级菜单
        if not (entry.caps.scan or entry.caps.approve or entry.caps.reject):
            self.run_worker(self._skill_detail(entry))
            return

        self._open_skill_action_picker(entry)

    def _open_skill_action_picker(self, entry: SkillEntry) -> None:
        state = review_state(entry.skill_dir)
        can_approve_now = entry.caps.approve and (
            not entry.caps.approve_needs_safe
            or (not state.unscanned and not state.is_dangerous and not state.stale))
        approve_label = (t(t(t("批准（置为已放行）"))) if not entry.caps.approve_needs_safe
                         else t(t(t("批准（需扫描结论=安全）"))))
        items = [
            ("detail", Text(t(t(t("查看安全发现"))))),
            ("scan", Text(t(t(t("扫描 / 重新扫描")))), not entry.caps.scan),
            ("approve", Text(approve_label), not can_approve_now),
            ("reject", Text(t(t(t("拒绝（置为未放行）")))), not entry.caps.reject),
        ]
        self.app.push_screen(
            SelectScreen(f"{entry.name} · [{entry.owner_label}]", items),
            partial(self._on_skill_action_picked, entry),
        )

    def _on_skill_action_picked(self, entry: SkillEntry, value: Optional[str]) -> None:
        if value == "scan":
            self.run_worker(self._skill_scan(entry))
        elif value == "approve":
            self.run_worker(self._skill_approve(entry))
        elif value == "reject":
            self.run_worker(self._skill_reject(entry))
        elif value == "detail":
            self.run_worker(self._skill_detail(entry))

    async def _skill_scan(self, entry: SkillEntry) -> None:
        if not self._caps_now(entry.skill_dir).scan:
            await self.mount_notice(t(t(t('你没有扫描「{}」的权限')), entry.name))
            return

        state, result = skill_review_service.scan(entry.skill_dir)
        verdict = t(t(t("危险"))) if state.is_dangerous else t(t(t("安全")))
        await self.mount_notice(
            t(t(t('**已扫描 · {}** → 结论：{}\n\n{}\n扫描只是把危险构造摆出来，不构成安全保证；放行与否仍由有权限者判断。')), entry.name, verdict, _format_findings(result))
        )

    async def _skill_approve(self, entry: SkillEntry) -> None:
        caps = self._caps_now(entry.skill_dir)
        state = review_state(entry.skill_dir)
        if not caps.approve:
            await self.mount_notice(t(t(t('你没有批准「{}」的权限')), entry.name))
            return

        if caps.approve_needs_safe and (state.unscanned or state.is_dangerous or state.stale):
            await self.mount_notice(
                t(t(t('「{}」需先扫描、结论为安全、且内容未变更，才能被非管理员批准')), entry.name))
            return

        skill_review_service.release(entry.skill_dir)
        kind = t(t(t("脚本将可执行、技能会加载"))) if has_scripts(entry.skill_dir) else t(t(t("纯提示词技能，本就默认加载")))
        await self.mount_notice(t(t(t('已放行「{}」——agent 现在会加载它（{}）')), entry.name, kind))

    async def _skill_reject(self, entry: SkillEntry) -> None:
        if not self._caps_now(entry.skill_dir).reject:
            await self.mount_notice(t(t(t('你没有拒绝「{}」的权限')), entry.name))
            return

        skill_review_service.reject(entry.skill_dir)
        await self.mount_notice(t(t(t('已拒绝「{}」——agent 不再加载它，其脚本也不能执行')), entry.name))

    async def _skill_detail(self, entry: SkillEntry) -> None:
        state = review_state(entry.skill_dir)
        loaded = (not state.rejected) if not has_scripts(entry.skill_dir) else state.runnable
        head = (
            f"**{entry.name}** [{entry.owner_label}]\n\n"
            f"- 放行状态：{t(t(t('已放行'))) if state.released else (t(t(t('已拒绝'))) if state.rejected else t(t(t('未放行'))))}\n"
            f"- 扫描结论：{t(t(t('未扫描'))) if state.unscanned else (t(t(t('危险'))) if state.is_dangerous else t(t(t('安全'))))}"
            f"{t(t(t('（内容已变更，需重审）'))) if state.stale else ''}\n"
            f"- agent 当前：{t(t(t('加载'))) if loaded else t(t(t('不加载')))}"
            f"{t(t(t('（且可执行其脚本）'))) if has_scripts(entry.skill_dir) and loaded else ''}\n\n"
        )

        if state.unscanned:
            body = t(t(t("尚未扫描，暂无安全发现。"))) if not state.findings else _format_finding_dicts(state.findings)
        else:
            body = _format_finding_dicts(state.findings) if state.findings else t(t(t("未发现已知危险模式。")))

        await self.mount_notice(head + body)

    async def _open_memory(self, name: Optional[str]) -> None:
        ctx = get_current_ctx()
        if ctx.is_default:
            await self.mount_notice(t(t(t("长期记忆仅登录后可用"))))
            return

        if not ctx.memory_path:
            await self.mount_notice(t(t(t("记忆目录不存在"))))
            return

        target = memory_file(ctx.uid, name or "core.md")
        if target is None:
            await self.mount_notice(t(t(t('没有这个记忆文件：{}（/memory 可查看全部）')), name))
            return

        content = _read_memory(target)
        if content is None:
            await self.mount_notice(t(t(t('读取失败：{}')), name))
            return

        if name:
            await self.mount_memory(t(t(t('**长期记忆 · {}**\n\n{}')), name, content))
            return

        counts = " · ".join(t(t(t('{} {}行')), n, c) for n, c in list_memory_files(ctx.uid))
        await self.mount_memory(t(t(t('**长期记忆**\n\n`{}`\n\n{}')), counts, content))

    # admin不进列表：其天花板是T4，只受服务封顶约束，授权表对admin无意义
    def _grantable_users(self) -> list[dict]:
        return [r for r in user_service.list_users() if not r["is_admin"]]

    def _open_exec_limit_picker(self) -> None:
        """第一级：选账号（可搜索）。用Option承载"全部"——_rebuild对Option不过滤，故它常驻。

        管理员不进列表：其天花板是T4，只受服务封顶约束，授权表对admin无意义。
        """
        items: list = []
        targets = self._grantable_users()
        for row in targets:
            uid, name = row["uid"], row["username"]
            grant = user_service.get_exec_grant(uid)
            state = grant.name if grant else t(t(t("未授权（天花板默认 T1）")))
            items.append((f"{_EXEC_GRANT_PREFIX}{uid}",
                          Text.assemble((name, "bold"), (f"   {state}", "dim")), False, name))

        cap = config.EXEC_LEVEL.name
        # Option不过滤（SelectScreen._rebuild对Option直接放行），故"全部"项在搜索时仍可达
        all_row = Option(Text.assemble((t(t(t('全部账号（{} 个）')), len(targets)), "bold cyan")), id=_EXEC_GRANT_ALL)
        items.insert(0, all_row)
        self.app.push_screen(
            SelectScreen(t(t(t('执行档位授权（上限 = 服务封顶 {}）')), cap), items,
                         filterable=len(targets) > 8,
                         hint=t(t(t("↑↓ 选择    Enter 确认    Esc 取消"))) + (t(t(t("    输入以搜索"))) if len(targets) > 8 else "")),
            self._on_exec_limit_user_picked)

    def _on_exec_limit_user_picked(self, value: Optional[str]) -> None:
        if not value or not value.startswith(_EXEC_GRANT_PREFIX):
            return

        target = value[len(_EXEC_GRANT_PREFIX):]
        # 空uid会写出一条无人能用的脏行（uid=""），且弹窗标题为空——直接拒绝
        if not target:
            return

        if target == "*":
            uids = [r["uid"] for r in self._grantable_users()]
            if not uids:
                self.run_worker(self.mount_notice(t(t(t("没有可授权的账号（管理员不在授权范围内）")))))
                return
            label = t(t(t('全部 {} 个账号')), len(uids))
        else:
            uids = [target]
            label = next((r["username"] for r in user_service.list_users() if r["uid"] == target), target)

        self._open_exec_grant_picker(uids, label)

    def _open_exec_grant_picker(self, uids: list, label: str) -> None:
        """第二级：选档位。T0等同收回——两者天花板都是OFF。多uid时"当前"只在全体一致时标注。"""
        grants = {u: user_service.get_exec_grant(u) or ExecLevel.OFF for u in uids}
        distinct = set(grants.values())
        current = distinct.pop() if len(distinct) == 1 else None
        cap = config.EXEC_LEVEL

        items: list = []
        for level in ExecLevel:
            if level > cap:
                break
            desc = t(LEVEL_PICKER_DESC[level])
            if level == ExecLevel.OFF:
                desc = t(t(t("收回授权 · 天花板回到 T1")))
            if level == current:
                parts = [(f"T{int(level)} {desc}", "bold green"), (t(t(t("   ← 当前"))), "green")]
            else:
                parts = [(f"T{int(level)} {desc}", "")]
            items.append((level.name, Text.assemble(*parts)))

        self.app.push_screen(
            SelectScreen(t(t(t('{} 的执行档位上限（配置封顶 {}）')), label, cap.name), items),
            partial(self._on_exec_grant_picked, uids, label),
        )

    # SelectScreen的回调是同步的，实际写库要await mount_notice，交给worker
    def _on_exec_grant_picked(self, uids: list, label: str, value: Optional[str]) -> None:
        self.run_worker(self._apply_exec_grant(uids, label, value))

    async def _apply_exec_grant(self, uids: list, label: str, value: Optional[str]) -> None:
        # 校验放在真正写库的这一层，而非只在UI路由层拦：
        # uid为空的授权行谁都用不到，只会污染user_exec_grants
        uids = [u for u in uids if u]
        if not value or not uids:
            return

        try:
            requested = parse_level_spec(value)
        except ValueError as err:
            await self.mount_notice(f"{err}")
            return

        if requested == ExecLevel.OFF:
            for uid in uids:
                user_service.clear_exec_grant(uid)
            await self.mount_notice(t(t(t('已收回 {} 的执行档位授权，天花板回到 T1')), label))
            return

        for uid in uids:
            user_service.set_exec_grant(uid, requested)
        await self.mount_notice(
            t(t(t('已授予 {} 最高 {} 的执行档位。他们可用 `/exec` 在 T0~{} 之间自行调整（仅本会话生效，不写配置）。')), label, requested.name, requested.name)
        )

    # 管理员逐人授予执行档位。授予值不得超过服务封顶——admin的能力不应超过这个部署给它的
    async def _open_exec_limit(self, args: list) -> None:
        ctx = get_current_ctx()
        if not ctx.is_admin:
            await self.mount_notice(t(t(t("只有管理员可以设置执行档位授权"))))
            return

        if not args:
            self._open_exec_limit_picker()
            return

        if len(args) < 2:
            await self.mount_notice(t(t(t("用法：`/exec-limit <用户名> <T0~T4>` 或 `/exec-limit <用户名> off`"))))
            return

        username, raw = args[0], args[1]
        target = user_service.get_uid_by_username(username)
        if target is None:
            await self.mount_notice(t(t(t('没有名为 `{}` 的账号')), username))
            return

        if raw.strip().lower() == "off":
            user_service.clear_exec_grant(target)
            await self.mount_notice(t(t(t('已收回 `{}` 的执行档位授权，其天花板回到 T1')), username))
            return

        # parse_level对认不出的输入回落OFF，直接用会把打错的档位当成"收回授权"。
        # 与parse_quota_spec同一立场：非法输入必须报错，不能悄悄变成另一个值。
        try:
            requested = parse_level_spec(raw)
        except ValueError as err:
            await self.mount_notice(t(t(t('{}（可用取值：T0~T4 或 `off`）')), err))
            return

        if requested == ExecLevel.OFF:
            user_service.clear_exec_grant(target)
            await self.mount_notice(t(t(t('已收回 `{}` 的执行档位授权，其天花板回到 T1')), username))
            return

        cap = config.EXEC_LEVEL
        if requested > cap:
            await self.mount_notice(
                t(t(t('不能授予 {}：当前服务封顶为 {}，授权不得超过它')), requested.name, cap.name)
            )
            return

        user_service.set_exec_grant(target, requested)
        await self.mount_notice(
            t(t(t('已授予 `{}` 最高 {} 的执行档位。他可用 `/exec` 在 T0~{} 之间自行调整（仅本会话生效，不写配置）。')), username, requested.name, requested.name)
        )

    # ── /project-limit：管理员给普通账号开通/收回项目功能 ──

    def _open_project_limit(self) -> None:
        ctx = get_current_ctx()
        if not ctx.is_admin:
            self.run_worker(self.mount_notice(t("只有管理员可以设置项目功能授权")))
            return

        targets = self._grantable_users()
        if not targets:
            self.run_worker(self.mount_notice(t("没有可授权的普通账号")))
            return

        items: list = []
        for row in targets:
            uid, name = row["uid"], row["username"]
            enabled = user_service.get_projects_enabled(uid)
            state = t("已开通") if enabled else t("未开通")
            items.append((
                f"{_PROJECT_GRANT_PREFIX}{uid}",
                Text.assemble((name, "bold"), (f"   {state}", "green" if enabled else "dim")),
                False,
                name,
            ))
        items.insert(0, Option(
            Text.assemble((t("全部账号（{} 个）", len(targets)), "bold cyan")),
            id=_PROJECT_GRANT_ALL,
        ))
        self.app.push_screen(
            SelectScreen(
                t("项目功能授权"), items,
                filterable=len(targets) > 8,
                hint=t("↑↓ 选择    Enter 确认    Esc 取消")
                     + (t("    输入以搜索") if len(targets) > 8 else ""),
            ),
            self._on_project_limit_user_picked,
        )

    def _on_project_limit_user_picked(self, value: Optional[str]) -> None:
        if not value:
            return
        if value == _PROJECT_GRANT_ALL:
            uids = [r["uid"] for r in self._grantable_users()]
            label = t("全部 {} 个账号", len(uids))
        elif value.startswith(_PROJECT_GRANT_PREFIX):
            uid = value[len(_PROJECT_GRANT_PREFIX):]
            if not uid:
                return
            uids = [uid]
            label = next((r["username"] for r in user_service.list_users() if r["uid"] == uid), uid)
        else:
            return
        if uids:
            self._open_project_limit_actions(uids, label)

    def _open_project_limit_actions(self, uids: list, label: str) -> None:
        states = {uid: user_service.get_projects_enabled(uid) for uid in uids}
        current = next(iter(states.values())) if len(set(states.values())) == 1 else None
        items: list = []
        for value, text in (("on", t("开通项目功能")), ("off", t("收回项目功能"))):
            mark = t("   ← 当前") if (value == "on") == current and current is not None else ""
            items.append((value, Text.assemble((text, "bold green" if mark else ""), (mark, "green"))))

        self.app.push_screen(
            SelectScreen(t("{} 的项目功能", label), items),
            partial(self._on_project_limit_action_picked, uids, label),
        )

    def _on_project_limit_action_picked(self, uids: list, label: str,
                                        value: Optional[str]) -> None:
        if value:
            self.run_worker(self._apply_project_limit(uids, label, value))

    async def _apply_project_limit(self, uids: list, label: str, value: str) -> None:
        if not get_current_ctx().is_admin:
            await self.mount_notice(t("只有管理员可以设置项目功能授权"))
            return
        enabled = value == "on"
        for uid in uids:
            user_service.set_projects_enabled(uid, enabled)
        if enabled:
            await self.mount_notice(t("已为 {} 开通项目功能；对方重新登录后生效", label))
        else:
            await self.mount_notice(t("已收回 {} 的项目功能；对方当前已登录的会话仍可继续", label))

    # ── /web-limit：配额。普通用户只读自己的，admin 走账号列表弹窗 ──

    def _quota_label(self, state) -> str:
        if not state.enabled:
            return t(t(t("已禁用")))
        if state.unlimited:
            return t(t(t("不限次数")))
        suffix = t(t(t("（默认）"))) if not state.overridden else t(t(t('（今日用 {}）')), state.used)
        return t(t(t('{} 次/天{}')), state.daily_limit, suffix)

    def _open_web_limit(self) -> None:
        """配额只管 NovaBot 自带的能力；用户自备 provider 的调用不经过这里。

        仅管理员可用：命令表里对其他身份不显示，手打同样拒绝——
        不给无权者一条"只能看"的入口。
        """
        ctx = get_current_ctx()
        if not ctx.is_admin:
            self.run_worker(self.mount_notice(t(t(t("只有管理员可以查看与设置网络搜索配额")))))
            return

        self._open_web_limit_picker()

    def _open_web_limit_picker(self) -> None:
        users = user_service.list_users()
        by_uid: dict[str, str] = {}
        items: list = []
        for row in users:
            uid, name = row["uid"], row["username"]
            by_uid[uid] = name
            state = web_quota_service.state(uid)
            items.append((uid, Text.assemble((name, "bold"),
                                             (f"  {self._quota_label(state)}", "dim")),
                          False, name))

        # Option不过滤（SelectScreen._rebuild对Option直接放行），故"全部账号"搜索时仍可达
        items.insert(0, Option(Text.assemble((t(t(t('全部账号（{} 个）')), len(users)), "bold cyan")),
                               id=_WEB_LIMIT_ALL))
        self.app.push_screen(
            SelectScreen(t(t(t("网络搜索配额"))), items, filterable=len(users) > 8,
                         hint=t(t(t("↑↓ 选择    Enter 继续    Esc 取消")))
                              + (t(t(t("    输入以搜索"))) if len(users) > 8 else "")),
            partial(self._on_web_limit_user_picked, by_uid))

    def _on_web_limit_user_picked(self, by_uid: dict, value: Optional[str]) -> None:
        if not value:
            return

        if value == _WEB_LIMIT_ALL:
            uids = list(by_uid.keys())
            label = t(t(t('全部 {} 个账号')), len(uids))
        else:
            uids = [value]
            label = by_uid.get(value, value)

        self._open_web_limit_actions(uids, label)

    def _open_web_limit_actions(self, uids: list, label: str) -> None:
        items = [
            ("count", Text(t(t(t("设置每日次数"))))),
            ("off", Text(t(t(t("禁用 NovaBot 自带搜索"))))),
            ("unlimited", Text(t(t(t("不限次数"))))),
            ("default", Text(t(t(t("恢复默认（清除覆盖）"))))),
        ]
        self.app.push_screen(SelectScreen(t(t(t('{} 的搜索配额')), label), items),
                             partial(self._on_web_limit_action_picked, uids, label))

    def _on_web_limit_action_picked(self, uids: list, label: str, value: Optional[str]) -> None:
        if value == "count":
            self.run_worker(self._web_limit_set_count(uids, label))
        elif value == "off":
            self.run_worker(self._web_limit_apply(uids, label, False, DEFAULT_DAILY_LIMIT))
        elif value == "unlimited":
            self.run_worker(self._web_limit_apply(uids, label, True, UNLIMITED))
        elif value == "default":
            self.run_worker(self._web_limit_clear(uids, label))

    async def _web_limit_set_count(self, uids: list, label: str) -> None:
        values = await self.app.push_screen_wait(
            InputScreen(t(t(t('{} 的每日搜索次数')), label), [("limit", t(t(t("每日次数"))), False)]))
        if not values:
            return

        raw = str(values.get("limit", "")).strip()
        try:
            spec = parse_quota_spec(raw)
        except ValueError as err:
            await self.mount_notice(t(t(t('{}（可用取值：数字，0 起）')), err))
            return

        if spec is None:
            await self._web_limit_clear(uids, label)
            return

        enabled, limit = spec
        await self._web_limit_apply(uids, label, enabled, limit)

    async def _web_limit_apply(self, uids: list, label: str, enabled: bool, limit: int) -> None:
        if not get_current_ctx().is_admin:
            await self.mount_notice(t(t(t("只有管理员可以设置网络搜索配额"))))
            return

        for uid in uids:
            web_quota_service.set_quota(uid, enabled, limit)

        if not enabled:
            summary = t(t(t("禁止使用 NovaBot 自带的网络能力")))
        elif limit == UNLIMITED:
            summary = t(t(t("允许，且不限次数")))
        else:
            summary = t(t(t('允许，每天 {} 次')), limit)

        await self.mount_notice(t(t(t('已更新 {} 的网络配额：{}')), label, summary))

    async def _web_limit_clear(self, uids: list, label: str) -> None:
        if not get_current_ctx().is_admin:
            await self.mount_notice(t(t(t("只有管理员可以设置网络搜索配额"))))
            return

        for uid in uids:
            web_quota_service.clear_quota(uid)

        after = web_quota_service.state(uids[0])
        await self.mount_notice(
            t(t(t('已清除 {} 的配额覆盖，回到默认：允许，{} 次/天')), label, after.daily_limit))

    # ── /web-config：自备搜索接口。key一旦写入，该用户的搜索走自备接口且不占NovaBot配额 ──

    def _open_web_config(self, uid: Optional[str] = None) -> None:
        """我的自备搜索接口。uid=None=当前身份；传GLOBAL_SCOPE=admin的全局档（所有账号共享）。"""
        ctx = get_current_ctx()
        scope = uid or ctx.uid
        is_global = scope == GLOBAL_SCOPE
        rows = user_web_provider_service.list_for(scope)

        items: list = []
        for row in rows:
            provider = row["provider"]
            marks = [t(t(t("已设key"))) if row["has_key"] else t(t(t("无key")))]
            if not row["enabled"]:
                marks.append(t(t(t("已停用"))))
            endpoint = f"｜{row['endpoint']}" if row["endpoint"] else ""
            items.append((provider,
                          Text.assemble((provider, "bold"), (f" {endpoint}", "dim"),
                                        (f"（{'，'.join(marks)}）", "dim")),
                          False, provider))

        # Option不过滤，故"添加搜索接口"这一项在搜索时仍可达
        items.insert(0, Option(Text(t(t(t("添加搜索接口")))), id=_WEB_CONFIG_ADD))
        if ctx.is_admin and not is_global:
            items.insert(1, Option(Text(t(t(t("管理全局搜索接口（所有账号共享）")))), id=_WEB_CONFIG_GLOBAL))

        title = t(t(t("全局搜索接口（所有账号共享）"))) if is_global else (
            t(t(t("我的自备搜索接口"))) if rows else t(t(t("我的自备搜索接口（尚未配置）"))))
        self.app.push_screen(
            SelectScreen(title, items, filterable=len(items) > 8,
                         hint=t(t(t("↑↓ 选择    Enter 继续    Esc 取消")))
                              + (t(t(t("    输入以搜索"))) if len(items) > 8 else "")),
            partial(self._on_web_config_picked, scope))

    def _on_web_config_picked(self, scope: str, value: Optional[str]) -> None:
        if not value:
            return

        if value == _WEB_CONFIG_GLOBAL:
            self._open_web_config(GLOBAL_SCOPE)
            return
        if value == _WEB_CONFIG_ADD:
            self._open_web_config_kind_picker(scope)
            return

        self._open_web_config_actions(scope, value)

    def _open_web_config_kind_picker(self, scope: str) -> None:
        items = [(kind, Text(kind)) for kind in PROVIDER_KINDS]
        self.app.push_screen(SelectScreen(t(t(t("添加哪种搜索接口"))), items),
                             partial(self._on_web_config_kind_picked, scope))

    def _on_web_config_kind_picked(self, scope: str, value: Optional[str]) -> None:
        if not value:
            return

        self.run_worker(self._web_config_set(scope, value, adding=True))

    def _open_web_config_actions(self, scope: str, provider: str) -> None:
        row = user_web_provider_service.get(scope, provider)

        items = [("set", Text(t(t(t("设置/更新 key 或 endpoint")))))]
        if provider == "custom_mcp":
            items.append(("tool", Text(t(t(t("指定 MCP 工具名"))))))
        if row and row.get("enabled"):
            items.append(("off", Text(t(t(t("停用"))))))
        else:
            items.append(("on", Text(t(t(t("启用"))))))
        items.append(("del", Text(t(t(t("删除"))))))

        self.app.push_screen(SelectScreen(f"{provider}", items),
                             partial(self._on_web_config_action_picked, scope, provider))

    def _on_web_config_action_picked(self, scope: str, provider: str, value: Optional[str]) -> None:
        if value == "set":
            self.run_worker(self._web_config_set(scope, provider))
        elif value == "tool":
            self.run_worker(self._web_config_set_tool(scope, provider))
        elif value in ("on", "off"):
            self.run_worker(self._web_config_toggle(scope, provider, value == "on"))
        elif value == "del":
            self.run_worker(self._web_config_delete(scope, provider))

    async def _web_config_set(self, scope: str, provider: str, adding: bool = False) -> None:
        ctx = get_current_ctx()
        steps = _provider_input_steps(provider, include_tool=adding)
        values = await self.app.push_screen_wait(InputScreen(t(t(t('配置 {}')), provider), steps))
        if not values:
            return

        key = str(values.get("key", "")).strip()
        endpoint = str(values.get("endpoint", "")).strip()
        tool = str(values.get("tool", "")).strip()
        await self._web_config_save(scope, provider, key, endpoint, tool)

    async def _web_config_save(self, scope: str, provider: str, key: str, endpoint: str,
                               tool: str = "") -> None:
        ctx = get_current_ctx()
        existing = user_web_provider_service.get(scope, provider)

        # upsert是覆盖语义：未填的字段不能顺手抹掉已有的key/工具名
        if not key and existing and existing.get("api_key"):
            key = existing["api_key"]
        extra = {"tool": tool} if tool else (existing.get("extra") if existing else None)

        try:
            # 内网endpoint只有admin能配：自建SearXNG常在本机/局域网，
            # 但普通账号指向内网等于让NovaBot替它探测内网
            user_web_provider_service.upsert(scope, provider, api_key=key or None,
                                             endpoint=endpoint or None, extra=extra,
                                             allow_private=ctx.is_admin)
        except ProviderConfigError as err:
            await self.mount_notice(t(t(t('配置未保存：{}')), err))
            return

        if scope == GLOBAL_SCOPE:
            await self.mount_notice(
                t(t(t('已保存全局 `{}` 的搜索接口配置（key已写入用户数据，不回显）。所有账号的搜索都会先走它。')), provider))
            return

        await self.mount_notice(
            t(t(t('已保存 `{}` 的搜索接口配置（key已写入用户数据，不回显）。你的搜索将优先走它，不再消耗 NovaBot 配额。')), provider)
        )

    async def _web_config_set_tool(self, scope: str, provider: str) -> None:
        if not user_web_provider_service.get(scope, provider):
            await self.mount_notice(t(t(t('尚未配置 `{}`')), provider))
            return

        values = await self.app.push_screen_wait(
            InputScreen(t(t(t('{} 的 MCP 工具名')), provider), [("tool", t(t(t("工具名"))), False)]))
        if not values:
            return

        tool = str(values.get("tool", "")).strip()
        if not tool:
            return

        user_web_provider_service.set_extra_key(scope, provider, "tool", tool)
        await self.mount_notice(t(t(t('已设置 `{}` 的 MCP 工具名为 `{}`')), provider, tool))

    async def _web_config_toggle(self, scope: str, provider: str, enabled: bool) -> None:
        if not user_web_provider_service.set_enabled(scope, provider, enabled):
            await self.mount_notice(t(t(t('尚未配置 `{}`')), provider))
            return

        await self.mount_notice(f"已{t(t(t('启用'))) if enabled else t(t(t('停用')))} `{provider}`")

    async def _web_config_delete(self, scope: str, provider: str) -> None:
        if not user_web_provider_service.get(scope, provider):
            await self.mount_notice(t(t(t('尚未配置 `{}`')), provider))
            return

        user_web_provider_service.remove(scope, provider)
        await self.mount_notice(t(t(t('已删除 `{}` 的配置')), provider))

    # ── /mcp：我的 MCP server（E9） ─────────────────────────────
    # 用户层server默认关闭、且需管理员放行才生效——填了不等于能用。

    def _open_mcp(self) -> None:
        ctx = get_current_ctx()
        rows = mcp_registry.list_user_servers(ctx.uid)

        mode_text = {"ask": t(t(t("每次确认"))), "allow": t(t(t("信任·免确认"))), "deny": t(t(t("不用")))}
        items: list = []
        for row in rows:
            marks = [mode_text.get(row.get("mode") or "ask", t(t(t("每次确认"))))]
            if not row["enabled"]:
                marks.append(t(t(t("已停用"))))
            items.append((row["name"],
                          Text.assemble((row["name"], "bold"),
                                        (f" {row['url']}", "dim"),
                                        (f"（{'，'.join(marks)}）", "dim")),
                          False, row["name"]))

        # Option不过滤，故"添加"与"管理他人"这两项在搜索时仍可达
        items.insert(0, Option(Text(t(t(t("添加 MCP server")))), id=_MCP_ADD))
        limit = mcp_registry.get_user_limit(ctx.uid)
        items.insert(1, Option(Text(t(t(t('设置我可连接的数量上限（当前 {}）')), limit)), id=_MCP_LIMIT))
        if ctx.is_admin:
            items.insert(2, Option(Text(t(t(t("管理其他账号的 MCP 能力")))), id=_MCP_ADMIN))

        title = t(t(t("我的 MCP server"))) if rows else t(t(t("我的 MCP server（尚未配置）")))
        self.app.push_screen(
            SelectScreen(title, items, filterable=len(items) > 8,
                         hint=t(t(t("↑↓ 选择    Enter 继续    Esc 取消")))
                              + (t(t(t("    输入以搜索"))) if len(items) > 8 else "")),
            self._on_mcp_picked)

    def _on_mcp_picked(self, value: Optional[str]) -> None:
        if not value:
            return

        if value == _MCP_ADD:
            self.run_worker(self._mcp_add())
            return
        if value == _MCP_LIMIT:
            self.run_worker(self._mcp_set_my_limit())
            return
        if value == _MCP_ADMIN:
            self._open_mcp_admin_picker()
            return

        self._open_mcp_actions(value)

    def _open_mcp_actions(self, name: str) -> None:
        ctx = get_current_ctx()
        row = mcp_registry.get_user_server(ctx.uid, name)

        items = [("edit", Text(t(t(t("编辑名称 / URL"))))),
                 ("mode", Text(t(t(t("设置审批模式（每次确认 / 信任免确认 / 不用）"))))),
                 ("tools", Text(t(t(t("设置工具权限（allow / ask / deny）")))))]
        if row and row.get("enabled"):
            items.append(("off", Text(t(t(t("停用"))))))
        else:
            items.append(("on", Text(t(t(t("启用"))))))
        items.append(("del", Text(t(t(t("删除"))))))

        self.app.push_screen(SelectScreen(f"{name}", items),
                             partial(self._on_mcp_action_picked, name))

    def _on_mcp_action_picked(self, name: str, value: Optional[str]) -> None:
        if value == "edit":
            self.run_worker(self._mcp_edit(name))
        elif value == "mode":
            self._open_mcp_mode_picker(name)
        elif value == "tools":
            self.run_worker(self._mcp_open_tool_modes(name))
        elif value == "on":
            self.run_worker(self._mcp_toggle(name, True))
        elif value == "off":
            self.run_worker(self._mcp_toggle(name, False))
        elif value == "del":
            self.run_worker(self._mcp_delete(name))

    def _open_mcp_mode_picker(self, name: str) -> None:
        ctx = get_current_ctx()
        row = mcp_registry.get_user_server(ctx.uid, name) or {}
        current = row.get("mode") or "ask"

        descs = {
            "ask": t(t(t("每次调用都要你确认（最安全）"))),
            "allow": t(t(t("信任这个 server，免确认（省事但你自担）"))),
            "deny": t(t(t("不用它"))),
        }
        items = []
        for mode in ("ask", "allow", "deny"):
            mark = t(t(t("   ← 当前"))) if mode == current else ""
            items.append((mode, Text.assemble((mode, "bold"), (f"  {descs[mode]}", "dim"),
                                              (mark, "green"))))

        self.app.push_screen(SelectScreen(t(t(t('{} 的审批模式')), name), items),
                             partial(self._on_mcp_mode_picked, name))

    def _on_mcp_mode_picked(self, name: str, value: Optional[str]) -> None:
        if not value:
            return

        ctx = get_current_ctx()
        try:
            mcp_registry.set_user_server_mode(ctx.uid, name, value)
        except ValueError as err:
            self.run_worker(self.mount_notice(str(err)))
            return

        text = {"ask": t(t(t("每次调用都要确认"))), "allow": t(t(t("信任它、免确认"))), "deny": t(t(t("不用它")))}.get(value, value)
        self.run_worker(self.mount_notice(t(t(t('已把 `{}` 的审批模式设为：{}')), name, text)))

    # ── 工具级权限（E9.2.2）：缺行=继承 server 模式 ──────────────

    async def _mcp_open_tool_modes(self, name: str) -> None:
        """逐工具设 allow/ask/deny。工具名要连上 server 才知道，故先连接再列。"""
        ctx = get_current_ctx()
        row = mcp_registry.get_user_server(ctx.uid, name)
        if not row:
            await self.mount_notice(t(t(t('没有名为 `{}` 的 MCP server')), name))
            return

        try:
            tools = await asyncio.to_thread(_mcp_fetch_tool_names, ctx.uid, row)
        except (MCPError, OSError, ValueError) as err:
            await self.mount_notice(t(t(t('连接 `{}` 失败：{}')), name, str(err)[:120]))
            return

        if not tools:
            await self.mount_notice(t(t(t('`{}` 没有暴露任何工具')), name))
            return

        server_mode = row.get("mode") or "ask"
        mode_text = {"ask": t(t(t("每次确认"))), "allow": t(t(t("信任·免确认"))), "deny": t(t(t("不用")))}
        overrides = mcp_registry.list_tool_modes(ctx.uid, name)

        # 设完一个回到列表看结果；Esc 逐层退出
        while True:
            items: list = []
            for tool in tools:
                override = overrides.get(tool)
                effective = resolve_tool_mode(server_mode, override)
                mark = mode_text[effective] + (t(t(t("，单独设置"))) if override else t(t(t("，继承 server"))))
                items.append((tool, Text.assemble((tool, "bold"), (f"  {mark}", "dim"))))
            items.append((_MCP_TOOLS_CLEAR, Text(t(t(t("全部继承 server 模式"))), style="dim")))

            picked = await self.app.push_screen_wait(
                SelectScreen(t(t(t('{} 的工具权限（server：{}）')), name, mode_text.get(server_mode, server_mode)),
                             items))
            if not picked:
                return

            if picked == _MCP_TOOLS_CLEAR:
                mcp_registry.clear_server_tool_modes(ctx.uid, name)
                overrides = {}
                continue

            mode = await self.app.push_screen_wait(
                SelectScreen(t(t(t('{} 的权限')), picked), _mcp_tool_mode_items(server_mode)))
            if mode is None:
                continue

            if mode == _MCP_TOOL_INHERIT:
                mcp_registry.clear_tool_mode(ctx.uid, name, picked)
            else:
                mcp_registry.set_tool_mode(ctx.uid, name, picked, mode)
            overrides = mcp_registry.list_tool_modes(ctx.uid, name)

    async def _mcp_add(self) -> None:
        ctx = get_current_ctx()
        values = await self.app.push_screen_wait(
            InputScreen(t(t(t("添加 MCP server"))), MCP_SERVER_STEPS))
        if not values:
            return

        name = str(values.get("name", "")).strip()
        url = str(values.get("url", "")).strip()
        if not name or not url:
            await self.mount_notice(t(t(t("名称与 URL 都要填"))))
            return

        try:
            # allow_private=是否管理员：内网/回环的MCP server只有管理员能配
            mcp_registry.upsert_user_server(ctx.uid, name, url, allow_private=ctx.is_admin)
        except ValueError as err:
            await self.mount_notice(str(err))
            return

        granted = mcp_registry.is_user_mcp_granted(ctx.uid)
        if granted:
            await self.mount_notice(
                t(t(t('已添加 `{}`（{}）。默认停用且每次调用需确认——可在 /mcp 里启用它、或把它设为信任免确认。')), name, url))
        else:
            await self.mount_notice(
                t(t(t('已添加 `{}`（{}）。**管理员尚未开放你的 MCP 能力**，开放后才可启用；即使开放，每次调用默认仍需你确认。')), name, url))

    async def _mcp_edit(self, name: str) -> None:
        ctx = get_current_ctx()
        row = mcp_registry.get_user_server(ctx.uid, name)
        if not row:
            await self.mount_notice(t(t(t('没有名为 `{}` 的 MCP server')), name))
            return

        values = await self.app.push_screen_wait(
            InputScreen(t(t(t('编辑 {}')), name), MCP_SERVER_STEPS))
        if not values:
            return

        new_name = str(values.get("name", "")).strip() or name
        url = str(values.get("url", "")).strip() or row["url"]
        try:
            mcp_registry.upsert_user_server(ctx.uid, new_name, url, row.get("headers"),
                                            row.get("mode") or "ask",
                                            allow_private=ctx.is_admin)
        except ValueError as err:
            await self.mount_notice(str(err))
            return
        if new_name != name:
            mcp_registry.remove_user_server(ctx.uid, name)

        await self.mount_notice(t(t(t('已更新 `{}`（{}）')), new_name, url))

    async def _mcp_toggle(self, name: str, enabled: bool) -> None:
        ctx = get_current_ctx()
        row = mcp_registry.get_user_server(ctx.uid, name)
        if not row:
            await self.mount_notice(t(t(t('没有名为 `{}` 的 MCP server')), name))
            return

        if enabled and not mcp_registry.is_user_mcp_granted(ctx.uid):
            await self.mount_notice(
                t(t(t('管理员尚未开放你的 MCP 能力，开放后才能启用 `{}`。')), name))
            return

        mcp_registry.set_user_server_enabled(ctx.uid, name, enabled)
        await self.mount_notice(f"已{t(t(t('启用'))) if enabled else t(t(t('停用')))} `{name}`")

    async def _mcp_delete(self, name: str) -> None:
        ctx = get_current_ctx()
        if not mcp_registry.get_user_server(ctx.uid, name):
            await self.mount_notice(t(t(t('没有名为 `{}` 的 MCP server')), name))
            return

        mcp_registry.remove_user_server(ctx.uid, name)
        await self.mount_notice(t(t(t('已删除 `{}`')), name))

    async def _mcp_set_my_limit(self) -> None:
        """用户自己设"最多同时连几个"。系统层不占这个额度。"""
        ctx = get_current_ctx()
        values = await self.app.push_screen_wait(
            InputScreen(t(t(t("我可连接的数量上限"))), MCP_LIMIT_STEPS))
        if not values:
            return

        raw = str(values.get("limit", "")).strip()
        try:
            limit = int(raw)
        except ValueError:
            await self.mount_notice(t(t(t('上限要填整数，收到的是 `{}`')), raw))
            return

        if limit < 0:
            await self.mount_notice(t(t(t("上限不能为负"))))
            return

        mcp_registry.set_user_limit(ctx.uid, limit)
        await self.mount_notice(t(t(t('已把可连接数量上限设为 {} 个')), limit))

    # ── admin 侧：只管"开不开"该账号的MCP能力 ──────────────────
    # 具体每个server怎么用（审批模式/启停/连几个）由用户自己管

    def _open_mcp_admin_picker(self) -> None:
        users = [r for r in user_service.list_users() if not r["is_admin"]]
        if not users:
            self.run_worker(self.mount_notice(t(t(t("没有可管理的账号")))))
            return

        items: list = []
        for row in users:
            granted = mcp_registry.is_user_mcp_granted(row["uid"])
            mark = t(t(t("已开放"))) if granted else t(t(t("未开放")))
            items.append((row["uid"],
                          Text.assemble((row["username"], "bold"),
                                        (t(t(t('  MCP能力：{}')), mark), "dim")),
                          False, row["username"]))
        self.app.push_screen(SelectScreen(t(t(t("选择账号"))), items), self._on_mcp_admin_picked)

    def _on_mcp_admin_picked(self, value: Optional[str]) -> None:
        if not value:
            return

        rows = user_service.list_users()
        target = next((r for r in rows if r["uid"] == value), None)
        if target is None:
            return

        self._open_mcp_admin_servers(target["uid"], target["username"])

    def _open_mcp_admin_servers(self, uid: str, username: str) -> None:
        granted = mcp_registry.is_user_mcp_granted(uid)
        rows = mcp_registry.list_user_servers(uid)

        items: list = [
            (_MCP_TOGGLE_GRANT,
             Text.assemble((t(t(t('关闭 {} 的 MCP 能力')), username), "bold") if granted
                           else (t(t(t('开放 {} 的 MCP 能力')), username), "bold"),
                           (t(t(t("   ← 当前"))) if not granted else ""),)),
        ]
        for row in rows:
            mode_text = {"ask": t(t(t("每次确认"))), "allow": t(t(t("信任·免确认"))), "deny": t(t(t("不用")))}
            mark = mode_text.get(row.get("mode") or "ask", t(t(t("每次确认"))))
            if not row["enabled"]:
                mark += t(t(t("，已停用")))
            items.append((f"{_MCP_ADMIN_ROW}:{row['name']}",
                          Text.assemble((row["name"], "bold"), (f"  {row['url']}", "dim"),
                                        (f"（{mark}）", "dim")),
                          False, row["name"]))

        title = t(t(t('{} 的 MCP 能力')), username) + ("" if granted else t(t(t("（未开放）"))))
        self.app.push_screen(SelectScreen(title, items), partial(self._on_mcp_admin_row_picked, uid, username))

    def _on_mcp_admin_row_picked(self, uid: str, username: str, value: Optional[str]) -> None:
        if not value:
            return

        if value == _MCP_TOGGLE_GRANT:
            granted = mcp_registry.is_user_mcp_granted(uid)
            mcp_registry.set_user_mcp_granted(uid, not granted)
            self.run_worker(self.mount_notice(
                f"已{t(t(t('关闭'))) if granted else t(t(t('开放')))} `{username}` 的 MCP 能力"))
            return

        if value.startswith(f"{_MCP_ADMIN_ROW}:"):
            # 该账号配了哪些server、审批模式如何——由用户自己管，admin这里只看得见
            name = value.split(":", 1)[1]
            self.run_worker(self.mount_notice(
                t(t(t('`{}` 的审批模式与启停由该用户自己设置；admin 只需决定是否开放其 MCP 能力。')), name)))

    # 档位选择弹窗：只列该身份能到达的档位，每行标注能力与风险。
    # 不做"隐藏T4"式保护——admin是机主本人，风险文案摆出来由他判断，
    # ���匿名/非admin压根看不到这条命令。
    def _open_exec_picker(self) -> None:
        ctx = get_current_ctx()
        session_id = self._current_session_id()
        current = resolve_level(config.EXEC_LEVEL, ctx, session_id)
        override = get_session_override(ctx.uid, session_id) if session_id else None
        ceiling = effective_ceiling(ctx, config.EXEC_LEVEL)

        source = t(t(t("会话内临时设置"))) if override is not None else t(t(t("身份默认")))
        items: list = []
        for level in ExecLevel:
            if level > ceiling:
                break
            if level == current:
                parts = [(f"T{int(level)} {t(LEVEL_PICKER_DESC[level])}", "bold green"), (t(t(t("   ← 当前"))), "green")]
            else:
                parts = [(f"T{int(level)} {t(LEVEL_PICKER_DESC[level])}", "")]
            items.append((level.name, Text.assemble(*parts)))

        if override is not None:
            items.append((_EXEC_RESET, Text.assemble((t(t(t("回到身份默认 "))), ""), (ctx.default_level.name, "dim"))))

        title = (t(t(t('脚本执行档位  当前 T{}（{}）  天花板 T{}')), int(current), source, int(ceiling))
                 if session_id else t(t(t("脚本执行档位（当前无会话，仅可查看）"))))
        self.app.push_screen(SelectScreen(title, items), self._on_exec_picked)

    # SelectScreen的回调是同步的，通知要await mount_notice，故交给worker执行
    def _on_exec_picked(self, value: Optional[str]) -> None:
        if value:
            self.run_worker(self._apply_exec_choice(value))

    async def _apply_exec_choice(self, value: str) -> None:
        ctx = get_current_ctx()
        session_id = self._current_session_id()
        if not session_id:
            await self.mount_notice(t(t(t("当前会话不可用，无法临时设置档位"))))
            return

        if value == _EXEC_RESET:
            set_session_override(ctx.uid, session_id, None)
            await self.mount_notice(t(t(t('已回到身份默认档位 **{}**')), ctx.default_level.name))
            return

        requested = parse_level(value)
        set_session_override(ctx.uid, session_id, requested)
        effective = resolve_level(config.EXEC_LEVEL, ctx, session_id)
        detail = RISK_TEXT[effective]
        if effective != requested:
            detail += (t(t(t('；这个身份/部署的上限是 **{}**，选择无法突破它。')), effective_ceiling(ctx, config.EXEC_LEVEL).name))

        await self.mount_notice(t(t(t('本会话脚本执行档位：**{}** —— {}')), effective.name, detail))

    async def _open_exec(self, args: list) -> None:
        """查看或会话内临时调整脚本执行档位（§10）"""
        ctx = get_current_ctx()
        session_id = self._current_session_id()

        if not args:
            self._open_exec_picker()
            return

        if args[0] == "reset":
            if session_id:
                set_session_override(ctx.uid, session_id, None)
            await self.mount_notice(
                t(t(t('已回到身份默认档位 **{}**')), ctx.default_level.name)
            )
            return

        raw = args[0]
        if not raw.lower().startswith("level="):
            await self.mount_notice(t(t(t("用法：`/exec level=T0`~`/exec level=T4`，或 `/exec reset`"))))
            return

        requested = parse_level(raw.split("=", 1)[1])
        if not session_id:
            await self.mount_notice(t(t(t("当前会话不可用，无法临时设置档位"))))
            return

        set_session_override(ctx.uid, session_id, requested)
        effective = resolve_level(config.EXEC_LEVEL, ctx, session_id)

        if effective != requested:
            await self.mount_notice(
                t(t(t('已请求 **{}**，但实际生效 **{}**（{}）；这个身份/部署的上限是 **{}**，无法突破它。')), requested.name, effective.name, RISK_TEXT[effective], effective_ceiling(ctx, config.EXEC_LEVEL).name)
            )
            return

        await self.mount_notice(
            t(t(t('本会话档位已切到 **{}** —— ⚠️ {}（仅本次会话生效，`/exec reset` 可随时回到默认）')), effective.name, RISK_TEXT[effective])
        )

    async def _execute_remember(self, content: str) -> None:
        ctx = get_current_ctx()
        if ctx.is_default:
            await self.mount_notice(t(t(t("长期记忆仅登录后可用"))))
            return

        instruction = build_remember_instruction(content)
        if instruction is None:
            await self.mount_notice(remember_hint(content))
            return

        # 指令原样上屏，用户能确认模型实际收到的是什么
        await self.send_message(instruction)
    # endregion

    # region 宿主必须实现的钩子
    async def do_new_session(self) -> None: ...
    async def do_switch_session(self, entry: SessionEntry) -> None: ...
    async def mount_notice(self, text: str) -> None: ...
    async def mount_memory(self, text: str) -> None: ...
    async def send_message(self, text: str) -> None: ...
    async def mount_help(self) -> None: ...
    async def do_identity_changed(self, ctx, keep_session: bool) -> None: ...
    # endregion
