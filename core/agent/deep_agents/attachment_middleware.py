# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : attachment_middleware.py
# @Software    : VSCode
# @Datetime    : 2026/11/03 10:30:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 附件只在一轮内内联：更早的收起为引用，内联总量超限时从最老开始丢

"""为什么在模型调用前改、而不是在落库时改：state 里的原始消息是界面重放与排障的
唯一依据，收起的只是"这次请求发什么"。所有改写都发生在请求副本上，
`message.model_copy()` 保证不原地修改 state 对象。

两条规则（详见 design 5.3）：

1. **按轮收起**：最后一条"真实用户消息"之前的附件内容替换成引用（label + 路径 +
   重读提示）；图片块一律不发送。`nova_rehydrated` 标记的消息是 `read_attachment`
   注入的，不算边界，也不算旧轮次。
2. **字节兜底**：当前轮仍内联的图片 base64 总量 >= 25 MiB 时，从最老开始收起，
   直到 <= 15 MiB；注入的图片计入总量但永不驱逐，避免"重读 → 被收 → 再读"。
"""

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from service.attachment.attachments import (
    ATTACH_END,
    REF_LINE_RE,
    PendingImages,
    collapse_text,
)

REHYDRATED_KEY = "nova_rehydrated"
MAX_INLINE_BYTES = 25 * 1024 * 1024
TARGET_INLINE_BYTES = 15 * 1024 * 1024
INJECT_HEADER = "[附件图片] 以下图片随本轮请求提供；路径见上一条用户消息。"
_IMAGE_REF_HINT = (
    '需要时用 read_attachment("{path}") 重新读取；不要凭记忆描述或引用已收起的内容。'
)
_IMAGE_REF_FALLBACK = "该图片已收起；需要时请用户重新附图。"


def _is_rehydrated(message: Any) -> bool:
    return bool((getattr(message, "additional_kwargs", None) or {}).get(REHYDRATED_KEY))


def _blocks(message: Any) -> list:
    content = getattr(message, "content", None)
    return content if isinstance(content, list) else []


def _is_image(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "image_url"


def _image_bytes(block: Any) -> int:
    """base64 data URL 的载荷字节数；不是图片块返回 0。"""
    if not _is_image(block):
        return 0

    inner = block.get("image_url")
    url = inner.get("url", "") if isinstance(inner, dict) else inner
    if not isinstance(url, str):
        return 0

    _prefix, _sep, payload = url.partition(",")
    return len(payload)


def _message_image_bytes(message: Any) -> int:
    return sum(_image_bytes(block) for block in _blocks(message))


def _ref_paths(message: Any) -> list[str]:
    """按出现顺序取消息里的附件引用行（label = path (mime, size)）。"""
    paths: list[str] = []
    for block in _blocks(message):
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        for line in block["text"].splitlines():
            ref = REF_LINE_RE.match(line.strip())
            if ref:
                paths.append(ref.group(2))

    return paths


def degrade_message(message: Any) -> Any:
    """把一条旧消息收成"引用 + 用户原文"；没有附件内容时原样返回同一个对象。"""
    content = getattr(message, "content", None)

    if isinstance(content, str):
        if ATTACH_END not in content:
            return message
        collapsed = collapse_text(content)
        return message.model_copy(update={"content": collapsed})

    if not isinstance(content, list):
        return message

    texts = [
        block["text"] for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    joined = "\n".join(texts)
    has_image = any(_is_image(block) for block in content)
    if not has_image and ATTACH_END not in joined:
        return message

    # 图片块直接丢弃；有边界标记时连正文（文档/文本内联）一起收掉
    return message.model_copy(update={"content": collapse_text(joined)})


def _evict_oldest(message: Any, need: int) -> tuple[Any, int]:
    """把消息里最老的图片换成引用文本，直到省出 `need` 字节。

    返回 (新消息或原对象, 省下的字节)。图片与引用行按出现顺序配对。
    """
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return message, 0

    paths = _ref_paths(message)
    blocks: list = []
    image_index = 0
    saved = 0
    changed = False

    for block in content:
        if _is_image(block):
            if need > 0:
                size = _image_bytes(block)
                path = paths[image_index] if image_index < len(paths) else ""
                hint = _IMAGE_REF_HINT.format(path=path) if path else _IMAGE_REF_FALLBACK
                blocks.append({"type": "text", "text": f"[图片已收起] {hint}"})
                saved += size
                need -= size
                changed = True
            else:
                blocks.append(block)
            image_index += 1
            continue
        blocks.append(block)

    if not changed:
        return message, 0
    return message.model_copy(update={"content": blocks}), saved


class AttachmentLifecycleMiddleware(AgentMiddleware):
    """附件在请求层的生命周期：一轮内内联，之后收起，超量时兜底。"""

    def __init__(
        self,
        max_inline_bytes: int = MAX_INLINE_BYTES,
        target_inline_bytes: int = TARGET_INLINE_BYTES,
        pending_images: PendingImages | None = None,
    ) -> None:
        self.max_inline_bytes = max_inline_bytes
        self.target_inline_bytes = target_inline_bytes
        # 与 read_attachment / 服务层共享同一个实例：工具追加、422 清空都要立刻可见
        self.pending_images = pending_images if pending_images is not None else PendingImages()

    def _boundary(self, messages: list) -> int:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, HumanMessage) and not _is_rehydrated(message):
                return index

        return len(messages)

    def _with_pending(self, messages: list) -> list:
        """把本轮图片追加成一条临时 user 消息；不写 state，每次模型调用都重新追加。"""
        if not self.pending_images:
            return messages

        injected = HumanMessage(
            content=[{"type": "text", "text": INJECT_HEADER}, *list(self.pending_images)],
            additional_kwargs={REHYDRATED_KEY: True},
        )
        return [*messages, injected]

    def _rewrite(self, messages: list) -> list:
        boundary = self._boundary(messages)
        rewritten = [
            degrade_message(message) if index < boundary else message
            for index, message in enumerate(messages)
        ]
        rewritten = self._with_pending(rewritten)

        total = sum(
            _message_image_bytes(message)
            for index, message in enumerate(rewritten) if index >= boundary
        )
        if total < self.max_inline_bytes:
            return rewritten

        # 从最老开始丢；注入的图片不参与驱逐（否则会陷入反复重读）
        for index in range(boundary, len(rewritten)):
            if total <= self.target_inline_bytes:
                break
            if _is_rehydrated(rewritten[index]):
                continue
            replaced, saved = _evict_oldest(rewritten[index], total - self.target_inline_bytes)
            if replaced is not rewritten[index]:
                rewritten[index] = replaced
                total -= saved

        return rewritten

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(request.override(messages=self._rewrite(list(request.messages))))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(request.override(messages=self._rewrite(list(request.messages))))
