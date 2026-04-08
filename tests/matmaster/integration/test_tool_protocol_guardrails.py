from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from matmaster.core.agent import AgentKernel
from matmaster.providers.openai_provider import OpenAIProvider
from matmaster.types.errors import LLMError
from matmaster.types.messages import AssistantMessage, ToolMessage
from tests.matmaster.core.agent_kernel_test_helpers import (
    _make_spec,
    _make_tool_registry,
)


async def _async_iter(items):
    for item in items:
        yield item


def _make_stream_chunk(
    content: str | None = None,
    tool_calls: list[object] | None = None,
    finish_reason: str | None = None,
) -> MagicMock:
    mock = MagicMock()
    choice = MagicMock()
    choice.delta.content = content
    choice.delta.reasoning_content = None
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
    tc_delta = MagicMock()
    tc_delta.index = index
    tc_delta.id = call_id
    if name is None and arguments is None:
        tc_delta.function = None
    else:
        tc_delta.function.name = name
        tc_delta.function.arguments = arguments
    return tc_delta


class _NoCallProvider:
    def __init__(self) -> None:
        self.call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        raise AssertionError("unused")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self.call_count += 1
        if False:
            yield None


class TestToolProtocolGuardrailsIntegration:
    @pytest.mark.asyncio
    async def test_duplicate_id_stream_executes_tool_once(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _async_iter(
                [
                    _make_stream_chunk(
                        tool_calls=[
                            _make_tool_call_delta(
                                index=0,
                                call_id="tc-dup",
                                name="test_tool",
                                arguments='{"x": 1}',
                            )
                        ]
                    ),
                    _make_stream_chunk(
                        tool_calls=[
                            _make_tool_call_delta(
                                index=1,
                                call_id="tc-dup",
                                name="test_tool",
                                arguments='{"x": 1}',
                            )
                        ]
                    ),
                    _make_stream_chunk(finish_reason="tool_calls"),
                ]
            ),
            _async_iter(
                [
                    _make_stream_chunk(content="done", finish_reason="stop"),
                ]
            ),
        ]
        provider._client = mock_client

        registry, tools = _make_tool_registry(tool_names=["test_tool"])
        spec = _make_spec(provider=provider, tool_registry=registry, max_turns=2)
        kernel = AgentKernel()

        async for _event in kernel.run_stream(spec, "run test"):
            pass

        assert tools[0].calls == [("test_tool", {"x": 1})]
        assert mock_client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_pure_tool_turn_sends_empty_string_content_on_second_call(
        self,
    ) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _async_iter(
                [
                    _make_stream_chunk(
                        tool_calls=[
                            _make_tool_call_delta(
                                index=0,
                                call_id="tc-1",
                                name="test_tool",
                                arguments='{"x": 1}',
                            )
                        ]
                    ),
                    _make_stream_chunk(finish_reason="tool_calls"),
                ]
            ),
            _async_iter(
                [
                    _make_stream_chunk(content="done", finish_reason="stop"),
                ]
            ),
        ]
        provider._client = mock_client

        registry, _ = _make_tool_registry(tool_names=["test_tool"])
        spec = _make_spec(provider=provider, tool_registry=registry, max_turns=2)
        kernel = AgentKernel()

        async for _event in kernel.run_stream(spec, "run test"):
            pass

        second_call_kwargs = mock_client.chat.completions.create.await_args_list[1].kwargs
        assistant_turn = second_call_kwargs["messages"][2]

        assert assistant_turn["role"] == "assistant"
        assert assistant_turn["content"] == ""
        assert assistant_turn["tool_calls"][0]["id"] == "tc-1"

    @pytest.mark.asyncio
    async def test_malformed_history_fails_before_provider_call(self) -> None:
        provider = _NoCallProvider()
        registry, _ = _make_tool_registry(tool_names=["test_tool"])
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()
        history = [
            AssistantMessage(content=None),
            ToolMessage(tool_call_id="orphan", tool_name="test_tool", content="oops"),
        ]

        with pytest.raises(LLMError, match="orphan tool message"):
            async for _event in kernel.run_stream(spec, "next turn", history=history):
                pass

        assert provider.call_count == 0
