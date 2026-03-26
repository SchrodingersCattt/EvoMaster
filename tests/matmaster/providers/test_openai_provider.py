"""Tests for OpenAIProvider -- concrete LLMProvider implementation.

All tests use unittest.mock.patch to mock the OpenAI client -- no real API
calls are made. Tests verify Protocol conformance, construction, chat()
response mapping, chat_stream() response mapping, and error handling.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import openai
import pytest

from matmaster.types.llm_provider import LLMProvider
from matmaster.providers.openai_provider import OpenAIProvider
from matmaster.types.errors import LLMError
from matmaster.types.messages import LLMResponse, StreamChunk, ToolCallData


# ── Protocol conformance ────────────────────────────────


class TestProtocolConformance:
    def test_protocol_conformance(self) -> None:
        """OpenAIProvider satisfies LLMProvider Protocol."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert isinstance(provider, LLMProvider)

    def test_has_chat_method(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert hasattr(provider, "chat")
        assert callable(provider.chat)

    def test_has_chat_stream_method(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert hasattr(provider, "chat_stream")
        assert callable(provider.chat_stream)


# ── Construction ────────────────────────────────────────


class TestConstruction:
    def test_construction(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["api_key"] == "sk-test"
            assert call_kwargs["base_url"] is None
            assert call_kwargs["timeout"] == 300.0
            assert call_kwargs["max_retries"] == 0
            assert "http_client" in call_kwargs

    def test_custom_base_url(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                base_url="https://custom.api",
            )
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["api_key"] == "sk-test"
            assert call_kwargs["base_url"] == "https://custom.api"
            assert call_kwargs["timeout"] == 300.0
            assert call_kwargs["max_retries"] == 0
            assert "http_client" in call_kwargs

    def test_custom_config(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                temperature=0.5,
                max_tokens=100,
            )
            assert provider._temperature == 0.5
            assert provider._max_tokens == 100

    def test_max_retries_stored(self) -> None:
        """max_retries stored as _max_retries, SDK gets max_retries=0."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            provider = OpenAIProvider(
                model="gpt-4o-mini", api_key="sk-test", max_retries=5
            )
            assert provider._max_retries == 5
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["max_retries"] == 0
            assert "http_client" in call_kwargs

    def test_retry_delay_stored(self) -> None:
        """Custom retry_delay stored as _retry_delay."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                retry_delay=2.0,
            )
            assert provider._retry_delay == 2.0


# ── Stream timeout construction ─────────────────────────


class TestStreamTimeoutConstruction:
    def test_stream_timeout_stored(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                stream_timeout=120.0,
                stream_idle_timeout=60.0,
            )
        assert provider.stream_timeout == 120.0
        assert provider.stream_idle_timeout == 60.0

    def test_stream_timeout_defaults_none(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert provider.stream_timeout is None
        assert provider.stream_idle_timeout is None

    def test_max_retries_property(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(
                model="gpt-4o-mini", api_key="sk-test", max_retries=5
            )
        assert provider.max_retries == 5

    def test_retry_delay_property(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(
                model="gpt-4o-mini", api_key="sk-test", retry_delay=2.0
            )
        assert provider.retry_delay == 2.0

    def test_custom_httpx_client_created(self) -> None:
        """When stream timeouts provided, custom httpx.Client is passed to OpenAI."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                timeout=1200.0,
                stream_timeout=120.0,
                stream_idle_timeout=60.0,
            )
            call_kwargs = mock_cls.call_args
            assert "http_client" in call_kwargs.kwargs
            http_client = call_kwargs.kwargs["http_client"]
            # read timeout = max(60, 120) + 10 = 130
            assert http_client.timeout.read == 130.0
            assert http_client.timeout.connect == 15.0
            assert http_client.timeout.write == 30.0
            assert http_client.timeout.pool == 15.0

    def test_httpx_client_fallback_without_stream_timeouts(self) -> None:
        """Without stream timeouts, httpx client uses general timeout for read."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                timeout=300.0,
            )
            call_kwargs = mock_cls.call_args
            http_client = call_kwargs.kwargs["http_client"]
            # read timeout = max(300, 300) + 10 = 310
            assert http_client.timeout.read == 310.0


# ── chat() response mapping ────────────────────────────


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
    def test_chat_content(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_mock_completion(
                content="Hello"
            )

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            result = provider.chat([{"role": "user", "content": "Hi"}])

            assert isinstance(result, LLMResponse)
            assert result.content == "Hello"
            assert result.finish_reason == "stop"

    def test_chat_tool_calls(self) -> None:
        tc_mock = MagicMock()
        tc_mock.id = "tc-1"
        tc_mock.function.name = "get_weather"
        tc_mock.function.arguments = '{"city": "Beijing"}'

        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_mock_completion(
                content=None,
                tool_calls=[tc_mock],
            )

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            result = provider.chat([{"role": "user", "content": "Weather?"}])

            assert result.tool_calls is not None
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].id == "tc-1"
            assert result.tool_calls[0].name == "get_weather"
            assert result.tool_calls[0].arguments == {"city": "Beijing"}

    def test_chat_usage(self) -> None:
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 15

        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_mock_completion(
                usage=usage,
            )

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            result = provider.chat([{"role": "user", "content": "Hi"}])

            assert result.usage == {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            }

    def test_chat_finish_reason(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_mock_completion(
                finish_reason="stop"
            )

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            result = provider.chat([{"role": "user", "content": "Hi"}])

            assert result.finish_reason == "stop"


# ── chat_stream() response mapping ─────────────────────


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
    def test_chat_stream_reasoning_content(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = iter([
                _make_stream_chunk(reasoning_content="thinking..."),
                _make_stream_chunk(content="answer", finish_reason="stop"),
            ])

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            chunks = list(
                provider.chat_stream([{"role": "user", "content": "Hi"}])
            )

            assert len(chunks) == 2
            assert chunks[0].reasoning_content == "thinking..."
            assert chunks[1].content == "answer"
            assert chunks[1].finish_reason == "stop"

    def test_chat_stream_content(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = iter([
                _make_stream_chunk(content="He"),
                _make_stream_chunk(content="llo"),
                _make_stream_chunk(finish_reason="stop"),
            ])

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            chunks = list(
                provider.chat_stream([{"role": "user", "content": "Hi"}])
            )

            assert len(chunks) == 3
            assert chunks[0].content == "He"
            assert chunks[1].content == "llo"
            assert chunks[2].finish_reason == "stop"
            assert all(isinstance(c, StreamChunk) for c in chunks)

    def test_chat_stream_tool_call_deltas(self) -> None:
        tc_delta = MagicMock()
        tc_delta.index = 0
        tc_delta.id = "tc-1"
        tc_delta.function.name = "fn"
        tc_delta.function.arguments = '{"a": 1}'

        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = iter([
                _make_stream_chunk(tool_calls=[tc_delta]),
                _make_stream_chunk(finish_reason="stop"),
            ])

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            chunks = list(
                provider.chat_stream([{"role": "user", "content": "Hi"}])
            )

            assert chunks[0].tool_call_deltas is not None
            assert len(chunks[0].tool_call_deltas) == 1
            assert chunks[0].tool_call_deltas[0]["index"] == 0
            assert chunks[0].tool_call_deltas[0]["id"] == "tc-1"
            assert chunks[0].tool_call_deltas[0]["name"] == "fn"
            assert chunks[0].tool_call_deltas[0]["arguments"] == '{"a": 1}'

    def test_chat_stream_empty_choices(self) -> None:
        """Chunks with no choices are skipped."""
        empty_chunk = MagicMock()
        empty_chunk.choices = []

        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = iter([
                empty_chunk,
                _make_stream_chunk(content="ok", finish_reason="stop"),
            ])

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            chunks = list(
                provider.chat_stream([{"role": "user", "content": "Hi"}])
            )

            assert len(chunks) == 1
            assert chunks[0].content == "ok"

    def test_chat_stream_returns_iterator(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = iter([
                _make_stream_chunk(content="ok", finish_reason="stop"),
            ])

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            result = provider.chat_stream([{"role": "user", "content": "Hi"}])

            # Verify it's iterable
            for chunk in result:
                assert isinstance(chunk, StreamChunk)


class TestChatStreamUsage:
    def test_stream_options_included_in_kwargs(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = iter([])

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            list(provider.chat_stream([{"role": "user", "content": "hi"}]))

            call_kwargs = mock_client.chat.completions.create.call_args
            assert call_kwargs.kwargs.get("stream_options") == {
                "include_usage": True
            }

    def test_usage_emitted_as_final_chunk(self) -> None:
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 15

        usage_only_chunk = MagicMock()
        usage_only_chunk.choices = []
        usage_only_chunk.usage = usage

        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = iter([
                _make_stream_chunk(content="answer", finish_reason="stop"),
                usage_only_chunk,
            ])

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            chunks = list(provider.chat_stream([{"role": "user", "content": "Hi"}]))

            assert len(chunks) == 2
            assert chunks[1].usage == {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            }


# ── Error handling ──────────────────────────────────────


class TestErrorHandling:
    def test_invalid_json_in_tool_call_arguments(self) -> None:
        """Invalid JSON in tool_call arguments is handled gracefully."""
        tc_mock = MagicMock()
        tc_mock.id = "tc-1"
        tc_mock.function.name = "fn"
        tc_mock.function.arguments = "not valid json {"

        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_mock_completion(
                content=None,
                tool_calls=[tc_mock],
            )

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            result = provider.chat([{"role": "user", "content": "test"}])

            assert result.tool_calls is not None
            assert result.tool_calls[0].arguments == {"_raw": "not valid json {"}

    def test_empty_arguments(self) -> None:
        """Empty/None arguments returns empty dict."""
        tc_mock = MagicMock()
        tc_mock.id = "tc-1"
        tc_mock.function.name = "fn"
        tc_mock.function.arguments = None

        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_mock_completion(
                content=None,
                tool_calls=[tc_mock],
            )

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            result = provider.chat([{"role": "user", "content": "test"}])

            assert result.tool_calls is not None
            assert result.tool_calls[0].arguments == {}

    def test_chat_with_tools_kwarg(self) -> None:
        """chat() passes tools to the API when provided."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_mock_completion()

            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            tools = [{"type": "function", "function": {"name": "fn"}}]
            provider.chat(
                [{"role": "user", "content": "Hi"}],
                tools=tools,
            )

            call_kwargs = mock_client.chat.completions.create.call_args
            assert call_kwargs.kwargs.get("tools") == tools or (
                len(call_kwargs.args) == 0
                and "tools" in call_kwargs.kwargs
            )


# ── chat_stream() exception translation ─────────────────


class TestChatStreamExceptionTranslation:
    def _make_provider(self) -> tuple[OpenAIProvider, MagicMock]:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        return provider, mock_client

    def test_timeout_raises_retryable_llm_error(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APITimeoutError(request=MagicMock())
        )
        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True
        assert exc_info.value.__cause__ is not None

    def test_connection_error_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )
        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_rate_limit_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.RateLimitError(
                response=MagicMock(status_code=429, headers={}),
                body=None, message="rate limited",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_internal_server_error_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.InternalServerError(
                response=MagicMock(status_code=500, headers={}),
                body=None, message="server error",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_auth_error_raises_non_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.AuthenticationError(
                response=MagicMock(status_code=401, headers={}),
                body=None, message="invalid key",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is False

    def test_context_length_raises_non_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.BadRequestError(
                response=MagicMock(status_code=400, headers={}),
                body=None, message="context length exceeded",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is False

    def test_generic_bad_request_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.BadRequestError(
                response=MagicMock(status_code=400, headers={}),
                body=None, message="something went wrong",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_httpx_read_timeout_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        import httpx
        mock_client.chat.completions.create.side_effect = httpx.ReadTimeout(
            "read timed out"
        )
        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_chat_stream_accepts_timeout_override(self) -> None:
        """timeout kwarg is forwarded to SDK create call."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.return_value = iter([
            _make_stream_chunk(content="ok", finish_reason="stop"),
        ])
        list(provider.chat_stream(
            [{"role": "user", "content": "Hi"}],
            timeout=600.0,
        ))
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("timeout") == 600.0
