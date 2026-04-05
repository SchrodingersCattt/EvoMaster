"""Tests for OpenAIProvider -- exception translation and error_category.

Split from test_openai_provider.py to keep file under 1000 lines.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

from matmaster.providers.openai_provider import OpenAIProvider
from matmaster.types.errors import LLMError


async def _async_iter(items):
    """Convert a list into an async iterator for mock streaming."""
    for item in items:
        yield item


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


# -- chat_stream() exception translation ----------------------------------


class TestChatStreamExceptionTranslation:
    def _make_provider(self) -> tuple[OpenAIProvider, AsyncMock]:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        provider._client = mock_client
        return provider, mock_client

    async def test_timeout_raises_retryable_llm_error(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError(
            request=MagicMock()
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "Hi"}])
            ]
        assert exc_info.value.retryable is True
        assert exc_info.value.__cause__ is not None

    async def test_connection_error_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.APIConnectionError(
            request=MagicMock()
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "Hi"}])
            ]
        assert exc_info.value.retryable is True

    async def test_rate_limit_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.RateLimitError(
            response=MagicMock(status_code=429, headers={}),
            body=None,
            message="rate limited",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "Hi"}])
            ]
        assert exc_info.value.retryable is True

    async def test_internal_server_error_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.InternalServerError(
            response=MagicMock(status_code=500, headers={}),
            body=None,
            message="server error",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "Hi"}])
            ]
        assert exc_info.value.retryable is True

    async def test_auth_error_raises_non_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.AuthenticationError(
            response=MagicMock(status_code=401, headers={}),
            body=None,
            message="invalid key",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "Hi"}])
            ]
        assert exc_info.value.retryable is False

    async def test_context_length_raises_non_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.BadRequestError(
            response=MagicMock(status_code=400, headers={}),
            body=None,
            message="context length exceeded",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "Hi"}])
            ]
        assert exc_info.value.retryable is False

    async def test_generic_bad_request_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.BadRequestError(
            response=MagicMock(status_code=400, headers={}),
            body=None,
            message="something went wrong",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "Hi"}])
            ]
        assert exc_info.value.retryable is True

    async def test_httpx_read_timeout_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        import httpx

        mock_client.chat.completions.create.side_effect = httpx.ReadTimeout(
            "read timed out"
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "Hi"}])
            ]
        assert exc_info.value.retryable is True

    async def test_chat_stream_accepts_timeout_override(self) -> None:
        """timeout kwarg is forwarded to SDK create call."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(content="ok", finish_reason="stop"),
            ]
        )
        _ = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "Hi"}],
                timeout=600.0,
            )
        ]
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("timeout") == 600.0


# -- chat_stream() error_category ------------------------------------------


class TestChatStreamErrorCategory:
    """Verify chat_stream raises LLMError with correct error_category."""

    def _make_provider(self) -> tuple[OpenAIProvider, AsyncMock]:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        provider._client = mock_client
        return provider, mock_client

    async def test_timeout_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError(
            request=MagicMock()
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "hi"}])
            ]
        assert exc_info.value.error_category == "timeout"
        assert exc_info.value.retryable is True

    async def test_connection_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.APIConnectionError(
            request=MagicMock()
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "hi"}])
            ]
        assert exc_info.value.error_category == "connection"

    async def test_rate_limit_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.RateLimitError(
            response=MagicMock(status_code=429, headers={}),
            body=None,
            message="rate limited",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "hi"}])
            ]
        assert exc_info.value.error_category == "rate_limit"

    async def test_server_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.InternalServerError(
            response=MagicMock(status_code=500, headers={}),
            body=None,
            message="server error",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "hi"}])
            ]
        assert exc_info.value.error_category == "server"

    async def test_auth_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.AuthenticationError(
            response=MagicMock(status_code=401, headers={}),
            body=None,
            message="invalid key",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "hi"}])
            ]
        assert exc_info.value.error_category == "auth"
        assert exc_info.value.retryable is False

    async def test_context_overflow_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.BadRequestError(
            response=MagicMock(status_code=400, headers={}),
            body=None,
            message="This model's maximum context length is 8192 tokens",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "hi"}])
            ]
        assert exc_info.value.error_category == "context_overflow"
        assert exc_info.value.retryable is False

    async def test_bad_request_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = openai.BadRequestError(
            response=MagicMock(status_code=400, headers={}),
            body=None,
            message="invalid parameter",
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [
                c
                async for c in provider.chat_stream([{"role": "user", "content": "hi"}])
            ]
        assert exc_info.value.error_category == "bad_request"
        assert exc_info.value.retryable is True
