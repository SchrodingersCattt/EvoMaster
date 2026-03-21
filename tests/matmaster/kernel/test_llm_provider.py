"""Tests for matmaster.kernel.llm_provider -- LLMProvider Protocol."""

from __future__ import annotations

from typing import Any, Iterator

from matmaster.kernel.llm_provider import LLMProvider
from matmaster.kernel.types import LLMResponse, StreamChunk, ToolCallData


# ── Mock implementations ──────────────────────────────


class CompleteLLMProvider:
    """Mock that satisfies the LLMProvider Protocol."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="response", finish_reason="stop")

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")


class IncompleteLLMProvider:
    """Mock missing chat_stream -- should NOT satisfy Protocol."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="response")


# ── Protocol conformance tests ────────────────────────


class TestLLMProviderProtocol:
    def test_protocol_check_complete(self) -> None:
        provider = CompleteLLMProvider()
        assert isinstance(provider, LLMProvider)

    def test_protocol_check_incomplete(self) -> None:
        provider = IncompleteLLMProvider()
        assert not isinstance(provider, LLMProvider)


# ── Functional tests ─────────────────────────────────


class TestLLMProviderUsage:
    def test_chat_returns_llm_response(self) -> None:
        provider = CompleteLLMProvider()
        result = provider.chat([{"role": "user", "content": "hello"}])
        assert isinstance(result, LLMResponse)
        assert result.content == "response"
        assert result.finish_reason == "stop"

    def test_chat_stream_returns_iterator(self) -> None:
        provider = CompleteLLMProvider()
        chunks = list(provider.chat_stream([{"role": "user", "content": "hello"}]))
        assert len(chunks) == 1
        assert isinstance(chunks[0], StreamChunk)
        assert chunks[0].content == "hello"
        assert chunks[0].finish_reason == "stop"

    def test_chat_with_tools(self) -> None:
        provider = CompleteLLMProvider()
        tools = [{"type": "function", "function": {"name": "test", "parameters": {}}}]
        result = provider.chat(
            [{"role": "user", "content": "use tool"}],
            tools=tools,
        )
        assert isinstance(result, LLMResponse)
