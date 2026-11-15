# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : chunk.py
# @Software    : VSCode
# @Datetime    : 2026/04/19 20:48:11
# @Author      : leo liu
# @Version     : 1.0
# @Description : 对langchain_openai.BaseChatOpenAI的部分方法引用

from typing import Any,cast
from collections.abc import Mapping
from langchain_core.messages.ai import UsageMetadata
from langchain_core.messages import (
    BaseMessageChunk,
    HumanMessageChunk,
    AIMessageChunk,
    SystemMessageChunk,
    ToolCallChunk,
    FunctionMessageChunk,
    ToolMessageChunk,
    ChatMessageChunk
)
from langchain_core.messages.ai import (
    InputTokenDetails,
    OutputTokenDetails,
)
from langchain_core.outputs import ChatGenerationChunk

from .reasoning import extract_reasoning

def _convert_delta_to_message_chunk(
    _dict: Mapping[str, Any], default_class: type[BaseMessageChunk]
) -> BaseMessageChunk:
    """Convert to a LangChain message chunk."""
    id_ = _dict.get("id")
    role = cast(str, _dict.get("role"))

    # 思考字段名各端点不同（reasoning_content / reasoning / thinking / 块数组），统一探测
    reasoning_content = extract_reasoning(_dict)
    content = cast(str, _dict.get("content") or "")

    # 正文只保留content，reasoning单独放入additional_kwargs，避免思考内容混入历史回灌模型
    additional_kwargs: dict = {}
    if reasoning_content:
        additional_kwargs["reasoning_content"] = reasoning_content
    if _dict.get("function_call"):
        function_call = dict(_dict["function_call"])
        if "name" in function_call and function_call["name"] is None:
            function_call["name"] = ""
        additional_kwargs["function_call"] = function_call
    tool_call_chunks = []
    if raw_tool_calls := _dict.get("tool_calls"):
        try:
            tool_call_chunks = [
                ToolCallChunk(
                    name=rtc["function"].get("name"),
                    args=rtc["function"].get("arguments"),
                    id=rtc.get("id"),
                    index=rtc["index"],
                )
                for rtc in raw_tool_calls
            ]
        except KeyError:
            pass

    if role == "user" or default_class == HumanMessageChunk:
        return HumanMessageChunk(content=content, id=id_)
    if role == "assistant" or default_class == AIMessageChunk:
        return AIMessageChunk(
            content=content,
            additional_kwargs=additional_kwargs,
            id=id_,
            tool_call_chunks=tool_call_chunks,  # type: ignore[arg-type]
        )
    if role in ("system", "developer") or default_class == SystemMessageChunk:
        if role == "developer":
            additional_kwargs = {"__openai_role__": "developer"}
        else:
            additional_kwargs = {}
        return SystemMessageChunk(
            content=content, id=id_, additional_kwargs=additional_kwargs
        )
    if role == "function" or default_class == FunctionMessageChunk:
        return FunctionMessageChunk(content=content, name=_dict["name"], id=id_)
    if role == "tool" or default_class == ToolMessageChunk:
        return ToolMessageChunk(
            content=content, tool_call_id=_dict["tool_call_id"], id=id_
        )
    if role or default_class == ChatMessageChunk:
        return ChatMessageChunk(content=content, role=role, id=id_)

    return default_class(content=content, id=id_)  # type: ignore[call-arg]

def _create_usage_metadata(
    oai_token_usage: dict, service_tier: str | None = None
) -> UsageMetadata:
    input_tokens = oai_token_usage.get("prompt_tokens") or 0
    output_tokens = oai_token_usage.get("completion_tokens") or 0
    total_tokens = oai_token_usage.get("total_tokens") or input_tokens + output_tokens
    if service_tier not in {"priority", "flex"}:
        service_tier = None
    service_tier_prefix = f"{service_tier}_" if service_tier else ""
    input_token_details: dict = {
        "audio": (oai_token_usage.get("prompt_tokens_details") or {}).get(
            "audio_tokens"
        ),
        f"{service_tier_prefix}cache_read": (
            oai_token_usage.get("prompt_tokens_details") or {}
        ).get("cached_tokens"),
    }
    output_token_details: dict = {
        "audio": (oai_token_usage.get("completion_tokens_details") or {}).get(
            "audio_tokens"
        ),
        f"{service_tier_prefix}reasoning": (
            oai_token_usage.get("completion_tokens_details") or {}
        ).get("reasoning_tokens"),
    }
    if service_tier is not None:
        # Avoid counting cache and reasoning tokens towards the service tier token
        # counts, since service tier tokens are already priced differently
        input_token_details[service_tier] = input_tokens - input_token_details.get(
            f"{service_tier_prefix}cache_read", 0
        )
        output_token_details[service_tier] = output_tokens - output_token_details.get(
            f"{service_tier_prefix}reasoning", 0
        )
    return UsageMetadata(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_token_details=InputTokenDetails(
            **{k: v for k, v in input_token_details.items() if v is not None}
        ),
        output_token_details=OutputTokenDetails(
            **{k: v for k, v in output_token_details.items() if v is not None}
        ),
    )

def _convert_chunk_to_generation_chunk(
    chunk: dict,
    default_chunk_class: type,
    base_generation_info: dict | None
) -> ChatGenerationChunk | None:
    if chunk.get("type") == "content.delta":  # From beta.chat.completions.stream
        return None
    token_usage = chunk.get("usage")
    choices = (
        chunk.get("choices", [])
        # From beta.chat.completions.stream
        or chunk.get("chunk", {}).get("choices", [])
    )

    usage_metadata: UsageMetadata | None = (
        _create_usage_metadata(token_usage, chunk.get("service_tier"))
        if token_usage
        else None
    )
    if len(choices) == 0:
        # logprobs is implicitly None
        generation_chunk = ChatGenerationChunk(
            message=default_chunk_class(content="", usage_metadata=usage_metadata),
            generation_info=base_generation_info,
        )

        # llama-server的timings（含实时生成速度predicted_per_second）随usage块下发，透传给上层
        if usage_metadata and isinstance(generation_chunk.message, AIMessageChunk):
            if timings := chunk.get("timings"):
                generation_chunk.message.response_metadata["timings"] = timings

        return generation_chunk

    choice = choices[0]
    if choice["delta"] is None:
        return None

    message_chunk = _convert_delta_to_message_chunk(
        choice["delta"], default_chunk_class
    )
    generation_info = {**base_generation_info} if base_generation_info else {}

    if finish_reason := choice.get("finish_reason"):
        generation_info["finish_reason"] = finish_reason
        if model_name := chunk.get("model"):
            generation_info["model_name"] = model_name
        if system_fingerprint := chunk.get("system_fingerprint"):
            generation_info["system_fingerprint"] = system_fingerprint
        if service_tier := chunk.get("service_tier"):
            generation_info["service_tier"] = service_tier
        if isinstance(message_chunk, AIMessageChunk):
            message_chunk.chunk_position = "last"

    logprobs = choice.get("logprobs")
    if logprobs:
        generation_info["logprobs"] = logprobs

    if usage_metadata and isinstance(message_chunk, AIMessageChunk):
        message_chunk.usage_metadata = usage_metadata

    message_chunk.response_metadata["model_provider"] = "openai"
    return ChatGenerationChunk(
        message=message_chunk, generation_info=generation_info or None
    )

