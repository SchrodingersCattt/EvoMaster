"""LLMProvider Protocol for the agent kernel.

Defines the interface that any LLM backend must implement to be used
by AgentKernel. Uses @runtime_checkable to support isinstance() checks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from matmaster.types.messages import LLMResponse, StreamChunk


@runtime_checkable
class LLMProvider(Protocol):
    """LLM backend interface for the agent kernel.

    Implementations wrap a specific LLM API (OpenAI, Anthropic, etc.)
    and provide non-streaming (chat) and streaming (chat_stream) methods.
    Both are async. Provider lifecycle is managed via async context manager:
    __aenter__ initializes the client, __aexit__ closes connections.
    Retry logic lives in Kernel._call_llm_streaming(), not in the provider.
    """

    async def __aenter__(self) -> LLMProvider: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None: ...

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]: ...
