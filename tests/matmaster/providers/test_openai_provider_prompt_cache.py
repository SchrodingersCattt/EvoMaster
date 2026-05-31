"""Prompt-cache and lifecycle payload tests for OpenAIProvider."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.providers.openai_provider import (
    AnthropicPromptCacheOptions,
    OpenAIProvider,
)
from matmaster.types.errors import LLMError


async def _async_iter(items):
    """Convert a list into an async iterator for mock streaming."""
    for item in items:
        yield item


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


class TestAnthropicPromptCacheRequestPayload:
    """Anthropic prompt cache is applied only at provider request boundary."""

    def _provider(
        self,
        *,
        automatic: bool = False,
        latest_user_breakpoint: bool = True,
        tool_result_breakpoint: bool = False,
        flexible_breakpoint: bool = False,
        max_breakpoints: int = 4,
        min_flexible_chars: int = 1000,
    ) -> OpenAIProvider:
        return OpenAIProvider(
            model="claude-opus-4-6",
            api_key="sk-test",
            prompt_cache_options=AnthropicPromptCacheOptions(
                system_prompt_breakpoint=True,
                cache_control={"type": "ephemeral"},
                automatic=automatic,
                latest_user_breakpoint=latest_user_breakpoint,
                tool_result_breakpoint=tool_result_breakpoint,
                flexible_breakpoint=flexible_breakpoint,
                max_breakpoints=max_breakpoints,
                min_flexible_chars=min_flexible_chars,
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

    async def test_chat_applies_semantic_cache_points(self) -> None:
        provider = self._provider(
            automatic=True,
            latest_user_breakpoint=True,
            tool_result_breakpoint=True,
            flexible_breakpoint=False,
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion()
        provider._client = mock_client

        await provider.chat(
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "old"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {"name": "a", "arguments": "{}"},
                        },
                        {
                            "id": "call_b",
                            "type": "function",
                            "function": {"name": "b", "arguments": "{}"},
                        },
                    ],
                },
                {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
                {"role": "tool", "tool_call_id": "call_b", "content": "result b"},
                {"role": "user", "content": "current"},
            ]
        )

        sent = mock_client.chat.completions.create.await_args.kwargs["messages"]
        assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert sent[1] == {"role": "user", "content": "old"}
        assert "cache_control" not in sent[3]
        assert sent[4]["cache_control"] == {"type": "ephemeral"}
        assert sent[5]["content"] == [
            {
                "type": "text",
                "text": "current",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    async def test_prompt_cache_skips_incomplete_tool_group(self) -> None:
        provider = self._provider(
            automatic=True,
            latest_user_breakpoint=True,
            tool_result_breakpoint=True,
            flexible_breakpoint=False,
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion()
        provider._client = mock_client

        await provider.chat(
            [
                {"role": "system", "content": "system prompt"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {"name": "a", "arguments": "{}"},
                        },
                        {
                            "id": "call_b",
                            "type": "function",
                            "function": {"name": "b", "arguments": "{}"},
                        },
                    ],
                },
                {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
                {"role": "user", "content": "current"},
            ]
        )

        sent = mock_client.chat.completions.create.await_args.kwargs["messages"]
        assert "cache_control" not in sent[2]
        assert sent[3]["content"][0]["cache_control"] == {"type": "ephemeral"}

    async def test_prompt_cache_respects_max_breakpoints(self) -> None:
        provider = self._provider(
            automatic=True,
            latest_user_breakpoint=True,
            tool_result_breakpoint=True,
            flexible_breakpoint=True,
            max_breakpoints=2,
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion()
        provider._client = mock_client

        await provider.chat(
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "previous"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {"name": "a", "arguments": "{}"},
                        },
                    ],
                },
                {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
                {"role": "user", "content": "current"},
            ]
        )

        sent = mock_client.chat.completions.create.await_args.kwargs["messages"]
        assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert sent[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in sent[3]

    async def test_prompt_cache_flexible_marks_largest_remaining_value(self) -> None:
        provider = self._provider(
            automatic=True,
            latest_user_breakpoint=True,
            tool_result_breakpoint=False,
            flexible_breakpoint=True,
            min_flexible_chars=10,
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion()
        provider._client = mock_client

        await provider.chat(
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "this older user message is valuable"},
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "current"},
            ]
        )

        sent = mock_client.chat.completions.create.await_args.kwargs["messages"]
        assert sent[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert sent[3]["content"][0]["cache_control"] == {"type": "ephemeral"}

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
                assert provider._client is mock_client
                mock_client.close.assert_not_awaited()
            mock_client.close.assert_awaited_once()
            assert provider._client is None
