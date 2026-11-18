# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : thinking.py
# @Description : 思考档位：把「关 / 低 / 中 / 高」翻译成各家端点的请求字段

"""思考档位与请求字段。

2026 年起很多模型已经没有"关思考"这个硬开关，取而代之的是档位：

| 端点 | 实际字段 |
|---|---|
| OpenAI GPT-5.x | `reasoning_effort`: none / low / medium / high / (xhigh) |
| DeepSeek | `thinking: {type}` + `reasoning_effort`: low / high / max |
| Gemini 3 | `thinking_level`: minimal / low / medium / high |
| Claude | `thinking: {type, budget_tokens}` + `effort` |
| Qwen / DashScope | `enable_thinking`: true/false |
| llama-server | `enable_thinking` + `chat_template_kwargs` |

发错字段不会报错，只会被**静默忽略**——用户以为关掉了，实际还在烧 token。
所以这里把"用哪种写法"与"档位"分开：写法显式配置（或按 URL 猜），档位只表达语义，
不支持的档位宁可不发也不猜。
"""

from typing import Optional

# 统一档位。minimal / max / xhigh 这些端专有值都折叠进这四档：
# 关=off；最省=low；默认=medium；最费=high
LEVELS = ("off", "low", "medium", "high")
# 不覆盖：请求里一个思考字段都不发，由端点自己决定。
# 只用于"我们确实不知道该模型默认档位"的场景（如动态发现出来的模型）
AUTO = "auto"
LEVEL_ALIASES = {
    "0": "off", "false": "off", "disabled": "off", "none": "off", "no": "off", "off": "off",
    "1": "medium", "true": "medium", "enabled": "medium", "on": "medium", "yes": "medium",
    "minimal": "low", "low": "low",
    "mid": "medium", "middle": "medium", "medium": "medium",
    "high": "high", "max": "high", "xhigh": "high",
}

# 按 URL 猜写法用的标记（与 HttpOpenAi 共用同一份，避免两处漂移）。
# opencode.ai 是 OpenAI 兼容网关：实测 enable_thinking 被静默忽略，reasoning_effort
# 有效（none 能真的关掉推理），所以归到 effort 一类。
OPENAI_URL_MARKERS = ("api.openai.com", "openai.azure.com", "opencode.ai")
DEEPSEEK_URL_MARKERS = ("deepseek.com", "/deepseek/")
LOCAL_URL_PREFIXES = ("http://127.0.0.1", "http://localhost", "http://[::1]", "unix://")

# 请求体写法的原子部件。用 "+" 组合，例如 DeepSeek 的开关与档位要一起发：
#   thinking_type+reasoning_effort
PARTS = ("enable_thinking", "thinking_type", "template_kwargs", "reasoning_effort", "thinking_level")
STYLE_NONE = "none"


def normalize_level(level) -> Optional[str]:
    """把任意输入归一成四档之一；None/空/auto 返回 None（跟随模型默认）。

    bool 仍被接受：True=medium、False=off。老配置与老接口都还在用布尔。
    """
    if level is None or level == "":
        return None
    if isinstance(level, bool):
        return "medium" if level else "off"
    text = str(level).strip().lower()
    if not text or text in ("auto", "default", "model"):
        return None
    if text in LEVEL_ALIASES:
        return LEVEL_ALIASES[text]
    raise ValueError(f"未知的思考档位：{level!r}；可用：{', '.join(LEVELS)}（或 auto）")


def is_auto(level) -> bool:
    return isinstance(level, str) and level.strip().lower() == AUTO


def normalize_style(style) -> str:
    """校验并归一请求写法；"none" 表示一个字段都不发。

    组合写法按字母序归一，配置里写 `reasoning_effort+thinking_type` 与
    `thinking_type+reasoning_effort` 等价。
    """
    text = str(style or "").strip()
    if not text:
        return ""
    if text == STYLE_NONE:
        return STYLE_NONE

    parts = sorted({part.strip() for part in text.split("+") if part.strip()})
    unknown = [part for part in parts if part not in PARTS]
    if unknown:
        raise ValueError(
            f"未知的 thinking_style: {style!r}；可用部件：{', '.join(PARTS)}"
            f"（或用 + 组合，或写 {STYLE_NONE} 表示不发）"
        )
    return "+".join(parts)


def resolve_style(url: str, configured: Optional[str]=None) -> str:
    """显式配置优先，否则按 URL 猜；猜不到的通用端点按 DashScope 的顶层布尔发。"""
    if configured:
        return normalize_style(configured)
    if any(marker in (url or "") for marker in OPENAI_URL_MARKERS):
        return "reasoning_effort"
    if any(marker in (url or "") for marker in DEEPSEEK_URL_MARKERS):
        return "thinking_type"
    if (url or "").startswith(LOCAL_URL_PREFIXES):
        return "enable_thinking+template_kwargs"
    return "enable_thinking"


def style_parts(style: str) -> tuple[str, ...]:
    if not style or style == STYLE_NONE:
        return ()
    return tuple(style.split("+"))


def supports_levels(style: str) -> bool:
    """该写法能不能表达档位（而不只是开关）。用于 UI 决定显示几档。"""
    parts = style_parts(style)
    return "reasoning_effort" in parts or "thinking_level" in parts


def levels_for(style: str, configured=None) -> tuple[str, ...]:
    """这个写法能表达的档位：只认开关的端点给 off/medium 两档，effort 类给四档。

    显式配置 thinking_levels 时以配置为准（按 LEVELS 的固定顺序输出，去重）。
    """
    if configured:
        allowed = set()
        for item in configured:
            try:
                level = normalize_level(item)
            except ValueError:
                continue  # 配错的档位名直接忽略：宁少一档，不让整个目录接口 500
            if level:
                allowed.add(level)
        ordered = tuple(level for level in LEVELS if level in allowed)
        if ordered:
            return ordered
    return LEVELS if supports_levels(style) else ("off", "medium")


def default_off_value(style: str) -> str:
    """关闭思考时发给"没有关这个值"的端点用什么。

    - effort 类：OpenAI 认 none；更老的推理模型只认 minimal
    - Gemini 3 的 thinking_level：minimal
    """
    return "minimal" if "thinking_level" in style_parts(style) else "none"


def build_fields(level: str, style: str, off_value: Optional[str]=None) -> dict:
    """按档位与写法拼出请求字段；空 dict 表示这个端点什么都不发。"""
    parts = style_parts(style)
    if not parts or not level or is_auto(level):
        return {}

    enabled = level != "off"
    off = off_value or default_off_value(style)
    fields: dict = {}

    if "thinking_type" in parts:
        fields["thinking"] = {"type": "enabled" if enabled else "disabled"}
    if "enable_thinking" in parts:
        fields["enable_thinking"] = enabled
    if "template_kwargs" in parts:
        fields["chat_template_kwargs"] = {"enable_thinking": enabled}

    if "reasoning_effort" in parts:
        # 只有 effort、没有独立开关的端点（OpenAI GPT-5）：关 = 最低档
        if enabled:
            fields["reasoning_effort"] = level
        elif "thinking_type" not in parts and "enable_thinking" not in parts:
            fields["reasoning_effort"] = off

    if "thinking_level" in parts:
        fields["thinking_level"] = level if enabled else off

    return fields
