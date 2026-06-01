"""Concrete LLMProvider implementation using the OpenAI Python SDK.

Provides OpenAIProvider that satisfies the LLMProvider Protocol, wrapping
the openai.AsyncOpenAI client for async chat and streaming calls.
Client lifecycle is managed via async context manager: __aenter__ creates
the AsyncOpenAI + httpx.AsyncClient, __aexit__ closes connections.
Retry strategy is handled by Kernel._call_llm_streaming(), not by the provider.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import openai

from matmaster.types.llm_provider import LLMProvider  # noqa: F401
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
    ToolCallData,
    parse_tool_arguments,
)

logger = logging.getLogger(__name__)


@dataclass
class _StreamToolCallState:
    """Provider-local state for one logical streaming tool call."""

    output_index: int
    id: str = ""
    name: str = ""
    arguments: str = ""

    def has_payload(self) -> bool:
        return bool(self.arguments)


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
    message_index: int
    placement: str  # "text_content" | "tool_message"
    priority: int


def _add_text_content_cache_control(
    message: dict[str, Any],
    cache_control: dict[str, str],
) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        if not content.strip():
            return False
        message["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": dict(cache_control),
            }
        ]
        return True
    if not isinstance(content, list):
        return False
    for part in reversed(content):
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and part.get("text", "").strip()
        ):
            if "cache_control" not in part:
                part["cache_control"] = dict(cache_control)
            return True
    return False


def _add_tool_message_cache_control(
    message: dict[str, Any],
    cache_control: dict[str, str],
) -> bool:
    if message.get("role") != "tool":
        return False
    content = message.get("content")
    if isinstance(content, str) and not content.strip():
        return False
    if isinstance(content, list) and not content:
        return False
    if content is None:
        return False
    if "cache_control" not in message:
        message["cache_control"] = dict(cache_control)
    return True


def _tool_call_ids(message: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict) and call.get("id"):
            ids.append(str(call["id"]))
    return ids


def _latest_completed_tool_group_tail(messages: list[dict[str, Any]]) -> int | None:
    for assistant_idx in range(len(messages) - 1, -1, -1):
        assistant = messages[assistant_idx]
        if assistant.get("role") != "assistant":
            continue
        expected = set(_tool_call_ids(assistant))
        if not expected:
            continue

        seen: set[str] = set()
        tail_idx: int | None = None
        for idx in range(assistant_idx + 1, len(messages)):
            message = messages[idx]
            if message.get("role") == "assistant" and _tool_call_ids(message):
                break
            if message.get("role") != "tool":
                continue
            tool_call_id = message.get("tool_call_id")
            if tool_call_id in expected:
                seen.add(str(tool_call_id))
                tail_idx = idx
                if seen == expected:
                    return tail_idx
        return None
    return None


def _message_text_size(message: dict[str, Any]) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return len(content.strip())
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                total += len(part["text"].strip())
        return total
    return 0


def _select_flexible_cache_target(
    messages: list[dict[str, Any]],
    used: set[int],
    options: AnthropicPromptCacheOptions,
) -> _CacheTarget | None:
    candidates: list[tuple[int, int, str]] = []
    for idx, message in enumerate(messages):
        if idx in used:
            continue
        role = message.get("role")
        if role not in {"user", "tool"}:
            continue
        size = _message_text_size(message)
        if size < options.min_flexible_chars:
            continue
        placement = "tool_message" if role == "tool" else "text_content"
        candidates.append((size, idx, placement))
    if not candidates:
        return None
    _, idx, placement = max(candidates)
    return _CacheTarget(idx, placement, 3)


def _select_anthropic_cache_targets(
    messages: list[dict[str, Any]],
    options: AnthropicPromptCacheOptions,
) -> list[_CacheTarget]:
    targets: list[_CacheTarget] = []
    used: set[int] = set()

    def append(target: _CacheTarget) -> None:
        if len(targets) >= options.max_breakpoints:
            return
        if target.message_index in used:
            return
        targets.append(target)
        used.add(target.message_index)

    if options.system_prompt_breakpoint:
        system_idx = next(
            (idx for idx, m in enumerate(messages) if m.get("role") == "system"),
            None,
        )
        if system_idx is not None:
            append(_CacheTarget(system_idx, "text_content", 0))

    if options.automatic and options.latest_user_breakpoint:
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "user":
                append(_CacheTarget(idx, "text_content", 1))
                break

    if options.automatic and options.tool_result_breakpoint:
        tool_tail_idx = _latest_completed_tool_group_tail(messages)
        if tool_tail_idx is not None:
            append(_CacheTarget(tool_tail_idx, "tool_message", 2))

    if options.automatic and options.flexible_breakpoint:
        flexible = _select_flexible_cache_target(messages, used, options)
        if flexible is not None:
            append(flexible)

    return targets


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
    """Best-effort extraction of prompt cache-read tokens from an API usage object.

    Supports two conventions:
    - OpenAI: ``usage.prompt_tokens_details.cached_tokens``
    - Anthropic-compatible: ``usage.cache_read_input_tokens``
    Returns 0 when neither field is present.
    """
    # OpenAI SDK: prompt_tokens_details.cached_tokens
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        val = getattr(details, "cached_tokens", None)
        if isinstance(val, int) and val > 0:
            return val
    # Anthropic-compatible proxy
    val = getattr(usage, "cache_read_input_tokens", None)
    if isinstance(val, int) and val > 0:
        return val
    return 0


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


class OpenAIProvider:
    """LLMProvider implementation backed by the OpenAI Python SDK.

    Satisfies the LLMProvider Protocol. Uses async context manager for
    client lifecycle: __init__ stores parameters only, __aenter__ creates
    AsyncOpenAI client, __aexit__ closes connections.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 300.0,
        stream_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        prompt_cache_options: AnthropicPromptCacheOptions | None = None,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._stream_timeout = stream_timeout
        self._stream_idle_timeout = stream_idle_timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._prompt_cache_options = prompt_cache_options
        self._extra_kwargs = extra_kwargs or {}
        self._client: openai.AsyncOpenAI | None = None
        self._enter_count: int = 0

    async def __aenter__(self) -> OpenAIProvider:
        self._enter_count += 1
        if self._client is not None:
            return self

        import httpx

        _first_token_t = (
            self._stream_timeout if self._stream_timeout is not None else self._timeout
        )
        _idle_t = (
            self._stream_idle_timeout
            if self._stream_idle_timeout is not None
            else self._timeout
        )
        _read_t = float(max(_idle_t, _first_token_t) + 10)

        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=15.0,
                read=_read_t,
                write=30.0,
                pool=15.0,
            )
        )
        self._client = openai.AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            http_client=http_client,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[type-arg]
        self._enter_count -= 1
        if self._enter_count > 0:
            return
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _ensure_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            raise RuntimeError(
                "OpenAIProvider must be used as async context manager: "
                "'async with provider:'"
            )
        return self._client

    def _prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return provider request messages with prompt-cache markers applied."""
        options = self._prompt_cache_options
        if options is None or (
            not options.system_prompt_breakpoint and not options.automatic
        ):
            return messages

        from matmaster.types.errors import LLMError

        prepared = copy.deepcopy(messages)

        if options.system_prompt_breakpoint:
            if not any(message.get("role") == "system" for message in prepared):
                raise LLMError(
                    "anthropic prompt cache enabled but no system message was found",
                    retryable=False,
                    error_category="payload_validation",
                )

        targets = _select_anthropic_cache_targets(prepared, options)
        for target in targets:
            message = prepared[target.message_index]
            if target.placement == "tool_message":
                ok = _add_tool_message_cache_control(message, options.cache_control)
            else:
                ok = _add_text_content_cache_control(message, options.cache_control)
            if (
                not ok
                and target.placement == "text_content"
                and message.get("role") == "system"
            ):
                raise LLMError(
                    "anthropic prompt cache requires a non-empty string system prompt",
                    retryable=False,
                    error_category="payload_validation",
                )

        return prepared

    @property
    def stream_timeout(self) -> float | None:
        return self._stream_timeout

    @property
    def stream_idle_timeout(self) -> float | None:
        return self._stream_idle_timeout

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def retry_delay(self) -> float:
        return self._retry_delay

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
                    from matmaster.types.errors import LLMError

                    raise LLMError(
                        f"duplicate tool_call id detected in stream: {delta.get('id')}: {exc}",
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

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        """Non-streaming async chat completion."""
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._prepare_messages(messages),
            "temperature": self._temperature,
        }
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if self._extra_kwargs:
            kwargs.update(self._extra_kwargs)

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message

        # Map tool_calls
        tool_calls: list[ToolCallData] | None = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                args = parse_tool_arguments(tc.function.arguments or "")
                tool_calls.append(
                    ToolCallData(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        # Map usage
        usage: dict[str, int] = {}
        usage_vendor: dict[str, Any] | None = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            cache_read = _extract_cached_tokens(response.usage)
            if cache_read:
                usage["cache_read_tokens"] = cache_read
            usage_vendor = _openai_usage_to_vendor_dict(response.usage)

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            usage_vendor=usage_vendor,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Streaming async chat completion, yields StreamChunk per delta.

        Translates all SDK exceptions to LLMError so callers only need to
        catch one type. retryable=True for transient errors (timeout,
        connection, rate-limit, server); retryable=False for permanent errors
        (auth, context-length exceeded).
        """
        import httpx as _httpx

        from matmaster.types.errors import LLMError

        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._prepare_messages(messages),
            "temperature": self._temperature,
            "stream": True,
        }
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if tools:
            kwargs["tools"] = tools
        if self._extra_kwargs:
            kwargs.update(self._extra_kwargs)
        stream_options = kwargs.get("stream_options", {})
        if not isinstance(stream_options, dict):
            stream_options = {}
        kwargs["stream_options"] = {**stream_options, "include_usage": True}
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            stream = await client.chat.completions.create(**kwargs)
            last_chunk_usage: dict[str, int] | None = None
            last_chunk_usage_vendor: dict[str, Any] | None = None
            active_tool_calls: dict[int, _StreamToolCallState] = {}
            next_tool_call_index = 0

            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if (
                    isinstance(getattr(usage, "prompt_tokens", None), int)
                    and isinstance(getattr(usage, "completion_tokens", None), int)
                    and isinstance(getattr(usage, "total_tokens", None), int)
                ):
                    last_chunk_usage = {
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                        "total_tokens": usage.total_tokens,
                    }
                    cache_read = _extract_cached_tokens(usage)
                    if cache_read:
                        last_chunk_usage["cache_read_tokens"] = cache_read
                    last_chunk_usage_vendor = _openai_usage_to_vendor_dict(usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason
                reasoning_content = getattr(delta, "reasoning_content", None)

                # Map tool_call deltas
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

        except openai.APITimeoutError as exc:
            raise LLMError(str(exc), retryable=True, error_category="timeout") from exc
        except openai.APIConnectionError as exc:
            raise LLMError(
                str(exc), retryable=True, error_category="connection"
            ) from exc
        except openai.RateLimitError as exc:
            raise LLMError(
                str(exc), retryable=True, error_category="rate_limit"
            ) from exc
        except openai.InternalServerError as exc:
            raise LLMError(str(exc), retryable=True, error_category="server") from exc
        except _httpx.ReadTimeout as exc:
            raise LLMError(str(exc), retryable=True, error_category="timeout") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LLMError(str(exc), retryable=False, error_category="auth") from exc
        except openai.BadRequestError as exc:
            err_str = str(exc)
            err_text = err_str.lower()
            if "context" in err_text and ("length" in err_text or "token" in err_text):
                raise LLMError(
                    str(exc), retryable=False, error_category="context_overflow"
                ) from exc
            if _is_non_retryable_tool_protocol_bad_request(
                err_str
            ) or _is_non_retryable_content_shape_bad_request(err_str):
                raise LLMError(
                    str(exc), retryable=False, error_category="bad_request"
                ) from exc
            raise LLMError(
                str(exc), retryable=True, error_category="bad_request"
            ) from exc
