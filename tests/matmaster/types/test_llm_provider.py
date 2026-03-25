"""Tests for matmaster.types.llm_provider -- LLMProvider Protocol."""

from __future__ import annotations

from typing import Any, Iterator

from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk, ToolCallData


# ── Mock implementations ──────────────────────────────


class CompleteLLMProvider:
    """Mock that satisfies the LLMProvider Protocol."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="response", finish_reason="stop")

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse:
        return self.chat(messages, tools)

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


# ── chat_with_retry Protocol tests ──────────────────


class MissingRetryProvider:
    """Mock with chat() + chat_stream() but NO chat_with_retry -- should fail Protocol."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="response")

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")


class TestChatWithRetryProtocol:
    def test_protocol_requires_chat_with_retry(self) -> None:
        """A class with chat + chat_stream but no chat_with_retry must fail isinstance."""
        provider = MissingRetryProvider()
        assert not isinstance(provider, LLMProvider)

    def test_chat_with_retry_returns_llm_response(self) -> None:
        """CompleteLLMProvider.chat_with_retry returns LLMResponse."""
        provider = CompleteLLMProvider()
        result = provider.chat_with_retry([{"role": "user", "content": "hello"}])
        assert isinstance(result, LLMResponse)
        assert result.content == "response"

    def test_chat_with_retry_with_tools(self) -> None:
        """chat_with_retry accepts optional tools parameter."""
        provider = CompleteLLMProvider()
        tools = [{"type": "function", "function": {"name": "test", "parameters": {}}}]
        result = provider.chat_with_retry(
            [{"role": "user", "content": "use tool"}],
            tools=tools,
        )
        assert isinstance(result, LLMResponse)

    def test_incomplete_still_fails(self) -> None:
        """IncompleteLLMProvider (missing chat_stream + chat_with_retry) still fails."""
        provider = IncompleteLLMProvider()
        assert not isinstance(provider, LLMProvider)

    def test_mock_provider_conforms(self) -> None:
        """MockLLMProvider from conftest satisfies Protocol with chat_with_retry."""
        from tests.matmaster.core.conftest import MockLLMProvider

        provider = MockLLMProvider()
        assert isinstance(provider, LLMProvider)
