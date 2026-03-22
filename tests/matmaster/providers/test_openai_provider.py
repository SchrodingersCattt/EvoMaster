"""Tests for OpenAIProvider -- concrete LLMProvider implementation.

All tests use unittest.mock.patch to mock the OpenAI client -- no real API
calls are made. Tests verify Protocol conformance, construction, chat()
response mapping, chat_stream() response mapping, chat_with_retry() retry
logic, and error handling.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import openai
import pytest

from matmaster.types.llm_provider import LLMProvider
from matmaster.providers.openai_provider import OpenAIProvider
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

    def test_has_chat_with_retry_method(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert hasattr(provider, "chat_with_retry")
        assert callable(provider.chat_with_retry)


# ── Construction ────────────────────────────────────────


class TestConstruction:
    def test_construction(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            mock_cls.assert_called_once_with(
                api_key="sk-test",
                base_url=None,
                timeout=300.0,
                max_retries=0,
            )

    def test_custom_base_url(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                base_url="https://custom.api",
            )
            mock_cls.assert_called_once_with(
                api_key="sk-test",
                base_url="https://custom.api",
                timeout=300.0,
                max_retries=0,
            )

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
            mock_cls.assert_called_once_with(
                api_key="sk-test",
                base_url=None,
                timeout=300.0,
                max_retries=0,
            )

    def test_retry_delay_stored(self) -> None:
        """Custom retry_delay stored as _retry_delay."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                retry_delay=2.0,
            )
            assert provider._retry_delay == 2.0


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
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = None,
) -> MagicMock:
    """Create a mock streaming chunk matching the OpenAI SDK structure."""
    mock = MagicMock()
    choice = MagicMock()
    choice.delta.content = content
    choice.delta.tool_calls = tool_calls
    choice.finish_reason = finish_reason
    mock.choices = [choice]
    return mock


class TestChatStreamContent:
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


# ── chat_with_retry() tests ──────────────────────────


class TestChatWithRetry:
    """Tests for OpenAIProvider.chat_with_retry() explicit retry logic."""

    def _make_provider(self) -> tuple[OpenAIProvider, MagicMock]:
        """Create provider with mocked OpenAI client, return (provider, mock_client)."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            provider = OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                max_retries=3,
                retry_delay=1.0,
            )
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
