# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : chat_service.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description :

import json
import asyncio
import re
import time
from typing import Any, AsyncIterator, Union, List, Optional

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.exceptions import ContextOverflowError
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from config import config
from logger import logger
from const.messages import msg
from enums import EventTypeEnum
from exception.base_exception import CoreError
from exception.model_exception import ModelUnsupportedContentError
from exception.session_exception import SessionAccessError
from util.text_utils import ThinkTagSplitter
from core.agent import persistence
from core.agent.deep_agents.agent import Agent
from core.agent.deep_agents.compaction import compact_thread
from core.llm.context_budget import (
    ContextLimits,
    Verdict,
    base_tokens,
    last_context,
    preflight,
    record_base,
    record_context,
)
from core.llm.token_counter import count_tokens_cjk
from service.attachment.attachments import (
    Attachment, AttachmentError, PendingImages, build_model_message, resolve_uploaded_ids,
)
from service.notice.sink import NoticeSink
from service.session.backfill import Loaded, load_history
from service.session import approvals
from service.session.recorder import SessionRecorder
from service.session.session_service import session_service
from service.user.user_service import UserCtx, get_current_ctx
from service.web.external_content import unwrap_external_content
from ..schema import ChatInputSchema

class ChatEvent(dict):
    """ 结构化会话事件：event/answer/session_id/create_at """

# 内部簿记类工具不对外发调用事件（write_todos调用频繁，展示噪音大）
TOOLS_EVENT_HIDDEN = frozenset({"write_todos"})

# 工具卡片展开区展示的结果上限：有些工具会整份文件地返回，卡片只需要够看懂，
# 不该把上百 KB 的正文塞进 SSE 与消息表
TOOL_OUTPUT_MAX = 4000

# 工具参数生成期间给卡片刷进度的最小间隔：write_file 的整份正文就是一个参数，
# 不报进度时卡片会长时间只有工具名，看着像卡死；刷太密又只是无意义的 SSE 流量
ARGS_PROGRESS_INTERVAL = 0.4

# 确认卡里参数正文的上限：write_file 的正文就是一个参数，整份文件几十 KB。
# 卡片要能看清"要写什么"，但没必要把正文整份塞进 SSE 与一次 DOM 渲染；
# 超长时只给前 N 字，并在卡片上标明总长度
INTERRUPT_ARGS_MAX = 4000

# 模型确实开始产出的证据：只有这些事件说明 thread 在内存里已经有状态。
# warning（MCP 连不上等）与 error 不算——那时模型可能一次都没跑过
LIVE_EVENTS = frozenset({"think", "answer", "tool_call", "tool_end", "usage", "interrupt"})

# 补给悬空tool_call的消息：上一回合被中止时，那个assistant消息带了tool_calls
# 却没有配对的tool响应，OpenAI兼容端点会直接400
DANGLING_TOOL_MESSAGE = "[已取消] 用户中止了该操作，未执行。"

class ChatService:
    def __init__(self, input: ChatInputSchema, ctx: Optional[UserCtx]=None,
                 *, attachments: Optional[List[Attachment]]=None):
        self.input = input
        self.ctx = ctx or get_current_ctx()
        # 会话主键就是 agent 的 thread_id，签发只有一处（service.session.create）。
        # 客户端带来的 id 必须已经属于它自己：自造 id 一律拒绝，不静默新建——
        # 否则用户以为"我的会话丢了"，而实际是拿别人的键在续聊。
        # builder 先建：它把"没指定模型"解析成配置默认值，落库要用解析后的那个
        builder = Agent(model=input.model, enable_thinking=input.enable_thinking)
        if input.session_id:
            if not session_service.owns(input.session_id, self.ctx.uid):
                raise SessionAccessError(msg.MSG_SESSION_NOT_OWNED)
            self.session_id = input.session_id
        else:
            # 签发即落行：首事件里回传给客户端的键必须当场可用。
            # 本地 uuid4 生成再等 record_turn_start 落行的话，被闸门拒掉的第一轮
            # 会把一个"服务端刚给过、服务端又不认"的键交出去，客户端一重试就被拒。
            try:
                self.session_id = session_service.create(
                    model=builder.model, thinking=input.enable_thinking, user_id=self.ctx.uid
                )
            except ValueError as exc:
                raise SessionAccessError(str(exc), code="session.create_blocked") from exc
        self.thread_id = self.session_id

        # 项目归属：只有已有会话行可能带 project_id（新建会话仍是普通会话）。
        # 已有项目会话不校验 can_projects：授权收回后仍要继续（spec §8.3），
        # 只校验归属与目录可用；目录失效就拒绝本轮，不回落 workspace。
        self.project = None
        row = session_service.get_session(self.session_id, self.ctx.uid)
        if row and row.get("project_id"):
            from service.project.context import load_project_context
            from service.project.errors import ProjectError

            try:
                self.project = load_project_context(row["project_id"], self.ctx.uid)
            except ProjectError as exc:
                raise SessionAccessError(
                    "项目目录不可用，无法开始本轮对话", code=exc.code
                ) from exc

        # 附件计划由表现层收集（复制与同意都要弹窗），"token → 模型输入"的展开属于业务。
        # Web 只提交 opaque file id；服务端在刚验证过归属的会话目录内解析它，不接受物理路径。
        if attachments is not None:
            self.attachments = attachments
        elif input.attachments:
            try:
                self.attachments = resolve_uploaded_ids(
                    input.attachments, thread_id=self.thread_id,
                    is_admin=self.ctx.is_admin
                    or (self.ctx.is_default and not config.MULTI_USER),
                )
            except AttachmentError as exc:
                raise SessionAccessError(str(exc), code=exc.code or "attachment.invalid") from exc
        else:
            self.attachments = []
        # 附件硬编码展开：PDF/Office -> anydoc Markdown；文本直接内联。
        # 图片只进本轮待注入队列，不拼进 state：checkpoint 里只留文本与路径。
        self.message_content, self.model_query, image_blocks = build_model_message(
            input.query, self.attachments, thread_id=self.thread_id
        )
        self.pending_images = PendingImages(image_blocks)
        self.image_count = len(self.pending_images)

        # 端点故障提示的收集器：工具与装配层把"哪个端点连不上"塞进来，
        # 服务层在流里 drain 成WARNING事件，聊天页渲染成黄色提示行。
        # 去重键用会话键——键由服务端签发且全局唯一，不会有两个用户共用一个键
        self.notices = NoticeSink(self.session_id)

        self.agent = builder.get_agent(
            ctx=self.ctx, session_id=self.session_id, thread_id=self.thread_id,
            notices=self.notices, pending_images=self.pending_images,
            project=self.project,
        )
        # /compact 复用同一个摘要引擎：另建一个实例会拿到不同的事件键/归档目录，压完下一轮会重复压
        self.summarization = getattr(builder, "summarization", None)
        # 预算由真实模型窗口推导；模型没有 profile 时为 None，界面只显示绝对值、闸门关闭
        max_input = getattr(builder, "max_input_tokens", None)
        self.limits = (
            ContextLimits.from_window(max_input, getattr(builder, "max_output_tokens", 0))
            if max_input else None
        )

        self.config: RunnableConfig = {"configurable": {"thread_id": self.thread_id}}
        # 会话第一层的唯一写入者：TUI 与 API 共用，表现层不再直接落库
        self.recorder = SessionRecorder(
            session_id=self.session_id, user_id=self.ctx.uid,
            model=builder.model, thinking=input.enable_thinking
        )
        # 模型调用段号：每次usage块到达递增，think/answer事件按段交叉展示
        self._call_seq = 0
        # 已发起但未执行的工具调用参数：tool_call_id -> {name, args}，resume后执行仍能取到
        self._tool_calls: dict[str, dict] = {}
        # 本回合开始/结束时读到的状态：用来判断压缩是否发生，并复用给有效消息估算
        self._turn_values: dict = {}
        # 本轮结束后是否会触发压缩（基于上一次已知占用 + 新输入）
        self._compact_pending = False
        # 本回合开始时的已知占用（用于压缩前后对比）
        self._turn_start_context = 0
        # 本回合各次模型调用的输入 token（按发生顺序）
        self._call_inputs: list[int] = []
        # 基线只在每个会话第一次拿到 usage 时反推一次，避免工具增多后重复记账
        self._first_usage = True
        # 本回合从库里回灌的历史（服务层决定，不是客户端带的）
        self.backfilled: list = []
        # 本实例是否已声明"这个 thread 在我这儿是活的"（模型真正产出后才置位）
        self._live_marked = False

    # 事件流经这里过一遍记录器：会话第一层的写入只发生在这一个管道里。
    # 落库失败不能把模型流一起带走——回答已经生成，缺一行历史比答到一半断掉好受得多。
    #
    # 真正的写入必须离开事件循环线程：LangGraph 的 AsyncSqliteSaver 与聊天史共用同一个
    # SQLite 库，它在写事务中间会 await，靠事件循环推进 commit。如果这里同步写库，
    # 写锁等待会卡住事件循环，saver 的 commit 永远排不上，5 秒忙等超时后就是
    # "database is locked"。改成 to_thread 后锁会自然释放，写入在后台线程排队即可。
    DB_EVENTS = frozenset({"tool_end", "usage", "context", "compact"})

    async def _observe(self, event: ChatEvent) -> None:
        try:
            # think/answer 只进内存缓冲区，不需要为每个 token 做一次线程切换
            if event.get("event") in self.DB_EVENTS:
                await asyncio.to_thread(self.recorder.observe, event)
            else:
                self.recorder.observe(event)
        except Exception as ex:
            logger.warning(f"会话落库失败（本轮继续）：{ex}")

    async def _finish_turn(self) -> None:
        try:
            await asyncio.to_thread(self.recorder.finish)
        except Exception as ex:
            logger.warning(f"会话收尾落库失败：{ex}")

    async def _record_turn_start(self) -> None:
        try:
            await asyncio.to_thread(
                self.recorder.record_turn_start, self.input.query, self.attachments
            )
        except Exception as ex:
            logger.warning(f"用户行落库失败（本轮继续）：{ex}")

    async def _record_assistant(self, answer: str, think: str) -> None:
        try:
            await asyncio.to_thread(self.recorder.record_assistant, answer, think)
        except Exception as ex:
            logger.warning(f"回答落库失败（本轮继续）：{ex}")

    async def _record_usage(self, tokens_in: int, tokens_out: int, elapsed: float = 0.0) -> None:
        """非流式路径没有 SSE 事件可发，直接让记录器看见与流式同形状的 usage。"""
        try:
            await asyncio.to_thread(self.recorder.observe, {"event": "usage", "stats": {
                "input": tokens_in, "output": tokens_out, "tps": 0.0, "elapsed": elapsed}})
        except Exception as ex:
            logger.warning(f"用量落库失败（本轮继续）：{ex}")

    # 活性标记必须在模型真的开始产出之后：闸门放行但模型立刻失败的一轮不算"有运行态"。
    # 降级模式没有磁盘 checkpoint，唯一判据就是这个标记——提前打上会让下一轮跳过回灌
    def _mark_live_once(self, event: ChatEvent) -> None:
        if self._live_marked or event.get("event") not in LIVE_EVENTS:
            return

        self._live_marked = True
        persistence.mark_live(self.thread_id)

    # checkpoint 降级时每回合的第一个事件。故意不进 NoticeSink：
    # 那里的"同键每会话只报一次"会让第二个会话、以及重启后的第一条流都静默，
    # 而客户端要靠这条事件判断本轮状态，必须每回合都有（界面自己决定只渲染一次）
    def _persistence_warning(self) -> Optional[ChatEvent]:
        if persistence.is_persistent():
            return None

        return self._build_event(msg.MSG_PERSISTENCE_MEMORY_MODE, event=EventTypeEnum.WARNING,
                                 stats={"source": "persistence"})

    # 回灌 + 闸门：先决定"这轮该灌什么、灌得下吗"，再决定要不要落库
    def _prepare_turn(self) -> tuple[Loaded, Verdict]:
        # 会话库走本模块的引用：测试与部署换库时，回灌与落库必须看到同一个实例
        query_tokens = count_tokens_cjk([{"role": "user", "content": self.model_query}]) + self.image_count * 85
        base = base_tokens(self.thread_id)
        loaded = load_history(self.session_id, user_id=self.ctx.uid,
                              limits=self.limits, service=session_service,
                              base=base, query_tokens=query_tokens)
        self.backfilled = loaded.messages
        # 占用估算把回灌与新输入一起算：与上一版"history + query"同口径
        new_tokens = count_tokens_cjk(self.backfilled + [{"role": "user", "content": self.model_query}]) + self.image_count * 85

        current = last_context(self.thread_id) or 0
        self._turn_start_context = current
        self._compact_pending = bool(self.limits and current + new_tokens >= self.limits.trigger)

        verdict = preflight(self.model_query, limits=self.limits,
                            base=base, history=self.backfilled,
                            extra_tokens=self.image_count * 85)
        return loaded, verdict

    # 流式会话
    async def astream(self) -> AsyncIterator[ChatEvent]:
        lease_id = await asyncio.to_thread(
            session_service.acquire_lease, self.session_id, self.ctx.uid
        )
        if lease_id is None:
            yield self._build_event("会话正在删除或已不存在，请刷新会话列表。", event=EventTypeEnum.ERROR, stats={"code": "chat.deleting"})
            return

        heartbeat = asyncio.create_task(self._lease_heartbeat(lease_id))
        events = self._astream_owned()
        try:
            async for event in events:
                yield event
        finally:
            # Closing this outer generator must explicitly close its inner generator too;
            # Python's async-for does not guarantee that aclose propagates to the iterator.
            await events.aclose()
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            await asyncio.to_thread(session_service.release_lease, lease_id)

    async def _astream_with_repair(self, agent_input) -> AsyncIterator[ChatEvent]:
        """首次调用若被 422（非文本内容）拒绝，修掉历史里的非文本块后重试一次。

        典型场景：早先用 read_file 读过图片，图片块留在 checkpoint 里；之后即使
        不再带附件，每条消息仍会把它一起发出去，端点持续 422。
        """
        attempt = 0
        while True:
            retry = False
            produced = False
            async for event in self._astream_input(agent_input):
                code = (event.get("stats") or {}).get("code")
                if (event.get("event") == "error"
                        and code == "chat.model_unsupported_content"
                        and attempt == 0 and not produced):
                    if await self._sanitize_non_text_history():
                        yield self._build_event(
                            "历史中的图片/非文本内容已移除（当前模型不支持），正在重试。",
                            event=EventTypeEnum.WARNING,
                            stats={"code": "chat.history_repaired"},
                        )
                        retry = True
                        break

                if event.get("event") in ("think", "answer", "tool_call", "tool_end"):
                    produced = True
                self._mark_live_once(event)
                await self._observe(event)
                yield event

            if not retry:
                return
            attempt += 1

    @staticmethod
    def _text_only_content(content) -> str:
        """把消息内容里的非文本块降级成文本占位（保留文本部分）。"""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")

        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                btype = str(block.get("type") or "")
                if btype in ("text", "input_text"):
                    parts.append(str(block.get("text") or ""))
                else:
                    parts.append(f"[非文本内容已移除: {btype or 'unknown'}]")
                continue

            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
            else:
                parts.append("[非文本内容已移除]")

        return "\n".join(p for p in parts if p) or "[非文本内容已移除]"

    async def _sanitize_non_text_history(self) -> bool:
        """把 checkpoint 历史里的图片/二进制块替换成文本占位，返回是否有改动。

        A 方案之后当前轮的图片不在 state，而在待注入队列里；只清历史不会让重试
        变干净，所以先清队列。任一清理发生都要返回 True，否则不会触发重试。
        """
        cleared = bool(self.pending_images)
        self.pending_images.clear()
        try:
            state = await self.agent.aget_state(self.config)
            messages = list((state.values or {}).get("messages") or [])
        except Exception as ex:
            logger.warning(f"读取历史以清理非文本内容失败: {ex}")
            return cleared

        patches = []
        for msg in messages:
            content = getattr(msg, "content", None)
            text = self._text_only_content(content)
            if text != content:
                patches.append(msg.model_copy(update={"content": text}))

        if not patches:
            return cleared

        try:
            await self.agent.aupdate_state(self.config, {"messages": patches})
        except Exception as ex:
            logger.warning(f"清理历史非文本内容失败: {ex}")
            return cleared

        logger.warning(f"历史中有 {len(patches)} 条消息含非文本内容，已替换为文本占位并重试")
        return True

    async def _lease_heartbeat(self, lease_id: str) -> None:
        """长请求续租；delete_wait 会等租约释放/过期后才 purge checkpoint。"""
        while True:
            await asyncio.sleep(session_service.LEASE_TTL_SECONDS / 3)
            try:
                renewed = await asyncio.to_thread(session_service.renew_lease, lease_id)
                if not renewed:
                    logger.error(f"会话租约已失效（lease={lease_id}），删除协调不再有保护")
                    return
            except Exception as exc:
                # 一次 sqlite busy/瞬时 IO 不应直接杀掉 heartbeat；下一周期重试，
                # delete 仍会等现存租约到期。若数据库持续不可用，日志会明确暴露这条风险。
                logger.warning(f"续期会话租约失败（lease={lease_id}），将重试：{exc}")

    async def _astream_owned(self) -> AsyncIterator[ChatEvent]:
        warning = self._persistence_warning()
        if warning is not None:
            yield warning

        loaded, verdict = self._prepare_turn()

        # 闸门在这里是权威：被拒的一轮不碰 checkpoint、不落库、不发模型请求。
        # 唯一放行的是"为凑满最少 10 条而超预算"——那是刻意选择接对上下文而不是拒发
        if not verdict.ok:
            if not loaded.floor_applied:
                yield self._build_event(verdict.message, event=EventTypeEnum.ERROR,
                                        stats={"source": "gate", "code": "chat.gate_rejected"})
                return
            self.notices.add("backfill-floor",
                             f"为保住上下文按最少 {len(loaded.messages)} 条回灌，已超出本次预算，交给压缩处理。")

        # 闸门过了才落用户行：被拒的提问不进历史，也不进下一轮的回灌
        await self._record_turn_start()
        # 新回合开始前先收拾上一回合遗留的未应答工具调用（ESC中止导致）
        self._turn_values = await self._settle_dangling_tool_calls()
        # finally 必须包在**本生成器自己**身上：取消（ESC、运行中再次提交）与异常退出都要刷，
        # 否则库里留下没有回答的提问，重开时模型会看到两条并列的用户消息。
        # 之前把这层放在嵌套的 `_recorded()` 里，外层关闭不会关闭内层，只能等 GC 兜底
        try:
            async for event in self._astream_with_repair(self._build_input()):
                yield event
        finally:
            await self._finish_turn()

    # 恢复interrupt会话
    def aresume(self, decision: str, interrupt_id: str | None = None) -> AsyncIterator[ChatEvent]:
        return self._astream_resume(decision, interrupt_id)

    # 手动压缩：不开新回合、不调主模型，只把旧消息摘要掉并更新占用快照
    async def acompact(self) -> AsyncIterator[ChatEvent]:
        lease_id = await asyncio.to_thread(
            session_service.acquire_lease, self.session_id, self.ctx.uid
        )
        if lease_id is None:
            yield self._build_event("会话正在删除或已不存在，请刷新会话列表。", event=EventTypeEnum.ERROR, stats={"code": "chat.deleting"})
            return
        heartbeat = asyncio.create_task(self._lease_heartbeat(lease_id))
        events = self._acompact_owned()
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            await asyncio.to_thread(session_service.release_lease, lease_id)

    async def _acompact_owned(self) -> AsyncIterator[ChatEvent]:
        # 压缩后的 context 事件照常落库——占用快照不能只在界面上活着
        try:
            async for event in self._acompact_events():
                self._mark_live_once(event)
                await self._observe(event)
                yield event
        finally:
            await self._finish_turn()

    async def _acompact_events(self) -> AsyncIterator[ChatEvent]:
        warning = self._persistence_warning()
        if warning is not None:
            yield warning

        # MCP 连接失败等装配期提示先发出去，否则这次操作会把它们吞掉
        async for event in self._emit_notices():
            yield event

        if self.summarization is None:
            yield self._build_event("当前 agent 未启用摘要中间件，无法手动压缩", event=EventTypeEnum.ERROR, stats={"code": "chat.compact_unavailable"})
            return

        try:
            outcome = await compact_thread(
                self.agent, self.summarization, self.config, self.thread_id
            )
        except Exception as ex:
            logger.warning(f"手动压缩异常: {ex}")
            yield self._build_event(f"上下文压缩失败：{ex}", event=EventTypeEnum.ERROR)
            return

        if not outcome.ok and outcome.reason:
            # "没有可压的"是正常反馈，不是错误
            yield self._build_event(outcome.reason, event=EventTypeEnum.WARNING)
            return

        if not outcome.ok:
            yield self._build_event(
                "上下文压缩失败，历史未改动。",
                event=EventTypeEnum.COMPACT,
                stats={"ok": False, "before": outcome.before, "after": 0,
                       "messages": 0, "file": "", "summary": ""},
            )
            yield self._build_event(
                f"压缩失败：{outcome.error}。历史未改动，可稍后重试。",
                event=EventTypeEnum.ERROR,
            )
            return

        self._compact_pending = False
        yield self._build_event(
            f"已压缩 {outcome.messages} 条消息：{outcome.before / 1000:.1f}k → {outcome.after / 1000:.1f}k",
            event=EventTypeEnum.COMPACT,
            stats={"ok": True, "before": outcome.before, "after": outcome.after,
                   "messages": outcome.messages, "file": outcome.file or "",
                   "summary": outcome.summary},
        )
        # 压缩后没有真实 usage，用同一把尺子估算并标 ~；下一轮真实调用会覆盖为精确值
        yield self._build_event(
            self._context_text(outcome.after),
            event=EventTypeEnum.CONTEXT,
            stats=self._context_stats(outcome.after, source="estimate"),
        )

    # 历史消息仅在thread上下文缺失时由调用方传入，回灌到对话前部
    def _build_input(self) -> Any:
        return {"messages": list(self.backfilled) + [{"role": "user", "content": self.message_content}]}

    # 非流式会话，think与answer拆分返回
    async def ainvoke(self) -> dict:
        lease_id = await asyncio.to_thread(
            session_service.acquire_lease, self.session_id, self.ctx.uid
        )
        if lease_id is None:
            return {"session_id": self.session_id, "error": "会话正在删除或已不存在，请刷新会话列表。"}
        heartbeat = asyncio.create_task(self._lease_heartbeat(lease_id))
        try:
            return await self._ainvoke_owned()
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            await asyncio.to_thread(session_service.release_lease, lease_id)

    async def _ainvoke_owned(self) -> dict:
        warning = self._persistence_warning()
        loaded, verdict = self._prepare_turn()
        if not verdict.ok and not loaded.floor_applied:
            # 非流式没有事件可发：拒发理由直接进 error 字段
            return {"session_id": self.session_id, "error": verdict.message}

        await self._record_turn_start()
        await self._settle_dangling_tool_calls()
        t0 = time.monotonic()
        result = await self.agent.ainvoke(
            self._build_input(),
            config=self.config
        )
        # 非流式没有事件流：模型返回就当它跑过了（失败会在此之前抛出）
        persistence.mark_live(self.thread_id)

        interrupts = await self._get_interrupts()
        if interrupts:
            await self._finish_turn()
            return {
                "session_id": self.session_id,
                "interrupts": interrupts
            }

        content = self._to_text(result["messages"][-1].content) if result.get("messages") else ""
        last_msg = result["messages"][-1] if result.get("messages") else None

        # think优先取协议reasoning字段，兼容模型直接在正文输出think标签的情况
        splitter = ThinkTagSplitter()
        segments = splitter.feed(content) + splitter.flush()

        reasoning = ""
        if isinstance(last_msg, AIMessage):
            reasoning = last_msg.additional_kwargs.get("reasoning_content", "") or ""
        reasoning += "".join(t for c, t in segments if c == "think")

        answer = "".join(t for c, t in segments if c == "answer")
        notices = [] if warning is None else [{"source": "persistence", "text": warning["answer"]}]
        # 非流式没有事件流，最终结果直接落成一段
        await self._record_assistant(answer, reasoning)
        # 非流式同样要记三类口径：占用取本次调用的输入 token，用量按本次调用落库。
        # 端点不返回 usage 时保持 0 而不是估算——宁缺勿错
        usage = getattr(last_msg, "usage_metadata", None) or {}
        tokens_in = usage.get("input_tokens") or 0
        tokens_out = usage.get("output_tokens") or 0
        if tokens_in:
            self._record_context(tokens_in)
            # 与流式同一条路径落库：context 事件由记录器写 sessions.context_tokens
            await self._observe(self._build_event(
                self._context_text(tokens_in), event=EventTypeEnum.CONTEXT,
                stats=self._context_stats(tokens_in)))
            await self._record_usage(tokens_in, tokens_out, round(time.monotonic() - t0, 1))
        await self._finish_turn()

        return {
            "session_id": self.session_id,
            "think": reasoning if self.input.show_thinking else "",
            "answer": answer,
            "notices": notices,
        }

    async def _astream_input(self, agent_input) -> AsyncIterator[ChatEvent]:
        # 每个请求独立拆分器，兜底处理正文中直接输出think标签的模型
        splitter = ThinkTagSplitter()
        # 本次流内按 tool_call index 组装参数，usage块（调用结束）时提交到实例级缓存
        assembling: dict[int, dict] = {}

        t0 = time.monotonic()
        # 本次模型调用的起点：usage 块到达时用它算这次调用的实际耗时
        call_started = t0
        last_tick = t0
        last_tick_units = 0
        # 当前模型调用的流式增量计数≈token数（llama-server每token一个chunk），用于实时速度
        cur_units = 0

        try:
            # MCP的连接失败在建agent时就已产生，此刻就能报
            async for notice_event in self._emit_notices():
                yield notice_event

            async for msg_chunk, _meta in self.agent.astream(
                input=agent_input,
                config=self.config,
                stream_mode="messages"
            ):
                if isinstance(msg_chunk, ToolMessage):
                    name = getattr(msg_chunk, "name", "") or ""
                    if name and name not in TOOLS_EVENT_HIDDEN:
                        call_id = getattr(msg_chunk, "tool_call_id", "") or ""
                        entry = self._tool_calls.pop(call_id, None) or {}
                        args = entry.get("args", "")
                        desc = self._tool_desc(name, args)
                        ok = getattr(msg_chunk, "status", "success") != "error"
                        # answer 仍是 TUI 那一行摘要；卡片需要的参数与结果分开放进 stats，
                        # 否则 Web 只能拿到 "name(args)"，展开区永远是空的
                        yield self._build_event(
                            desc, event=EventTypeEnum.TOOL_END, tool=name, ok=ok,
                            stats={
                                "args": self._tool_args(args),
                                "output": self._tool_output(getattr(msg_chunk, "content", "")),
                            },
                        )
                        # 工具执行途中发现的端点故障（websearch降级）在这里报
                        async for notice_event in self._emit_notices():
                            yield notice_event
                    continue

                if not isinstance(msg_chunk, AIMessageChunk):
                    continue

                # 模型调用的usage块（无content），这里立刻发一条 usage：
                # 必须在这一刻记账，不能等流末尾——用户 ESC/停止会 aclose 生成器，
                # 已跑完的调用如果还没发事件就永久丢账
                if msg_chunk.usage_metadata:
                    tokens = msg_chunk.usage_metadata.get("input_tokens") or 0
                    output = msg_chunk.usage_metadata.get("output_tokens") or 0
                    timings = (msg_chunk.response_metadata or {}).get("timings") or {}
                    elapsed = round(time.monotonic() - call_started, 1)
                    gen_s = (timings.get("predicted_ms") or 0) / 1000 or (time.monotonic() - call_started)
                    tps = output / gen_s if gen_s > 0 else 0.0
                    cur_units = 0
                    last_tick_units = 0
                    self._call_seq += 1
                    for entry in assembling.values():
                        if entry["id"]:
                            self._tool_calls[entry["id"]] = entry
                    assembling.clear()

                    # 占用只取本次调用的输入 token（精确），不能拿本轮累加值
                    if tokens > 0:
                        self._record_context(tokens)
                        yield self._build_event(
                            self._context_text(tokens), event=EventTypeEnum.CONTEXT,
                            stats=self._context_stats(tokens))
                    ev = self._build_event(
                        f"输入 {tokens} tok · 输出 {output} tok · 平均 {tps:.1f} t/s",
                        event=EventTypeEnum.USAGE,
                        stats={"tps": round(tps, 1), "input": tokens, "output": output,
                               "elapsed": elapsed})
                    yield ev
                    call_started = time.monotonic()
                    continue

                # 工具调用开始：按index组装id/name/args增量
                for tc in getattr(msg_chunk, "tool_call_chunks", None) or []:
                    entry = assembling.setdefault(
                        tc.get("index") or 0,
                        {"id": "", "name": "", "args": "", "arg_at": 0.0})
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    if tc.get("name"):
                        entry["name"] = tc["name"]
                    if tc.get("args"):
                        entry["args"] += tc["args"]

                    name = entry["name"]
                    if not name or name in TOOLS_EVENT_HIDDEN:
                        continue
                    if tc.get("name"):
                        yield self._build_event(
                            name, event=EventTypeEnum.TOOL_CALL, call=self._call_seq,
                            stats={"index": tc.get("index") or 0})
                        entry["arg_at"] = time.monotonic()
                        continue
                    # 参数可能比答案还长（write_file 的正文就是一个参数）：按节流发进度，
                    # 卡片上能看到路径与已生成字符数。正文本身绝不回传，只给短字段与计数。
                    if tc.get("args") and time.monotonic() - entry["arg_at"] >= ARGS_PROGRESS_INTERVAL:
                        entry["arg_at"] = time.monotonic()
                        preview = self._args_preview(entry["args"])
                        yield self._build_event(
                            preview, event=EventTypeEnum.TOOL_ARGS, tool=name,
                            call=self._call_seq,
                            stats={"arg": preview, "chars": len(entry["args"]),
                                   "index": tc.get("index") or 0},
                        )

                # 标准reasoning增量走think通道
                reasoning = msg_chunk.additional_kwargs.get("reasoning_content", "")
                if reasoning:
                    cur_units += 1
                    if self.input.show_thinking:
                        yield self._build_event(reasoning, event=EventTypeEnum.THINK, call=self._call_seq)

                if msg_chunk.content:
                    cur_units += 1
                    for channel, text in splitter.feed(self._to_text(msg_chunk.content)):
                        if channel == "think" and not self.input.show_thinking:
                            continue
                        yield self._build_event(text, event=EventTypeEnum(channel), call=self._call_seq)

                # 每秒一次实时统计：速度=最近一个窗口内的实际增量，
                # 避免把工具执行/prefill等待算进分母；无新输出则不刷新（保留上一速度）
                now = time.monotonic()
                if cur_units != last_tick_units and now - last_tick >= 1.0:
                    tps = (cur_units - last_tick_units) / (now - last_tick)
                    last_tick, last_tick_units = now, cur_units
                    ev = self._build_event(f"{cur_units} tok · {tps:.1f} t/s", event=EventTypeEnum.PROGRESS)
                    # token数一律以usage块实际值为准，progress仅驱动实时speed
                    ev["stats"] = {"tps": round(tps, 1)}
                    yield ev

            for channel, text in splitter.flush():
                if channel == "think" and not self.input.show_thinking:
                    continue
                yield self._build_event(text, event=EventTypeEnum(channel), call=self._call_seq)

            # 收尾只读一次状态：压缩标记与未决中断取自同一个 checkpoint。
            # 分两次读等于把同一份 checkpoint 剖两遍，大历史下这段空档用户是能感觉到的。
            tail_started = time.monotonic()
            state = await self._read_state()

            # 本回合是否发生压缩：比较回合前后 _summarization_event 的签名
            compact_event = self._build_compact_event(state)
            if compact_event is not None:
                yield compact_event

            async for notice_event in self._emit_notices():
                yield notice_event

            async for event in self._emit_interrupts(state):
                yield event

            # 模型流结束到确认卡出现的这段空档必须可量化：排查"点确认前等了很久"时，
            # 日志能直接区分是模型在生成参数，还是后端收尾真的慢
            logger.debug(f"[chat] 回合收尾耗时 {1000 * (time.monotonic() - tail_started):.0f}ms")
        except ContextOverflowError as ex:
            # 端点先报溢出、压缩也没救回来：给可执行的下一步，而不是一句原始异常
            logger.warning(f"上下文溢出: {ex}")
            budget = self.limits.budget if self.limits else 0
            yield self._build_event(
                "上下文超过模型可用窗口，压缩后仍放不下。",
                event=EventTypeEnum.COMPACT,
                stats={"ok": False, "before": self._turn_start_context,
                       "after": 0, "messages": 0, "file": "", "summary": ""},
            )
            hint = f"（可用预算 {budget / 1000:.1f}k tok）" if budget else ""
            yield self._build_event(
                f"上下文超过模型可用窗口{hint}，压缩后仍放不下。"
                "可以拆分成两次发送，或保存为文件让我按路径读取。",
                event=EventTypeEnum.ERROR,
            )
        except CoreError as ce:
            logger.warning(f"核心逻辑异常: {getattr(ce, 'err_desc', str(ce))}")
            yield self._model_error_event(ce)
        except Exception as ex:
            logger.warning(f"会话异常: {str(ex)}")
            yield self._model_error_event(ex)

    def _model_error_event(self, ex: Exception) -> ChatEvent:
        """模型请求失败：能用固定提示的就给固定提示，其余保留原始异常。"""
        if isinstance(ex, ModelUnsupportedContentError) or (
            "422" in str(ex) and "unprocessable" in str(ex).lower()
        ):
            message = (ex.err_desc if isinstance(ex, ModelUnsupportedContentError)
                       else ModelUnsupportedContentError().err_desc)
            return self._build_event(
                message, event=EventTypeEnum.ERROR,
                stats={"code": "chat.model_unsupported_content"},
            )

        text = getattr(ex, "err_desc", None) or str(ex) or "会话异常"
        return self._build_event(text, event=EventTypeEnum.ERROR)

    # 把收集到的端点故障提示转成WARNING事件
    async def _emit_notices(self) -> AsyncIterator[ChatEvent]:
        try:
            notices = self.notices.drain()
        except Exception as ex:
            # 诊断功能不该把会话搞挂——收集失败就丢掉这一批
            logger.warning(f"端点故障提示收集异常: {ex}")
            return

        for notice in notices:
            yield self._build_event(notice.text, event=EventTypeEnum.WARNING)

    async def _astream_resume(self, decision: str, interrupt_id: str | None = None) -> AsyncIterator[ChatEvent]:
        lease_id = await asyncio.to_thread(
            session_service.acquire_lease, self.session_id, self.ctx.uid
        )
        if lease_id is None:
            yield self._build_event("会话正在删除或已不存在，请刷新会话列表。", event=EventTypeEnum.ERROR, stats={"code": "chat.deleting"})
            return
        heartbeat = asyncio.create_task(self._lease_heartbeat(lease_id))
        events = self._astream_resume_owned(decision, interrupt_id)
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            await asyncio.to_thread(session_service.release_lease, lease_id)

    async def _astream_resume_owned(self, decision: str, interrupt_id: str | None = None) -> AsyncIterator[ChatEvent]:
        warning = self._persistence_warning()
        if warning is not None:
            yield warning

        # resume 也是新回合：重置逐回合簿记，并以当前状态为压缩检测起点。
        # 不重置会把上一回合已上报的压缩再报一遍；也不能做基线反推——
        # resume 没有 query，反推会把整段历史当成 system prompt。
        self._turn_start_context = last_context(self.thread_id) or 0
        self._call_inputs = []
        self._compact_pending = False
        self._first_usage = False
        try:
            state = await self.agent.aget_state(self.config)
            self._turn_values = state.values or {}
        except Exception:
            self._turn_values = {}

        command = await self._build_resume_command(decision, interrupt_id)
        try:
            async for event in self._astream_with_repair(command):
                yield event
        finally:
            await self._finish_turn()

    def _remember_always(self, interrupts: list[dict],
                         interrupt_id: str | None = None) -> None:
        """把 always 记录成具体作用域；只认本次 interrupt 自己带的动作。"""
        targets = [it for it in interrupts
                   if interrupt_id is None or it["interrupt_id"] == interrupt_id]
        if not targets:
            targets = interrupts[:1]

        project = getattr(self, "project", None)
        root = project.path if project is not None else None
        for interrupt in targets:
            approvals.record_always(self.ctx.uid, self.session_id,
                                    interrupt.get("actions") or [], root)

    # 构建resume指令，决策数与interrupt的待确认动作数对齐
    async def _build_resume_command(self, decision: str, interrupt_id: str | None = None) -> Command:
        """构建resume指令。

        两处外部约束决定形状：langchain的HITL中间件校验决策数必须等于待确认动作数；
        langgraph在多于一个未决interrupt时要求resume按interrupt id分别恢复，
        否则抛"you must specify the interrupt id when resuming"。

        指定interrupt_id时只恢复那一个，其余保持挂起——用户逐个确认，不会因为
        一次点头就把没看过的URL也放行。不指定时恢复全部未决（API的"全批"语义）。
        """
        interrupts = await self._get_interrupts()
        if decision == "always":
            self._remember_always(interrupts, interrupt_id)
            # HITL 只认 approve/reject/edit/respond；always 必须先落账再按 approve 恢复
            decision = "approve"

        if not interrupts:
            return Command(resume={"decisions": [{"type": decision}]})

        targets = [it for it in interrupts if interrupt_id is None or it["interrupt_id"] == interrupt_id]
        if not targets:
            # id对不上（已被别的流程消掉）时退回第一个未决，避免恢复空转
            targets = interrupts[:1]

        if len(interrupts) == 1:
            it = targets[0]
            return Command(resume={"decisions": [{"type": decision}] * (len(it.get("actions", [])) or 1)})

        # 多于一个未决：按interrupt id分别恢复
        return Command(resume={
            it["interrupt_id"]: {"decisions": [{"type": decision}] * (len(it.get("actions", [])) or 1)}
            for it in targets
        })

    # ESC中止留下的悬空tool_calls会让下一轮模型调用直接400：assistant消息带了
    # tool_calls却没有配对的tool响应，OpenAI兼容端点拒绝这种历史。
    # 补一条"已取消"的tool消息把历史补合法——不恢复也不跑模型：
    # 恢复会让模型就旧问题再答一遍，甚至重试同一个动作又造出新的悬空。
    async def _settle_dangling_tool_calls(self) -> dict:
        try:
            state = await self.agent.aget_state(self.config)
            values = state.values or {}
            messages = values.get("messages") or []
            answered = {m.tool_call_id for m in messages
                        if isinstance(m, ToolMessage) and m.tool_call_id}
            patches = [
                ToolMessage(content=DANGLING_TOOL_MESSAGE, name=tc.get("name", ""),
                            tool_call_id=tc["id"])
                for msg in messages if isinstance(msg, AIMessage)
                for tc in (getattr(msg, "tool_calls", None) or [])
                if tc.get("id") and tc["id"] not in answered
            ]
            if not patches:
                return values

            await self.agent.aupdate_state(self.config, {"messages": patches})
            logger.warning(f"补了 {len(patches)} 条未应答的工具调用（上一回合被中止）")
            # 重启后恢复出的待确认动作已被放弃，必须让用户知道它没执行
            self.notices.add("dangling-cancelled", "上次未确认的操作已取消，未执行。")
            return values
        except Exception as ex:
            # 清理失败不该把会话搞挂——最坏还是原来的400
            logger.warning(f"清理未应答的工具调用失败: {ex}")
            return {}

    # region 上下文占用与压缩

    def _record_context(self, tokens: int) -> None:
        """登记本轮每次模型调用的真实输入 token，并反推一次基线。"""
        if self._first_usage:
            prior = self._effective_messages(self._turn_values) or list(self.backfilled)
            approx = count_tokens_cjk(prior + [{"role": "user", "content": self.model_query}])
            base = tokens - approx
            # resume/压缩后首调时 approx 可能大于真实输入：0 或负数不是有效基线，宁可不用
            if base > 0:
                record_base(self.thread_id, base)
            self._first_usage = False

        self._call_inputs.append(tokens)
        record_context(self.thread_id, tokens)

    def _context_text(self, tokens: int) -> str:
        window = self.limits.window if self.limits else 0
        if not window:
            return f"ctx {tokens / 1000:.1f}k"

        return f"ctx {tokens / 1000:.1f}k/{window / 1000:.0f}k ({round(tokens / window * 100)}%)"

    def _context_stats(self, tokens: int, source: str="usage") -> dict:
        limits = self.limits
        return {
            "tokens": tokens,
            "limit": limits.window if limits else 0,
            "budget": limits.budget if limits else 0,
            "trigger": limits.trigger if limits else 0,
            "ratio": round(tokens / limits.window, 3) if limits else None,
            "source": source,
            "compact_pending": self._compact_pending,
        }

    @staticmethod
    def _effective_messages(values: dict) -> list:
        """压缩后实际发给模型的消息：摘要 + cutoff 之后的消息。

        cutoff 越界/缺失时退回完整消息，宁可多算也不能因为坏数据把展示搞崩。
        """
        values = values or {}
        messages = list(values.get("messages") or [])
        event = values.get("_summarization_event") or {}
        cutoff = event.get("cutoff_index")
        summary = event.get("summary_message")
        if summary is not None and isinstance(cutoff, int) and 0 < cutoff <= len(messages):
            return [summary] + messages[cutoff:]
        return messages

    @staticmethod
    def _compaction_signature(values: dict) -> tuple:
        event = (values or {}).get("_summarization_event") or {}
        summary = event.get("summary_message")
        return (
            event.get("cutoff_index"),
            ChatService._to_text(getattr(summary, "content", "")) if summary is not None else "",
            event.get("file_path"),
        )

    async def _read_state(self):
        """回合收尾读一次状态；失败返回 None，让压缩/中断各自降级而不是整轮报错。"""
        try:
            return await self.agent.aget_state(self.config)
        except Exception as ex:
            logger.warning(f"读取会话状态失败: {ex}")
            return None

    def _build_compact_event(self, state) -> Optional[ChatEvent]:
        """回合前后比较压缩标记；变化才发 compact 事件。"""
        if state is None:
            return None

        values = state.values or {}

        if self._compaction_signature(self._turn_values) == self._compaction_signature(values):
            return None

        event = values.get("_summarization_event") or {}
        previous = (self._turn_values or {}).get("_summarization_event") or {}
        cutoff = event.get("cutoff_index") or 0
        previous_cutoff = previous.get("cutoff_index") or 0
        # cutoff 是状态里的绝对下标（多次压缩会累加），本次真正压掉的是差值
        removed = cutoff - previous_cutoff if cutoff >= previous_cutoff else cutoff

        summary = event.get("summary_message")
        summary_text = self._to_text(getattr(summary, "content", "")) if summary is not None else ""
        before, after = self._compaction_bounds()

        self._compact_pending = False
        # 下一回合/下一次 resume 以压缩后的状态为起点，避免把同一次压缩再报一遍
        self._turn_values = values

        if self.limits and after >= self.limits.trigger:
            self.notices.add("context-still-full", "上下文压缩后仍接近上限，建议新开会话。")

        return self._build_event(
            f"已压缩 {removed} 条消息：{before / 1000:.1f}k → {after / 1000:.1f}k",
            event=EventTypeEnum.COMPACT,
            stats={
                "ok": True,
                "before": before,
                "after": after,
                "messages": removed,
                "file": event.get("file_path"),
                "summary": summary_text,
            },
        )

    def _compaction_bounds(self) -> tuple[int, int]:
        """压缩前后的输入 token。

        一次回合内的输入只增不减（消息只会追加），所以第一次明显回落就是压缩边界：
        回落前一次是 before，回落那次是 after。没有回落说明压缩发生在第一次调用之前。
        """
        calls = self._call_inputs
        if not calls:
            return self._turn_start_context, 0

        for i in range(1, len(calls)):
            if calls[i] < calls[i - 1]:
                return calls[i - 1], calls[i]

        return self._turn_start_context, calls[0]

    # endregion

    # 输出当前thread挂起的interrupt事件（可读摘要，避免dict repr进入富文本导致解析崩溃）
    async def _emit_interrupts(self, state=None) -> AsyncIterator[ChatEvent]:
        for interrupt in await self._get_interrupts(state):
            yield self._build_event(
                self._describe_interrupt(interrupt),
                event=EventTypeEnum.INTERRUPT,
                interrupt_id=interrupt["interrupt_id"],
                # answer 是给 TUI 的一行摘要（参数值压到 50 字）；
                # Web 的确认卡要看清参数本身，所以另给一份结构化动作
                stats={"actions": [self._interrupt_action(a) for a in interrupt.get("actions") or []]},
            )

    @classmethod
    def _interrupt_action(cls, action: dict) -> dict:
        """确认卡上的一条待执行动作：工具名 + 可读参数文本（超长截断）。"""
        text = cls._render_args(action.get("args"))

        total = len(text)
        truncated = total > INTERRUPT_ARGS_MAX
        return {
            "tool": action.get("tool", ""),
            "args": text[:INTERRUPT_ARGS_MAX] if truncated else text,
            "chars": total,
            "truncated": truncated,
        }

    @staticmethod
    def _dump(value) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _render_args(cls, args) -> str:
        """把工具参数排成确认卡能读的文本：多行字符串保持换行，不转义成 \\n。

        write_file 的正文就是一个含换行的字符串。直接 json.dumps 会把整份文件压成
        一行 `\\n` 转义的长文本，确认卡上根本看不出要写什么；这里按顶层字段分块，
        多行值原样换行缩进两格。嵌套对象/数组仍用缩进 JSON（它们通常很短）。
        """
        if args is None:
            return ""
        if isinstance(args, str):
            return args
        if not isinstance(args, dict):
            return cls._dump(args)

        blocks = []
        for key, value in args.items():
            if isinstance(value, str) and "\n" in value:
                body = "\n".join("  " + line for line in value.rstrip("\n").split("\n"))
                blocks.append(f"{key}:\n{body}")
            elif isinstance(value, (dict, list)):
                body = "\n".join("  " + line for line in cls._dump(value).split("\n"))
                blocks.append(f"{key}:\n{body}")
            elif isinstance(value, str):
                blocks.append(f'{key}: {value}' if value else f'{key}: ""')
            else:
                blocks.append(f"{key}: {cls._dump(value)}")
        return "\n".join(blocks)

    # 工具调用记录描述：name(k=v, ...)，长参数值截断
    @staticmethod
    def _arg_parts(args_json: str) -> list[str]:
        try:
            args = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}

        parts = []
        for k, v in args.items():
            s = " ".join(str(v).split())
            if len(s) > 50:
                s = f"{s[:50]}…（共{len(str(v))}字）"
            parts.append(f"{k}={s}")
        return parts

    # 参数还在逐块生成时的卡片摘要：只抽路径这类短字段。
    # 参数 JSON 尚未闭合，json.loads 一定失败；正文（例如整份文件）绝不进摘要。
    _PATH_RE = re.compile(r'"(file_path|path)"\s*:\s*"((?:[^"\\]|\\.)*)"')

    @classmethod
    def _args_preview(cls, args_json: str) -> str:
        match = cls._PATH_RE.search(args_json or "")
        return f"{match.group(1)}={match.group(2)}" if match else ""

    @classmethod
    def _tool_args(cls, args_json: str) -> str:
        """参数部分（不含工具名），给 Web 卡片的摘要行用。"""
        return ", ".join(cls._arg_parts(args_json))

    @classmethod
    def _tool_desc(cls, name: str, args_json: str) -> str:
        return f"{name}({cls._tool_args(args_json)})"

    @staticmethod
    def _tool_output(content) -> str:
        """把 ToolMessage.content 压成卡片展开区的纯文本，超长截断。

        content 可能是字符串，也可能是 content blocks（文本/图片混排）；
        图片等非文本块没有可展示的正文，直接跳过。
        """
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                else:
                    text = getattr(block, "text", None)
                if isinstance(text, str) and text:
                    parts.append(text)
            text = "\n".join(parts)
        else:
            text = "" if content is None else str(content)

        # 展示层不要把安全提示和随机标记一起给用户看；真正的模型上下文不受影响
        text = unwrap_external_content(text).strip()
        if len(text) > TOOL_OUTPUT_MAX:
            text = f"{text[:TOOL_OUTPUT_MAX]}\n…（已截断，共 {len(text)} 字）"
        return text

    # 待确认事项摘要：每个action一行，长参数值截断展示，避免文件全文挤爆确认区
    @staticmethod
    def _describe_interrupt(interrupt: dict) -> str:
        lines = []
        for action in interrupt.get("actions", []):
            parts = []
            for k, v in (action.get("args") or {}).items():
                s = " ".join(str(v).split())
                if len(s) > 50:
                    s = f"{s[:50]}…（共{len(str(v))}字）"
                parts.append(f"{k}={s}")
            lines.append(f"{action.get('tool', '')}({', '.join(parts)})")

        return "\n".join(lines) or str(interrupt)

    # 读取当前thread挂起的interrupt信息
    async def _get_interrupts(self, state=None) -> list[dict]:
        if state is None:
            state = await self._read_state()
        if state is None:
            return []

        pending = []
        for task in (state.tasks or ()):
            # 已完成或出错的task仍带着当初的interrupt，那是陈旧残留，不算未决
            if getattr(task, "result", None) is not None or getattr(task, "error", None) is not None:
                continue
            pending.extend(getattr(task, "interrupts", None) or ())

        if not pending:
            pending = list(state.interrupts or ())

        project = getattr(self, "project", None)
        project_root = project.path if project is not None else None
        interrupts = []
        for it in pending:
            value: dict = it.value if isinstance(it.value, dict) else {"detail": str(it.value)}
            actions = []
            for ar in value.get("action_requests", []):
                if not isinstance(ar, dict):
                    continue
                tool = ar.get("name", "")
                args = ar.get("args", {})
                actions.append({
                    "tool": tool, "args": args,
                    # 只新增字段，不改旧结构；前端据此显示“总是允许”的作用域
                    "approval": approvals.derive_approval(tool, args, project_root),
                })

            interrupts.append({"interrupt_id": it.id, "actions": actions})

        return interrupts

    # 多段content（如thinking+answer）拼接为纯文本
    @staticmethod
    def _to_text(content: Union[str, List[Any]]) -> str:
        if isinstance(content, list):
            return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)

        return str(content)

    def _build_event(self, answer: str, event: EventTypeEnum=EventTypeEnum.ANSWER, **extra) -> ChatEvent:
        return ChatEvent({
            "event": event.value,
            "answer": answer,
            "session_id": self.session_id,
            "create_at": int(time.time()),
            **extra
        })
