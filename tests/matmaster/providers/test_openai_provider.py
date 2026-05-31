"""Tests for OpenAIProvider -- concrete LLMProvider implementation.

All tests use unittest.mock to mock the OpenAI client -- no real API
calls are made. Tests verify Protocol conformance, construction, async
chat() response mapping, async chat_stream() response mapping,
error handling, and async context manager
lifecycle.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.providers.openai_provider import (
    AnthropicPromptCacheOptions,
    OpenAIProvider,
)
from matmaster.types.errors import LLMError
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk


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
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test", max_retries=5)
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
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test", max_retries=5)
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
        with patch(
            "matmaster.providers.openai_provider.openai.AsyncOpenAI"
        ) as mock_cls:
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
        with patch(
            "matmaster.providers.openai_provider.openai.AsyncOpenAI"
        ) as mock_cls:
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

    async def test_chat_usage_vendor_preserves_anthropic_cache_fields(self) -> None:
        class Usage:
            prompt_tokens = 10
            completion_tokens = 5
            total_tokens = 15
            input_tokens = 10
            output_tokens = 5
            cache_creation_input_tokens = 123
            cache_read_input_tokens = 45
            cache_creation = {"ephemeral_5m_input_tokens": 123}

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            usage=Usage(),
        )
        provider._client = mock_client

        result = await provider.chat([{"role": "user", "content": "Hi"}])

        assert result.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_read_tokens": 45,
        }
        assert result.usage_vendor == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 123,
            "cache_read_input_tokens": 45,
            "cache_creation": {"ephemeral_5m_input_tokens": 123},
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


class TestAnthropicPromptCacheRequestPayload:
    """Anthropic prompt cache is applied only at provider request boundary."""

    def _provider(self) -> OpenAIProvider:
        return OpenAIProvider(
            model="claude-opus-4-6",
            api_key="sk-test",
            prompt_cache_options=AnthropicPromptCacheOptions(
                system_prompt_breakpoint=True,
                cache_control={"type": "ephemeral"},
            ),
        )

    async def test_chat_applies_system_cache_breakpoint(self) -> None:
        provider = self._provider()
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion()
        provider._client = mock_client

        await provider.chat(
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "Hi"},
            ]
        )

        call_kwargs = mock_client.chat.completions.create.await_args.kwargs
        assert call_kwargs["messages"][0] == {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "system prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
        assert call_kwargs["messages"][1] == {"role": "user", "content": "Hi"}

    async def test_chat_stream_applies_system_cache_breakpoint(self) -> None:
        provider = self._provider()
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [_make_stream_chunk(content="ok", finish_reason="stop")]
        )
        provider._client = mock_client

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                [
                    {"role": "system", "content": "system prompt"},
                    {"role": "user", "content": "Hi"},
                ]
            )
        ]

        assert chunks[0].content == "ok"
        call_kwargs = mock_client.chat.completions.create.await_args.kwargs
        assert call_kwargs["messages"][0]["content"][0]["cache_control"] == {
            "type": "ephemeral"
        }

    async def test_prompt_cache_does_not_mutate_original_messages(self) -> None:
        provider = self._provider()
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion()
        provider._client = mock_client
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "Hi"},
        ]

        await provider.chat(messages)

        assert messages == [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "Hi"},
        ]

    async def test_prompt_cache_requires_system_message(self) -> None:
        provider = self._provider()
        provider._client = AsyncMock()

        with pytest.raises(LLMError) as exc_info:
            await provider.chat([{"role": "user", "content": "Hi"}])

        assert exc_info.value.retryable is False
        assert exc_info.value.error_category == "payload_validation"
        assert "no system message" in str(exc_info.value)

    async def test_prompt_cache_requires_non_empty_string_system_content(
        self,
    ) -> None:
        provider = self._provider()
        provider._client = AsyncMock()

        with pytest.raises(LLMError) as exc_info:
            await provider.chat([{"role": "system", "content": ""}])

        assert exc_info.value.retryable is False
        assert exc_info.value.error_category == "payload_validation"
        assert "non-empty string system prompt" in str(exc_info.value)


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


def _make_tool_call_delta(
    *,
    index: int,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> MagicMock:
    """Create a mock streaming tool-call delta."""
    tc_delta = MagicMock()
    tc_delta.index = index
    tc_delta.id = call_id
    if name is None and arguments is None:
        tc_delta.function = None
    else:
        tc_delta.function.name = name
        tc_delta.function.arguments = arguments
    return tc_delta


class TestChatStreamContent:
    async def test_chat_stream_reasoning_content(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(reasoning_content="thinking..."),
                _make_stream_chunk(content="answer", finish_reason="stop"),
            ]
        )
        provider._client = mock_client
        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert len(chunks) == 2
        assert chunks[0].reasoning_content == "thinking..."
        assert chunks[1].content == "answer"
        assert chunks[1].finish_reason == "stop"

    async def test_chat_stream_content(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(content="He"),
                _make_stream_chunk(content="llo"),
                _make_stream_chunk(finish_reason="stop"),
            ]
        )
        provider._client = mock_client
        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert len(chunks) == 3
        assert chunks[0].content == "He"
        assert chunks[1].content == "llo"
        assert chunks[2].finish_reason == "stop"
        assert all(isinstance(c, StreamChunk) for c in chunks)

    async def test_chat_stream_tool_call_deltas(self) -> None:
        tc_delta = _make_tool_call_delta(
            index=0,
            call_id="tc-1",
            name="fn",
            arguments='{"a": 1}',
        )

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(tool_calls=[tc_delta]),
                _make_stream_chunk(finish_reason="stop"),
            ]
        )
        provider._client = mock_client
        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert chunks[0].tool_call_deltas is not None
        assert len(chunks[0].tool_call_deltas) == 1
        assert chunks[0].tool_call_deltas[0]["index"] == 0
        assert chunks[0].tool_call_deltas[0]["id"] == "tc-1"
        assert chunks[0].tool_call_deltas[0]["name"] == "fn"
        assert chunks[0].tool_call_deltas[0]["arguments"] == '{"a": 1}'

    async def test_chat_stream_rewrites_same_index_when_tool_name_changes(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-bash",
                            name="Bash",
                            arguments='{"command": "pwd"}',
                        )
                    ]
                ),
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-skill",
                            name="Skill",
                            arguments='{"skill": "chemistry"}',
                        )
                    ]
                ),
                _make_stream_chunk(finish_reason="stop"),
            ]
        )
        provider._client = mock_client

        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert chunks[0].tool_call_deltas == [
            {
                "index": 0,
                "id": "tc-bash",
                "name": "Bash",
                "arguments": '{"command": "pwd"}',
            }
        ]
        assert chunks[1].tool_call_deltas == [
            {
                "index": 1,
                "id": "tc-skill",
                "name": "Skill",
                "arguments": '{"skill": "chemistry"}',
            }
        ]

    async def test_chat_stream_rewrites_same_index_when_id_changes(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-1",
                            name="Bash",
                            arguments='{"command": "pwd"}',
                        )
                    ]
                ),
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-2",
                            name="Bash",
                            arguments='{"command": "which python3"}',
                        )
                    ]
                ),
                _make_stream_chunk(finish_reason="stop"),
            ]
        )
        provider._client = mock_client

        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert chunks[0].tool_call_deltas == [
            {
                "index": 0,
                "id": "tc-1",
                "name": "Bash",
                "arguments": '{"command": "pwd"}',
            }
        ]
        assert chunks[1].tool_call_deltas == [
            {
                "index": 1,
                "id": "tc-2",
                "name": "Bash",
                "arguments": '{"command": "which python3"}',
            }
        ]

    async def test_chat_stream_keeps_same_index_for_normal_argument_streaming(
        self,
    ) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-1",
                            name="Bash",
                            arguments='{"command": "which ',
                        )
                    ]
                ),
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            arguments='python3 && python3 --version"}',
                        )
                    ]
                ),
                _make_stream_chunk(finish_reason="stop"),
            ]
        )
        provider._client = mock_client

        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert chunks[0].tool_call_deltas == [
            {
                "index": 0,
                "id": "tc-1",
                "name": "Bash",
                "arguments": '{"command": "which ',
            }
        ]
        assert chunks[1].tool_call_deltas == [
            {
                "index": 0,
                "arguments": 'python3 && python3 --version"}',
            }
        ]

    async def test_chat_stream_assigns_monotonic_indices_for_late_collision(
        self,
    ) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-1",
                            name="Bash",
                            arguments='{"command": "pwd"}',
                        )
                    ]
                ),
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=1,
                            call_id="tc-2",
                            name="Skill",
                            arguments='{"skill": "chemistry"}',
                        )
                    ]
                ),
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-3",
                            name="Bash",
                            arguments='{"command": "which python3"}',
                        )
                    ]
                ),
                _make_stream_chunk(finish_reason="stop"),
            ]
        )
        provider._client = mock_client

        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert chunks[0].tool_call_deltas == [
            {
                "index": 0,
                "id": "tc-1",
                "name": "Bash",
                "arguments": '{"command": "pwd"}',
            }
        ]
        assert chunks[1].tool_call_deltas == [
            {
                "index": 1,
                "id": "tc-2",
                "name": "Skill",
                "arguments": '{"skill": "chemistry"}',
            }
        ]
        assert chunks[2].tool_call_deltas == [
            {
                "index": 2,
                "id": "tc-3",
                "name": "Bash",
                "arguments": '{"command": "which python3"}',
            }
        ]

    async def test_chat_stream_drops_exact_duplicate_id_on_different_index(
        self,
    ) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-dup",
                            name="Bash",
                            arguments='{"command":"pwd"}',
                        )
                    ]
                ),
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=1,
                            call_id="tc-dup",
                            name="Bash",
                            arguments='{"command":"pwd"}',
                        )
                    ]
                ),
                _make_stream_chunk(finish_reason="stop"),
            ]
        )
        provider._client = mock_client

        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert chunks[0].tool_call_deltas == [
            {
                "index": 0,
                "id": "tc-dup",
                "name": "Bash",
                "arguments": '{"command":"pwd"}',
            }
        ]
        assert chunks[1].tool_call_deltas in (None, [])

    async def test_chat_stream_merges_duplicate_id_continuation_on_different_index(
        self,
    ) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-dup",
                            name="Bash",
                            arguments='{"command":"which ',
                        )
                    ]
                ),
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=1,
                            call_id="tc-dup",
                            name="Bash",
                            arguments='python3"}',
                        )
                    ]
                ),
                _make_stream_chunk(finish_reason="stop"),
            ]
        )
        provider._client = mock_client

        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert chunks[0].tool_call_deltas == [
            {
                "index": 0,
                "id": "tc-dup",
                "name": "Bash",
                "arguments": '{"command":"which ',
            }
        ]
        assert chunks[1].tool_call_deltas == [
            {
                "index": 0,
                "arguments": 'python3"}',
            }
        ]

    async def test_chat_stream_duplicate_id_with_conflicting_name_raises(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-dup",
                            name="Bash",
                            arguments='{"command":"pwd"}',
                        )
                    ]
                ),
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=1,
                            call_id="tc-dup",
                            name="Skill",
                            arguments='{"skill":"brainstorming"}',
                        )
                    ]
                ),
            ]
        )
        provider._client = mock_client

        with pytest.raises(LLMError, match="duplicate tool_call id"):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    [{"role": "user", "content": "Hi"}]
                )
            ]

    async def test_chat_stream_duplicate_id_with_conflicting_arguments_raises(
        self,
    ) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=0,
                            call_id="tc-dup",
                            name="Bash",
                            arguments='{"command":"pwd"}',
                        )
                    ]
                ),
                _make_stream_chunk(
                    tool_calls=[
                        _make_tool_call_delta(
                            index=1,
                            call_id="tc-dup",
                            name="Bash",
                            arguments='{"command":"which python3"}',
                        )
                    ]
                ),
            ]
        )
        provider._client = mock_client

        with pytest.raises(LLMError, match="duplicate tool_call id"):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    [{"role": "user", "content": "Hi"}]
                )
            ]

    async def test_chat_stream_empty_choices(self) -> None:
        """Chunks with no choices are skipped."""
        empty_chunk = MagicMock()
        empty_chunk.choices = []

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                empty_chunk,
                _make_stream_chunk(content="ok", finish_reason="stop"),
            ]
        )
        provider._client = mock_client
        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert len(chunks) == 1
        assert chunks[0].content == "ok"

    async def test_chat_stream_returns_async_iterator(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(content="ok", finish_reason="stop"),
            ]
        )
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
        _ = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "hi"}])
        ]

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("stream_options") == {"include_usage": True}

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
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(content="answer", finish_reason="stop"),
                usage_only_chunk,
            ]
        )
        provider._client = mock_client
        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

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
            len(call_kwargs.args) == 0 and "tools" in call_kwargs.kwargs
        )


# -- Async context manager lifecycle tests --------------------------------


class TestAsyncContextManager:
    async def test_aenter_creates_client(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert provider._client is None
        with patch(
            "matmaster.providers.openai_provider.openai.AsyncOpenAI"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            async with provider:
                assert provider._client is mock_client
            mock_cls.assert_called_once()

    async def test_aexit_closes_client(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        with patch(
            "matmaster.providers.openai_provider.openai.AsyncOpenAI"
        ) as mock_cls:
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
        from matmaster.types.llm_provider import LLMProvider
        from matmaster.validation import validate_async_protocol

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        errors = validate_async_protocol(provider, LLMProvider)
        assert errors == [], f"Protocol validation errors: {errors}"

    async def test_reentrant_context_manager(self) -> None:
        """Nested async-with on same provider must not close client early.

        Reproduces the spawn bug: parent enters provider, child enters the
        same provider, child exits -> client must stay alive for parent.
        """
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        with patch(
            "matmaster.providers.openai_provider.openai.AsyncOpenAI"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client

            async with provider:  # parent enters
                assert provider._client is mock_client
                async with provider:  # child enters (reentrant)
                    assert provider._client is mock_client
                # child exits -- client must still be alive
                assert provider._client is mock_client
                mock_client.close.assert_not_awaited()
            # parent exits -- now client should be closed
            mock_client.close.assert_awaited_once()
            assert provider._client is None
