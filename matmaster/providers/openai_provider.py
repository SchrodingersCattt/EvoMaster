"""Concrete LLMProvider implementation using the OpenAI Python SDK.

Provides OpenAIProvider that satisfies the LLMProvider Protocol, wrapping
the openai.OpenAI client for synchronous chat and
streaming calls. Retry strategy is handled by Kernel._call_llm()
with exponential backoff, not delegated to SDK.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

import openai

from matmaster.types.llm_provider import LLMProvider  # noqa: F401
from matmaster.types.messages import LLMResponse, StreamChunk, ToolCallData

logger = logging.getLogger(__name__)


class OpenAIProvider:
    """LLMProvider implementation backed by the OpenAI Python SDK.

    Satisfies the LLMProvider Protocol. Retry strategy is handled
    by Kernel._call_llm() with exponential backoff, not delegated to SDK.
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
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._stream_timeout = stream_timeout
        self._stream_idle_timeout = stream_idle_timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._extra_kwargs = extra_kwargs or {}

        import httpx

        _first_token_t = stream_timeout if stream_timeout is not None else timeout
        _idle_t = stream_idle_timeout if stream_idle_timeout is not None else timeout
        _read_t = float(max(_idle_t, _first_token_t) + 10)

        http_client = httpx.Client(
            timeout=httpx.Timeout(
                connect=15.0,
                read=_read_t,
                write=30.0,
                pool=15.0,
            )
        )

        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,  # retry handled by Kernel._call_llm, not SDK
            http_client=http_client,
        )

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

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Non-streaming chat completion."""
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

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message

        # Map tool_calls
        tool_calls: list[ToolCallData] | None = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                args = self._parse_arguments(tc.function.arguments)
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

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
        )

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> Iterator[StreamChunk]:
        """Streaming chat completion, yields StreamChunk per delta.

        Translates all SDK exceptions to LLMError so callers only need to
        catch one type. retryable=True for transient errors (timeout,
        connection, rate-limit, server); retryable=False for permanent errors
        (auth, context-length exceeded).
        """
        from matmaster.types.errors import LLMError
        import httpx as _httpx

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
            stream = self._client.chat.completions.create(**kwargs)
            last_chunk_usage: dict[str, int] | None = None

            for chunk in stream:
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
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason
                reasoning_content = getattr(delta, "reasoning_content", None)

                # Map tool_call deltas
                tool_call_deltas: list[dict[str, Any]] | None = None
                if delta.tool_calls:
                    tool_call_deltas = []
                    for tc_delta in delta.tool_calls:
                        d: dict[str, Any] = {"index": tc_delta.index}
                        if tc_delta.id:
                            d["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                d["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                d["arguments"] = tc_delta.function.arguments
                        tool_call_deltas.append(d)

                yield StreamChunk(
                    content=delta.content,
                    reasoning_content=reasoning_content,
                    tool_call_deltas=tool_call_deltas,
                    finish_reason=finish_reason,
                )

            if last_chunk_usage is not None:
                yield StreamChunk(usage=last_chunk_usage)

        except (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
            raise LLMError(str(exc), retryable=True) from exc
        except _httpx.ReadTimeout as exc:
            raise LLMError(str(exc), retryable=True) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LLMError(str(exc), retryable=False) from exc
        except openai.BadRequestError as exc:
            err_str = str(exc).lower()
            if "context" in err_str and ("length" in err_str or "token" in err_str):
                raise LLMError(str(exc), retryable=False) from exc
            raise LLMError(str(exc), retryable=True) from exc

    @staticmethod
    def _parse_arguments(raw: str | None) -> dict[str, Any]:
        """Parse JSON arguments from OpenAI tool call."""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse tool call arguments: %s", raw[:200])
            return {"_raw": raw}
