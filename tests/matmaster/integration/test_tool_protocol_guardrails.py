from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from matmaster.core.agent import AgentKernel
from matmaster.providers.openai_provider import OpenAIProvider
from matmaster.types.errors import LLMError
from matmaster.types.messages import AssistantMessage, ToolMessage
from src.services.chat_history import ChatHistoryConverter
from tests.matmaster.core.agent_kernel_test_helpers import (
    _make_tool_registry,
    make_kernel_runtime,
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
        kernel_runtime = make_kernel_runtime(
            provider=provider, tool_registry=registry, max_turns=2
        )
        kernel = AgentKernel()

        async for _event in kernel.run_stream(kernel_runtime, "run test"):
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
        kernel_runtime = make_kernel_runtime(
            provider=provider, tool_registry=registry, max_turns=2
        )
        kernel = AgentKernel()

        async for _event in kernel.run_stream(kernel_runtime, "run test"):
            pass

        second_call_kwargs = mock_client.chat.completions.create.await_args_list[
            1
        ].kwargs
        assistant_turn = second_call_kwargs["messages"][2]

        assert assistant_turn["role"] == "assistant"
        assert assistant_turn["content"] == ""
        assert assistant_turn["tool_calls"][0]["id"] == "tc-1"

    @pytest.mark.asyncio
    async def test_malformed_history_fails_before_provider_call(self) -> None:
        provider = _NoCallProvider()
        registry, _ = _make_tool_registry(tool_names=["test_tool"])
        kernel_runtime = make_kernel_runtime(provider=provider, tool_registry=registry)
        kernel = AgentKernel()
        history = [
            AssistantMessage(content=None),
            ToolMessage(tool_call_id="orphan", tool_name="test_tool", content="oops"),
        ]

        with pytest.raises(LLMError, match="orphan tool message"):
            async for _event in kernel.run_stream(
                kernel_runtime, "next turn", history=history
            ):
                pass

        assert provider.call_count == 0

    @pytest.mark.asyncio
    async def test_history_with_missing_tool_result_fails_before_provider_call(
        self,
    ) -> None:
        provider = _NoCallProvider()
        registry, _ = _make_tool_registry(tool_names=["test_tool"])
        kernel_runtime = make_kernel_runtime(provider=provider, tool_registry=registry)
        kernel = AgentKernel()
        history = [
            AssistantMessage(
                content=None,
                tool_calls=[
                    {"id": "tc-1", "name": "test_tool", "arguments": {"x": 1}},
                    {"id": "tc-2", "name": "test_tool", "arguments": {"x": 2}},
                ],
            ),
            ToolMessage(tool_call_id="tc-1", tool_name="test_tool", content="ok"),
        ]

        with pytest.raises(LLMError, match="missing tool_result ids"):
            async for _event in kernel.run_stream(
                kernel_runtime, "next turn", history=history
            ):
                pass

        assert provider.call_count == 0

    @pytest.mark.asyncio
    async def test_wrapped_assistant_state_history_normalizes_none_content_before_provider_call(
        self,
    ) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(content="done", finish_reason="stop"),
            ]
        )
        provider._client = mock_client

        registry, _ = _make_tool_registry(tool_names=["test_tool"])
        kernel_runtime = make_kernel_runtime(
            provider=provider, tool_registry=registry, max_turns=1
        )
        kernel = AgentKernel()

        history = ChatHistoryConverter.events_to_messages(
            [
                {"source": "User", "type": "query", "content": "previous turn"},
                {
                    "source": "MatMaster",
                    "type": "assistant_state",
                    "content": {
                        "state": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "tc-1",
                                    "name": "test_tool",
                                    "arguments": {"x": 1},
                                }
                            ],
                        }
                    },
                },
                {
                    "source": "MatMaster",
                    "type": "tool_result",
                    "content": {"id": "tc-1", "name": "test_tool", "result": "ok"},
                },
            ]
        )

        assert isinstance(history[1], AssistantMessage)
        assert history[1].content is None

        async for _event in kernel.run_stream(
            kernel_runtime, "next turn", history=history
        ):
            pass

        first_call_kwargs = mock_client.chat.completions.create.await_args.kwargs
        assistant_turn = next(
            m
            for m in first_call_kwargs["messages"]
            if m["role"] == "assistant" and m.get("tool_calls")
        )
        assert assistant_turn["content"] == ""
        assert assistant_turn["tool_calls"][0]["id"] == "tc-1"
