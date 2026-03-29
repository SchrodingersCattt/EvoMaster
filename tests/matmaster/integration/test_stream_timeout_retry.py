"""Integration test: full chain from build_provider through kernel retry."""

from __future__ import annotations

from typing import Any, AsyncIterator

from matmaster.core.agent import AgentKernel
from matmaster.types.errors import LLMError
from matmaster.types.messages import LLMResponse, StreamChunk, UserMessage
from matmaster.types.runtime import AgentRuntimeSpec


class RetryTestProvider:
    """Mock provider: first chat_stream raises LLMError, second succeeds."""

    def __init__(self) -> None:
        self._call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="unused", finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self._call_count += 1
        if self._call_count == 1:
            raise LLMError("timeout", retryable=True)
        yield StreamChunk(content="answer", finish_reason="stop")


class TestStreamTimeoutRetryIntegration:
    async def test_provider_retries_through_kernel(self) -> None:
        """Timeout in chat_stream -> LLMError -> kernel retries -> success."""
        provider = RetryTestProvider()

        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt="test",
            retry_delay=0.0,
        )
        kernel = AgentKernel()
        result = await kernel.run(spec, "hi")

        assert result.result.reason == "natural"
        assert provider._call_count == 2
