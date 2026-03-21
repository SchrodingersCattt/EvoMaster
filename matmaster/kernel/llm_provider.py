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
    and provide both blocking (chat) and streaming (chat_stream) methods.
    """

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]: ...
