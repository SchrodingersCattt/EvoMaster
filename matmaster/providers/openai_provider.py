"""Concrete LLMProvider implementation using the OpenAI Python SDK.

Provides OpenAIProvider that satisfies the LLMProvider Protocol, wrapping
the openai.AsyncOpenAI client for async chat and streaming calls.
Client lifecycle is managed via async context manager: __aenter__ creates
the AsyncOpenAI + httpx.AsyncClient, __aexit__ closes connections.
Retry strategy is handled by Kernel._call_llm_streaming(), not by the provider.
"""

from __future__ import annotations

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
    ) -> LLMResponse:
        """Non-streaming async chat completion."""
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if tools:
            kwargs["tools"] = tools
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
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            cache_read = _extract_cached_tokens(response.usage)
            if cache_read:
                usage["cache_read_tokens"] = cache_read

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
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
            "messages": messages,
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
                yield StreamChunk(usage=last_chunk_usage)

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
            err_str = str(exc).lower()
            if "context" in err_str and ("length" in err_str or "token" in err_str):
                raise LLMError(
                    str(exc), retryable=False, error_category="context_overflow"
                ) from exc
            raise LLMError(
                str(exc), retryable=True, error_category="bad_request"
            ) from exc
