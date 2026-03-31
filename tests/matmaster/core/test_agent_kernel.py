"""AgentKernel tests: termination, hooks, guards, streaming, history, execution order."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from typing import Any

import pytest

from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
from matmaster.types.runtime import KernelResult

from .agent_kernel_test_helpers import (
    ChunkRecordingHook,
    DenyGuard,
    RecordingHook,
    SkipHook,
    StopHook,
    StreamingProvider,
    ToolCallingProvider,
    _make_spec,
    _make_tool_registry,
)


class TestNaturalFinish:
    """LLM returns no tool_calls -> FinishEvent(reason='natural')."""

    @pytest.mark.asyncio
    async def test_natural_finish(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(content='Hello'),
                StreamChunk(finish_reason='stop'),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test task')

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == 'natural'
        assert result.result.final_content == 'Hello'

    @pytest.mark.asyncio
    async def test_natural_finish_messages(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(content='Hello'),
                StreamChunk(finish_reason='stop'),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test task')

        assert result.result.reason == 'natural'


class TestMaxTurns:
    """LLM always returns tool_calls, max_turns reached."""

    @pytest.mark.asyncio
    async def test_max_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id='tc-1', name='some_tool', arguments={'x': 1})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=999)
        spec = _make_spec(provider=provider, max_turns=2)
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test')

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == 'max_turns'


class TestExternalCancel:
    """stop_event.set() -> FinishEvent(reason='cancelled')."""

    @pytest.mark.asyncio
    async def test_cancel_before_run(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()
        stop_event.set()
        spec = _make_spec()
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test', stop_event=stop_event)

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == 'cancelled'

    @pytest.mark.asyncio
    async def test_cancel_during_run(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()

        class CancelAfterFirstTurnProvider:
            def __init__(self) -> None:
                self._call_count = 0

            async def __aenter__(self) -> CancelAfterFirstTurnProvider:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
            ) -> LLMResponse:
                return LLMResponse(content='unused', finish_reason='stop')

            async def chat_stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                self._call_count += 1
                if self._call_count == 1:
                    yield StreamChunk(
                        tool_call_deltas=[
                            {
                                'index': 0,
                                'id': 'tc-1',
                                'name': 'tool',
                                'arguments': '{}',
                            }
                        ],
                    )
                    yield StreamChunk(finish_reason='stop')
                    stop_event.set()
                else:
                    yield StreamChunk(content='done', finish_reason='stop')

        spec = _make_spec(provider=CancelAfterFirstTurnProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test', stop_event=stop_event)

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == 'cancelled'

    @pytest.mark.asyncio
    async def test_cancel_mid_stream(self) -> None:
        """stop_event set during chat_stream -> cancelled before turn completes."""
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()

        class ManyChunksProvider:
            async def __aenter__(self) -> ManyChunksProvider:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
            ) -> LLMResponse:
                return LLMResponse(content='unused', finish_reason='stop')

            async def chat_stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                for i in range(20):
                    if i == 8:
                        stop_event.set()
                    yield StreamChunk(content='x')
                yield StreamChunk(finish_reason='stop')

        spec = _make_spec(provider=ManyChunksProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test', stop_event=stop_event)

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == 'cancelled'
        assert result.result.num_turns == 1

    @pytest.mark.asyncio
    async def test_cancel_between_tool_turns(self) -> None:
        """stop_event set during tool execution -> cancelled at next turn boundary.

        With parallel tool execution, both approved tools in the same batch
        run concurrently via asyncio.gather. The stop_event set during
        execution is detected at the next turn's pre-loop check, not between
        tools within the same batch.
        """
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()

        class StopAfterFirst:
            @property
            def name(self) -> str:
                return 'first_tool'

            @property
            def description(self) -> str:
                return ''

            @property
            def json_schema(self) -> dict[str, Any]:
                return {'type': 'object', 'properties': {}}

            async def execute(self, arguments: dict[str, Any]) -> str:
                stop_event.set()
                return 'done-first'

        class SecondTool:
            @property
            def name(self) -> str:
                return 'second_tool'

            @property
            def description(self) -> str:
                return ''

            @property
            def json_schema(self) -> dict[str, Any]:
                return {'type': 'object', 'properties': {}}

            async def execute(self, arguments: dict[str, Any]) -> str:
                return 'done-second'

        class TwoDistinctToolCallsProvider:
            """Yields two tool calls with distinct stream indices."""

            async def __aenter__(self) -> TwoDistinctToolCallsProvider:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
            ) -> LLMResponse:
                return LLMResponse(content='unused', finish_reason='stop')

            async def chat_stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            'index': 0,
                            'id': 'tc-1',
                            'name': 'first_tool',
                            'arguments': '{}',
                        },
                    ],
                )
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            'index': 1,
                            'id': 'tc-2',
                            'name': 'second_tool',
                            'arguments': '{}',
                        },
                    ],
                )
                yield StreamChunk(finish_reason='stop')

        registry = ToolRegistry()
        registry.register(StopAfterFirst(), source='test')
        registry.register(SecondTool(), source='test')
        spec = _make_spec(
            provider=TwoDistinctToolCallsProvider(),
            tool_registry=registry,
            max_turns=5,
        )
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test', stop_event=stop_event)

        # Cancelled at next turn boundary after parallel tools complete
        assert result.result.reason == 'cancelled'


class TestHookStopped:
    """should_continue returns False -> FinishEvent(reason='hook_stopped')."""

    @pytest.mark.asyncio
    async def test_hook_stopped(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec(hooks=[StopHook()])
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test')

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == 'hook_stopped'


class TestGuardBlocks:
    """Guard blocks tool call -> BLOCKED message, hooks NOT triggered."""

    @pytest.mark.asyncio
    async def test_guard_blocks(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id='tc-1', name='bad_tool', arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content='ok'
        )
        recording = RecordingHook()
        tool_reg, tools = _make_tool_registry(['bad_tool'])
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            guards=[DenyGuard('bad_tool')],
            hooks=[recording],
            max_turns=5,
        )
        kernel = AgentKernel()
        await kernel.run(spec, 'test')

        # Tool should NOT have been executed
        bad_tool = tools[0]
        assert len(bad_tool.calls) == 0
        # Hooks pre_tool_call/post_tool_call should NOT be called for blocked tool
        assert 'pre_tool_call' not in recording.calls
        assert 'post_tool_call' not in recording.calls

    @pytest.mark.asyncio
    async def test_guard_block_triggers_hook(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.core.hooks import BaseHook
        from matmaster.types.guards import GuardResult

        class GuardBlockRecorder(BaseHook):
            def __init__(self) -> None:
                self.blocked: list[tuple[str, str | None]] = []

            async def on_guard_blocked(
                self, tool_call: ToolCallData, result: GuardResult
            ) -> None:
                self.blocked.append((tool_call.name, result.reason))

        tc = ToolCallData(id='tc-1', name='bad_tool', arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content='ok'
        )
        recorder = GuardBlockRecorder()
        tool_reg, _ = _make_tool_registry(['bad_tool'])
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            guards=[DenyGuard('bad_tool', reason='no access')],
            hooks=[recorder],
            max_turns=5,
        )
        kernel = AgentKernel()
        await kernel.run(spec, 'test')

        assert len(recorder.blocked) == 1
        assert recorder.blocked[0] == ('bad_tool', 'no access')


class TestHookSkip:
    """Hook SKIP -> tool NOT executed, ToolMessage with 'skipped by hook'."""

    @pytest.mark.asyncio
    async def test_hook_skip(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id='tc-1', name='skip_me', arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content='ok'
        )
        tool_reg, tools = _make_tool_registry(['skip_me'])
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            hooks=[SkipHook('skip_me')],
            max_turns=5,
        )
        kernel = AgentKernel()
        await kernel.run(spec, 'test')

        # Tool should NOT have been executed
        skip_tool = tools[0]
        assert len(skip_tool.calls) == 0


class TestStreamingAccumulation:
    """Provider yields chunks, kernel accumulates to LLMResponse."""

    @pytest.mark.asyncio
    async def test_streaming_accumulation(self) -> None:
        from matmaster.core.agent import AgentKernel

        chunk_hook = ChunkRecordingHook()
        provider = StreamingProvider(
            [
                StreamChunk(content='He'),
                StreamChunk(content='llo'),
                StreamChunk(finish_reason='stop'),
            ]
        )
        spec = _make_spec(provider=provider, hooks=[chunk_hook])
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test')

        assert result.result.reason == 'natural'
        assert result.result.final_content == 'Hello'
        assert [chunk.stream_state for chunk in chunk_hook.chunks] == [
            'start',
            'streaming',
            'streaming',
            'end',
        ]
        assert chunk_hook.chunks[0].stream_id == chunk_hook.chunks[-1].stream_id
        assert chunk_hook.chunks[1].content == 'He'
        assert chunk_hook.chunks[2].content == 'llo'


class TestFinishValidation:
    """Natural finish must validate terminal finish_reason before commit."""

    @pytest.mark.asyncio
    async def test_non_stop_finish_reason_does_not_commit_natural_finish(self) -> None:
        from matmaster.core.agent import AgentKernel

        chunk_hook = ChunkRecordingHook()
        provider = StreamingProvider(
            [
                StreamChunk(content='partial'),
                StreamChunk(finish_reason='length'),
            ]
        )
        spec = _make_spec(provider=provider, hooks=[chunk_hook])
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test')

        assert result.result.reason == 'invalid_finish'
        assert result.result.status == 'failed'
        assert result.result.final_content is None
        assert [chunk.stream_state for chunk in chunk_hook.chunks] == [
            'start',
            'streaming',
            'end',
        ]


class TestToolCallDelta:
    """Provider yields tool_call_deltas, kernel accumulates to ToolCallData."""

    @pytest.mark.asyncio
    async def test_tool_call_delta(self) -> None:
        from matmaster.core.agent import AgentKernel

        # Simulate streaming tool call deltas
        chunks = [
            StreamChunk(tool_call_deltas=[{'index': 0, 'id': 'tc1', 'name': 'fn'}]),
            StreamChunk(tool_call_deltas=[{'index': 0, 'arguments': '{"a":'}]),
            StreamChunk(tool_call_deltas=[{'index': 0, 'arguments': '1}'}]),
            StreamChunk(finish_reason='stop'),
        ]

        class TwoPhaseProvider:
            """First call returns tool calls, second returns content."""

            def __init__(self) -> None:
                self._call_count = 0

            async def __aenter__(self) -> TwoPhaseProvider:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
            ) -> LLMResponse:
                return LLMResponse(content='unused', finish_reason='stop')

            async def chat_stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                self._call_count += 1
                if self._call_count == 1:
                    for chunk in chunks:
                        yield chunk
                else:
                    yield StreamChunk(content='done', finish_reason='stop')

        tool_reg, tools = _make_tool_registry(['fn'])
        spec = _make_spec(provider=TwoPhaseProvider(), tool_registry=tool_reg)
        kernel = AgentKernel()
        await kernel.run(spec, 'test')

        # Tool should have been called with parsed arguments
        fn_tool = tools[0]
        assert len(fn_tool.calls) == 1
        assert fn_tool.calls[0][0] == 'fn'
        assert fn_tool.calls[0][1] == {'a': 1}


class TestToolCallDeltaDuplicateName:
    """Regression: proxy sends full name in multiple chunks must not concatenate."""

    @pytest.mark.asyncio
    async def test_duplicate_full_name_not_concatenated(self) -> None:
        """LiteLLM proxy may repeat full tool name across chunks.

        Before fix: 'use_skill' + 'use_skill' → 'use_skilluse_skill'
        After fix:  second chunk overwrites, name stays 'use_skill'
        """
        from matmaster.core.agent import AgentKernel

        chunks = [
            StreamChunk(
                tool_call_deltas=[
                    {'index': 0, 'id': 'tc1', 'name': 'fn', 'arguments': '{"a":'}
                ]
            ),
            # Proxy resends full name in a later chunk
            StreamChunk(
                tool_call_deltas=[{'index': 0, 'name': 'fn', 'arguments': '1}'}]
            ),
            StreamChunk(finish_reason='stop'),
        ]

        class TwoPhaseProvider:
            def __init__(self) -> None:
                self._call_count = 0

            async def __aenter__(self) -> TwoPhaseProvider:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
            ) -> LLMResponse:
                return LLMResponse(content='unused', finish_reason='stop')

            async def chat_stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                self._call_count += 1
                if self._call_count == 1:
                    for chunk in chunks:
                        yield chunk
                else:
                    yield StreamChunk(content='done', finish_reason='stop')

        tool_reg, tools = _make_tool_registry(['fn'])
        spec = _make_spec(provider=TwoPhaseProvider(), tool_registry=tool_reg)
        kernel = AgentKernel()
        await kernel.run(spec, 'test')

        fn_tool = tools[0]
        assert len(fn_tool.calls) == 1
        assert fn_tool.calls[0][0] == 'fn'  # NOT 'fnfn'
        assert fn_tool.calls[0][1] == {'a': 1}

    @pytest.mark.asyncio
    async def test_different_name_on_same_index_uses_last(self) -> None:
        """If proxy sends conflicting names on same index, keep last (not concat).

        Before fix: 'list_dir' + 'read_file' → 'list_dirread_file'
        After fix:  name = 'read_file' (last wins, at least a valid tool name)
        """
        from matmaster.core.agent import AgentKernel

        chunks = [
            StreamChunk(
                tool_call_deltas=[
                    {'index': 0, 'id': 'tc1', 'name': 'list_dir', 'arguments': '{}'}
                ]
            ),
            # Bug in proxy: second tool call reuses index 0
            StreamChunk(
                tool_call_deltas=[{'index': 0, 'name': 'read_file', 'arguments': ''}]
            ),
            StreamChunk(finish_reason='stop'),
        ]

        class TwoPhaseProvider:
            def __init__(self) -> None:
                self._call_count = 0

            async def __aenter__(self) -> TwoPhaseProvider:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
            ) -> LLMResponse:
                return LLMResponse(content='unused', finish_reason='stop')

            async def chat_stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                self._call_count += 1
                if self._call_count == 1:
                    for chunk in chunks:
                        yield chunk
                else:
                    yield StreamChunk(content='done', finish_reason='stop')

        tool_reg, tools = _make_tool_registry(['list_dir', 'read_file'])
        spec = _make_spec(provider=TwoPhaseProvider(), tool_registry=tool_reg)
        kernel = AgentKernel()
        await kernel.run(spec, 'test')

        # Should execute with a valid tool name, not 'list_dirread_file'
        all_calls = []
        for t in tools:
            all_calls.extend(t.calls)
        assert len(all_calls) == 1
        assert all_calls[0][0] in ('list_dir', 'read_file')
        assert all_calls[0][0] != 'list_dirread_file'


class TestFullCycle:
    """Turn 1: tool_call -> execute. Turn 2: natural finish."""

    @pytest.mark.asyncio
    async def test_full_cycle(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id='tc-1', name='my_tool', arguments={'key': 'val'})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content='final answer'
        )
        tool_reg, tools = _make_tool_registry(['my_tool'], result='tool output')
        recording = RecordingHook()
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            hooks=[recording],
            max_turns=10,
        )
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test')

        assert result.result.reason == 'natural'
        assert result.result.final_content == 'final answer'
        my_tool = tools[0]
        assert len(my_tool.calls) == 1
        assert my_tool.calls[0][0] == 'my_tool'


class TestHistoryParameter:
    """AgentKernel.run() with history parameter."""

    @pytest.mark.asyncio
    async def test_history_inserts_between_system_and_user(self) -> None:
        """history messages are placed between SystemMessage and UserMessage(task)."""
        from matmaster.core.agent import AgentKernel

        captured_messages: list[list[dict[str, Any]]] = []

        class CapturingProvider:
            async def __aenter__(self) -> CapturingProvider:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
            ) -> LLMResponse:
                return LLMResponse(content='unused', finish_reason='stop')

            async def chat_stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                captured_messages.append(messages)
                yield StreamChunk(content='ok', finish_reason='stop')

        history = [
            UserMessage(content='hi'),
            AssistantMessage(content='hello'),
        ]
        spec = _make_spec(provider=CapturingProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, 'new question', history=history)

        assert result.result.reason == 'natural'
        # Check captured API messages structure
        msgs = captured_messages[0]
        assert msgs[0]['role'] == 'system'
        assert msgs[1]['role'] == 'user'
        assert msgs[1]['content'] == 'hi'
        assert msgs[2]['role'] == 'assistant'
        assert msgs[2]['content'] == 'hello'
        assert msgs[3]['role'] == 'user'
        assert msgs[3]['content'] == 'new question'

    @pytest.mark.asyncio
    async def test_history_none_is_backward_compatible(self) -> None:
        """history=None produces [SystemMessage, UserMessage(task)]."""
        from matmaster.core.agent import AgentKernel

        captured_messages: list[list[dict[str, Any]]] = []

        class CapturingProvider:
            async def __aenter__(self) -> CapturingProvider:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
            ) -> LLMResponse:
                return LLMResponse(content='unused', finish_reason='stop')

            async def chat_stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                captured_messages.append(messages)
                yield StreamChunk(content='ok', finish_reason='stop')

        spec = _make_spec(provider=CapturingProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test task', history=None)

        assert result.result.reason == 'natural'
        msgs = captured_messages[0]
        assert len(msgs) == 2
        assert msgs[0]['role'] == 'system'
        assert msgs[1]['role'] == 'user'
        assert msgs[1]['content'] == 'test task'

    @pytest.mark.asyncio
    async def test_empty_history_is_backward_compatible(self) -> None:
        """history=[] produces [SystemMessage, UserMessage(task)]."""
        from matmaster.core.agent import AgentKernel

        captured_messages: list[list[dict[str, Any]]] = []

        class CapturingProvider:
            async def __aenter__(self) -> CapturingProvider:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
            ) -> LLMResponse:
                return LLMResponse(content='unused', finish_reason='stop')

            async def chat_stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                captured_messages.append(messages)
                yield StreamChunk(content='ok', finish_reason='stop')

        spec = _make_spec(provider=CapturingProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test task', history=[])

        assert result.result.reason == 'natural'
        msgs = captured_messages[0]
        assert len(msgs) == 2
        assert msgs[0]['role'] == 'system'
        assert msgs[1]['role'] == 'user'
        assert msgs[1]['content'] == 'test task'


class TestExecutionOrder:
    """Recording hook tracks correct call order."""

    @pytest.mark.asyncio
    async def test_execution_order(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id='tc-1', name='tool', arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content='done'
        )
        recording = RecordingHook()
        tool_reg, _ = _make_tool_registry(['tool'])
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            hooks=[recording],
            max_turns=10,
        )
        kernel = AgentKernel()
        await kernel.run(spec, 'test')

        # Turn 1: pre_llm_call -> should_continue -> on_stream_chunk(s) -> pre_tool_call -> post_tool_call
        # Turn 2: pre_llm_call -> should_continue -> on_stream_chunk(s) -> natural finish
        assert recording.calls[0] == 'pre_llm_call'
        assert recording.calls[1] == 'should_continue'
        # on_stream_chunk called at least once
        assert 'on_stream_chunk' in recording.calls
        assert 'pre_tool_call' in recording.calls
        assert 'post_tool_call' in recording.calls
        # pre_tool_call comes after on_stream_chunk for turn 1
        first_stream = recording.calls.index('on_stream_chunk')
        first_pre_tool = recording.calls.index('pre_tool_call')
        assert first_stream < first_pre_tool


class TestKernelRunResultMessages:
    """kernel.run() returns KernelRunResult with message transcript."""

    @pytest.mark.asyncio
    async def test_natural_finish_returns_messages(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.types.runtime import KernelRunResult

        provider = StreamingProvider(
            [
                StreamChunk(content='Hello'),
                StreamChunk(finish_reason='stop'),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test task')

        assert isinstance(result, KernelRunResult)
        assert result.result.reason == 'natural'
        # Messages: [SystemMessage, UserMessage, AssistantMessage]
        assert len(result.messages) == 3
        assert isinstance(result.messages[0], SystemMessage)
        assert isinstance(result.messages[1], UserMessage)
        assert isinstance(result.messages[2], AssistantMessage)
        assert result.messages[2].content == 'Hello'

    @pytest.mark.asyncio
    async def test_tool_cycle_returns_all_messages(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.types.runtime import KernelRunResult

        tc = ToolCallData(id='tc-1', name='my_tool', arguments={'key': 'val'})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content='final'
        )
        tool_reg, _ = _make_tool_registry(['my_tool'], result='tool output')
        spec = _make_spec(provider=provider, tool_registry=tool_reg, max_turns=10)
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test')

        assert isinstance(result, KernelRunResult)
        assert result.result.reason == 'natural'
        # Messages: System, User, Assistant(tool_calls), ToolMessage, Assistant(final)
        assert len(result.messages) == 5
        assert isinstance(result.messages[2], AssistantMessage)
        assert result.messages[2].tool_calls is not None
        assert isinstance(result.messages[3], ToolMessage)
        assert isinstance(result.messages[4], AssistantMessage)
        assert result.messages[4].content == 'final'
