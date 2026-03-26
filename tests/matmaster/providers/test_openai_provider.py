"""Tests for OpenAIProvider -- concrete LLMProvider implementation.

All tests use unittest.mock to mock the OpenAI client -- no real API
calls are made. Tests verify Protocol conformance, construction, async
chat() response mapping, async chat_stream() response mapping,
chat_with_retry() retry logic, error handling, and async context manager
lifecycle.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from matmaster.types.llm_provider import LLMProvider
from matmaster.providers.openai_provider import OpenAIProvider
from matmaster.types.errors import LLMError
from matmaster.types.messages import LLMResponse, StreamChunk, ToolCallData


async def _async_iter(items):
    """Convert a list into an async iterator for mock streaming."""
    for item in items:
        yield item


# -- Protocol conformance ------------------------------------------------


class TestProtocolConformance:
    def test_protocol_conformance(self) -> None:
        """OpenAIProvider satisfies LLMProvider Protocol."""
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert isinstance(provider, LLMProvider)

    def test_has_chat_method(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert hasattr(provider, "chat")
        assert callable(provider.chat)

    def test_has_chat_stream_method(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert hasattr(provider, "chat_stream")
        assert callable(provider.chat_stream)

    def test_has_chat_with_retry_method(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert hasattr(provider, "chat_with_retry")
        assert callable(provider.chat_with_retry)

    def test_validate_async_protocol(self) -> None:
        from matmaster.validation import validate_async_protocol

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        errors = validate_async_protocol(provider, LLMProvider)
        assert errors == [], f"Protocol validation errors: {errors}"


# -- Construction --------------------------------------------------------


class TestConstruction:
    def test_construction(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert provider._client is None
        assert provider._api_key == "sk-test"
        assert provider._base_url is None
        assert provider._timeout == 300.0

    def test_custom_base_url(self) -> None:
        provider = OpenAIProvider(
            model="gpt-4o-mini",
            api_key="sk-test",
            base_url="https://custom.api",
        )
        assert provider._base_url == "https://custom.api"

    def test_custom_config(self) -> None:
        provider = OpenAIProvider(
            model="gpt-4o-mini",
            api_key="sk-test",
            temperature=0.5,
            max_tokens=100,
        )
        assert provider._temperature == 0.5
        assert provider._max_tokens == 100

    def test_max_retries_stored(self) -> None:
        """max_retries stored as _max_retries."""
        provider = OpenAIProvider(
            model="gpt-4o-mini", api_key="sk-test", max_retries=5
        )
        assert provider._max_retries == 5

    def test_retry_delay_stored(self) -> None:
        """Custom retry_delay stored as _retry_delay."""
        provider = OpenAIProvider(
            model="gpt-4o-mini",
            api_key="sk-test",
            retry_delay=2.0,
        )
        assert provider._retry_delay == 2.0


# -- Stream timeout construction -----------------------------------------


class TestStreamTimeoutConstruction:
    def test_stream_timeout_stored(self) -> None:
        provider = OpenAIProvider(
            model="gpt-4o-mini",
            api_key="sk-test",
            stream_timeout=120.0,
            stream_idle_timeout=60.0,
        )
        assert provider.stream_timeout == 120.0
        assert provider.stream_idle_timeout == 60.0

    def test_stream_timeout_defaults_none(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert provider.stream_timeout is None
        assert provider.stream_idle_timeout is None

    def test_max_retries_property(self) -> None:
        provider = OpenAIProvider(
            model="gpt-4o-mini", api_key="sk-test", max_retries=5
        )
        assert provider.max_retries == 5

    def test_retry_delay_property(self) -> None:
        provider = OpenAIProvider(
            model="gpt-4o-mini", api_key="sk-test", retry_delay=2.0
        )
        assert provider.retry_delay == 2.0

    async def test_custom_httpx_client_created(self) -> None:
        """When stream timeouts provided, custom httpx.AsyncClient is passed."""
        provider = OpenAIProvider(
            model="gpt-4o-mini",
            api_key="sk-test",
            timeout=1200.0,
            stream_timeout=120.0,
            stream_idle_timeout=60.0,
        )
        with patch("matmaster.providers.openai_provider.openai.AsyncOpenAI") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            async with provider:
                pass
            call_kwargs = mock_cls.call_args
            http_client = call_kwargs.kwargs["http_client"]
            # read timeout = max(60, 120) + 10 = 130
            assert http_client.timeout.read == 130.0
            assert http_client.timeout.connect == 15.0
            assert http_client.timeout.write == 30.0
            assert http_client.timeout.pool == 15.0

    async def test_httpx_client_fallback_without_stream_timeouts(self) -> None:
        """Without stream timeouts, httpx client uses general timeout for read."""
        provider = OpenAIProvider(
            model="gpt-4o-mini",
            api_key="sk-test",
            timeout=300.0,
        )
        with patch("matmaster.providers.openai_provider.openai.AsyncOpenAI") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            async with provider:
                pass
            call_kwargs = mock_cls.call_args
            http_client = call_kwargs.kwargs["http_client"]
            # read timeout = max(300, 300) + 10 = 310
            assert http_client.timeout.read == 310.0


# -- chat() response mapping ---------------------------------------------


def _make_mock_completion(
    content: str | None = "Hello",
    tool_calls: list[Any] | None = None,
    finish_reason: str = "stop",
    usage: Any | None = None,
) -> MagicMock:
    """Create a mock ChatCompletion matching the OpenAI SDK structure."""
    mock = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = tool_calls
    choice.finish_reason = finish_reason
    mock.choices = [choice]
    mock.usage = usage
    return mock


class TestChatContent:
    async def test_chat_content(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            content="Hello"
        )
        provider._client = mock_client
        result = await provider.chat([{"role": "user", "content": "Hi"}])

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello"
        assert result.finish_reason == "stop"

    async def test_chat_tool_calls(self) -> None:
        tc_mock = MagicMock()
        tc_mock.id = "tc-1"
        tc_mock.function.name = "get_weather"
        tc_mock.function.arguments = '{"city": "Beijing"}'

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            content=None,
            tool_calls=[tc_mock],
        )
        provider._client = mock_client
        result = await provider.chat([{"role": "user", "content": "Weather?"}])

        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "tc-1"
        assert result.tool_calls[0].name == "get_weather"
        assert result.tool_calls[0].arguments == {"city": "Beijing"}

    async def test_chat_usage(self) -> None:
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 15

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            usage=usage,
        )
        provider._client = mock_client
        result = await provider.chat([{"role": "user", "content": "Hi"}])

        assert result.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    async def test_chat_finish_reason(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            finish_reason="stop"
        )
        provider._client = mock_client
        result = await provider.chat([{"role": "user", "content": "Hi"}])

        assert result.finish_reason == "stop"


# -- chat_stream() response mapping ---------------------------------------


def _make_stream_chunk(
    content: str | None = None,
    reasoning_content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = None,
) -> MagicMock:
    """Create a mock streaming chunk matching the OpenAI SDK structure."""
    mock = MagicMock()
    choice = MagicMock()
    choice.delta.content = content
    choice.delta.reasoning_content = reasoning_content
    choice.delta.tool_calls = tool_calls
    choice.finish_reason = finish_reason
    mock.choices = [choice]
    return mock


class TestChatStreamContent:
    async def test_chat_stream_reasoning_content(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter([
            _make_stream_chunk(reasoning_content="thinking..."),
            _make_stream_chunk(content="answer", finish_reason="stop"),
        ])
        provider._client = mock_client
        chunks = [chunk async for chunk in provider.chat_stream(
            [{"role": "user", "content": "Hi"}]
        )]

        assert len(chunks) == 2
        assert chunks[0].reasoning_content == "thinking..."
        assert chunks[1].content == "answer"
        assert chunks[1].finish_reason == "stop"

    async def test_chat_stream_content(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter([
            _make_stream_chunk(content="He"),
            _make_stream_chunk(content="llo"),
            _make_stream_chunk(finish_reason="stop"),
        ])
        provider._client = mock_client
        chunks = [chunk async for chunk in provider.chat_stream(
            [{"role": "user", "content": "Hi"}]
        )]

        assert len(chunks) == 3
        assert chunks[0].content == "He"
        assert chunks[1].content == "llo"
        assert chunks[2].finish_reason == "stop"
        assert all(isinstance(c, StreamChunk) for c in chunks)

    async def test_chat_stream_tool_call_deltas(self) -> None:
        tc_delta = MagicMock()
        tc_delta.index = 0
        tc_delta.id = "tc-1"
        tc_delta.function.name = "fn"
        tc_delta.function.arguments = '{"a": 1}'

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter([
            _make_stream_chunk(tool_calls=[tc_delta]),
            _make_stream_chunk(finish_reason="stop"),
        ])
        provider._client = mock_client
        chunks = [chunk async for chunk in provider.chat_stream(
            [{"role": "user", "content": "Hi"}]
        )]

        assert chunks[0].tool_call_deltas is not None
        assert len(chunks[0].tool_call_deltas) == 1
        assert chunks[0].tool_call_deltas[0]["index"] == 0
        assert chunks[0].tool_call_deltas[0]["id"] == "tc-1"
        assert chunks[0].tool_call_deltas[0]["name"] == "fn"
        assert chunks[0].tool_call_deltas[0]["arguments"] == '{"a": 1}'

    async def test_chat_stream_empty_choices(self) -> None:
        """Chunks with no choices are skipped."""
        empty_chunk = MagicMock()
        empty_chunk.choices = []

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter([
            empty_chunk,
            _make_stream_chunk(content="ok", finish_reason="stop"),
        ])
        provider._client = mock_client
        chunks = [chunk async for chunk in provider.chat_stream(
            [{"role": "user", "content": "Hi"}]
        )]

        assert len(chunks) == 1
        assert chunks[0].content == "ok"

    async def test_chat_stream_returns_async_iterator(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter([
            _make_stream_chunk(content="ok", finish_reason="stop"),
        ])
        provider._client = mock_client
        result = provider.chat_stream([{"role": "user", "content": "Hi"}])

        # Verify it's an async iterable
        async for chunk in result:
            assert isinstance(chunk, StreamChunk)


class TestChatStreamUsage:
    async def test_stream_options_included_in_kwargs(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter([])
        provider._client = mock_client
        _ = [chunk async for chunk in provider.chat_stream(
            [{"role": "user", "content": "hi"}]
        )]

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("stream_options") == {
            "include_usage": True
        }

    async def test_usage_emitted_as_final_chunk(self) -> None:
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 15

        usage_only_chunk = MagicMock()
        usage_only_chunk.choices = []
        usage_only_chunk.usage = usage

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter([
            _make_stream_chunk(content="answer", finish_reason="stop"),
            usage_only_chunk,
        ])
        provider._client = mock_client
        chunks = [chunk async for chunk in provider.chat_stream(
            [{"role": "user", "content": "Hi"}]
        )]

        assert len(chunks) == 2
        assert chunks[1].usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }


# -- Error handling -------------------------------------------------------


class TestErrorHandling:
    async def test_invalid_json_in_tool_call_arguments(self) -> None:
        """Invalid JSON in tool_call arguments is handled gracefully."""
        tc_mock = MagicMock()
        tc_mock.id = "tc-1"
        tc_mock.function.name = "fn"
        tc_mock.function.arguments = "not valid json {"

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            content=None,
            tool_calls=[tc_mock],
        )
        provider._client = mock_client
        result = await provider.chat([{"role": "user", "content": "test"}])

        assert result.tool_calls is not None
        assert result.tool_calls[0].arguments == {"_raw": "not valid json {"}

    async def test_empty_arguments(self) -> None:
        """Empty/None arguments returns empty dict."""
        tc_mock = MagicMock()
        tc_mock.id = "tc-1"
        tc_mock.function.name = "fn"
        tc_mock.function.arguments = None

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            content=None,
            tool_calls=[tc_mock],
        )
        provider._client = mock_client
        result = await provider.chat([{"role": "user", "content": "test"}])

        assert result.tool_calls is not None
        assert result.tool_calls[0].arguments == {}

    async def test_chat_with_tools_kwarg(self) -> None:
        """chat() passes tools to the API when provided."""
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion()
        provider._client = mock_client

        tools = [{"type": "function", "function": {"name": "fn"}}]
        await provider.chat(
            [{"role": "user", "content": "Hi"}],
            tools=tools,
        )

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("tools") == tools or (
            len(call_kwargs.args) == 0
            and "tools" in call_kwargs.kwargs
        )


# -- chat_with_retry() tests ----------------------------------------------


class TestChatWithRetry:
    """Tests for OpenAIProvider.chat_with_retry() explicit retry logic."""

    def _make_provider(self) -> tuple[OpenAIProvider, AsyncMock]:
        """Create provider with mocked client, return (provider, mock_client)."""
        provider = OpenAIProvider(
            model="gpt-4o-mini",
            api_key="sk-test",
            max_retries=3,
            retry_delay=1.0,
        )
        mock_client = AsyncMock()
        provider._client = mock_client
        return provider, mock_client

    def test_success_no_retry(self) -> None:
        """chat_with_retry returns LLMResponse on first success."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            content="Hello"
        )

        result = provider.chat_with_retry([{"role": "user", "content": "Hi"}])

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello"
        assert mock_client.chat.completions.create.call_count == 1

    def test_retry_on_connection_error(self) -> None:
        """Retries on APIConnectionError, succeeds on second attempt."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = [
            openai.APIConnectionError(request=MagicMock()),
            _make_mock_completion(content="recovered"),
        ]

        with patch("matmaster.providers.openai_provider.time.sleep"):
            result = provider.chat_with_retry([{"role": "user", "content": "Hi"}])

        assert result.content == "recovered"
        assert mock_client.chat.completions.create.call_count == 2

    def test_retry_on_timeout_error(self) -> None:
        """Retries on APITimeoutError, succeeds on second attempt."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = [
            openai.APITimeoutError(request=MagicMock()),
            _make_mock_completion(content="recovered"),
        ]

        with patch("matmaster.providers.openai_provider.time.sleep"):
            result = provider.chat_with_retry([{"role": "user", "content": "Hi"}])

        assert result.content == "recovered"
        assert mock_client.chat.completions.create.call_count == 2

    def test_retry_on_rate_limit(self) -> None:
        """Retries on RateLimitError (429), succeeds on second attempt."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = [
            openai.RateLimitError(
                response=MagicMock(status_code=429, headers={}),
                body=None,
                message="rate limited",
            ),
            _make_mock_completion(content="recovered"),
        ]

        with patch("matmaster.providers.openai_provider.time.sleep"):
            result = provider.chat_with_retry([{"role": "user", "content": "Hi"}])

        assert result.content == "recovered"
        assert mock_client.chat.completions.create.call_count == 2

    def test_retry_on_server_error(self) -> None:
        """Retries on InternalServerError (500), succeeds on second attempt."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = [
            openai.InternalServerError(
                response=MagicMock(status_code=500, headers={}),
                body=None,
                message="server error",
            ),
            _make_mock_completion(content="recovered"),
        ]

        with patch("matmaster.providers.openai_provider.time.sleep"):
            result = provider.chat_with_retry([{"role": "user", "content": "Hi"}])

        assert result.content == "recovered"
        assert mock_client.chat.completions.create.call_count == 2

    def test_no_retry_on_auth_error(self) -> None:
        """AuthenticationError raises immediately, no retry."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.AuthenticationError(
                response=MagicMock(status_code=401, headers={}),
                body=None,
                message="invalid key",
            )
        )

        with pytest.raises(openai.AuthenticationError):
            provider.chat_with_retry([{"role": "user", "content": "Hi"}])

        assert mock_client.chat.completions.create.call_count == 1

    def test_no_retry_on_context_length(self) -> None:
        """BadRequestError with 'context length' raises immediately."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.BadRequestError(
                response=MagicMock(status_code=400, headers={}),
                body=None,
                message="context length exceeded",
            )
        )

        with pytest.raises(openai.BadRequestError):
            provider.chat_with_retry([{"role": "user", "content": "Hi"}])

        assert mock_client.chat.completions.create.call_count == 1

    def test_exhausted_retries(self) -> None:
        """All retries exhausted raises RuntimeError."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )

        with patch("matmaster.providers.openai_provider.time.sleep"):
            with pytest.raises(RuntimeError, match="LLM call failed after 3 attempts"):
                provider.chat_with_retry([{"role": "user", "content": "Hi"}])

        assert mock_client.chat.completions.create.call_count == 3

    def test_exponential_backoff(self) -> None:
        """Verify time.sleep called with exponential delays."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )

        with patch("matmaster.providers.openai_provider.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError):
                provider.chat_with_retry(
                    [{"role": "user", "content": "Hi"}],
                    max_retries=3,
                    retry_delay=1.0,
                )

        # Backoff: 1.0 * 2^0 = 1.0, 1.0 * 2^1 = 2.0
        # Last attempt does NOT sleep (only sleeps between retries)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)

    def test_custom_max_retries(self) -> None:
        """chat_with_retry respects custom max_retries parameter."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )

        with patch("matmaster.providers.openai_provider.time.sleep"):
            with pytest.raises(RuntimeError, match="LLM call failed after 5 attempts"):
                provider.chat_with_retry(
                    [{"role": "user", "content": "Hi"}],
                    max_retries=5,
                )

        assert mock_client.chat.completions.create.call_count == 5

    def test_custom_retry_delay(self) -> None:
        """chat_with_retry respects custom retry_delay parameter."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )

        with patch("matmaster.providers.openai_provider.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError):
                provider.chat_with_retry(
                    [{"role": "user", "content": "Hi"}],
                    max_retries=3,
                    retry_delay=0.5,
                )

        # Backoff: 0.5 * 2^0 = 0.5, 0.5 * 2^1 = 1.0
        mock_sleep.assert_any_call(0.5)
        mock_sleep.assert_any_call(1.0)


# -- chat_stream() exception translation ----------------------------------


class TestChatStreamExceptionTranslation:
    def _make_provider(self) -> tuple[OpenAIProvider, AsyncMock]:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        provider._client = mock_client
        return provider, mock_client

    async def test_timeout_raises_retryable_llm_error(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APITimeoutError(request=MagicMock())
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "Hi"}]
            )]
        assert exc_info.value.retryable is True
        assert exc_info.value.__cause__ is not None

    async def test_connection_error_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "Hi"}]
            )]
        assert exc_info.value.retryable is True

    async def test_rate_limit_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.RateLimitError(
                response=MagicMock(status_code=429, headers={}),
                body=None, message="rate limited",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "Hi"}]
            )]
        assert exc_info.value.retryable is True

    async def test_internal_server_error_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.InternalServerError(
                response=MagicMock(status_code=500, headers={}),
                body=None, message="server error",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "Hi"}]
            )]
        assert exc_info.value.retryable is True

    async def test_auth_error_raises_non_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.AuthenticationError(
                response=MagicMock(status_code=401, headers={}),
                body=None, message="invalid key",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "Hi"}]
            )]
        assert exc_info.value.retryable is False

    async def test_context_length_raises_non_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.BadRequestError(
                response=MagicMock(status_code=400, headers={}),
                body=None, message="context length exceeded",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "Hi"}]
            )]
        assert exc_info.value.retryable is False

    async def test_generic_bad_request_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.BadRequestError(
                response=MagicMock(status_code=400, headers={}),
                body=None, message="something went wrong",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "Hi"}]
            )]
        assert exc_info.value.retryable is True

    async def test_httpx_read_timeout_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        import httpx
        mock_client.chat.completions.create.side_effect = httpx.ReadTimeout(
            "read timed out"
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "Hi"}]
            )]
        assert exc_info.value.retryable is True

    async def test_chat_stream_accepts_timeout_override(self) -> None:
        """timeout kwarg is forwarded to SDK create call."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.return_value = _async_iter([
            _make_stream_chunk(content="ok", finish_reason="stop"),
        ])
        _ = [c async for c in provider.chat_stream(
            [{"role": "user", "content": "Hi"}],
            timeout=600.0,
        )]
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("timeout") == 600.0


# -- Async context manager lifecycle tests --------------------------------


class TestAsyncContextManager:
    async def test_aenter_creates_client(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert provider._client is None
        with patch("matmaster.providers.openai_provider.openai.AsyncOpenAI") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            async with provider:
                assert provider._client is mock_client
            mock_cls.assert_called_once()

    async def test_aexit_closes_client(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        with patch("matmaster.providers.openai_provider.openai.AsyncOpenAI") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            async with provider:
                pass
            mock_client.close.assert_awaited_once()
            assert provider._client is None

    async def test_chat_without_context_raises(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        with pytest.raises(RuntimeError, match="async context manager"):
            await provider.chat([{"role": "user", "content": "Hi"}])

    async def test_chat_stream_without_context_raises(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        with pytest.raises(RuntimeError, match="async context manager"):
            async for _ in provider.chat_stream([{"role": "user", "content": "Hi"}]):
                pass

    async def test_validate_async_protocol(self) -> None:
        from matmaster.validation import validate_async_protocol
        from matmaster.types.llm_provider import LLMProvider

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        errors = validate_async_protocol(provider, LLMProvider)
        assert errors == [], f"Protocol validation errors: {errors}"
