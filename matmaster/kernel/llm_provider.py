"""LLMProvider Protocol for the agent kernel.

Defines the interface that any LLM backend must implement to be used
by AgentKernel. Uses @runtime_checkable to support isinstance() checks.
"""

from __future__ import annotations

from typing import Any, Iterator, Protocol, runtime_checkable

from matmaster.kernel.types import LLMResponse, StreamChunk


@runtime_checkable
class LLMProvider(Protocol):
    """LLM backend interface for the agent kernel.

    Implementations wrap a specific LLM API (OpenAI, Anthropic, etc.)
    and provide blocking (chat), retry-aware (chat_with_retry), and
    streaming (chat_stream) methods.

    Implementations of chat_with_retry must handle retry with exponential
    backoff. Non-retryable errors (context length exceeded, malformed input)
    must be raised immediately without retry.
    """

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse: ...

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]: ...
