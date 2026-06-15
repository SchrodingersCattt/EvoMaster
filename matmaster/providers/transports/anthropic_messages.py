"""Native Anthropic Messages transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic

from matmaster.providers.image_payloads import (
    ImagePayloadError,
    inline_image_url_as_base64,
)
from matmaster.providers.transport import Transport, dump_model_to_jsonable
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    LLMResponse,
    Message,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


@dataclass(frozen=True)
class AnthropicPromptCacheOptions:
    """Provider-local Anthropic prompt cache controls."""

    system_prompt_breakpoint: bool
    cache_control: dict[str, str]
    automatic: bool = False
    latest_user_breakpoint: bool = True
    tool_result_breakpoint: bool = False
    flexible_breakpoint: bool = False
    max_breakpoints: int = 4
    min_flexible_chars: int = 1000


@dataclass(frozen=True)
class _CacheTarget:
    section: str
    index: int
    content_index: int | None
    priority: int


@dataclass
class _StreamBlockState:
    type: str
    id: str | None = None
    name: str | None = None
    output_index: int | None = None
    thinking: str = ""
    signature: str | None = None
    arguments: str = ""
    redacted_thinking: dict[str, Any] | None = None


def _message_text_size(message: dict[str, Any]) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return len(content.strip())
    if isinstance(content, list):
        return sum(
            len(part["text"].strip())
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return 0


def _select_flexible_cache_target(
    messages: list[dict[str, Any]],
    used: set[int],
    options: AnthropicPromptCacheOptions,
) -> _CacheTarget | None:
    candidates: list[tuple[int, int]] = []
    for idx, message in enumerate(messages):
        if idx in used:
            continue
        if message.get("role") != "user":
            continue
        size = _message_text_size(message)
        if size >= options.min_flexible_chars:
            candidates.append((size, idx))
    if not candidates:
        return None
    _, idx = max(candidates)
    return _CacheTarget("message", idx, None, 3)


def _effective_message_cache_slot(
    messages: list[dict[str, Any]], target: _CacheTarget
) -> tuple[int, int | None]:
    if target.content_index is not None:
        return (target.index, target.content_index)
    content = messages[target.index].get("content")
    if isinstance(content, list) and content:
        return (target.index, len(content) - 1)
    return (target.index, None)


def _select_anthropic_cache_targets(
    *,
    has_system: bool,
    messages: list[dict[str, Any]],
    options: AnthropicPromptCacheOptions,
    emit_top_level_auto: bool = True,
) -> list[_CacheTarget]:
    targets: list[_CacheTarget] = []
    used_slots: set[tuple[int, int | None]] = set()
    used_whole_message_indexes: set[int] = set()
    automatic_uses_top_level = options.automatic and emit_top_level_auto
    max_block_targets = options.max_breakpoints - (1 if automatic_uses_top_level else 0)
    max_block_targets = max(0, max_block_targets)

    def append(target: _CacheTarget) -> None:
        if len(targets) >= max_block_targets:
            return
        if target.section == "message":
            slot = _effective_message_cache_slot(messages, target)
            if slot in used_slots:
                return
        targets.append(target)
        if target.section == "message":
            used_slots.add(slot)
            if target.content_index is None:
                used_whole_message_indexes.add(target.index)

    if options.system_prompt_breakpoint and has_system:
        append(_CacheTarget("system", 0, None, 0))
    if options.automatic and options.latest_user_breakpoint:
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "user":
                append(_CacheTarget("message", idx, None, 1))
                break
    if options.automatic and options.tool_result_breakpoint:
        for idx in range(len(messages) - 1, -1, -1):
            blocks = messages[idx].get("content")
            if messages[idx].get("role") != "user" or not isinstance(blocks, list):
                continue
            result_indexes = [
                block_idx
                for block_idx, block in enumerate(blocks)
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            if result_indexes:
                append(_CacheTarget("message", idx, result_indexes[-1], 2))
                break
    if options.automatic and options.flexible_breakpoint:
        flexible = _select_flexible_cache_target(
            messages, used_whole_message_indexes, options
        )
        if flexible is not None:
            append(flexible)
    return targets


def _with_cache_control(
    block: dict[str, Any], cache_control: dict[str, str]
) -> dict[str, Any]:
    out = dict(block)
    out["cache_control"] = dict(cache_control)
    return out


def _mark_content_block(
    message: dict[str, Any],
    cache_control: dict[str, str],
    content_index: int | None = None,
) -> None:
    content = message.get("content")
    if isinstance(content, list) and content:
        idx = content_index if content_index is not None else len(content) - 1
        content[idx] = _with_cache_control(content[idx], cache_control)
        return
    if isinstance(content, str):
        message["content"] = [
            {"type": "text", "text": content, "cache_control": dict(cache_control)}
        ]


def _text_block(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    return [{"type": "text", "text": text}]


def _image_block(image: ImageContentPart) -> dict[str, Any]:
    url = image.url
    if url.startswith("data:") and ";base64," in url:
        header, data = url.split(";base64,", 1)
        media_type = image.mime_type or header.removeprefix("data:") or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _user_content_blocks(message: UserMessage) -> list[dict[str, Any]]:
    blocks = _text_block(message.content)
    blocks.extend(_image_block(image) for image in message.images)
    return blocks


def _tool_use_blocks(tool_calls: list[ToolCallData] | None) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_use",
            "id": tc.id,
            "name": tc.name,
            "input": tc.arguments,
        }
        for tc in (tool_calls or [])
    ]


def _tool_result_block(message: ToolMessage) -> dict[str, Any]:
    if message.images:
        blocks: list[dict[str, Any]] = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        blocks.extend(_image_block(image) for image in message.images)
        return {
            "type": "tool_result",
            "tool_use_id": message.tool_call_id,
            "content": blocks,
        }
    return {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": message.content or "",
    }


def _thinking_blocks_from_payload(
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not payload:
        return []
    raw = payload.get("thinking")
    if not isinstance(raw, list):
        return []
    return [dict(block) for block in raw if isinstance(block, dict)]


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", {})
        item = {
            "name": function["name"],
            "input_schema": function.get("parameters")
            or {"type": "object", "properties": {}},
        }
        if function.get("description"):
            item["description"] = function["description"]
        converted.append(item)
    return converted


def _map_tool_choice(tool_choice: str | dict | None) -> dict[str, str] | None:
    if tool_choice is None or tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "none":
        return {"type": "none"}
    raise LLMError(
        "anthropic_messages with thinking enabled supports only tool_choice auto/none",
        retryable=False,
        error_category="bad_request",
    )


def _is_context_overflow(text: str) -> bool:
    lowered = text.lower()
    if "context" in lowered and (
        "token" in lowered or "length" in lowered or "window" in lowered
    ):
        return True
    return (
        "prompt" in lowered
        and "too long" in lowered
        and ("token" in lowered or "maximum" in lowered)
    )


def _is_non_retryable_anthropic_bad_request(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        "signature",
        "thinking",
        "cache_control",
        "tool_result",
        "tool_use",
        "must be immediately after",
        "input_schema",
    )
    return any(pattern in lowered for pattern in patterns)


def _usage_value(usage: Any, key: str) -> Any:
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def _anthropic_usage_to_scalar_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    prompt = int(_usage_value(usage, "input_tokens") or 0)
    completion = int(_usage_value(usage, "output_tokens") or 0)
    out = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    cache_read = _usage_value(usage, "cache_read_input_tokens")
    if isinstance(cache_read, int) and cache_read > 0:
        out["cache_read_tokens"] = cache_read
    cache_write = _usage_value(usage, "cache_creation_input_tokens")
    if isinstance(cache_write, int) and cache_write > 0:
        out["cache_write_tokens"] = cache_write
    details = _usage_value(usage, "output_tokens_details")
    if isinstance(details, dict):
        reasoning = details.get("thinking_tokens")
    else:
        reasoning = (
            getattr(details, "thinking_tokens", None) if details is not None else None
        )
    if isinstance(reasoning, int) and reasoning > 0:
        out["reasoning_tokens"] = reasoning
    return out


def _map_stop_reason(stop_reason: str | None) -> str | None:
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "refusal": "content_filter",
        "pause_turn": "stop",
    }.get(stop_reason, stop_reason)


class AnthropicMessagesTransport(Transport):
    """Native Anthropic Messages API transport."""

    transport_tag = "anthropic_messages"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_options: AnthropicPromptCacheOptions | None = None,
        timeout: float = 300.0,
        stream_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        super().__init__(
            timeout=timeout,
            stream_timeout=stream_timeout,
            stream_idle_timeout=stream_idle_timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._prompt_cache_options = prompt_cache_options

    async def _open_client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            http_client=self._build_http_client(),
        )

    async def _close_client(self, client: anthropic.AsyncAnthropic) -> None:
        await client.close()

    def _emit_top_level_auto_cache(self) -> bool:
        """automatic 时是否随请求发顶层 cache_control（native 默认发）。"""
        return True

    def _image_block(self, image: ImageContentPart) -> dict[str, Any]:
        return _image_block(image)

    def _user_content_blocks(self, message: UserMessage) -> list[dict[str, Any]]:
        blocks = _text_block(message.content)
        blocks.extend(self._image_block(image) for image in message.images)
        return blocks

    def _tool_result_block(self, message: ToolMessage) -> dict[str, Any]:
        if message.images:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(self._image_block(image) for image in message.images)
            return {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": blocks,
            }
        return {
            "type": "tool_result",
            "tool_use_id": message.tool_call_id,
            "content": message.content or "",
        }

    def _assistant_to_wire(self, message: AssistantMessage) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        blocks.extend(
            _thinking_blocks_from_payload(self._claim_provider_state(message))
        )
        blocks.extend(_text_block(message.content))
        blocks.extend(_tool_use_blocks(message.tool_calls))
        return {"role": "assistant", "content": blocks}

    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        validate_tool_turn_sequence(messages)
        out: list[dict[str, Any]] = []
        idx = 0
        while idx < len(messages):
            message = messages[idx]
            if isinstance(message, SystemMessage):
                idx += 1
                continue
            if isinstance(message, UserMessage):
                out.append(
                    {"role": "user", "content": self._user_content_blocks(message)}
                )
                idx += 1
                continue
            if isinstance(message, AssistantMessage):
                out.append(self._assistant_to_wire(message))
                idx += 1
                if not message.tool_calls:
                    continue
                result_blocks: list[dict[str, Any]] = []
                while idx < len(messages) and isinstance(messages[idx], ToolMessage):
                    result_blocks.append(self._tool_result_block(messages[idx]))
                    idx += 1
                if idx < len(messages) and isinstance(messages[idx], UserMessage):
                    result_blocks.extend(self._user_content_blocks(messages[idx]))
                    idx += 1
                if result_blocks:
                    out.append({"role": "user", "content": result_blocks})
                continue
            if isinstance(message, ToolMessage):
                out.append(
                    {"role": "user", "content": [self._tool_result_block(message)]}
                )
                idx += 1
                continue
            idx += 1
        return out

    def build_kwargs(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        system_messages = [m for m in messages if isinstance(m, SystemMessage)]
        system_text = "\n\n".join(m.content or "" for m in system_messages).strip()
        converted_messages = self.convert_messages(messages)
        system_value: str | list[dict[str, Any]] | None = system_text or None
        options = self._prompt_cache_options
        if options is not None:
            emit_top_level_auto = self._emit_top_level_auto_cache()
            targets = _select_anthropic_cache_targets(
                has_system=bool(system_value),
                messages=converted_messages,
                options=options,
                emit_top_level_auto=emit_top_level_auto,
            )
            for target in targets:
                if target.section == "system" and isinstance(system_value, str):
                    system_value = [
                        {
                            "type": "text",
                            "text": system_value,
                            "cache_control": dict(options.cache_control),
                        }
                    ]
                elif target.section == "message":
                    _mark_content_block(
                        converted_messages[target.index],
                        options.cache_control,
                        target.content_index,
                    )
            if options.automatic and emit_top_level_auto:
                kwargs_extra_body = {"cache_control": dict(options.cache_control)}
            else:
                kwargs_extra_body = {}
        else:
            kwargs_extra_body = {}
        converted_tools = _convert_tools(tools)
        mapped_tool_choice = _map_tool_choice(tool_choice)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": converted_messages,
            "thinking": {"type": "adaptive", "display": "summarized"},
        }
        if system_value:
            kwargs["system"] = system_value
        if self._reasoning_effort:
            kwargs["output_config"] = {"effort": self._reasoning_effort}
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if converted_tools:
            kwargs["tools"] = converted_tools
            if mapped_tool_choice is not None:
                kwargs["tool_choice"] = mapped_tool_choice
        elif tool_choice == "none":
            kwargs["tool_choice"] = {"type": "none"}
        if stream:
            kwargs["stream"] = True
        if kwargs_extra_body:
            kwargs["extra_body"] = kwargs_extra_body
        return kwargs

    def normalize_response(self, raw: Any) -> LLMResponse:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        thinking_blocks: list[dict[str, Any]] = []
        tool_calls: list[ToolCallData] = []
        for block in getattr(raw, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "thinking":
                thinking = getattr(block, "thinking", "")
                signature = getattr(block, "signature", None)
                payload = {"type": "thinking", "thinking": thinking}
                if signature:
                    payload["signature"] = signature
                thinking_blocks.append(payload)
                if thinking:
                    reasoning_parts.append(thinking)
            elif block_type == "redacted_thinking":
                dumped = dump_model_to_jsonable(block)
                if isinstance(dumped, dict):
                    thinking_blocks.append(dumped)
            elif block_type == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCallData(
                        id=block.id,
                        name=block.name,
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )
        provider_state = None
        if thinking_blocks:
            from matmaster.types.messages import ProviderState

            provider_state = ProviderState(
                transport=self.transport_tag,
                payload={"thinking": thinking_blocks},
            )
        usage = getattr(raw, "usage", None)
        return LLMResponse(
            content="".join(text_parts) or None,
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=tool_calls or None,
            finish_reason=_map_stop_reason(getattr(raw, "stop_reason", None)),
            usage=_anthropic_usage_to_scalar_dict(usage),
            usage_vendor=dump_model_to_jsonable(usage) if usage is not None else None,
            provider_state=provider_state,
        )

    async def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        blocks: dict[int, _StreamBlockState] = {}
        thinking_payload: list[dict[str, Any]] = []
        next_tool_call_index = 0
        usage_snapshot: dict[str, Any] = {}
        finish_reason: str | None = None

        async for event in raw_iter:
            event_type = getattr(event, "type", None)
            if event_type == "message_start":
                usage = getattr(getattr(event, "message", None), "usage", None)
                dumped_usage = dump_model_to_jsonable(usage)
                if isinstance(dumped_usage, dict):
                    usage_snapshot.update(dumped_usage)
                continue
            if event_type == "content_block_start":
                block = getattr(event, "content_block", None)
                block_type = getattr(block, "type", "")
                state = _StreamBlockState(
                    type=block_type,
                    id=getattr(block, "id", None),
                    name=getattr(block, "name", None),
                )
                if block_type == "tool_use":
                    state.output_index = next_tool_call_index
                    next_tool_call_index += 1
                elif block_type == "redacted_thinking":
                    dumped = dump_model_to_jsonable(block)
                    if isinstance(dumped, dict):
                        state.redacted_thinking = dumped
                blocks[int(getattr(event, "index", 0))] = state
                if block_type == "tool_use":
                    yield StreamChunk(
                        tool_call_deltas=[
                            {
                                "index": state.output_index,
                                "id": state.id,
                                "name": state.name,
                            }
                        ]
                    )
                continue
            if event_type == "content_block_delta":
                idx = int(getattr(event, "index", 0))
                state = blocks.setdefault(idx, _StreamBlockState(type=""))
                delta = getattr(event, "delta", None)
                delta_type = getattr(delta, "type", None)
                if delta_type == "thinking_delta":
                    text = getattr(delta, "thinking", "") or ""
                    state.thinking += text
                    yield StreamChunk(reasoning_content=text)
                elif delta_type == "signature_delta":
                    state.signature = getattr(delta, "signature", None)
                elif delta_type == "text_delta":
                    yield StreamChunk(content=getattr(delta, "text", "") or "")
                elif delta_type == "input_json_delta":
                    part = getattr(delta, "partial_json", "") or ""
                    state.arguments += part
                    yield StreamChunk(
                        tool_call_deltas=[
                            {
                                "index": (
                                    state.output_index
                                    if state.output_index is not None
                                    else idx
                                ),
                                "arguments": part,
                            }
                        ]
                    )
                continue
            if event_type == "content_block_stop":
                idx = int(getattr(event, "index", 0))
                state = blocks.get(idx)
                if state is not None and state.type == "thinking":
                    payload = {"type": "thinking", "thinking": state.thinking}
                    if state.signature:
                        payload["signature"] = state.signature
                    thinking_payload.append(payload)
                elif (
                    state is not None
                    and state.type == "redacted_thinking"
                    and state.redacted_thinking is not None
                ):
                    thinking_payload.append(state.redacted_thinking)
                continue
            if event_type == "message_delta":
                finish_reason = _map_stop_reason(
                    getattr(getattr(event, "delta", None), "stop_reason", None)
                )
                usage = getattr(event, "usage", None)
                dumped_usage = dump_model_to_jsonable(usage)
                if isinstance(dumped_usage, dict):
                    usage_snapshot.update(dumped_usage)
                if finish_reason:
                    yield StreamChunk(finish_reason=finish_reason)

        if thinking_payload:
            from matmaster.types.messages import ProviderState

            yield StreamChunk(
                provider_state=ProviderState(
                    transport=self.transport_tag,
                    payload={"thinking": thinking_payload},
                )
            )
        if usage_snapshot:
            yield StreamChunk(
                usage=_anthropic_usage_to_scalar_dict(usage_snapshot),
                usage_vendor=usage_snapshot,
            )

    def classify_error(self, exc: Exception) -> LLMError | None:
        if isinstance(exc, LLMError):
            return None
        if isinstance(exc, anthropic.APITimeoutError):
            return LLMError(str(exc), retryable=True, error_category="timeout")
        if isinstance(exc, anthropic.APIConnectionError):
            return LLMError(str(exc), retryable=True, error_category="connection")
        if isinstance(exc, anthropic.RateLimitError):
            return LLMError(str(exc), retryable=True, error_category="rate_limit")
        overloaded_error = getattr(anthropic, "OverloadedError", None)
        server_errors: tuple[type[BaseException], ...]
        if isinstance(overloaded_error, type):
            server_errors = (anthropic.InternalServerError, overloaded_error)
        else:
            server_errors = (anthropic.InternalServerError,)
        if isinstance(exc, server_errors):
            return LLMError(str(exc), retryable=True, error_category="server")
        if isinstance(
            exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)
        ):
            return LLMError(str(exc), retryable=False, error_category="auth")
        if isinstance(exc, anthropic.BadRequestError):
            text = str(exc)
            if _is_context_overflow(text):
                return LLMError(
                    text, retryable=False, error_category="context_overflow"
                )
            if _is_non_retryable_anthropic_bad_request(text):
                return LLMError(text, retryable=False, error_category="bad_request")
            return LLMError(text, retryable=True, error_category="bad_request")
        return None

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools, tool_choice=tool_choice)
        async with client.messages.stream(**kwargs) as stream:
            final = await stream.get_final_message()
        return self.normalize_response(final)

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools)
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            async with client.messages.stream(**kwargs) as stream:
                async for chunk in self.normalize_stream(stream):
                    yield chunk
        except Exception as exc:  # noqa: BLE001
            err = self.classify_error(exc)
            if err is not None:
                raise err from exc
            raise


class BedrockAnthropicTransport(AnthropicMessagesTransport):
    """Bedrock 后端方言：不接受顶层 cache_control，automatic 全走块级断点。"""

    def _emit_top_level_auto_cache(self) -> bool:
        return False

    def _image_block(self, image: ImageContentPart) -> dict[str, Any]:
        url = image.url
        if url.startswith("data:") and ";base64," in url:
            return _image_block(image)
        try:
            media_type, data = inline_image_url_as_base64(url)
        except ImagePayloadError as exc:
            raise LLMError(
                "Anthropic Bedrock image input requires base64 data, but the "
                f"image URL could not be inlined: {exc}",
                retryable=False,
                error_category="bad_request",
            ) from exc
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
