"""Concrete LLMProvider implementation using the OpenAI Python SDK.

Provides OpenAIProvider that satisfies the LLMProvider Protocol, wrapping
the openai.OpenAI client for both synchronous chat and streaming calls.
Retry logic is delegated to the OpenAI SDK's built-in max_retries parameter.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

import openai

from matmaster.kernel.llm_provider import LLMProvider  # noqa: F401
from matmaster.kernel.types import LLMResponse, StreamChunk, ToolCallData

logger = logging.getLogger(__name__)


class OpenAIProvider:
    """LLMProvider implementation backed by the OpenAI Python SDK.

    Satisfies the LLMProvider Protocol. Retry strategy is handled by the
    SDK's built-in max_retries (per user decision: retry internal to provider,
    kernel does not retry).
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
        max_retries: int = 3,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

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
    ) -> Iterator[StreamChunk]:
        """Streaming chat completion, yields StreamChunk per delta."""
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

        stream = self._client.chat.completions.create(**kwargs)

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

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
                tool_call_deltas=tool_call_deltas,
                finish_reason=finish_reason,
            )

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
