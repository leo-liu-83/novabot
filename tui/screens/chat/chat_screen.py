# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : chat_screen.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 15:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 聊天主页面

import asyncio
import json
import time
from typing import AsyncIterator, Optional

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.content import Content
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Collapsible, Label, Markdown, RichLog, Static
from rich.markup import escape
from rich.text import Text

from api.v1.chat.schema import ChatInputSchema
from api.v1.chat.service import ChatService, ChatEvent
from config import config
from const.messages import msg
from core.agent import persistence
from core.agent.deep_agents.exec_policy import ExecLevel, resolve_level
from core.llm.context_budget import BASE_FLOOR, limits_for_model
from core.llm.token_counter import count_tokens_cjk
from exception.session_exception import SessionAccessError
from service.attachment.attachments import (
    AttachmentError,
    decide,
    find_paths,
    from_json,
    human_size,
    kind_label,
    max_per_message,
    stage,
)
from rich.text import Text as RichText

from ...widgets import ATTACHMENT_TOKEN_RE, CustomTextArea, CopyButton, DotSpinner, RetryButton
from ..commands import CommandMixin
from ..auth import identity_label
from ..select_screen import SelectScreen
from service.user.user_service import get_current_ctx
from . import style
from ..sessions import (
    SessionEntry,
    count_messages,
    create_session,
    get_messages,
    update_session,
)
from tui.i18n import t

# 打开会话时默认加载的最近消息条数；更早的按页在上滑到顶时取
HISTORY_LIMIT = 30

# 首屏同步挂载的最近消息条数：其余历史在滚动/视口不足时分批补挂。
# 一页 30 条全部同步挂载意味着把每条长回答/思考的 Markdown 都解析一遍，
# 实测一个 28 条的会话要 3-4 秒才有内容；先挂最近几条，打开即读。
REPLAY_TAIL_MESSAGES = 4
# 每次补挂的消息条数：一条长回答的 Markdown 就要 100-200ms，
# 一次挂太多又会变成一次明显的卡顿
REPLAY_CHUNK_MESSAGES = 4

# 输入框下方右侧快捷键提示的可见宽度（"Ctrl+N newline  Enter submit  Ctrl+Q quit "）。
# 布局还没算出来（size 为 0）时用它估算项目信息的可用宽度。
SHORTCUTS_WIDTH = len("Ctrl+N newline  Enter submit  Ctrl+Q quit ")
# 提示行的固定开销：chat-container 左右 padding 各 2 + #chat-tags-box 的 padding-left 1
HINTS_CHROME = 5
# 项目名与路径之间的分隔符 " · " 宽度
SEP_WIDTH = 3


def _elide_middle(text: str, limit: int) -> str:
    """路径按中间省略：保留开头与结尾，根目录和文件名都还看得见。"""
    if limit < 1 or len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    head = max(1, (limit - 1) // 3)
    tail = limit - 1 - head
    if tail > 0:
        return f"{text[:head]}…{text[-tail:]}"
    return f"{text[:head]}…"


def project_segment(name: Optional[str], path: Optional[str], avail: int) -> str:
    """输入框下方的项目信息：项目名 + 项目目录，与 Web 输入框下的项目控件同口径。

    avail 是这一行里项目信息可用的字符宽度（已扣掉右侧快捷键与左侧执行提示）；
    路径按中间省略，保证不会被父容器裁掉尾巴上的目录名。
    无项目时显示“无项目”；只有名称没有目录（旧数据）时只显示名称。
    """
    name = (name or "").strip()
    path = (path or "").strip()
    if not name and not path:
        return f"[dim]{t('无项目')}[/]"

    if not path:
        return escape(_elide_middle(name, avail))

    room = avail - (len(name) + SEP_WIDTH if name else 0)
    if room < 1:
        # 项目名自己就超宽：先省略名称，至少留 1 格给路径，别把整行撑出屏幕
        name = _elide_middle(name, max(1, avail - SEP_WIDTH - 1))
        room = max(1, avail - len(name) - SEP_WIDTH)
    path_text = escape(_elide_middle(path, room))
    if not name:
        return f"[dim]{path_text}[/]"

    return f"{escape(name)} [dim]· {path_text}[/]"


class ChatLog(ScrollableContainer):
    """消息列表：滚到顶部时通知宿主加载更早的一页。"""

    class TopReached(Message):
        """滚动位置回到顶部（scroll_y 归零）"""

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if old_value > 0 and new_value <= 0:
            self.post_message(self.TopReached())


class ChatScreen(CommandMixin, Screen):
    """聊天页面：/ 命令弹出面板、流式双分区、interrupt确认"""

    CSS = style.CSS

    # 单轮内连续自动放行上限，超过则恢复人工确认，防模型tool call死循环
    AUTO_APPROVE_LIMIT = 5

    # 仅在待确认时生效（action内判空），不影响正常打字
    BINDINGS = [
        ("y", "approve", "Approve"),
        ("n", "reject", "Reject"),
        ("left", "confirm_left", "Left"),
        ("right", "confirm_right", "Right"),
        ("enter", "confirm_submit", "Confirm"),
        ("escape", "interrupt_run", "Interrupt"),
    ]

    def __init__(self, first_message: str | None = None, session: Optional[SessionEntry]=None, **kwargs):
        super().__init__(**kwargs)
        self._session = session or create_session(title=first_message or "新对话")
        self.session_id = self._session["id"]
        self.enable_thinking = self._session["thinking"]
        self._model_notice = self._resolve_session_model(self._session["model"])
        self.first_message = first_message
        self._first_message_processed = False
        self._pending_decision: Optional[asyncio.Event] = None
        self._decision_value = "reject"
        # 确认面板光标位与"本会话自动允许"开关（切换会话重置）
        self._confirm_idx = 0
        self._auto_after_approve = False
        self._always_approve = False
        # 单轮自动放行计数：模型循环发tool call时防止无限auto-approve
        self._auto_streak = 0
        # 本轮是否已提示过"达上限恢复人工确认"，避免同轮反复刷同一行
        self._auto_limit_notified = False
        # agent执行中标记与worker句柄（esc中断用）。
        # 注意：属性名不能叫_running——那是Textual MessagePump的内部标志（表示消息泵在跑），
        # 同名会被框架覆写成True，导致空闲时按ESC反复误报"已中断执行"。
        self._agent_running = False
        self._response_worker = None
        # 执行中的工具调用名集合（输入框上方状态条）
        self._active_tools: set[str] = set()
        # 会话累计token统计（usage精确值 + progress实时覆盖速度/进行中量）
        # 重开历史会话时恢复上次累计的token统计
        self._tok_in = self._session.get("tokens_in") or 0
        self._tok_out = self._session.get("tokens_out") or 0
        self._tok_tps = self._session.get("speed") or 0.0
        # 上下文占用：优先用落库快照；没有且已有历史时按同一计数器估算并标 ~
        self._ctx_tokens = self._session.get("context_tokens") or 0
        self._ctx_limit = self._session.get("context_limit") or 0
        self._ctx_limit_resolved = bool(self._ctx_limit)
        self._ctx_estimate = False
        self._ctx_pending = False
        self._ctx_model = self.current_model()
        # 运行世代序号：exclusive worker重入时防止旧worker的finally误关新worker的spinner
        self._run_seq = 0
        self._message_count = 0
        # 会话世代号：切换会话自增，旧世代worker立即停写
        self._generation = 0
        # thread在本进程未激活过的历史会话：UI重放 + 首次发送携带history回灌上下文
        self._replay_messages: list[dict] = []
        self._last_user_row: Optional[Widget] = None
        # 本条消息的附件计划：闸门拒发时原样还回输入区（文件已经暂存过，计划才是真相）
        self._last_send_plans: list[dict] = []
        self._degraded_shown = False
        # 分页状态：已加载页里最早那条消息的 id（上滑取它之前的一页），以及取页防重入标记
        self._oldest_message_id: Optional[int] = None
        self._loading_earlier = False
        # 已取到但还没挂载的旧消息（按时间顺序，越靠前越旧）：首屏只挂最近几条
        self._pending_groups: list[tuple[dict, list]] = []
        # 待发送附件（E10.4）：只存判定结果，复制发生在真正发送的那一刻——
        # 粘完又反悔不该在磁盘上留下副本
        self._pending_attachments: list[dict] = []
        # "本会话都同意"复制：进程内、不落盘、切换会话即失效（与档位会话覆盖同一姿态）
        self._attach_trust_session = False
        self._prepare_replay()

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-container"):
            with Container(id="chat-header-area"):
                with Horizontal(id="chat-header"):
                    yield Label(t(t(t("# 新对话"))), classes="chat-title", id="chat-title")
                    yield Label("", classes="chat-stats", id="chat-stats")
                    yield Label("", classes="chat-think", id="chat-think")
                    yield Label("", classes="chat-identity", id="chat-identity")

            with Vertical(id="messages-scroll"):
                with ChatLog(id="messages-list"):
                    pass

            with Container(id="confirm-bar"):
                with Container(id="confirm-wrapper"):
                    yield RichLog(id="confirm-detail", markup=True, wrap=True, auto_scroll=False, highlight=False)
                    with Horizontal(id="confirm-buttons"):
                        yield Button(t(t(t("允许一次 (y)"))), id="confirm-yes")
                        yield Button(t(t(t("本会话自动允许"))), id="confirm-auto")
                        yield Button(t(t(t("拒绝 (n)"))), id="confirm-no")
                        yield Label("", id="confirm-keys")

            yield from self.compose_command_popup()

            # 工具调用状态条：有工具执行中时显示于输入框上方
            with Horizontal(id="tool-status"):
                yield DotSpinner(id="tool-spinner")
                yield Label("", id="tool-text")

            # 附件以 [Image 1] token 的形式内联在输入框里（OpenCode 风格），
            # 没有独立的附件条；删掉 token 就等于移除附件。

            # 模型行：输入框上方常显（原先在输入框下方，与项目信息挤在同一行）
            with Container(id="chat-model-line"):
                yield Label("", id="chat-model")

            with Container(id="chat-input-area"):
                with Container(id="chat-input-container"):
                    yield CustomTextArea(
                        placeholder=t(t(t("Ask anything...  (输入 / 唤起命令)"))),
                        id="chat-input"
                    )

            with Container(id="input-hints"):
                with Horizontal(id="hints-row"):
                    # spinner+esc提示占位；项目信息常显，右侧快捷键位置不受影响
                    with Container(id="chat-tags-box"):
                        yield DotSpinner(id="chat-running")
                        yield Label("[dim]esc[/dim] interrupt", id="chat-interrupt")
                        yield Label("", id="chat-project")
                    yield Label("[dim]Ctrl+N[/dim] newline  [dim]Enter[/dim] submit  [dim]Ctrl+Q[/dim] quit ", id="chat-shortcuts")

    async def on_mount(self) -> None:
        self.query_one("#confirm-bar", Container).display = False
        self.query_one("#command-popup", Container).display = False
        self.query_one("#tool-status", Horizontal).display = False
        self.query_one("#tool-spinner", DotSpinner).can_focus = False
        # 初始隐藏一律用代码设置；CSS里写display:none会在刷新时覆盖运行时状态
        self.query_one("#chat-running", DotSpinner).display = False
        self.query_one("#chat-interrupt", Label).display = False
        # 粘贴时先给附件识别一次机会（只摘路径，普通文本照常插入）
        self.query_one(f"#{self._cmd_input_id}", CustomTextArea).paste_handler = self._handle_paste

        # 确认按钮只做鼠标点击目标，键盘决策由screen绑定统一处理，避免抢焦点吞键
        for bid in ("confirm-yes", "confirm-auto", "confirm-no"):
            self.query_one(f"#{bid}", Button).can_focus = False
        self.query_one("#confirm-detail", RichLog).can_focus = False
        self.query_one("#confirm-keys", Label).update(t("[dim]⇄ 选择  enter 确认[/dim]"))

        self.bind_command_input()
        self._refresh_cmd_status()
        self._refresh_identity()

        if self.first_message and not self._first_message_processed:
            self._first_message_processed = True
            # 首页提交的内容可能只是粘贴进来的文件路径（以 / 开头）：先摘附件，
            # 否则绝对路径会被当成命令执行，或整段路径被当正文发给模型。
            paths, remainder = find_paths(self.first_message)
            if paths:
                self._add_pending(paths)
                self.first_message = remainder
            # 欢迎页提交的/开头消息按命令执行，不作为query发送
            if self.first_message.startswith("/"):
                await self._execute_command(self.first_message)
            elif self.first_message:
                if self._replay_messages:
                    self._replay_ui(self._replay_messages)
                    self._replay_messages = []
                self._send(self.first_message)
        elif self._replay_messages:
            self._replay_ui(self._replay_messages)
            self._replay_messages = []

        # 会话绑定模型不可用时的回退提示
        if self._model_notice:
            await self.mount_notice(self._model_notice)
            self._model_notice = ""

        # 无论是否带首条消息，焦点都必须落在输入框；
        # 8.x的ScrollableContainer默认可聚焦，会在挂载时抢走键盘焦点
        messages_list = self.query_one("#messages-list", ScrollableContainer)
        messages_list.can_focus = False
        self.call_later(self.set_focus_on_mount)

    def set_focus_on_mount(self) -> None:
        """页面加载后设置输入框焦点"""
        try:
            text_area = self.query_one("#chat-input", CustomTextArea)
            text_area.focus()
        except Exception:
            pass

    def _refresh_cmd_status(self) -> None:
        state = {True: "[goldenrod]think:on[/]", False: "[dim]think:off[/]", None: "[dim]think:auto[/]"}[self.enable_thinking]
        # 切模型后窗口可能变化：旧的 limit/百分比必须失效，下次渲染按新模型解析
        current = self.current_model()
        if getattr(self, "_ctx_model", current) != current:
            self._ctx_model = current
            # 旧占用是旧窗口下量出来的：留着它会在小窗口模型上算出 >100% 的荒谬百分比。
            # 缓存条目也要一起清——切走再切回是从 _session 恢复界面的
            self._ctx_tokens = 0
            self._ctx_estimate = False
            self._ctx_limit = 0
            self._ctx_limit_resolved = False
            self._session["context_tokens"] = 0
            self._session["context_limit"] = 0
        # 头部只留 speed / ctx / msgs；令牌计数逻辑仍在服务层与缓存里维护
        parts = [
            # 降级标记放在最前面：它意味着"重启会丢这段对话"，比任何统计都值得先看一眼
            "" if persistence.is_persistent() else t(t(t("[red]⚠ 不持久化[/]"))),
            f"[dim]speed [/dim]{self._tok_tps:.1f} t/s",
            self._context_segment(),
            f"{self._message_count} msgs",
        ]
        self.query_one("#chat-stats", Label).update(" · ".join(x for x in parts if x))
        self.query_one("#chat-think", Label).update(state)
        self.query_one("#chat-model", Label).update(f"[b]Build[/] [dim]· {self.current_model()}[/]")
        self.query_one("#chat-project", Label).update(project_segment(
            self._session.get("project_name"),
            self._session.get("project_path"),
            self._project_avail_width(),
        ))

    def _project_avail_width(self) -> int:
        """项目信息可用的字符宽度：提示行总宽减去右侧快捷键与左侧 spinner/esc 提示。

        布局还没算出来（首帧、size 为 0）时按屏幕宽度估一个，交给下一次刷新修正。
        """
        try:
            total = self.query_one("#hints-row").size.width
            used = self.query_one("#chat-shortcuts").size.width
            if self._agent_running:
                used += self.query_one("#chat-running").size.width
                used += self.query_one("#chat-interrupt").size.width
            if total:
                return max(1, total - used - 1)  # 1 = #chat-tags-box 的 padding-left
        except Exception:
            pass
        return max(1, (self.size.width or 80) - HINTS_CHROME - SHORTCUTS_WIDTH)

    def on_resize(self) -> None:
        """窗口宽度变化后重新截断项目路径，否则换宽后仍按旧宽度省略/溢出。"""
        if not self.is_mounted:
            return
        self._refresh_cmd_status()

    # 模型窗口：落库快照没有时按当前模型解析一次，失败则只显示绝对值
    def _context_limit(self) -> int:
        if not self._ctx_limit_resolved:
            self._ctx_limit_resolved = True
            limits = limits_for_model(self.model)
            self._ctx_limit = limits.window if limits else 0
        return self._ctx_limit

    # 头部 ctx 段：<85% 常规前景色，≥85% goldenrod 并提示将压缩，≥95% red。
    # 百分比 <10% 保留一位小数（1M 窗口下整数会永远是 0%）；配色与阈值用同一个展示值判断
    def _context_segment(self) -> str:
        value = f"{'~' if self._ctx_estimate else ''}{self._fmt_tok(self._ctx_tokens)}"
        limit = self._context_limit()
        shown = None
        if limit > 0:
            pct = self._ctx_tokens / limit * 100
            if pct < 10:
                shown = pct
                value += f" ({pct:.1f}%)"
            else:
                shown = float(round(pct))
                value += f" ({int(shown)}%)"

        if shown is not None and shown >= 95:
            color = "red"
        elif shown is not None and shown >= 85:
            color = "goldenrod"
        else:
            color = None

        body = value if color is None else f"[{color}]{value}[/]"
        suffix = ""
        if self._ctx_pending or (shown is not None and shown >= 85):
            suffix = t(" · [{}]将压缩[/]", color or "goldenrod")
        return f"[dim]ctx [/dim]{body}{suffix}"

    # 会话键失效（库被重置/在别处删掉）不是模型配置问题：给错的修复指引只会让人去改配置
    async def _mount_session_error(self) -> None:
        slot: list = [None, None]
        await self._ensure_section("answer", slot)
        if slot[1] is not None:
            await slot[1].write(
                t(t(t("会话已失效：这个会话标识不再属于当前账号（可能已被删除，或数据库被重置）。\n\n"
                "- 用 `/new` 新建会话继续\n"
                "- 旧历史如果还在，可在 `/session` 里重新打开")))
            )
            await slot[1].stop()
        self.call_later(self._scroll_to_bottom)

    # 构建期配置错误（模型不存在等）转正文提示，附修复指引；不抛异常击穿worker
    async def _mount_config_error(self, detail: str) -> None:
        slot: list = [None, None]
        await self._ensure_section("answer", slot)
        if slot[1] is not None:
            await slot[1].write(
                t(t(t('当前默认模型不可用，请检查 `novabot.json` 配置。\n\n- 错误：{}\n- 若用 `/model` 指定过模型，可用 `/model` 重选一个可用模型\n- 否则请编辑配置文件中的 `agent.model` 及 `models` 段，确保对应 provider 与模型存在且已配置密钥\n- 修改配置后需重启 NovaBot 生效')), detail)
            )
            await slot[1].stop()
        self.call_later(self._scroll_to_bottom)

    # agent执行中：显示主题色运行图标与esc中断提示（项目信息保持常显）
    def _set_agent_running(self, running: bool, gen: int, seq: int) -> None:
        # 运行标志只认最新一代worker：切换会话会取消旧worker，其finally带着旧世代号进来，
        # 若也写标志，会把已取消的执行标成"仍在跑"，空闲时按ESC反复误报"已中断执行"。
        if seq == self._run_seq:
            self._agent_running = running

        # 世代守卫只用于UI：旧worker的收尾不能再改动画针与提示条
        if self._generation != gen or self._run_seq != seq:
            return
        # 执行结束（含中断/异常）时清空工具状态条，避免残留
        if not running:
            self._active_tools.clear()
        try:
            self.query_one("#chat-running", DotSpinner).display = running
            interrupt = self.query_one("#chat-interrupt", Label)
            if running:
                interrupt.update("[dim]esc[/dim] interrupt")
            interrupt.display = running
            # 项目信息常显：执行中也保留，与 Web 输入框下的项目控件一致。
            # spinner/esc 出现后可用宽度变窄，布局算完再重新截断一次路径。
            self.query_one("#tool-status", Horizontal).display = running and bool(self._active_tools)
            self.call_after_refresh(self._refresh_cmd_status)
        except Exception:
            pass

    # 工具调用完成记录：与思考/正文同级保留在消息流中（含成败标记）。
    # 落库在服务层记录器里做（tool_end 事件带 desc 与 ok，界面不再写库）
    def _mount_tool_record(self, desc: str, ok: bool = True) -> None:
        self.query_one("#messages-list", ScrollableContainer).mount(
            Label(self._tool_line_markup(desc, ok), classes="tool-call-line")
        )
        self._scroll_to_bottom()

    # 工具记录行：状态符号在前（绿✓/红✗），正文dim
    @staticmethod
    def _tool_line_markup(desc: str, ok: bool) -> str:
        mark = "[green]✓[/]" if ok else "[red]✗[/]"
        return f"{mark} [dim]{escape(desc)}[/]"

    # 头部token统计：progress实时刷新，usage轮末精确累计并落库
    @staticmethod
    def _fmt_tok(n: float) -> str:
        return f"{n / 1000:.1f}k" if n >= 10000 else str(int(n))

    @staticmethod
    def _fmt_secs(seconds: float) -> str:
        seconds = max(0.0, float(seconds or 0))
        if seconds < 60:
            return f"{seconds:.1f}s"
        return f"{int(seconds) // 60}m {int(seconds) % 60:02d}s"

    def _on_token_progress(self, stats: dict) -> None:
        # 生成中仅刷新speed；total/in/out等待模型返回usage实际值后更新
        self._tok_tps = stats.get("tps") or 0.0
        self._refresh_cmd_status()

    def _on_token_usage(self, stats: dict) -> None:
        # 落库由服务层记录器完成；这里只累计给头部显示、并同步内存缓存条目
        self._tok_in += stats.get("input") or 0
        self._tok_out += stats.get("output") or 0
        self._tok_tps = stats.get("tps") or self._tok_tps
        # 同步内存缓存条目（与SESSIONS同引用），切换会话时从这里恢复
        self._session["tokens_in"] = self._tok_in
        self._session["tokens_out"] = self._tok_out
        self._session["speed"] = self._tok_tps
        self._refresh_cmd_status()

    # 每次模型调用后的真实占用：更新头部与缓存（落库在服务层记录器里）
    def _on_context(self, stats: dict) -> None:
        self._ctx_tokens = stats.get("tokens") or 0
        # 事件是唯一事实来源：新模型没有窗口时 limit 就是 0，不能沿用旧模型的
        self._ctx_limit = stats.get("limit") or 0
        self._ctx_limit_resolved = True
        self._ctx_estimate = stats.get("source") != "usage"
        self._ctx_pending = bool(stats.get("compact_pending"))

        self._session["context_tokens"] = self._ctx_tokens
        self._session["context_limit"] = self._ctx_limit
        self._refresh_cmd_status()

    # 压缩条目的唯一渲染入口：实时事件与历史重放共用，避免刷新后长得不一样
    def _compact_widget(self, stats: dict) -> Collapsible:
        before = self._fmt_tok(stats.get("before") or 0)
        after = self._fmt_tok(stats.get("after") or 0)
        messages = stats.get("messages") or 0
        title = t(t(t('已压缩 {} 条消息 · {} → {} tok')), messages, before, after)

        lines = [stats.get("summary") or t(t(t("（摘要为空）")))]
        path = stats.get("file")
        if path:
            lines += ["", t(t(t('[dim]历史已归档：{}[/dim]')), path)]
        else:
            lines += ["", t(t(t("[yellow]摘要已生成，但历史文件未写入[/yellow]")))]

        return Collapsible(Markdown("\n".join(lines), classes="assistant-message"),
                           title=title, collapsed=True)

    # 压缩发生：消息流插一条默认收起的条目，展示前后占用、摘要与归档路径
    def _on_compact(self, stats: dict) -> None:
        if not stats.get("ok"):
            # 失败由紧随其后的 error 事件负责可读提示，这里不重复刷一条
            return

        self.query_one("#messages-list", ScrollableContainer).mount(self._compact_widget(stats))
        self._ctx_tokens = stats.get("after") or self._ctx_tokens
        self._ctx_estimate = False
        self._ctx_pending = False
        self._refresh_cmd_status()
        self._scroll_to_bottom()

    # 工具调用状态条：输入框上方显示进行中的工具（不含入参），spinner动画提示
    def _set_tool_active(self, name: str, active: bool) -> None:
        if active:
            self._active_tools.add(name)
        else:
            self._active_tools.discard(name)

        try:
            bar = self.query_one("#tool-status", Horizontal)
            bar.display = bool(self._active_tools)
            if self._active_tools:
                names = "、".join(sorted(self._active_tools))
                self.query_one("#tool-text", Label).update(t(t(t('[dim]正在调用工具:[/] {} …')), names))
        except Exception:
            pass

    # esc中断当前agent执行（含等待确认中的interrupt）
    def action_interrupt_run(self) -> None:
        if not (self._agent_running or self._pending_decision is not None):
            return
        worker = self._response_worker
        self._response_worker = None
        if worker is not None:
            worker.cancel()
        # 这里主动复位，不依赖worker的finally：worker若已结束或取消未触发清理，
        # 标志会卡在True，空闲时再按ESC又会重复追加"已中断执行"
        self._set_agent_running(False, self._generation, self._run_seq)
        self._pending_decision = None
        self._hide_confirm_bar()
        self.query_one("#messages-list", ScrollableContainer).mount(
            self._notice_label(t(t(t("[dim]— 已中断执行 —[/dim]"))))
        )
        self._scroll_to_bottom()

    # 界面重放数据与分页游标。上下文回灌不在这儿——那是服务层按窗口预算决定的事
    def _prepare_replay(self) -> None:
        # 默认只取最近一页；更早的由上滑分页取（库里是全的，一条不丢）
        self._oldest_message_id = None
        self._replay_messages = get_messages(self.session_id, HISTORY_LIMIT)
        if self._replay_messages:
            self._oldest_message_id = self._replay_messages[0].get("id")

        # 没有落库快照但已有历史：用与压缩相同的计数器估算，界面标 ~。
        # tool 记录行没有 tool_call_id，不能直接交给 convert_to_messages，先按角色过滤。
        if not getattr(self, "_ctx_tokens", 0):
            estimated = [m for m in self._replay_messages if m.get("role") in ("user", "assistant")]
            if estimated:
                try:
                    self._ctx_tokens = count_tokens_cjk(estimated) + BASE_FLOOR
                    self._ctx_estimate = True
                except Exception:
                    # 估算失败不能拦住会话打开；下一轮真实 usage 会补上
                    self._ctx_tokens = 0

    # 历史消息静态重放（think块按原样式还原），顺序与落库顺序一致
    def _replay_ui(self, messages: list[dict]) -> None:
        users = [m for m in messages if m["role"] == "user"]
        self._message_count = len(users)

        # 还有更早的没加载时，这一页的最早一条不是会话首条，不能用它命名标题
        if users and self._earlier_remaining() == 0:
            first = users[0]["content"]
            self.set_chat_title(first[:30] + "..." if len(first) > 30 else first)

        # 先挂最近几条：用户打开会话看的是最新内容，不该等整页 Markdown 解析完。
        # 更旧的留在 _pending_groups 里，上滑到顶或视口没占满时再分批补（见 _mount_pending）。
        tail_start = max(0, len(messages) - REPLAY_TAIL_MESSAGES)
        self._pending_groups = [
            (m, self._message_widgets([m])) for m in messages[:tail_start]
        ]
        widgets = self._message_widgets(messages[tail_start:])
        self.query_one("#messages-list", ScrollableContainer).mount(*widgets)
        self._refresh_earlier_hint()
        self._scroll_to_bottom()
        self._refresh_cmd_status()
        # 一页还没占满可视区时（滚不动就无法触发上滑），继续加载下一页
        self.call_after_refresh(self._fill_viewport)

    # 按落库顺序把消息记录转成widget；不挂载——分页时要整页插到列表顶部
    @staticmethod
    def _turn_usage(message: dict) -> dict:
        """历史 assistant 行上的本轮统计；没有或格式不对时返回空（不显示小计行）。"""
        try:
            meta = json.loads(message.get("meta") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        turn = meta.get("turn") if isinstance(meta, dict) else None
        if not isinstance(turn, dict):
            return {}
        if not (turn.get("input") or turn.get("output")):
            return {}
        return turn

    def _message_widgets(self, messages: list[dict]) -> list:
        widgets: list = []

        for m in messages:
            if m["role"] == "user":
                widgets.append(self._user_row(m["content"], from_json(m.get("attachments") or "")))
                # 整轮没有 assistant 正文时（只调工具就被打断）本轮统计挂在这条用户行上
                turn = self._turn_usage(m)
                if turn:
                    widgets.append(self._turn_total_label(turn))
            elif m["role"] == "tool":
                widgets.append(Label(self._tool_line_markup(m["content"], not m.get("think")),
                                     classes="tool-call-line"))
            elif m["role"] == "compact":
                # 压缩条目落库后必须按原样回到消息流：只靠 session.context_tokens
                # 能恢复数字，却恢复不了"这里压过什么"。
                try:
                    stats = json.loads(m.get("meta") or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    stats = {}
                if isinstance(stats, dict) and stats:
                    widgets.append(self._compact_widget(stats))
                elif m.get("content"):
                    widgets.append(self._notice_label(m["content"]))
            else:
                if m.get("think"):
                    widgets.append(
                        Container(
                            Container(
                                Label("[goldenrod italic]Thinking:[/]", classes="thinking-prefix"),
                                # 历史思考按纯文本重放：思考正文常是回答的数倍（实测一个
                                # 28 条的会话里 think 84k 字），交给 Markdown 解析会生成
                                # 上千个 block 控件，光是打开会话就要 2 秒以上。
                                Static(RichText(m["think"]), classes="thinking-message", markup=False),
                                classes="thinking-message-wrapper",
                            ),
                            classes="thinking-message-area",
                        )
                    )
                if m["content"]:
                    block, _md = self._answer_block(m["content"])
                    widgets.append(block)
                # 每轮统计随消息行落库（meta.turn）：不还原它，重开会话后
                # 历史轮次的「本轮 in/out」就全没了，只剩刚跑完那一条还在内存里
                turn = self._turn_usage(m)
                if turn:
                    widgets.append(self._turn_total_label(turn))

        return widgets

    def _turn_total_label(self, turn: dict) -> Label:
        """一行「本轮 in/out/耗时」：assistant 正文行与被打断的用户行共用。"""
        return Label(
            t("[dim]本轮 in {} · out {} · 耗时 {}[/dim]",
              self._fmt_tok(turn.get("input") or 0),
              self._fmt_tok(turn.get("output") or 0),
              self._fmt_secs(turn.get("elapsed") or 0)),
            classes="turn-total",
        )

    # region 上滑分页
    def _earlier_remaining(self) -> int:
        """比已加载最早那条还早的消息数。实时查：别的端可能还在往会话里追加。"""
        oldest = getattr(self, "_oldest_message_id", None)
        if oldest is None:
            return 0
        return count_messages(self.session_id, before_id=oldest)

    def _earlier_hidden(self) -> int:
        """还没挂到界面上的更早消息数：已取到但没挂的 + 库里还没取的。"""
        return len(self._pending_groups) + self._earlier_remaining()

    def _has_earlier(self) -> bool:
        return self._earlier_hidden() > 0

    def _refresh_earlier_hint(self) -> None:
        """列表顶部放一行"还有多少条更早"；加载完就撤掉。"""
        msg_list = self.query_one("#messages-list", ScrollableContainer)
        for child in list(msg_list.children):
            if child.has_class("load-earlier"):
                child.remove()

        remaining = self._earlier_hidden()
        if remaining <= 0:
            return

        hint = self._notice_label(t(t(t('[dim]↑ 还有 {} 条更早的消息，上滑加载[/dim]')), remaining))
        hint.add_class("load-earlier")
        msg_list.mount(hint, before=0)

    def _mount_pending(self, keep_position: bool) -> bool:
        """把最靠近当前内容的几条旧消息插到列表顶部；返回是否挂了东西。

        keep_position=True 用于用户上滑：把原来顶部那条锚回视口顶部，
        新挂的旧消息在它上方，视觉位置不跳。False 用于首屏补满视口：
        挂完继续贴底，用户仍然停在最新一条。
        """
        if not self._pending_groups:
            return False

        batch = self._pending_groups[-REPLAY_CHUNK_MESSAGES:]
        self._pending_groups = self._pending_groups[:-len(batch)]
        widgets = [widget for _message, group in batch for widget in group]

        msg_list = self.query_one("#messages-list", ScrollableContainer)
        anchor = next((w for w in msg_list.children if not w.has_class("load-earlier")), None)
        if anchor is not None:
            msg_list.mount(*widgets, before=anchor)
        else:
            msg_list.mount(*widgets)

        self._refresh_earlier_hint()
        if keep_position and anchor is not None:
            self.call_after_refresh(
                lambda: msg_list.scroll_to_widget(anchor, animate=False, top=True)
            )
        elif not keep_position:
            self.call_after_refresh(self._scroll_to_bottom)
        return True

    def on_chat_log_top_reached(self, event: ChatLog.TopReached) -> None:
        self._request_earlier()

    def _fill_viewport(self) -> None:
        try:
            msg_list = self.query_one("#messages-list", ScrollableContainer)
            # Textual 8 的 ScrollableContainer 没有 window_size（旧名）：
            # 取容器内容区高度，判断首屏是否还没占满、滚不动
            fits = msg_list.virtual_size.height <= msg_list.container_size.height
        except Exception:
            return

        if not fits:
            return
        # 首屏只有最近几条：先补挂已取到的旧消息，占满视口后才去库里翻页
        if self._mount_pending(keep_position=False):
            self.call_after_refresh(self._fill_viewport)
            return
        self._request_earlier()

    def _request_earlier(self) -> None:
        """上滑/补视口的唯一入口。

        先挂已经取到但还没挂的旧消息；都挂完了才去库里取上一页。
        取页旗标必须在启动 worker 之前、在同一个同步回合里立住，否则连滑两次
        会启动两个 worker，exclusive worker 把前一个中途取消：那时游标可能已经
        前移而 widget 还没挂载完，表现就是历史里莫名少一段。
        """
        if self._loading_earlier:
            return
        if self._mount_pending(keep_position=True):
            return
        if getattr(self, "_oldest_message_id", None) is None:
            return
        if self._earlier_remaining() <= 0:
            return

        self._loading_earlier = True
        self._load_earlier()

    @work(group="load-earlier")
    async def _load_earlier(self) -> None:
        oldest = self._oldest_message_id
        session_id = self.session_id
        try:
            page = get_messages(session_id, HISTORY_LIMIT, before_id=oldest)
            # 取页期间切了会话：这一页属于旧会话，不能挂到新会话上
            if self.session_id != session_id:
                return
            if not page:
                # 计数说有、取页取不到（并发删除）：不再重复取
                self._refresh_earlier_hint()
                return

            self._oldest_message_id = page[0].get("id", oldest)
            self._message_count += len([m for m in page if m["role"] == "user"])
            # 新取到的一页比现有消息都旧：接到待挂队列头部，保持时间顺序
            self._pending_groups = [
                (m, self._message_widgets([m])) for m in page
            ] + self._pending_groups

            # 加载到最早一页后，会话标题按首条提问命名（与不分页时一致）
            users = [m for m in page if m["role"] == "user"]
            if users and self._earlier_remaining() == 0:
                first = users[0]["content"]
                self.set_chat_title(first[:30] + "..." if len(first) > 30 else first)

            self._mount_pending(keep_position=True)
        finally:
            self._loading_earlier = False

        self.call_after_refresh(self._fill_viewport)
    # endregion

    # 「重问」：按该条提问原文再发一次。
    # 走_send而非直接改写历史：重试是一次新的提问，应当落库、可见、可审计。
    # 有工具待确认时_send会拦下并给出提示，这里不重复判断。
    def on_retry_button_submitted(self, event: RetryButton.Submitted) -> None:
        self._hide_confirm_bar()
        if event.attachments:
            # 带附件的提问重问时要连附件一起复用：文件早已复制进会话目录，不该再问一次
            self._send(event.query.strip() or " ".join(att.label for att in event.attachments),
                       attachments=event.attachments)
            return
        self._send(event.query)

    def set_chat_title(self, title: str) -> None:
        """设置聊天标题"""
        try:
            title_label = self.query_one("#chat-title", Label)
            title_label.update(f"# {title}")
        except Exception:
            pass

    # region 消息渲染（与参考实现一致的双分区流式结构）
    # 用户消息行：提问右侧带「重问」按钮；重放与实时发送共用同一形状
    def _user_row(self, text: str, attachments: Optional[list]=None) -> Container:
        shown = attachments or []
        row = Horizontal(
            Label(self._tokenized_text(text), classes="user-message"),
            RetryButton(lambda t=text: t, attachments=shown, classes="retry-btn"),
            classes="user-message-row",
        )

        # token 本身已经说明附件；不再额外挂一行 📎 列表
        return Container(Container(row, classes="user-message-wrapper"), classes="user-message-area")

    @staticmethod
    def _tokenized_text(text: str) -> RichText:
        """消息里的 [Image 1] 与输入框同样高亮，保持同一种视觉语言。"""
        rich = RichText()
        pos = 0
        for match in ATTACHMENT_TOKEN_RE.finditer(text):
            rich.append(text[pos:match.start()])
            rich.append(match.group(0), style="bold #141414 on #d79b00")
            pos = match.end()
        rich.append(text[pos:])
        return rich

    def add_user_message(self, text: str, attachments: Optional[list]=None) -> None:
        """添加用户消息到列表。提问行右侧带「重问」按钮，点它即用本条原文再发一次"""
        self._last_user_row = self._user_row(text, attachments)
        self.query_one("#messages-list", ScrollableContainer).mount(self._last_user_row)
        self._scroll_to_bottom()

    def _send_guards(self) -> bool:
        """发送前的两条拦截（运行中 / 待确认）。通过返回 True。

        单独拆出来是因为带附件的提交要先弹窗同意、再复制文件——
        拦截失败时这些动作一次都不该做，否则会在磁盘上留下没人认领的副本。
        """
        # 运行中拒绝新提问：add_response是exclusive worker，贸然再发会把上一轮取消掉。
        # _agent_running此前只用于esc中断判断，从不拦提交（见TODO.md记录的事故）。
        if self._agent_running:
            self._notice_blocked(t(t(t("模型还在回答中，按 esc 可中断后重新发送"))))
            return False

        # 有工具待确认时禁止发新消息：会取消确认worker并导致thread悬挂在interrupt
        if self._pending_decision is not None:
            self._notice_blocked(t(t(t("有操作待确认：按 y 允许 / n 拒绝"))))
            return False

        return True

    def _send(self, query: str, attachments: Optional[list]=None,
              plans: Optional[list]=None) -> bool:
        """发送一轮提问。返回是否受理——被拒绝时调用方需自行处理输入框里的原文。

        `query` 是用户原文（含 `[File 1]` 这类 token），界面与 DB 都用它；
        发给模型的内容把附件展开成清单后再发，两份内容在 DB 里分开存、重放时合回去。
        `plans` 是附件计划：服务层闸门拒发时界面上要原样还回去，不能只还文字。
        """
        self._auto_streak = 0
        self._auto_limit_notified = False

        if not self._send_guards():
            return False

        # token 是附件的展示形态，但不能脱离附件数据单独发送：
        # 重问/历史重放时附件 JSON 丢失，只把 [Image 1] 当文字发出去只会让模型困惑。
        if ATTACHMENT_TOKEN_RE.search(query or "") and not attachments:
            self._notice_blocked(t("这条消息只有附件标记、没有附件数据（重问无法恢复文件）。请重新粘贴文件后再发送。"))
            return False

        # 附件 token 与正文必须在同一条 query 里（DB/界面/重放同源）；
        # 调用方可能只传了正文（手输路径/脚本调用），这里统一归一化。
        if attachments:
            labels = " ".join(att.label for att in attachments)
            body = ATTACHMENT_TOKEN_RE.sub("", query or "").strip()
            query = " ".join(part for part in (labels, body) if part)

        self._message_count += 1
        self._last_send_plans = list(plans or [])
        # 标题命名与 user 行落库都在服务层记录器里；这里只更新界面与本进程缓存
        seed = (query or "").strip() or (attachments[0].name if attachments else "")
        title = None
        if seed and (self._message_count == 1 or self._session.get("title") in ("", "新对话")):
            title = seed[:30] + "..." if len(seed) > 30 else seed
            self.set_chat_title(title)
        # 缓存也要跟着改名：选择弹窗读的是这个条目，只改标签的话它会一直显示「新对话」
        update_session(self._session, title=title, model=self.model, thinking=self.enable_thinking)
        self.add_user_message(query, attachments=attachments)
        self._refresh_cmd_status()
        self._response_worker = self.add_response(query, attachments or [])
        return True

    # region 附件（E10.4）
    def receive_pasted_paths(self, paths: list[str], remainder: str = "") -> None:
        """首页粘贴文件路径后转交过来：token 放在输入框最前面，剩余文字跟在后面。"""
        self._add_pending(paths, insert_tokens=False)
        labels = " ".join(plan["label"] for plan in self._pending_attachments)
        box = self.query_one(f"#{self._cmd_input_id}", CustomTextArea)
        box.text = " ".join(part for part in (labels, remainder) if part).strip()
        box.focus()

    def _handle_paste(self, pasted: str) -> Optional[str]:
        """粘贴钩子：摘出其中的本地文件路径，返回剩余文本。

        返回 None 表示"这不是路径，照常粘贴"——普通文本粘贴的行为完全不变。
        """
        paths, remainder = find_paths(pasted)
        if not paths:
            return None

        self._add_pending(paths)
        return remainder

    def _shell_enabled(self) -> bool:
        """当前会话能不能用 shell 读到工作区外的文件——决定"引用原路径"还是"必须复制"。

        T2 的读范围已收敛到工作区（决策 27），所以 T2 不再算"能读全盘"：
        引用绝对路径会变成一条读不到的附件。T3 起每次读取都会经过确认，
        才允许不复制直接引用原路径。必须与中间件用同一个 resolve_level。
        """
        ctx = get_current_ctx()
        level = resolve_level(config.EXEC_LEVEL, ctx, self.session_id)
        return level >= ExecLevel.CONFIRM_ALL

    def _add_pending(self, paths: list[str], *, insert_tokens: bool = True) -> None:
        ctx = get_current_ctx()
        privileged = ctx.is_admin or (ctx.is_default and not config.MULTI_USER)
        workspace = ctx.workspace_path
        shell_enabled = self._shell_enabled()
        limit_count = max_per_message(privileged)
        existing = {p["path"] for p in self._pending_attachments}
        rejected: list[str] = []
        added: list[dict] = []
        # token 必须在这里就定下来：stage() 之前用户可能删掉某个 token
        counters: dict[str, int] = {}
        for pending in self._pending_attachments:
            counters[pending["kind"]] = counters.get(pending["kind"], 0) + 1

        for raw in paths:
            if limit_count is not None and len(self._pending_attachments) >= limit_count:
                rejected.append(t(t(t('一条消息最多 {} 个附件，后面的已忽略')), limit_count))
                break
            try:
                plan = decide(raw, workspace_path=workspace, shell_enabled=shell_enabled,
                              is_admin=privileged)
            except AttachmentError as err:
                rejected.append(str(err))
                continue
            if plan["path"] in existing:
                continue
            existing.add(plan["path"])
            counters[plan["kind"]] = counters.get(plan["kind"], 0) + 1
            plan["label"] = f"[{kind_label(plan['kind'])} {counters[plan['kind']]}]"
            self._pending_attachments.append(plan)
            added.append(plan)

        self._insert_attachment_tokens(added)
        if rejected:
            self._notice_blocked("；".join(rejected))

    def _insert_attachment_tokens(self, plans: list[dict]) -> None:
        if not plans:
            return
        box = self.query_one(f"#{self._cmd_input_id}", CustomTextArea)
        box.insert(" ".join(plan["label"] for plan in plans) + " ")
        box.focus()

    def _ensure_attachment_tokens(self) -> None:
        """附件计划还在、但输入框 token 被清掉时补回去（拒绝/拦截后的还原路径）。"""
        if not self._pending_attachments:
            return
        try:
            box = self.query_one(f"#{self._cmd_input_id}", CustomTextArea)
        except Exception:
            return
        missing = [p["label"] for p in self._pending_attachments if p["label"] not in box.text]
        if missing:
            box.text = " ".join(missing) + " " + (box.text or "")

    def _sync_pending_from_tokens(self) -> None:
        """输入框里的 token 是附件的可见状态：删掉 token 就等于移除附件。"""
        try:
            text = self.query_one(f"#{self._cmd_input_id}", CustomTextArea).text
        except Exception:
            return
        labels = ATTACHMENT_TOKEN_RE.findall(text)
        by_label = {plan["label"]: plan for plan in self._pending_attachments}
        self._pending_attachments = [by_label[label] for label in labels if label in by_label]

    def _on_input_text_changed(self, event) -> None:
        if getattr(event.text_area, "id", None) == self._cmd_input_id:
            self._sync_pending_from_tokens()

    async def _ask_copy_consent(self, plans: list[dict]) -> str:
        """三态同意：拒绝 / 本次同意 / 本会话都同意。

        文件清单作为 disabled 行摆在选项前——看不见"要复制哪些文件"的同意不是同意。
        """
        items: list = [
            (f"info-{i}", Text(f"  {p['name']}   {p['mime']}   {human_size(p['size'])}", style="dim"), True)
            for i, p in enumerate(plans)
        ]
        items += [
            ("once", Text(t(t(t("本次同意：复制这些文件并发送"))))),
            ("session", Text(t(t(t("本会话都同意：之后不再询问"))))),
            ("deny", Text(t(t(t("拒绝：不复制，消息也不发送"))))),
        ]
        screen = SelectScreen(
            t(t(t('要把 {} 个文件复制进会话附件目录吗？复制后 agent 能读取其内容')), len(plans)),
            items,
            hint=t(t(t("原文件位置不变。↑↓ 选择  Enter 确认  Esc 拒绝"))),
            content_highlight=False,
        )
        return (await self.app.push_screen_wait(screen)) or "deny"

    @work(exclusive=True, group="submit")
    async def _submit_message(self, text: str) -> None:
        """提交入口：识别手打路径 → 需要复制时征求同意 → 落盘 → 发送。"""
        paths, remainder = find_paths(text) if text else ([], text)
        if paths:
            # 打字/粘贴进来的路径先摘掉，再统一用 token 前缀 + 剩余文字重排输入框，
            # 保证 [Image 1] 与正文都在同一条消息里（OpenCode 风格）。
            self._add_pending(paths, insert_tokens=False)
            labels = " ".join(plan["label"] for plan in self._pending_attachments)
            body = ATTACHMENT_TOKEN_RE.sub("", remainder).strip()
            box = self.query_one(f"#{self._cmd_input_id}", CustomTextArea)
            box.text = " ".join(part for part in (labels, body) if part)
            text = box.text

        self._sync_pending_from_tokens()
        plans = list(self._pending_attachments)
        if not plans:
            # 没有附件：直接发；输入框只在发送受理后清空
            if text.strip():
                if self._send(text.strip()):
                    self._clear_input()
                else:
                    self._restore_input(text)
            return

        if not self._send_guards():
            self._restore_input(text)
            return

        copy_plans = [p for p in plans if p["mode"] == "copy"]
        if copy_plans and not self._attach_trust_session:
            choice = await self._ask_copy_consent(copy_plans)
            if choice == "session":
                self._attach_trust_session = True
            elif choice != "once":
                self._pending_attachments = plans
                self._restore_input(text)
                self._notice_blocked(t(t(t("未同意复制，附件与消息都没有发送"))))
                return

        try:
            ctx = get_current_ctx()
            attachments = stage(plans, thread_id=self.session_id, copy_allowed=True,
                                is_admin=ctx.is_admin
                                or (ctx.is_default and not config.MULTI_USER))
        except AttachmentError as err:
            self._restore_input(text)
            self._notice_blocked(t(t(t('附件处理失败：{}')), err))
            return

        self._pending_attachments = []

        # 只贴文件没写字时，界面与DB里显示token本身，重问才不会变成空提问
        display = text.strip() or " ".join(att.label for att in attachments)
        if self._send(display, attachments=attachments, plans=plans):
            self._clear_input()
        else:
            # 被发送前闸门拦下：附件计划留着，原文也还给输入框，用户可以直接再发一次
            self._pending_attachments = plans
            self._restore_input(text)

    def _clear_input(self) -> None:
        try:
            box = self.query_one(f"#{self._cmd_input_id}", CustomTextArea)
            box.text = ""
            box.styles.height = 2
        except Exception:
            pass

    def _restore_input(self, text: str) -> None:
        """把原文还给输入框——拦截/取消都不能让用户输入凭空消失。"""
        if not text:
            return
        box = self.query_one(f"#{self._cmd_input_id}", CustomTextArea)
        box.text = text
        self._ensure_attachment_tokens()
        box._adjust_height()
        box.focus()

    def _restore_attachments(self) -> None:
        """把本条消息的附件计划还给输入区。附件已在发送前暂存，重发会重新登记一次。"""
        if not self._last_send_plans:
            return
        self._pending_attachments = self._last_send_plans
        self._last_send_plans = []
        self._ensure_attachment_tokens()

    # endregion

    def _notice_blocked(self, detail: str) -> None:
        self.query_one("#messages-list", ScrollableContainer).mount(
            self._notice_label(f"[yellow]{detail}[/yellow]")
        )
        self._scroll_to_bottom()

    @work(exclusive=True)
    async def add_response(self, user_input: str, attachments: Optional[list]=None) -> None:
        """添加 AI 响应（ChatService流式事件驱动，think/answer区懒挂载）"""
        gen = self._generation
        self._run_seq += 1
        seq = self._run_seq
        self._set_agent_running(True, gen, seq)
        try:
            await self._respond(user_input, gen, attachments or [])
        finally:
            self._set_agent_running(False, gen, seq)

    # 手动压缩：与自动压缩共用同一事件键，压缩后只改展示，不产生新对话消息
    async def do_compact(self) -> None:
        if self._agent_running:
            self._notice_blocked(t(t(t("模型还在回答中，按 esc 可中断后再压缩"))))
            return
        if self._pending_decision is not None:
            self._notice_blocked(t(t(t("有操作待确认：按 y 允许 / n 拒绝"))))
            return

        self._response_worker = self._compact_worker()

    @work(exclusive=True)
    async def _compact_worker(self) -> None:
        gen = self._generation
        self._run_seq += 1
        seq = self._run_seq
        self._set_agent_running(True, gen, seq)
        try:
            try:
                service = ChatService(input=ChatInputSchema(
                    query="",
                    session_id=self.session_id,
                    model=self.model,
                    enable_thinking=self.enable_thinking,
                ))
            except SessionAccessError:
                await self._mount_session_error()
                return
            except Exception as ex:
                # 模型不存在等构建期错误不能让worker击穿TUI，转为正文区提示
                await self._mount_config_error(str(ex) or ex.__class__.__name__)
                return

            async for event in service.acompact():
                if self._generation != gen:
                    return

                kind = event["event"]
                if kind == "compact":
                    self._on_compact(event.get("stats") or {})
                elif kind == "context":
                    self._on_context(event.get("stats") or {})
                else:
                    # warning（没有可压的）与 error（压缩失败）都不进对话历史
                    await self.mount_notice(event["answer"])
                self.call_later(self._scroll_to_bottom)
        finally:
            self._set_agent_running(False, gen, seq)

    async def _respond(self, user_input: str, gen: int, attachments: Optional[list]=None) -> None:
        # off时前端同步屏蔽think下发：部分模型不遵守enable_thinking，仍会输出思考
        try:
            service = ChatService(input=ChatInputSchema(
                query=user_input,
                session_id=self.session_id,
                model=self.model,
                enable_thinking=self.enable_thinking,
                show_thinking=self.enable_thinking is not False,
            ), attachments=attachments or [])
        except SessionAccessError:
            await self._mount_session_error()
            return
        except Exception as ex:
            # 模型不存在等构建期错误不能让worker击穿TUI，转为正文区提示
            await self._mount_config_error(str(ex) or ex.__class__.__name__)
            return


        # 本轮累计：usage 是精确值；耗时优先用服务端统计（不含等待确认的空档）。
        # in/out 都是「本轮所有模型调用」的累加，下一个用户回合从 0 重新开始；
        # 头部的 ctx 是「最后一次模型调用」的 input——单次调用的回合两者必然相等
        turn_started = time.monotonic()
        turn_usage = {"input": 0, "output": 0, "elapsed": 0.0}

        # 按模型调用分段懒挂载：seq -> {"think":[md,stream], "answer":[md,stream]}，
        # 挂载顺序即事件到达顺序，实现思考/正文随调用轮次交叉显示
        segments: dict[int, dict] = {}
        cur_seq = 0

        events: Optional[AsyncIterator[ChatEvent]] = service.astream()

        # events为None表示本轮结束；interrupt确认后切换resume流继续输出
        while events is not None:
            resume_events: Optional[AsyncIterator[ChatEvent]] = None

            try:
                async for event in events:
                    if self._generation != gen:
                        break

                    kind = event["event"]

                    if kind in ("think", "answer"):
                        seq = event.get("call") or 0
                        cur_seq = max(cur_seq, seq)
                        seg = segments.setdefault(seq, {"think": [None, None], "answer": [None, None]})
                        slot = seg[kind]
                        await self._ensure_section(kind, slot)
                        await slot[1].write(event["answer"])
                    elif kind == "tool_call":
                        self._set_tool_active(event["answer"], True)
                    elif kind == "tool_end":
                        self._set_tool_active(event.get("tool") or event["answer"], False)
                        self._mount_tool_record(event["answer"], event.get("ok", True))
                    elif kind == "progress":
                        self._on_token_progress(event.get("stats") or {})
                    elif kind == "usage":
                        stats = event.get("stats") or {}
                        self._on_token_usage(stats)
                        turn_usage["input"] += stats.get("input") or 0
                        turn_usage["output"] += stats.get("output") or 0
                        turn_usage["elapsed"] += stats.get("elapsed") or 0
                    elif kind == "context":
                        self._on_context(event.get("stats") or {})
                    elif kind == "compact":
                        self._on_compact(event.get("stats") or {})
                    elif kind == "error" and (event.get("stats") or {}).get("source") == "gate":
                        # 闸门在服务层拦下：这轮没落库也没发模型，界面上把那条提问撤回去，
                        # 原文与附件还给输入区——用户看到的是"没发出去"，而不是"发出去然后报错"
                        if self._last_user_row is not None:
                            await self._last_user_row.remove()
                            self._last_user_row = None
                        self._message_count = max(0, self._message_count - 1)
                        self._restore_attachments()
                        self._refresh_cmd_status()
                        self._notice_blocked(event["answer"])
                        self._restore_input(user_input)
                        return
                    elif kind == "error":
                        seg = segments.setdefault(cur_seq, {"think": [None, None], "answer": [None, None]})
                        slot = seg["answer"]
                        await self._ensure_section("answer", slot)
                        # 系统错误事件带 code 时，答句是固定提示，可按界面语言翻译；
                        # 模型输出没有 code，走不到这里。
                        err_text = event["answer"]
                        if (event.get("stats") or {}).get("code"):
                            err_text = t(err_text)
                        await slot[1].write(t(t(t('**调用模型时出错**: {}')), err_text))
                    elif kind == "warning":
                        # 端点故障提示：只给你看，不进对话历史也不进模型上下文
                        if (event.get("stats") or {}).get("source") == "persistence":
                            # 服务层每回合都报（客户端要靠它判断本轮状态），界面只提一次
                            if not self._degraded_shown:
                                self._degraded_shown = True
                                await self.mount_notice(msg.MSG_PERSISTENCE_MEMORY_SHORT)
                        else:
                            warn_text = event['answer']
                            if (event.get("stats") or {}).get("code"):
                                warn_text = t(warn_text)
                            self.query_one("#messages-list", ScrollableContainer).mount(
                                self._notice_label(f"[yellow]{warn_text}[/yellow]")
                            )
                    elif kind == "interrupt":
                        for seg in segments.values():
                            for slot in seg.values():
                                if slot[1] is not None:
                                    await slot[1].stop()
                                    slot[1] = None

                        if self._always_approve and self._auto_streak < self.AUTO_APPROVE_LIMIT:
                            self._auto_streak += 1
                            resume_events = service.aresume("approve")
                            break

                        if self._always_approve and not self._auto_limit_notified:
                            self._auto_limit_notified = True
                            self.query_one("#messages-list", ScrollableContainer).mount(
                                self._notice_label(
                                    t(t(t('[yellow]本轮已自动放行 {} 次，后续工具执行恢复人工确认[/yellow]')), self.AUTO_APPROVE_LIMIT)
                                )
                            )

                        resume_events = await self._await_interrupt(
                            service, event["answer"], event.get("interrupt_id")
                        )
                        break

                    self.call_later(self._scroll_to_bottom)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                if self._generation != gen:
                    return
                seg = segments.setdefault(cur_seq, {"think": [None, None], "answer": [None, None]})
                slot = seg["answer"]
                await self._ensure_section("answer", slot)
                await slot[1].write(t(t(t('**界面处理异常**: {}')), str(ex)))
            finally:
                for seg in segments.values():
                    for slot in seg.values():
                        if slot[1] is not None:
                            await slot[1].stop()
                            slot[1] = None
                # 取消/异常退出时兜底清理确认状态，避免确认条残留、pending事件悬挂
                self._pending_decision = None
                self._hide_confirm_bar()

            events = resume_events

        # 旧世代worker被切换打断时，不再动新会话的UI状态
        if self._generation != gen:
            return

        # 每轮结束在消息流末尾留一行：只报本轮用量，会话累计已在库里，不在这里重复
        if turn_usage["input"] or turn_usage["output"]:
            elapsed = turn_usage["elapsed"] or (time.monotonic() - turn_started)
            self.query_one("#messages-list", ScrollableContainer).mount(
                Label(
                    t("[dim]本轮 in {} · out {} · 耗时 {}[/dim]",
                      self._fmt_tok(turn_usage["input"]),
                      self._fmt_tok(turn_usage["output"]),
                      self._fmt_secs(elapsed)),
                    classes="turn-total",
                )
            )
            self._scroll_to_bottom()

        self._hide_confirm_bar()
        self.call_later(self.set_focus_on_mount)

    # 卸载竞态中widget树可能已不完整，查询失败静默跳过
    def _hide_confirm_bar(self) -> None:
        try:
            self.query_one("#confirm-bar", Container).display = False
        except Exception:
            pass

    # 首次输出时挂载对应分区；resume复用已有Markdown重新取stream（挂载顺序即事件顺序）
    async def _ensure_section(self, kind: str, slot: list) -> None:
        if slot[1] is not None:
            return

        if slot[0] is not None:
            slot[1] = Markdown.get_stream(slot[0])
            return

        msg_list = self.query_one("#messages-list", ScrollableContainer)

        if kind == "think":
            md = Markdown("", classes="thinking-message")
            await msg_list.mount(
                Container(
                    Container(
                        Label("[goldenrod italic]Thinking:[/]", classes="thinking-prefix"),
                        md,
                        classes="thinking-message-wrapper",
                    ),
                    classes="thinking-message-area",
                )
            )
        else:
            block, md = self._answer_block()
            await msg_list.mount(block)

        slot[0] = md
        slot[1] = Markdown.get_stream(md)

    # 正文输出块：右上角复制按钮 + markdown正文；返回(block, md)供挂载与取source复制
    def _answer_block(self, source: str = "") -> tuple[Vertical, Markdown]:
        md = Markdown(source, classes="assistant-message")
        block = Vertical(
            CopyButton(lambda: md.source, classes="copy-btn"),
            md,
            classes="answer-block",
        )
        return block, md

    # 展示确认栏并等待决策，返回resume事件流；样式对齐opencode：标题+事项+文本tab按钮
    async def _await_interrupt(
        self, service: ChatService, detail: str, interrupt_id: Optional[str] = None
    ) -> AsyncIterator[ChatEvent]:
        bar = self.query_one("#confirm-bar", Container)
        log = self.query_one("#confirm-detail", RichLog)
        log.clear()
        log.write(t(t(t("[bold yellow]⚠ Permission required：待确认工具执行[/]"))))
        for i, line in enumerate(detail.splitlines() or [""]):
            log.write(f"[bold]← [/]{escape(line)}" if i == 0 else f"  {escape(line)}")
        log.write("")

        self._confirm_idx = 0
        self._refresh_confirm_buttons()
        bar.display = True

        # 焦点移出输入框，避免按键被TextArea消费；按钮仅鼠标点击，键盘由screen绑定接管
        self.set_focus(None)

        self._decision_value = "reject"
        self._auto_after_approve = False
        self._pending_decision = asyncio.Event()
        await self._pending_decision.wait()
        self._pending_decision = None

        bar.display = False
        auto = self._auto_after_approve
        self._auto_after_approve = False
        if auto:
            self._always_approve = True
            self.query_one("#messages-list", ScrollableContainer).mount(
                self._notice_label(t(t(t("[dim]— 本会话后续工具执行将自动允许 —[/dim]"))))
            )
        return service.aresume(self._decision_value, interrupt_id=interrupt_id)

    def _refresh_confirm_buttons(self) -> None:
        for i, bid in enumerate(("confirm-yes", "confirm-auto", "confirm-no")):
            btn = self.query_one(f"#{bid}", Button)
            btn.remove_class("-confirm-sel")
            if i == self._confirm_idx:
                btn.add_class("-confirm-sel")

    def _submit_confirm(self, idx: int) -> None:
        if self._pending_decision is None:
            return
        self._decision_value = "reject" if idx == 2 else "approve"
        if idx == 1:
            self._auto_after_approve = True
        self._pending_decision.set()

    def action_confirm_left(self) -> None:
        if self._pending_decision is not None:
            self._confirm_idx = (self._confirm_idx - 1) % 3
            self._refresh_confirm_buttons()

    def action_confirm_right(self) -> None:
        if self._pending_decision is not None:
            self._confirm_idx = (self._confirm_idx + 1) % 3
            self._refresh_confirm_buttons()

    def action_confirm_submit(self) -> None:
        if self._pending_decision is not None:
            self._submit_confirm(self._confirm_idx)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        idx = {"confirm-yes": 0, "confirm-auto": 1, "confirm-no": 2}.get(event.button.id or "")
        if idx is not None:
            self._submit_confirm(idx)

    # 确认等待期间按键直接走决策（输入框有焦点时也不落入文本）
    async def _intercept_key(self, key: str) -> bool:
        # 焦点在输入框时esc中断执行；命令popup打开时优先关popup
        if key == "escape" and not self._popup_visible() and (self._agent_running or self._pending_decision is not None):
            self.action_interrupt_run()
            return True

        if self._pending_decision is not None:
            if key == "y":
                self._submit_confirm(0)
                return True
            if key == "n":
                self._submit_confirm(2)
                return True
            if key in ("left", "shift+left"):
                self.action_confirm_left()
                return True
            if key in ("right", "shift+right"):
                self.action_confirm_right()
                return True
            if key == "enter":
                self.action_confirm_submit()
                return True
            return False

        return await super()._intercept_key(key)

    def action_approve(self) -> None:
        self._submit_confirm(0)

    def action_reject(self) -> None:
        self._submit_confirm(2)

    def _scroll_to_bottom(self) -> None:
        """滚动到消息列表底部（messages-list是唯一滚动层）"""
        self.query_one("#messages-list", ScrollableContainer).scroll_end(animate=False)
    # endregion

    # region 命令宿主实现（面板/执行逻辑见CommandMixin）
    def _current_session_id(self) -> Optional[str]:
        return self.session_id

    # /help：在消息区渲染帮助卡片
    async def mount_help(self) -> None:
        lines = [t(t(t("**命令列表**"))), ""]
        lines += [f"- `{n}` — {d}" for n, d in self._command_table()]
        lines += ["", t(t(t('当前模型 `{}` ｜ 会话数 {} ｜ `/model <provider/code>` 可跳过弹窗直接指定')), self.current_model(), len(self._sorted_sessions())), ""]
        lines += [t(t(t("**快捷键**：`Ctrl+N` 换行 `Enter` 提交 `↑↓` 选择 `Esc` 关闭面板 `Ctrl+T` 换主题 `Ctrl+Q` 退出")))]

        self.query_one("#messages-list", ScrollableContainer).mount(Markdown("\n".join(lines), classes="assistant-message"))
        self._scroll_to_bottom()

    # 切换会话：世代号自增（旧worker在下一个事件即停写，cancel是异步的不如gen可靠），
    # 再停worker、await清空消息区、换thread、重放历史、恢复状态
    async def do_switch_session(self, entry: SessionEntry) -> None:
        self._generation += 1
        worker = self._response_worker
        self._response_worker = None
        if worker is not None:
            worker.cancel()

        self._session = entry
        self.session_id = entry["id"]
        # "本会话都同意复制"只对原会话有效：切走/新建后必须重新征求同意
        self._attach_trust_session = False
        self._pending_attachments = []
        try:
            box = self.query_one(f"#{self._cmd_input_id}", CustomTextArea)
            box.text = ATTACHMENT_TOKEN_RE.sub("", box.text or "").strip()
        except Exception:
            pass
        model_notice = self._resolve_session_model(entry["model"])
        self.enable_thinking = entry["thinking"]
        self._message_count = 0
        self._always_approve = False
        self._confirm_idx = 0
        self._active_tools.clear()
        self._tok_in = entry.get("tokens_in") or 0
        self._tok_out = entry.get("tokens_out") or 0
        self._tok_tps = entry.get("speed") or 0.0
        self._ctx_tokens = entry.get("context_tokens") or 0
        self._ctx_limit = entry.get("context_limit") or 0
        self._ctx_limit_resolved = bool(self._ctx_limit)
        self._ctx_estimate = False
        self._ctx_pending = False
        self._ctx_model = self.current_model()
        self.query_one("#tool-status", Horizontal).display = False

        msg_list = self.query_one("#messages-list", ScrollableContainer)
        await msg_list.remove_children()
        # 旧会话没挂完的旧消息不能带到新会话里
        self._pending_groups = []

        self._prepare_replay()

        if self._replay_messages:
            self._replay_ui(self._replay_messages)
            self._replay_messages = []
        else:
            self.set_chat_title(t(entry["title"]))
            await self.mount_notice(t(t(t('已切换到会话: {}')), entry['title']))

        if model_notice:
            await self.mount_notice(model_notice)

        self._refresh_cmd_status()

    async def do_new_session(self) -> None:
        await self.do_switch_session(create_session())

    def _session_message_count(self) -> int:
        return self._message_count

    def _refresh_identity(self) -> None:
        self.query_one("#chat-identity", Label).update(identity_label(get_current_ctx()))

    # 身份切换：世代号自增让旧worker停写，再停worker；keep_session为真表示会话已过户给新身份，沿用当前thread
    async def do_identity_changed(self, ctx, keep_session: bool) -> None:
        self._generation += 1
        worker = self._response_worker
        self._response_worker = None
        if worker is not None:
            worker.cancel()

        self._refresh_identity()

        if not keep_session:
            await self.do_new_session()

        self._refresh_cmd_status()
        await self.mount_notice(t(t(t('当前身份：{}')), identity_label(ctx)))

    def _notice_label(self, content: str) -> Label:
        """构造一条提示行。上一条也是提示时，本条上边留空行——
        连着执行多个命令时各条提示不会黏成一片，看不出是几条。"""
        container = self.query_one("#messages-list", ScrollableContainer)
        children = list(container.children)
        classes = "notice-line"
        if children and children[-1].has_class("notice-line"):
            classes += " notice-sep"

        return Label(content, classes=classes)

    async def mount_notice(self, text: str) -> None:
        label = self._notice_label(f"[dim]— {text} —[/dim]")
        await self.query_one("#messages-list", ScrollableContainer).mount(label)
        self._scroll_to_bottom()

    async def mount_memory(self, text: str) -> None:
        self.query_one("#messages-list", ScrollableContainer).mount(
            Markdown(text, classes="assistant-message")
        )
        self._scroll_to_bottom()

    async def send_message(self, text: str) -> None:
        self._send(text)
    # endregion

    async def on_custom_text_area_submitted(self, event: CustomTextArea.Submitted) -> None:
        """处理用户输入提交"""
        if event.text_area.id != "chat-input":
            return

        user_input = event.text.strip()
        # 只粘了附件、一个字没写也要能发——"看看这个文件"是最常用的形态
        if not user_input and not self._pending_attachments:
            return

        if user_input.startswith("/") and not self._pending_attachments:
            event.text_area.text = ""
            event.text_area.styles.height = 2
            await self._execute_command(user_input)
            return

        # 不在这里清空：_submit_message 是 worker，它要读输入框里的 token 与待发送附件。
        # 清空/还原由它在发送受理或拒绝后统一处理。
        self._submit_message(user_input)
