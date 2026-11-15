# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : openai.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 20:47:59
# @Author      : leo liu
# @Version     : 1.0
# @Description :

import json
import re
import httpx

from pydantic import field_validator
from typing import Optional, Union, Dict, Any, Type, List, Iterator, AsyncIterator, Sequence, Callable, Literal, ClassVar

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.language_models.model_profile import ModelProfile
from langchain_core.exceptions import ContextOverflowError
from langchain_core.tools import BaseTool
from langchain_core.messages import HumanMessage, SystemMessage, AIMessageChunk, AIMessage, ToolMessage, BaseMessageChunk
from langchain_core.callbacks import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool

from logger import logger
from util.text_utils import THINK_OPEN, THINK_CLOSE
from exception.model_exception import (
    ModelCallError, ModelPromptError, ModelPropError, ModelUnsupportedContentError,
)
from .chunk import _convert_chunk_to_generation_chunk
from .reasoning import ReasoningWatch, extract_reasoning, warn_missing_reasoning


# 端点把"上下文超长"也报成 400/413/422，只凭状态码分不出来，必须看响应体。
# 这些标记与 deepagents 的 _is_context_overflow 保持一致，命中后抛 ContextOverflowError，
# 压缩中间件才能识别并先压缩再重试，而不是把原始 400 丢给用户。
_OVERFLOW_STATUS = frozenset({400, 413, 422})
_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "contextwindowexceedederror",
    "maximum context length",
    "exceeds the context window",
    "exceeds the available context size",
    "context window exceeded",
    "context limit exceeded",
    "input tokens exceed the configured limit",
    "prompt is too long",
)


def _safe_response_text(response: httpx.Response) -> str:
    try:
        return response.text
    except Exception:
        return ""


# 显式配置的关闭思考写法。拼错时必须在装配期就报错：
# 静默忽略等于"用户以为关了，其实还在烧 token 思考"。
THINKING_STYLES = frozenset({
    "enable_thinking", "thinking_type", "template_kwargs",
    "enable_thinking+template_kwargs", "enable_thinking+thinking_type",
})


def normalize_thinking_style(style: str) -> str:
    key = "+".join(sorted(part.strip() for part in str(style or "").split("+") if part.strip()))
    if key not in THINKING_STYLES:
        raise ModelPropError(
            f"未知的 thinking_style: {style!r}；可用：{', '.join(sorted(THINKING_STYLES))}"
        )
    return key


def _raise_model_error(exc: httpx.HTTPError) -> None:
    """把 httpx 异常翻成可分类的模型异常。"""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _OVERFLOW_STATUS:
        body = _safe_response_text(exc.response)
        if any(marker in f"{body} {exc}".lower() for marker in _OVERFLOW_MARKERS):
            raise ContextOverflowError(body.strip()[:500] or "context window exceeded") from exc

    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 422:
        # 422 的常见原因是端点不接受请求里的内容块（如纯文本模型收到图片）。
        # 统一翻成用户能照做的提示；具体响应体留给日志/排查。
        raise ModelUnsupportedContentError() from exc

    raise ModelCallError(f"模型请求异常：{str(exc)}")

class HttpOpenAi(BaseChatModel):
    """
    大语言模型基础对话
    同时支持流式和非流式
    目标大模型为POST方式调用
    """

    LOCAL_URL_PREFIXES: ClassVar[tuple] = ("http://127.0.0.1", "http://localhost", "http://[::1]", "unix://")

    api_key: str = "" # key，本地模型（如llama-server）可为空
    url: str # 调用接口
    model: str # 模型名称
    headers: Optional[dict] = None # 模型额外请求headers
    enable_thinking: bool = False # 是否深度思考
    # 关闭思考的请求体写法。不同端点参数名不同，发错了会被静默忽略：
    #   enable_thinking  — DashScope(Qwen) 顶层参数
    #   thinking_type    — DeepSeek：{"thinking": {"type": "enabled"|"disabled"}}
    #   template_kwargs  — llama-server：chat_template_kwargs.enable_thinking
    # None = 按 URL 猜；多个写法可以同时发（"a+b"），用于兼容代理后面的端点
    thinking_style: Optional[str] = None
    local_max_tokens: int = 4096 # 本地模型默认生成上限，防止失控循环生成
    # 端点的真实输出上限（可选，仅用于预算计算）。不配就回落到 local_max_tokens：
    # 猜错（配大了）会真溢出，配保守只是少给输出留余量
    max_output_tokens: Optional[int] = None
    max_input_tokens: Optional[int] = None # 最大输入token数，供LangChain压缩中间件读取
    stream_read_timeout: float = 300.0 # 流式读超时；本地模型长prompt预fill首个token可能很慢

    # 模型请求地址非空校验
    @field_validator('url')
    def not_none_check_url(cls, v):
        if not v:
            raise ModelPropError("模型的请求地址不能为空")
        return v

    # 模型名称非空校验
    @field_validator('model')
    def not_none_check_model(cls, v):
        if not v:
            raise ModelPropError("模型的名称不能为空")
        return v

    # 关闭思考的写法在装配期就校验：拼错会导致"以为关了其实还在烧 token"
    @field_validator('thinking_style')
    def check_thinking_style(cls, v):
        if v is None or v == "":
            return None
        return normalize_thinking_style(v)

    @property
    def _llm_type(self) -> str:
        return self.model

    # 暴露模型上下文窗口给LangChain/deepagents，供自动压缩计算阈值
    @property
    def max_tokens(self) -> int:
        """生成长度上限；deepagents 的输入预算会预留这部分输出。"""
        configured = self.max_output_tokens
        if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
            return configured
        return self.local_max_tokens

    def _resolve_model_profile(self) -> Optional[ModelProfile]:
        if not self.max_input_tokens or self.max_input_tokens <= 0:
            return None
        return {"max_input_tokens": self.max_input_tokens}

    def check_params(self, messages, **kwargs):
        if not messages:
            raise ModelPromptError("模型提示词不能为空")

        for k, v in kwargs.items():
            if v is None:
                continue
            if k == "temperature" and (v < 0 or v > 2):
                raise ModelPromptError("model temperature must be between 0 and 2")
            if k == "top_p" and (v <= 0 or v > 1):
                raise ModelPromptError("model top_p must be between 0 and 1")
            if k == "max_tokens" and v <= 0:
                raise ModelPromptError("model max_tokens must be larger than 0")

    # 构建请求头
    def _build_headers(self, stream: bool) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json"
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if self.headers:
            for key, value in self.headers.items():
                headers[key] = value

        return headers

    # langchain消息转OpenAI格式消息
    def _convert_messages(self, messages: List) -> list[dict]:
        if not messages:
            raise ModelPromptError("提示词定义异常")

        prompts = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                prompts.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                prompts.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                assistant_prompt = {
                    "role": "assistant",
                    "content": self._strip_think_tags(msg.content)
                }

                if msg.tool_calls and len(msg.tool_calls) > 0:
                    tool_calls = [
                        {
                            "id": tc["id"],
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"])
                            },
                            "type": "function"
                        }
                        for tc in msg.tool_calls
                    ]

                    assistant_prompt["tool_calls"] = tool_calls

                prompts.append(assistant_prompt)
            elif isinstance(msg, ToolMessage):
                prompts.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id
                    }
                )

        return prompts

    # 回传历史前剥离think标签，避免字面标签进入chat template诱发本地模型（llama-server）复读
    def _strip_think_tags(self, content: Any) -> Any:
        if not isinstance(content, str) or THINK_OPEN not in content:
            return content

        cleaned = re.sub(rf"{THINK_OPEN}.*?{THINK_CLOSE}", "", content, flags=re.S)
        # 未闭合的think段（如流被中断）整段丢弃
        idx = cleaned.find(THINK_OPEN)
        if idx >= 0:
            cleaned = cleaned[:idx]

        return cleaned.strip()

    # 各端点关闭/开启思考的写法不同，发错参数不会报错，只会被静默忽略。
    # DeepSeek 官方文档：OpenAI 格式用 {"thinking": {"type": "enabled"|"disabled"}}，
    # 它不认 enable_thinking（实测传 False 仍会输出 reasoning_content）。
    DEEPSEEK_URL_MARKERS: ClassVar[tuple] = ("deepseek.com", "/deepseek/")

    def _thinking_style(self) -> str:
        """determine which request fields carry the thinking switch."""
        if self.thinking_style:
            return normalize_thinking_style(self.thinking_style)

        if any(marker in self.url for marker in self.DEEPSEEK_URL_MARKERS):
            return "thinking_type"

        if self.url.startswith(self.LOCAL_URL_PREFIXES):
            return "enable_thinking+template_kwargs"

        return "enable_thinking"

    # 构建请求体
    def _build_payload(self, messages: List, stream: bool, **kwargs) -> dict:
        data = {
            "model": self.model,
            "messages": self._convert_messages(messages),
            "stream": stream,
            **{k: v for k, v in kwargs.items() if v is not None}
        }

        # llama-server需通过chat_template_kwargs把enable_thinking透传给聊天模板，顶层参数它不识别。
        # DeepSeek 则只认 thinking.type——发 enable_thinking 会被静默忽略，用户以为关掉了其实没关。
        style = self._thinking_style()
        if "thinking_type" in style:
            data["thinking"] = {"type": "enabled" if self.enable_thinking else "disabled"}
        if "enable_thinking" in style:
            data["enable_thinking"] = self.enable_thinking
        if "template_kwargs" in style:
            data["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}

        if self.url.startswith(HttpOpenAi.LOCAL_URL_PREFIXES):
            # 本地模型不传max_tokens时默认按ctx剩余生成，跑飞时可达数分钟，强制上限兜底
            data.setdefault("max_tokens", self.local_max_tokens)

        if stream:
            data["stream_options"] = {"include_usage": True}

        return data

    # 参考ChatOpenAI重写父类的bind_tools方法，实现function_call的功能
    def bind_tools(
        self,
        tools: Sequence[Union[Dict[str, Any], Type, Callable, BaseTool]],
        *,
        tool_choice: Optional[
            Union[dict, str, Literal["auto", "none", "required", "any"], bool]
        ] = None,
        strict: Optional[bool] = None,
        **kwargs: Any
    ) -> Runnable[LanguageModelInput, AIMessage]:
        formatted_tools = [
            convert_to_openai_tool(tool, strict=strict) for tool in tools
        ]

        if tool_choice:
            if isinstance(tool_choice, str):
                if tool_choice not in ("auto", "none", "any", "required"):
                    tool_choice = {
                        "type": "function",
                        "function": {"name": tool_choice}
                    }

                if tool_choice == "any":
                    tool_choice = "required"
            elif isinstance(tool_choice, bool):
                tool_choice = "required"
            elif isinstance(tool_choice, dict):
                tool_names = [
                    formatted_tool["function"]["name"]
                    for formatted_tool in formatted_tools
                ]

                if not any(
                    tool_name == tool_choice["function"]["name"]
                    for tool_name in tool_names
                ):
                    raise ValueError(
                        f"Tool choice {tool_choice} was specified, but the only "
                        f"provided tools were {tool_names}."
                    )
            else:
                raise ValueError(
                    f"Unrecognized tool_choice type. Expected str, bool or dict."
                    f"Received: {tool_choice}"
                )

            kwargs["tool_choice"] = tool_choice
        return super().bind(tools=formatted_tools, **kwargs)

    # 解析非流式响应为ChatResult
    def _parse_completion_response(self, jresp: dict) -> ChatResult:
        choices = jresp.get("choices") or []
        if not choices:
            raise ModelCallError(f"模型返回结果异常：{json.dumps(jresp, ensure_ascii=False)[:200]}")

        message = choices[0].get("message") or {}
        raw_tool_calls = message.get("tool_calls") or []
        # 思考字段名各端点不同，探测后仍写回规范键 reasoning_content，下游不用改
        reasoning_content = extract_reasoning(message)
        additional_kwargs = {"reasoning_content": reasoning_content} if reasoning_content else {}
        if not reasoning_content:
            warn_missing_reasoning(self.model, jresp.get("usage"))

        # 如果有tool_calls，进行tool_calls返回数据的处理
        if raw_tool_calls:
            tool_calls = [
                {
                    "name": tc["function"]["name"],
                    "args": json.loads(tc["function"]["arguments"]),
                    "id": tc["id"]
                }
                for tc in raw_tool_calls
            ]

            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(
                    content=message.get("content") or "",
                    tool_calls=tool_calls,
                    additional_kwargs=additional_kwargs
                ))]
            )

        # 非tool_calls的正常返回，reasoning放additional_kwargs不混入正文
        content = message.get("content", "") or ""

        return ChatResult(generations=[
            ChatGeneration(message=AIMessage(content=content, additional_kwargs=additional_kwargs))
        ])

    # 异步流式输出 重写父类方法
    async def _astream(
        self,
        messages: List,
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        # 参数校验
        self.check_params(messages, **kwargs)

        data = self._build_payload(messages, stream=True, **kwargs)
        headers = self._build_headers(stream=True)

        default_chunk_class: type[BaseMessageChunk] = AIMessageChunk
        is_first_chunk = True
        # 流式响应的思考正文和 usage 不在同一帧，跨帧累计后才能判断是否漏读
        watch = ReasoningWatch(self.model)

        timeout = httpx.Timeout(self.stream_read_timeout, connect=10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream('POST', self.url, json=data, headers=headers) as response:
                    if response.status_code >= 400:
                        # 先读响应体：上下文溢出的标记只存在于 body 里
                        await response.aread()
                    response.raise_for_status()
                    base_generation_info = {"headers": dict(response.headers)}

                    async for line in response.aiter_lines():
                        if not line:
                            continue

                        decoded = line.strip()
                        if not decoded.startswith("data:") or decoded == "data: [DONE]":
                            continue

                        try:
                            chunk = json.loads(decoded[5:])
                            generation_chunk = _convert_chunk_to_generation_chunk(
                                chunk,
                                default_chunk_class,
                                base_generation_info if is_first_chunk else {}
                            )

                            if generation_chunk is None:
                                continue

                            default_chunk_class = generation_chunk.message.__class__
                            is_first_chunk = False
                            watch.observe(generation_chunk)
                            yield generation_chunk

                        except json.JSONDecodeError:
                            logger.warning(f"模型返回结构异常:{decoded}")
                            continue

                    watch.finish()
            except httpx.HTTPError as e:
                _raise_model_error(e)
            except ModelCallError:
                raise
            except Exception as e:
                raise ModelCallError(f"模型请求异常：{str(e)}")

    # 流式输出 重写父类方法
    def _stream(
        self,
        messages: List,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        # 参数校验
        self.check_params(messages, **kwargs)

        data = self._build_payload(messages, stream=True, **kwargs)
        headers = self._build_headers(stream=True)

        default_chunk_class: type[BaseMessageChunk] = AIMessageChunk
        is_first_chunk = True
        # 流式响应的思考正文和 usage 不在同一帧，跨帧累计后才能判断是否漏读
        watch = ReasoningWatch(self.model)

        timeout = httpx.Timeout(self.stream_read_timeout, connect=10)
        with httpx.Client(timeout=timeout) as client:
            try:
                with client.stream('POST', self.url, json=data, headers=headers) as response:
                    if response.status_code >= 400:
                        # 先读响应体：上下文溢出的标记只存在于 body 里
                        response.read()
                    response.raise_for_status()
                    base_generation_info = {"headers": dict(response.headers)}

                    for line in response.iter_lines():
                        if not line:
                            continue

                        decoded = line.strip()
                        if not decoded.startswith("data:") or decoded == "data: [DONE]":
                            continue

                        try:
                            chunk = json.loads(decoded[5:])
                            generation_chunk = _convert_chunk_to_generation_chunk(
                                chunk,
                                default_chunk_class,
                                base_generation_info if is_first_chunk else {}
                            )

                            if generation_chunk is None:
                                continue

                            default_chunk_class = generation_chunk.message.__class__
                            is_first_chunk = False
                            watch.observe(generation_chunk)
                            yield generation_chunk

                        except json.JSONDecodeError:
                            logger.warning(f"模型返回结构异常:{decoded}")
                            continue

                    watch.finish()
            except httpx.HTTPError as e:
                _raise_model_error(e)
            except ModelCallError:
                raise
            except Exception as e:
                raise ModelCallError(f"模型请求异常：{str(e)}")

    async def _agenerate(
        self,
        messages: List,
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any
    ) -> ChatResult:
        # 参数校验
        self.check_params(messages, **kwargs)

        data = self._build_payload(messages, stream=False, **kwargs)
        headers = self._build_headers(stream=False)

        # 请求模型
        timeout = httpx.Timeout(120) # 生成模式耗时较长，需要添加请求超时等待时间
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(self.url, json=data, headers=headers)
                response.raise_for_status()

                return self._parse_completion_response(response.json())
            except httpx.HTTPError as e:
                _raise_model_error(e)
            except ModelCallError:
                raise
            except Exception as e:
                raise ModelCallError(f"模型请求异常：{str(e)}")

    def _generate(
        self,
        messages: List,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any
    ) -> ChatResult:
        # 参数校验
        self.check_params(messages, **kwargs)

        data = self._build_payload(messages, stream=False, **kwargs)
        headers = self._build_headers(stream=False)

        # 请求模型
        timeout = httpx.Timeout(120) # 生成模式耗时较长，需要添加请求超时等待时间
        with httpx.Client(timeout=timeout) as client:
            try:
                response = client.post(self.url, json=data, headers=headers)
                response.raise_for_status()

                return self._parse_completion_response(response.json())
            except httpx.HTTPError as e:
                _raise_model_error(e)
            except ModelCallError:
                raise
            except Exception as e:
                raise ModelCallError(f"模型请求异常：{str(e)}")
