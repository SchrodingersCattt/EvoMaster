"""ChatCompletionsTransport：纯 openai 风格 chat completions wire 协议。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import openai

from matmaster.providers.transport import Transport
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    Message,
    StreamChunk,
    ToolCallData,
    ToolMessage,
    UserMessage,
    parse_tool_arguments,
)

logger = logging.getLogger(__name__)


def _user_message_to_dict(message: UserMessage) -> dict[str, Any]:
    if not message.images:
        return {"role": message.role.value, "content": message.content}
    parts: list[dict[str, Any]] = []
    if message.content:
        parts.append({"type": "text", "text": message.content})
    for image in message.images:
        image_url: dict[str, Any] = {"url": image.url}
        if image.detail is not None:
            image_url["detail"] = image.detail
        parts.append({"type": "image_url", "image_url": image_url})
    return {"role": message.role.value, "content": parts}


def _assistant_message_to_dict(message: AssistantMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls is not None:
        payload["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments_json},
            }
            for tc in message.tool_calls
        ]
    return payload


def _message_to_openai_dict(message: Message) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        payload = _user_message_to_dict(message)
    elif isinstance(message, AssistantMessage):
        payload = _assistant_message_to_dict(message)
    elif isinstance(message, ToolMessage):
        payload = {
            "role": message.role.value,
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
    else:
        payload = {"role": message.role.value, "content": message.content}
    if payload.get("content") is None:
        payload["content"] = ""
    return payload


@dataclass
class _StreamToolCallState:
    """Provider-local state for one logical streaming tool call."""

    output_index: int
    id: str = ""
    name: str = ""
    arguments: str = ""

    def has_payload(self) -> bool:
        return bool(self.arguments)


def _is_complete_json_document(raw: str) -> bool:
    """Return True when *raw* is exactly one complete JSON document."""
    text = raw.strip()
    if not text:
        return False
    try:
        _, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return False
    return text[end:].strip() == ""


def _find_existing_tool_call_by_id(
    active_calls: dict[int, _StreamToolCallState],
    tool_call_id: str,
) -> _StreamToolCallState | None:
    for state in active_calls.values():
        if tool_call_id and state.id == tool_call_id:
            return state
    return None


def _reconcile_duplicate_tool_call_arguments(
    existing: str, incoming: str
) -> tuple[str, str | None]:
    if not incoming:
        return existing, None
    if not existing:
        return incoming, incoming
    if incoming == existing:
        return existing, None
    if incoming.startswith(existing):
        suffix = incoming[len(existing) :]
        return incoming, suffix or None
    if (
        existing
        and not _is_complete_json_document(existing)
        and not incoming.lstrip().startswith(("{", "["))
    ):
        return existing + incoming, incoming
    raise ValueError("conflicting duplicate tool_call arguments")


def _normalize_duplicate_id_delta(
    current: _StreamToolCallState,
    delta: dict[str, Any],
) -> dict[str, Any] | None:
    new_name = delta.get("name")
    if new_name and current.name and new_name != current.name:
        raise ValueError("duplicate tool_call id reused with different name")

    merged_args, emitted_suffix = _reconcile_duplicate_tool_call_arguments(
        current.arguments,
        str(delta.get("arguments") or ""),
    )
    current.arguments = merged_args

    if new_name and not current.name:
        current.name = new_name

    out = {"index": current.output_index}
    if not current.id and delta.get("id"):
        current.id = str(delta["id"])
        out["id"] = current.id
    if new_name and not delta.get("arguments"):
        out["name"] = new_name
    if emitted_suffix:
        out["arguments"] = emitted_suffix
    return out if len(out) > 1 else None


def _should_split_stream_tool_call(
    current: _StreamToolCallState, delta: dict[str, Any]
) -> bool:
    """Detect proxy/provider index collisions without breaking valid chunking."""
    if not current.has_payload():
        return False

    new_id = delta.get("id")
    if new_id and current.id and new_id != current.id:
        return True

    new_name = delta.get("name")
    if new_name and current.name and new_name != current.name:
        return True

    new_arguments = delta.get("arguments")
    if isinstance(new_arguments, str) and new_arguments.lstrip().startswith(("{", "[")):
        return _is_complete_json_document(current.arguments)

    return False


def _dump_usage_detail(value: Any) -> Any:
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json", exclude_none=True)
        except TypeError:
            return model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    cached = getattr(value, "cached_tokens", None)
    if cached is not None:
        return {"cached_tokens": cached}
    return value


def _openai_usage_to_vendor_dict(usage: Any) -> dict[str, Any]:
    """Serialize provider-native usage while preserving nested detail fields."""
    if usage is None:
        return {}
    model_dump = getattr(usage, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json", exclude_none=True)
        except TypeError:
            dumped = model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped

    out: dict[str, Any] = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = getattr(usage, key, None)
        if value is not None:
            out[key] = value

    for detail_key in (
        "prompt_tokens_details",
        "completion_tokens_details",
        "cache_creation",
    ):
        detail = _dump_usage_detail(getattr(usage, detail_key, None))
        if detail is None:
            continue
        if detail_key in ("prompt_tokens_details", "completion_tokens_details"):
            out[detail_key] = detail
            continue
        out[detail_key] = detail

    return out


def _extract_cached_tokens(usage: Any) -> int:
    """Best-effort extraction of prompt cache-read tokens from an API usage object."""
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        val = getattr(details, "cached_tokens", None)
        if isinstance(val, int) and val > 0:
            return val
    val = getattr(usage, "cache_read_input_tokens", None)
    if isinstance(val, int) and val > 0:
        return val
    return 0


def _extract_cache_write_tokens(usage: Any) -> int:
    """Best-effort extraction of prompt cache-write tokens."""
    val = getattr(usage, "cache_creation_input_tokens", None)
    if isinstance(val, int) and val > 0:
        return val
    cache_creation = _dump_usage_detail(getattr(usage, "cache_creation", None))
    if isinstance(cache_creation, dict):
        total = 0
        for value in cache_creation.values():
            if isinstance(value, int) and value > 0:
                total += value
        return total
    return 0


def _extract_reasoning_tokens(usage: Any) -> int:
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        val = getattr(details, "reasoning_tokens", None)
        if isinstance(val, int) and val > 0:
            return val
    val = getattr(usage, "reasoning_tokens", None)
    if isinstance(val, int) and val > 0:
        return val
    return 0


def _openai_usage_to_scalar_dict(usage: Any) -> dict[str, int]:
    out = {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }
    cache_read = _extract_cached_tokens(usage)
    if cache_read:
        out["cache_read_tokens"] = cache_read
    cache_write = _extract_cache_write_tokens(usage)
    if cache_write:
        out["cache_write_tokens"] = cache_write
    reasoning = _extract_reasoning_tokens(usage)
    if reasoning:
        out["reasoning_tokens"] = reasoning
    return out


def _is_non_retryable_tool_protocol_bad_request(err_str: str) -> bool:
    text = err_str.lower()
    patterns = (
        "duplicate ids",
        "unexpected `tool_use_id`",
        "must have a corresponding `tool_use` block",
        "expected toolresult blocks",
        "exceeds the number of tooluse blocks",
        "wrong number of tooluse blocks",
    )
    return any(pattern in text for pattern in patterns)


def _is_non_retryable_content_shape_bad_request(err_str: str) -> bool:
    text = err_str.lower()
    return (
        "invalid value for 'content'" in text
        and "expected a string" in text
        and "got null" in text
    ) or (
        "messages." in text
        and ".content" in text
        and "expected a string" in text
        and "got null" in text
    )


class ChatCompletionsTransport(Transport):
    """Pure OpenAI-compatible chat completions transport."""

    transport_tag = "chat_completions"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        reasoning_summary: str | None = None,
        extra_body: dict[str, Any] | None = None,
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
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._reasoning_summary = reasoning_summary
        self._extra_body = extra_body

    async def _open_client(self) -> openai.AsyncOpenAI:
        import httpx

        read_t = float(max(self.stream_idle_timeout, self.stream_timeout) + 10)
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=read_t, write=30.0, pool=15.0)
        )
        return openai.AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            http_client=http_client,
        )

    async def _close_client(self, client: openai.AsyncOpenAI) -> None:
        await client.close()

    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """canonical list[Message] -> OpenAI-compatible wire dicts."""
        validate_tool_turn_sequence(messages)
        return [_message_to_openai_dict(message) for message in messages]

    def build_kwargs(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self.convert_messages(messages),
            "temperature": self._temperature,
        }
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if tools:
            kwargs["tools"] = tools

        effort = (self._reasoning_effort or "").strip().lower()
        extra_body: dict[str, Any] = {}
        if effort:
            kwargs["reasoning_effort"] = effort
        if self._reasoning_summary:
            reasoning: dict[str, str] = {"summary": self._reasoning_summary}
            if effort:
                reasoning["effort"] = effort
            extra_body["reasoning"] = reasoning
        if self._extra_body:
            extra_body.update(self._extra_body)
        if extra_body:
            kwargs["extra_body"] = extra_body

        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
        elif tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return kwargs

    def classify_error(self, exc: Exception) -> LLMError | None:
        import httpx as _httpx

        if isinstance(exc, LLMError):
            return None
        if isinstance(exc, openai.APITimeoutError):
            return LLMError(str(exc), retryable=True, error_category="timeout")
        if isinstance(exc, openai.APIConnectionError):
            return LLMError(str(exc), retryable=True, error_category="connection")
        if isinstance(exc, openai.RateLimitError):
            return LLMError(str(exc), retryable=True, error_category="rate_limit")
        if isinstance(exc, openai.InternalServerError):
            return LLMError(str(exc), retryable=True, error_category="server")
        if isinstance(exc, _httpx.ReadTimeout):
            return LLMError(str(exc), retryable=True, error_category="timeout")
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return LLMError(str(exc), retryable=False, error_category="auth")
        if isinstance(exc, openai.BadRequestError):
            err_str = str(exc)
            err_text = err_str.lower()
            if "context" in err_text and ("length" in err_text or "token" in err_text):
                return LLMError(
                    str(exc), retryable=False, error_category="context_overflow"
                )
            if _is_non_retryable_tool_protocol_bad_request(
                err_str
            ) or _is_non_retryable_content_shape_bad_request(err_str):
                return LLMError(str(exc), retryable=False, error_category="bad_request")
            return LLMError(str(exc), retryable=True, error_category="bad_request")
        return None

    @staticmethod
    def _normalize_stream_tool_call_deltas(
        raw_deltas: list[dict[str, Any]],
        active_calls: dict[int, _StreamToolCallState],
        next_output_index: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Rewrite colliding provider indices into stable logical indices."""
        normalized: list[dict[str, Any]] = []

        for delta in raw_deltas:
            source_index = delta.get("index", 0)
            current = active_calls.get(source_index)

            duplicate_state = None
            if current is None and delta.get("id"):
                duplicate_state = _find_existing_tool_call_by_id(
                    active_calls,
                    str(delta["id"]),
                )
            if duplicate_state is not None:
                try:
                    item = _normalize_duplicate_id_delta(duplicate_state, delta)
                except ValueError as exc:
                    raise LLMError(
                        "duplicate tool_call id detected in stream: "
                        f"{delta.get('id')}: {exc}",
                        retryable=False,
                        error_category="bad_request",
                    ) from exc
                if item is not None:
                    normalized.append(item)
                continue

            if current is None:
                current = _StreamToolCallState(output_index=next_output_index)
                active_calls[source_index] = current
                next_output_index += 1
            elif _should_split_stream_tool_call(current, delta):
                logger.warning(
                    "Detected OpenAI-compatible tool_call index collision; "
                    "rewriting index (source_index=%s, prev_id=%s, new_id=%s, "
                    "prev_name=%s, new_name=%s)",
                    source_index,
                    current.id or "-",
                    delta.get("id") or "-",
                    current.name or "-",
                    delta.get("name") or "-",
                )
                current = _StreamToolCallState(output_index=next_output_index)
                active_calls[source_index] = current
                next_output_index += 1

            if delta.get("id"):
                current.id = delta["id"]
            if delta.get("name"):
                current.name = delta["name"]
            if delta.get("arguments"):
                current.arguments += delta["arguments"]

            item = {"index": current.output_index}
            if delta.get("id"):
                item["id"] = delta["id"]
            if delta.get("name"):
                item["name"] = delta["name"]
            if delta.get("arguments"):
                item["arguments"] = delta["arguments"]
            normalized.append(item)

        return normalized, next_output_index

    def normalize_response(self, raw: Any) -> LLMResponse:
        choice = raw.choices[0]
        message = choice.message
        tool_calls: list[ToolCallData] | None = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                args = parse_tool_arguments(tc.function.arguments or "")
                tool_calls.append(
                    ToolCallData(id=tc.id, name=tc.function.name, arguments=args)
                )
        usage: dict[str, int] = {}
        usage_vendor: dict[str, Any] | None = None
        if raw.usage:
            usage = _openai_usage_to_scalar_dict(raw.usage)
            usage_vendor = _openai_usage_to_vendor_dict(raw.usage)
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            usage_vendor=usage_vendor,
        )

    async def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        last_chunk_usage: dict[str, int] | None = None
        last_chunk_usage_vendor: dict[str, Any] | None = None
        active_tool_calls: dict[int, _StreamToolCallState] = {}
        next_tool_call_index = 0

        async for chunk in raw_iter:
            usage = getattr(chunk, "usage", None)
            if (
                isinstance(getattr(usage, "prompt_tokens", None), int)
                and isinstance(getattr(usage, "completion_tokens", None), int)
                and isinstance(getattr(usage, "total_tokens", None), int)
            ):
                last_chunk_usage = _openai_usage_to_scalar_dict(usage)
                last_chunk_usage_vendor = _openai_usage_to_vendor_dict(usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason
            reasoning_content = getattr(delta, "reasoning_content", None)

            tool_call_deltas: list[dict[str, Any]] | None = None
            if delta.tool_calls:
                raw_tool_call_deltas: list[dict[str, Any]] = []
                for tc_delta in delta.tool_calls:
                    d: dict[str, Any] = {"index": tc_delta.index}
                    if tc_delta.id:
                        d["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            d["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            d["arguments"] = tc_delta.function.arguments
                    raw_tool_call_deltas.append(d)
                tool_call_deltas, next_tool_call_index = (
                    self._normalize_stream_tool_call_deltas(
                        raw_tool_call_deltas,
                        active_tool_calls,
                        next_tool_call_index,
                    )
                )

            yield StreamChunk(
                content=delta.content,
                reasoning_content=reasoning_content,
                tool_call_deltas=tool_call_deltas,
                finish_reason=finish_reason,
            )

        if last_chunk_usage is not None:
            yield StreamChunk(
                usage=last_chunk_usage,
                usage_vendor=last_chunk_usage_vendor,
            )

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools, tool_choice=tool_choice)
        response = await client.chat.completions.create(**kwargs)
        return self.normalize_response(response)

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools, stream=True)
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            raw = await client.chat.completions.create(**kwargs)
            async for sc in self.normalize_stream(raw):
                yield sc
        except Exception as exc:  # noqa: BLE001
            err = self.classify_error(exc)
            if err is not None:
                raise err from exc
            raise
