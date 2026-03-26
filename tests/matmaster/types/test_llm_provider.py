"""Tests for matmaster.types.llm_provider -- LLMProvider Protocol (async)."""

from __future__ import annotations

from typing import Any, AsyncIterator

from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk, ToolCallData


# ── Mock implementations ──────────────────────────────


class CompleteLLMProvider:
    """Mock that satisfies the async LLMProvider Protocol."""

    async def __aenter__(self) -> CompleteLLMProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="response", finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")


class IncompleteLLMProvider:
    """Mock missing chat_stream -- should NOT satisfy Protocol."""

    async def chat(
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


# ── Functional tests (async) ─────────────────────────


class TestLLMProviderUsage:
    async def test_chat_returns_llm_response(self) -> None:
        provider = CompleteLLMProvider()
        result = await provider.chat([{"role": "user", "content": "hello"}])
        assert isinstance(result, LLMResponse)
        assert result.content == "response"
        assert result.finish_reason == "stop"

    async def test_chat_stream_returns_async_iterator(self) -> None:
        provider = CompleteLLMProvider()
        chunks = []
        async for chunk in provider.chat_stream(
            [{"role": "user", "content": "hello"}]
        ):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert isinstance(chunks[0], StreamChunk)
        assert chunks[0].content == "hello"
        assert chunks[0].finish_reason == "stop"

    async def test_chat_with_tools(self) -> None:
        provider = CompleteLLMProvider()
        tools = [
            {"type": "function", "function": {"name": "test", "parameters": {}}}
        ]
        result = await provider.chat(
            [{"role": "user", "content": "use tool"}],
            tools=tools,
        )
        assert isinstance(result, LLMResponse)


class TestMockProviderConforms:
    def test_mock_provider_from_conftest_satisfies_protocol(self) -> None:
        """MockAsyncLLMProvider from root conftest satisfies Protocol."""
        from tests.conftest import MockAsyncLLMProvider

        provider = MockAsyncLLMProvider()
        assert isinstance(provider, LLMProvider)


def test_chat_stream_accepts_timeout_kwarg() -> None:
    """Protocol allows optional timeout keyword argument."""
    import inspect
    from matmaster.types.llm_provider import LLMProvider

    sig = inspect.signature(LLMProvider.chat_stream)
    assert "timeout" in sig.parameters
    param = sig.parameters["timeout"]
    assert param.default is None
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
