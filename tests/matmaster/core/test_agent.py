"""Tests for AgentKernel execution loop.

Covers all termination paths (natural, max_turns, cancelled, hook_stopped),
guard blocking, hook SKIP, streaming accumulation, tool call delta
reassembly, full cycle, and execution order verification.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, AsyncIterator
from unittest.mock import MagicMock  # noqa: F401 -- kept for potential test use

import pytest

from matmaster.tools.tool_result import ToolResult
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.errors import LLMError
from matmaster.types.guards import Guard, GuardContext, GuardResult
from matmaster.types.runtime import AgentRuntimeSpec, KernelResult
from matmaster.core.hooks import BaseHook, HookAction
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    Message,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)

from .conftest import MockLLMProvider


# ── Helper fixtures ─────────────────────────────────────


class _CatchAllTool:
    """Tool that accepts any name and records calls for test assertions."""

    def __init__(self, result: str = "tool result") -> None:
        self._result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "catch-all test tool"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> str:
        self.calls.append((self._name, arguments))
        return self._result


def _make_tool_registry(
    tool_names: list[str] | None = None,
    result: str = "tool result",
) -> tuple[ToolRegistry, list[_CatchAllTool]]:
    """Create a ToolRegistry with named catch-all tools.

    Returns (registry, tools) so tests can inspect tool.calls.
    """
    registry = ToolRegistry()
    names = tool_names or ["test_tool", "some_tool", "bad_tool", "skip_me", "my_tool", "fn", "tool"]
    tools: list[_CatchAllTool] = []
    for n in names:
        t = _CatchAllTool(result=result)
        t._name = n
        tools.append(t)
        registry.register(t, source="test")
    return registry, tools


class StreamingProvider:
    """Mock provider that yields specific StreamChunk sequences."""

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        for chunk in self._chunks:
            yield chunk


class ToolCallingProvider:
    """Provider that returns tool_calls for N turns, then natural finish."""

    def __init__(
        self,
        tool_calls: list[ToolCallData],
        max_tool_turns: int = 999,
        final_content: str = "done",
    ) -> None:
        self._tool_calls = tool_calls
        self._max_tool_turns = max_tool_turns
        self._final_content = final_content
        self._call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self._call_count += 1
        if self._call_count <= self._max_tool_turns:
            for i, tc in enumerate(self._tool_calls):
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": i,
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": str(tc.arguments).replace("'", '"'),
                        }
                    ],
                )
            yield StreamChunk(finish_reason="stop")
        else:
            yield StreamChunk(content=self._final_content, finish_reason="stop")


class MultiToolProvider:
    """Provider that returns multiple tool_calls on first turn, then finishes.

    Unlike ToolCallingProvider, this always does exactly 1 tool turn then natural finish.
    Designed for parallel dispatch testing where precise tool_call control matters.
    """

    def __init__(self, tool_calls: list[ToolCallData], final_content: str = "done") -> None:
        self._tool_calls = tool_calls
        self._final_content = final_content
        self._call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None) -> AsyncIterator[StreamChunk]:
        self._call_count += 1
        if self._call_count == 1:
            for i, tc in enumerate(self._tool_calls):
                yield StreamChunk(
                    tool_call_deltas=[{
                        "index": i,
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": str(tc.arguments).replace("'", '"'),
                    }],
                )
            yield StreamChunk(finish_reason="stop")
        else:
            yield StreamChunk(content=self._final_content, finish_reason="stop")


class DenyGuard:
    """Guard that denies a specific tool name."""

    def __init__(self, deny_name: str, reason: str = "forbidden") -> None:
        self._deny_name = deny_name
        self._reason = reason

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        if ctx.tool_name == self._deny_name:
            return GuardResult(
                allowed=False,
                reason=self._reason,
                guidance="stop",
            )
        return GuardResult(allowed=True)


class SkipHook(BaseHook):
    """Hook that returns SKIP for a specific tool name."""

    def __init__(self, skip_name: str) -> None:
        self._skip_name = skip_name

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        if tool_call.name == self._skip_name:
            return HookAction.SKIP
        return HookAction.CONTINUE


class StopHook(BaseHook):
    """Hook that returns False from should_continue."""

    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        return False


class RecordingHook(BaseHook):
    """Hook that records all method calls in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self.calls.append("pre_tool_call")
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        self.calls.append("post_tool_call")

    async def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        self.calls.append("pre_llm_call")

    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        self.calls.append("should_continue")
        return True

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        self.calls.append("on_stream_chunk")


class ChunkRecordingHook(BaseHook):
    """Hook that records stream chunks."""

    def __init__(self) -> None:
        self.chunks: list[StreamChunk] = []

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        self.chunks.append(chunk)


class SegmentRecordingHook(BaseHook):
    """Hook that records completed logical segments."""

    def __init__(self) -> None:
        self.segments: list[tuple[str, str, str | None]] = []

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None:
        self.segments.append((segment_type, content, stream_id))


def _make_spec(
    *,
    provider: Any | None = None,
    tool_registry: ToolRegistry | None = None,
    guards: list[Any] | None = None,
    hooks: list[Any] | None = None,
    max_turns: int = 10,
    system_prompt: str = "You are a test agent",
) -> AgentRuntimeSpec:
    if tool_registry is None:
        tool_registry, _ = _make_tool_registry()
    return AgentRuntimeSpec(
        llm_provider=provider or MockLLMProvider(),
        tool_registry=tool_registry,
        guards=guards or [],
        hooks=hooks or [],
        max_turns=max_turns,
        system_prompt=system_prompt,
    )


# ── Tests ───────────────────────────────────────────────


class TestNaturalFinish:
    """LLM returns no tool_calls -> FinishEvent(reason='natural')."""

    async def test_natural_finish(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="Hello"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test task")

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "natural"
        assert result.result.final_content == "Hello"

    async def test_natural_finish_messages(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="Hello"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test task")

        assert result.result.reason == "natural"


class TestMaxTurns:
    """LLM always returns tool_calls, max_turns reached."""

    async def test_max_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="some_tool", arguments={"x": 1})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=999)
        spec = _make_spec(provider=provider, max_turns=2)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "max_turns"


class TestExternalCancel:
    """stop_event.set() -> FinishEvent(reason='cancelled')."""

    async def test_cancel_before_run(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()
        stop_event.set()
        spec = _make_spec()
        kernel = AgentKernel()
        result = await kernel.run(spec, "test", stop_event=stop_event)

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "cancelled"

    async def test_cancel_during_run(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()

        class CancelAfterFirstTurnProvider:
            def __init__(self) -> None:
                self._call_count = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            async def chat_stream(
                self,
                messages: list,
                tools: list | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                self._call_count += 1
                if self._call_count == 1:
                    yield StreamChunk(
                        tool_call_deltas=[
                            {"index": 0, "id": "tc-1", "name": "tool", "arguments": "{}"}
                        ],
                    )
                    yield StreamChunk(finish_reason="stop")
                    stop_event.set()
                else:
                    yield StreamChunk(content="done", finish_reason="stop")

        spec = _make_spec(provider=CancelAfterFirstTurnProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, "test", stop_event=stop_event)

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "cancelled"


class TestHookStopped:
    """should_continue returns False -> FinishEvent(reason='hook_stopped')."""

    async def test_hook_stopped(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec(hooks=[StopHook()])
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "hook_stopped"


class TestGuardBlocks:
    """Guard blocks tool call -> BLOCKED message, hooks NOT triggered."""

    async def test_guard_blocks(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="bad_tool", arguments={})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=1, final_content="ok")
        recording = RecordingHook()
        tool_reg, tools = _make_tool_registry(["bad_tool"])
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            guards=[DenyGuard("bad_tool")],
            hooks=[recording],
            max_turns=5,
        )
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        # Tool should NOT have been executed
        bad_tool = tools[0]
        assert len(bad_tool.calls) == 0
        # Hooks pre_tool_call/post_tool_call should NOT be called for blocked tool
        assert "pre_tool_call" not in recording.calls
        assert "post_tool_call" not in recording.calls

    async def test_guard_block_triggers_hook(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.core.hooks import BaseHook
        from matmaster.types.guards import GuardResult

        class GuardBlockRecorder(BaseHook):
            def __init__(self) -> None:
                self.blocked: list[tuple[str, str | None]] = []

            async def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None:
                self.blocked.append((tool_call.name, result.reason))

        tc = ToolCallData(id="tc-1", name="bad_tool", arguments={})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=1, final_content="ok")
        recorder = GuardBlockRecorder()
        tool_reg, _ = _make_tool_registry(["bad_tool"])
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            guards=[DenyGuard("bad_tool", reason="no access")],
            hooks=[recorder],
            max_turns=5,
        )
        kernel = AgentKernel()
        await kernel.run(spec, "test")

        assert len(recorder.blocked) == 1
        assert recorder.blocked[0] == ("bad_tool", "no access")


class TestHookSkip:
    """Hook SKIP -> tool NOT executed, ToolMessage with 'skipped by hook'."""

    async def test_hook_skip(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="skip_me", arguments={})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=1, final_content="ok")
        tool_reg, tools = _make_tool_registry(["skip_me"])
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            hooks=[SkipHook("skip_me")],
            max_turns=5,
        )
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        # Tool should NOT have been executed
        skip_tool = tools[0]
        assert len(skip_tool.calls) == 0


class TestStreamingAccumulation:
    """Provider yields chunks, kernel accumulates to LLMResponse."""

    async def test_streaming_accumulation(self) -> None:
        from matmaster.core.agent import AgentKernel

        chunk_hook = ChunkRecordingHook()
        provider = StreamingProvider([
            StreamChunk(content="He"),
            StreamChunk(content="llo"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider, hooks=[chunk_hook])
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.reason == "natural"
        assert result.result.final_content == "Hello"
        assert [chunk.stream_state for chunk in chunk_hook.chunks] == [
            "start",
            "streaming",
            "streaming",
            "end",
        ]
        assert chunk_hook.chunks[0].stream_id == chunk_hook.chunks[-1].stream_id
        assert chunk_hook.chunks[1].content == "He"
        assert chunk_hook.chunks[2].content == "llo"


class TestFinishValidation:
    """Natural finish must validate terminal finish_reason before commit."""

    async def test_non_stop_finish_reason_does_not_commit_natural_finish(self) -> None:
        from matmaster.core.agent import AgentKernel

        chunk_hook = ChunkRecordingHook()
        provider = StreamingProvider([
            StreamChunk(content="partial"),
            StreamChunk(finish_reason="length"),
        ])
        spec = _make_spec(provider=provider, hooks=[chunk_hook])
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.reason == "invalid_finish"
        assert result.result.status == "failed"
        assert result.result.final_content is None
        assert [chunk.stream_state for chunk in chunk_hook.chunks] == [
            "start",
            "streaming",
            "end",
        ]


class TestToolCallDelta:
    """Provider yields tool_call_deltas, kernel accumulates to ToolCallData."""

    async def test_tool_call_delta(self) -> None:
        from matmaster.core.agent import AgentKernel

        # Simulate streaming tool call deltas
        chunks = [
            StreamChunk(tool_call_deltas=[{"index": 0, "id": "tc1", "name": "fn"}]),
            StreamChunk(tool_call_deltas=[{"index": 0, "arguments": '{"a":'}]),
            StreamChunk(tool_call_deltas=[{"index": 0, "arguments": "1}"}]),
            StreamChunk(finish_reason="stop"),
        ]

        class TwoPhaseProvider:
            """First call returns tool calls, second returns content."""

            def __init__(self) -> None:
                self._call_count = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            async def chat_stream(
                self,
                messages: list,
                tools: list | None = None,
                *,
                timeout: float | None = None,
            ) -> AsyncIterator[StreamChunk]:
                self._call_count += 1
                if self._call_count == 1:
                    for chunk in chunks:
                        yield chunk
                else:
                    yield StreamChunk(content="done", finish_reason="stop")

        tool_reg, tools = _make_tool_registry(["fn"])
        spec = _make_spec(provider=TwoPhaseProvider(), tool_registry=tool_reg)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        # Tool should have been called with parsed arguments
        fn_tool = tools[0]
        assert len(fn_tool.calls) == 1
        assert fn_tool.calls[0][0] == "fn"
        assert fn_tool.calls[0][1] == {"a": 1}


class TestFullCycle:
    """Turn 1: tool_call -> execute. Turn 2: natural finish."""

    async def test_full_cycle(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="my_tool", arguments={"key": "val"})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="final answer"
        )
        tool_reg, tools = _make_tool_registry(["my_tool"], result="tool output")
        recording = RecordingHook()
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            hooks=[recording],
            max_turns=10,
        )
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.reason == "natural"
        assert result.result.final_content == "final answer"
        my_tool = tools[0]
        assert len(my_tool.calls) == 1
        assert my_tool.calls[0][0] == "my_tool"


class TestHistoryParameter:
    """AgentKernel.run() with history parameter."""

    async def test_history_inserts_between_system_and_user(self) -> None:
        """history messages are placed between SystemMessage and UserMessage(task)."""
        from matmaster.core.agent import AgentKernel

        captured_messages: list[list[dict[str, Any]]] = []

        class CapturingProvider:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            async def chat_stream(self, messages: list, tools: list | None = None, *, timeout: float | None = None) -> AsyncIterator[StreamChunk]:
                captured_messages.append(messages)
                yield StreamChunk(content="ok", finish_reason="stop")

        history = [
            UserMessage(content="hi"),
            AssistantMessage(content="hello"),
        ]
        spec = _make_spec(provider=CapturingProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, "new question", history=history)

        assert result.result.reason == "natural"
        # Check captured API messages structure
        msgs = captured_messages[0]
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "hi"
        assert msgs[2]["role"] == "assistant"
        assert msgs[2]["content"] == "hello"
        assert msgs[3]["role"] == "user"
        assert msgs[3]["content"] == "new question"

    async def test_history_none_is_backward_compatible(self) -> None:
        """history=None produces [SystemMessage, UserMessage(task)]."""
        from matmaster.core.agent import AgentKernel

        captured_messages: list[list[dict[str, Any]]] = []

        class CapturingProvider:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            async def chat_stream(self, messages: list, tools: list | None = None, *, timeout: float | None = None) -> AsyncIterator[StreamChunk]:
                captured_messages.append(messages)
                yield StreamChunk(content="ok", finish_reason="stop")

        spec = _make_spec(provider=CapturingProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, "test task", history=None)

        assert result.result.reason == "natural"
        msgs = captured_messages[0]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "test task"

    async def test_empty_history_is_backward_compatible(self) -> None:
        """history=[] produces [SystemMessage, UserMessage(task)]."""
        from matmaster.core.agent import AgentKernel

        captured_messages: list[list[dict[str, Any]]] = []

        class CapturingProvider:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            async def chat_stream(self, messages: list, tools: list | None = None, *, timeout: float | None = None) -> AsyncIterator[StreamChunk]:
                captured_messages.append(messages)
                yield StreamChunk(content="ok", finish_reason="stop")

        spec = _make_spec(provider=CapturingProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, "test task", history=[])

        assert result.result.reason == "natural"
        msgs = captured_messages[0]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "test task"


class TestExecutionOrder:
    """Recording hook tracks correct call order."""

    async def test_execution_order(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="tool", arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="done"
        )
        recording = RecordingHook()
        tool_reg, _ = _make_tool_registry(["tool"])
        spec = _make_spec(
            provider=provider,
            tool_registry=tool_reg,
            hooks=[recording],
            max_turns=10,
        )
        kernel = AgentKernel()
        await kernel.run(spec, "test")

        # Turn 1: pre_llm_call -> should_continue -> on_stream_chunk(s) -> pre_tool_call -> post_tool_call
        # Turn 2: pre_llm_call -> should_continue -> on_stream_chunk(s) -> natural finish
        assert recording.calls[0] == "pre_llm_call"
        assert recording.calls[1] == "should_continue"
        # on_stream_chunk called at least once
        assert "on_stream_chunk" in recording.calls
        assert "pre_tool_call" in recording.calls
        assert "post_tool_call" in recording.calls
        # pre_tool_call comes after on_stream_chunk for turn 1
        first_stream = recording.calls.index("on_stream_chunk")
        first_pre_tool = recording.calls.index("pre_tool_call")
        assert first_stream < first_pre_tool


class TestKernelRunResultMessages:
    """kernel.run() returns KernelRunResult with message transcript."""

    async def test_natural_finish_returns_messages(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.types.runtime import KernelRunResult

        provider = StreamingProvider([
            StreamChunk(content="Hello"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test task")

        assert isinstance(result, KernelRunResult)
        assert result.result.reason == "natural"
        # Messages: [SystemMessage, UserMessage, AssistantMessage]
        assert len(result.messages) == 3
        assert isinstance(result.messages[0], SystemMessage)
        assert isinstance(result.messages[1], UserMessage)
        assert isinstance(result.messages[2], AssistantMessage)
        assert result.messages[2].content == "Hello"

    async def test_tool_cycle_returns_all_messages(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.types.runtime import KernelRunResult

        tc = ToolCallData(id="tc-1", name="my_tool", arguments={"key": "val"})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="final"
        )
        tool_reg, _ = _make_tool_registry(["my_tool"], result="tool output")
        spec = _make_spec(provider=provider, tool_registry=tool_reg, max_turns=10)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert isinstance(result, KernelRunResult)
        assert result.result.reason == "natural"
        # Messages: System, User, Assistant(tool_calls), ToolMessage, Assistant(final)
        assert len(result.messages) == 5
        assert isinstance(result.messages[2], AssistantMessage)
        assert result.messages[2].tool_calls is not None
        assert isinstance(result.messages[3], ToolMessage)
        assert isinstance(result.messages[4], AssistantMessage)
        assert result.messages[4].content == "final"


class TestToolExecutionException:
    """Tool that raises exception -> error ToolMessage, run continues."""

    async def test_tool_exception_becomes_error_message(self) -> None:
        from matmaster.core.agent import AgentKernel

        class ExplodingTool:
            @property
            def name(self) -> str:
                return "boom"

            @property
            def description(self) -> str:
                return "explodes"

            @property
            def json_schema(self) -> dict[str, Any]:
                return {"type": "object", "properties": {}}

            async def execute(self, arguments: dict[str, Any]) -> str:
                raise RuntimeError("kaboom!")

        registry = ToolRegistry()
        registry.register(ExplodingTool(), source="test")

        tc = ToolCallData(id="tc-1", name="boom", arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="recovered"
        )
        spec = _make_spec(provider=provider, tool_registry=registry, max_turns=5)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.reason == "natural"
        assert result.result.final_content == "recovered"


class TestCallLlmUsageCapture:
    """_call_llm captures usage from StreamChunk into LLMResponse."""

    async def test_usage_captured_from_stream(self) -> None:
        from matmaster.core.agent import AgentKernel

        usage_data = {
            "prompt_tokens": 500,
            "completion_tokens": 100,
            "total_tokens": 600,
        }

        class UsageProvider:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages, tools=None):
                return LLMResponse(content="unused", finish_reason="stop")

            async def chat_stream(self, messages, tools=None, *, timeout: float | None = None):
                yield StreamChunk(content="hello")
                yield StreamChunk(finish_reason="stop", usage=usage_data)

        spec = _make_spec(provider=UsageProvider())
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.reason == "natural"
        response = await kernel._call_llm(spec, [UserMessage(content="test")])
        assert response.usage == usage_data

    async def test_segment_complete_hooks_run_for_reasoning_and_response(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(reasoning_content="think "),
                StreamChunk(content="answer"),
                StreamChunk(finish_reason="stop"),
            ]
        )
        segment_hook = SegmentRecordingHook()
        spec = _make_spec(provider=provider, hooks=[segment_hook])
        kernel = AgentKernel()

        response = await kernel._call_llm(spec, [UserMessage(content="test")])

        assert response.reasoning_content == "think "
        assert response.content == "answer"
        assert segment_hook.segments == [
            ("thought", "think ", "turn-1"),
            ("response", "answer", "turn-1"),
        ]


class TestCompactorIntegration:
    """Kernel calls compactor.compact_if_needed and update_message_count."""

    async def test_compactor_called_each_turn(self) -> None:
        from matmaster.core.agent import AgentKernel

        call_log: list[tuple[int, int]] = []

        class SpyCompactor:
            _last_llm_message_count = 0

            async def compact_if_needed(self, messages, last_usage, turn):
                call_log.append((len(messages), turn))

            def update_message_count(self, count):
                self._last_llm_message_count = count

        tc = ToolCallData(id="tc-1", name="tool", arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=2, final_content="done"
        )
        tool_reg, _ = _make_tool_registry(["tool"])
        spec = _make_spec(provider=provider, tool_registry=tool_reg, max_turns=10)
        spec = spec.model_copy(update={"compactor": SpyCompactor()})

        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.reason == "natural"
        assert len(call_log) == 3
        assert [turn for _, turn in call_log] == [1, 2, 3]

    async def test_last_usage_passed_to_compactor(self) -> None:
        from matmaster.core.agent import AgentKernel

        usage_log: list[dict] = []

        class UsageSpyCompactor:
            _last_llm_message_count = 0

            async def compact_if_needed(self, messages, last_usage, turn):
                usage_log.append(dict(last_usage))

            def update_message_count(self, count):
                self._last_llm_message_count = count

        usage_data = {
            "prompt_tokens": 500,
            "completion_tokens": 100,
            "total_tokens": 600,
        }

        class UsageTrackingProvider:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages, tools=None):
                return LLMResponse(content="unused", finish_reason="stop")

            async def chat_stream(self, messages, tools=None, *, timeout: float | None = None):
                yield StreamChunk(
                    content="done", finish_reason="stop", usage=usage_data
                )

        spec = _make_spec(provider=UsageTrackingProvider())
        spec = spec.model_copy(update={"compactor": UsageSpyCompactor()})

        kernel = AgentKernel()
        await kernel.run(spec, "test")

        assert usage_log[0] == {}

    async def test_no_compactor_no_error(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec()
        assert spec.compactor is None
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")
        assert result.result.reason == "natural"


class TestKernelResultFields:
    """KernelResult carries num_turns, stop_reason, and accumulated usage."""

    async def test_natural_finish_has_num_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="Hello", finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.num_turns == 1
        assert result.result.stop_reason == "stop"

    async def test_multi_turn_accumulates_usage(self) -> None:
        from matmaster.core.agent import AgentKernel

        class UsageTrackingToolProvider:
            def __init__(self) -> None:
                self._call_count = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages, tools=None):
                return LLMResponse(content="unused", finish_reason="stop")

            async def chat_stream(self, messages, tools=None, *, timeout: float | None = None):
                self._call_count += 1
                if self._call_count == 1:
                    yield StreamChunk(
                        tool_call_deltas=[
                            {"index": 0, "id": "tc-1", "name": "tool", "arguments": "{}"}
                        ],
                    )
                    yield StreamChunk(
                        finish_reason="stop",
                        usage={"prompt_tokens": 100, "completion_tokens": 50},
                    )
                else:
                    yield StreamChunk(
                        content="done",
                        finish_reason="stop",
                        usage={"prompt_tokens": 200, "completion_tokens": 30},
                    )

        tool_reg, _ = _make_tool_registry(["tool"])
        spec = _make_spec(provider=UsageTrackingToolProvider(), tool_registry=tool_reg)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.num_turns == 2
        assert result.result.usage["prompt_tokens"] == 300
        assert result.result.usage["completion_tokens"] == 80

    async def test_max_turns_has_correct_num_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="some_tool", arguments={"x": 1})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=999)
        spec = _make_spec(provider=provider, max_turns=3)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.reason == "max_turns"
        assert result.result.num_turns == 3

    async def test_cancelled_has_zero_turns_when_immediate(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()
        stop_event.set()
        spec = _make_spec()
        kernel = AgentKernel()
        result = await kernel.run(spec, "test", stop_event=stop_event)

        assert result.result.reason == "cancelled"
        assert result.result.num_turns == 0

    async def test_hook_stopped_has_correct_num_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec(hooks=[StopHook()])
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.reason == "hook_stopped"
        # hook_stopped fires after turn += 1 but before LLM call
        assert result.result.num_turns == 0

    async def test_invalid_finish_has_correct_fields(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="partial"),
            StreamChunk(finish_reason="length"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        assert result.result.reason == "invalid_finish"
        assert result.result.num_turns == 1
        assert result.result.stop_reason == "length"


class ErrorThenSuccessProvider:
    """Provider that raises LLMError N times, then succeeds."""

    def __init__(self, fail_count: int, error: LLMError) -> None:
        self._fail_count = fail_count
        self._error = error
        self._call_count = 0
        self.stream_timeout = 10.0
        self.max_retries = 3
        self.retry_delay = 0.0  # no sleep in tests

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise self._error
        yield StreamChunk(content="recovered", finish_reason="stop")


class TestCallLlmRetry:
    async def test_retry_on_retryable_error(self) -> None:
        """_call_llm retries on retryable LLMError and succeeds."""
        provider = ErrorThenSuccessProvider(
            fail_count=1,
            error=LLMError("timeout", retryable=True),
        )
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        response = await kernel._call_llm(spec, [UserMessage(content="hi")])
        assert response.content == "recovered"
        assert provider._call_count == 2

    async def test_no_retry_on_non_retryable_error(self) -> None:
        """_call_llm raises immediately on non-retryable LLMError."""
        provider = ErrorThenSuccessProvider(
            fail_count=1,
            error=LLMError("auth failed", retryable=False),
        )
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        with pytest.raises(LLMError, match="auth failed"):
            await kernel._call_llm(spec, [UserMessage(content="hi")])
        assert provider._call_count == 1

    async def test_all_retries_exhausted(self) -> None:
        """_call_llm raises LLMError (not RuntimeError) after all retries exhausted."""
        provider = ErrorThenSuccessProvider(
            fail_count=99,
            error=LLMError("timeout", retryable=True),
        )
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        with pytest.raises(LLMError, match="LLM stream failed") as exc_info:
            await kernel._call_llm(spec, [UserMessage(content="hi")])
        assert provider._call_count == 3  # max_retries default
        assert exc_info.value.retryable is False
        assert exc_info.value.attempts is not None
        assert len(exc_info.value.attempts) == 3

    async def test_retry_exhausted_carries_attempt_records(self) -> None:
        """Each attempt record has the required structured fields."""
        provider = ErrorThenSuccessProvider(
            fail_count=99,
            error=LLMError("conn refused", retryable=True, error_category="connection"),
        )
        provider.stream_timeout = 10.0
        provider.max_retries = 2
        provider.retry_delay = 0.0  # no wait in tests
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        with pytest.raises(LLMError) as exc_info:
            await kernel._call_llm(spec, [UserMessage(content="hi")])

        err = exc_info.value
        assert err.error_category == "connection"
        assert len(err.attempts) == 2
        for i, rec in enumerate(err.attempts):
            assert rec["attempt"] == i + 1
            assert rec["error_category"] == "connection"
            assert rec["error_type"] == "LLMError"
            assert "conn refused" in rec["error_message"]
            assert "timeout_used" in rec
            assert "elapsed_seconds" in rec
            assert rec["retryable"] is True

    async def test_timeout_doubles_on_retry(self) -> None:
        """Each retry doubles the timeout passed to chat_stream."""
        timeouts_seen: list[float | None] = []

        class TimeoutTracker:
            stream_timeout = 10.0
            max_retries = 3
            retry_delay = 0.0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages, tools=None):
                return LLMResponse(content="", finish_reason="stop")

            async def chat_stream(self, messages, tools=None, *, timeout=None):
                timeouts_seen.append(timeout)
                if len(timeouts_seen) < 3:
                    raise LLMError("timeout", retryable=True)
                yield StreamChunk(content="ok", finish_reason="stop")

        spec = AgentRuntimeSpec(
            llm_provider=TimeoutTracker(),
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        await kernel._call_llm(spec, [UserMessage(content="hi")])
        assert timeouts_seen == [10.0, 20.0, 40.0]

    async def test_llm_error_carries_category_and_attempts(self) -> None:
        """LLMError can carry error_category and attempts fields."""
        attempts = [{"attempt": 1, "error_type": "APITimeoutError"}]
        err = LLMError(
            "test",
            retryable=False,
            error_category="timeout",
            attempts=attempts,
        )
        assert err.error_category == "timeout"
        assert err.attempts == attempts
        assert not err.retryable

        # Backward compat: omitting new fields still works
        basic = LLMError("basic", retryable=True)
        assert basic.error_category is None
        assert basic.attempts is None


class TestParallelToolDispatch:
    """Tests for parallel tool dispatch via asyncio.gather."""

    async def test_parallel_execution_faster_than_serial(self):
        """Three tools each sleeping 0.2s should complete in ~0.2s, not ~0.6s."""
        from matmaster.core.agent import AgentKernel

        registry = ToolRegistry()

        class SlowTool:
            def __init__(self, name):
                self._name = name
            @property
            def name(self): return self._name
            @property
            def description(self): return "slow"
            @property
            def json_schema(self): return {"type": "object", "properties": {}}
            async def execute(self, arguments):
                await asyncio.sleep(0.2)
                return ToolResult(status="success", content=f"{self._name} done")

        for n in ["tool_a", "tool_b", "tool_c"]:
            registry.register(SlowTool(n), source="test")

        tcs = [
            ToolCallData(id="tc-1", name="tool_a", arguments={}),
            ToolCallData(id="tc-2", name="tool_b", arguments={}),
            ToolCallData(id="tc-3", name="tool_c", arguments={}),
        ]
        provider = MultiToolProvider(tool_calls=tcs)
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()

        start = time.monotonic()
        result = await kernel.run(spec, "test")
        elapsed = time.monotonic() - start

        # Parallel: ~0.2s + overhead. Serial: >= 0.6s.
        # Threshold 0.35s clearly separates them.
        assert elapsed < 0.35, f"Expected parallel execution < 0.35s, got {elapsed:.3f}s"
        tool_msgs = [m for m in result.messages if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 3

    async def test_gather_return_exceptions(self):
        """Failed tool returns error ToolResult, other tools unaffected per D-05."""
        from matmaster.core.agent import AgentKernel

        registry = ToolRegistry()

        class GoodTool:
            def __init__(self, name):
                self._name = name
            @property
            def name(self): return self._name
            @property
            def description(self): return "good"
            @property
            def json_schema(self): return {"type": "object", "properties": {}}
            async def execute(self, arguments):
                return ToolResult(status="success", content="ok")

        class BadTool:
            @property
            def name(self): return "bad_tool"
            @property
            def description(self): return "bad"
            @property
            def json_schema(self): return {"type": "object", "properties": {}}
            async def execute(self, arguments):
                raise RuntimeError("boom")

        registry.register(GoodTool("tool_a"), source="test")
        registry.register(BadTool(), source="test")
        registry.register(GoodTool("tool_c"), source="test")

        tcs = [
            ToolCallData(id="tc-1", name="tool_a", arguments={}),
            ToolCallData(id="tc-2", name="bad_tool", arguments={}),
            ToolCallData(id="tc-3", name="tool_c", arguments={}),
        ]
        provider = MultiToolProvider(tool_calls=tcs)
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        tool_msgs = [m for m in result.messages if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 3
        assert tool_msgs[0].content == "ok"  # tool_a success
        assert "RuntimeError" in tool_msgs[1].content  # bad_tool error
        assert "boom" in tool_msgs[1].content
        assert tool_msgs[2].content == "ok"  # tool_c success

    async def test_preserves_tool_call_order(self):
        """ToolMessages must follow original tool_calls order, not completion order."""
        from matmaster.core.agent import AgentKernel

        registry = ToolRegistry()

        class DelayTool:
            def __init__(self, name, delay):
                self._name = name
                self._delay = delay
            @property
            def name(self): return self._name
            @property
            def description(self): return "delay"
            @property
            def json_schema(self): return {"type": "object", "properties": {}}
            async def execute(self, arguments):
                await asyncio.sleep(self._delay)
                return ToolResult(status="success", content=self._name)

        # tool_a finishes last (0.15s), tool_b first (0.05s), tool_c middle (0.1s)
        registry.register(DelayTool("tool_a", 0.15), source="test")
        registry.register(DelayTool("tool_b", 0.05), source="test")
        registry.register(DelayTool("tool_c", 0.10), source="test")

        tcs = [
            ToolCallData(id="tc-1", name="tool_a", arguments={}),
            ToolCallData(id="tc-2", name="tool_b", arguments={}),
            ToolCallData(id="tc-3", name="tool_c", arguments={}),
        ]
        provider = MultiToolProvider(tool_calls=tcs)
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        tool_msgs = [m for m in result.messages if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 3
        # Order must match tool_calls order, not completion order
        assert tool_msgs[0].content == "tool_a"
        assert tool_msgs[1].content == "tool_b"
        assert tool_msgs[2].content == "tool_c"

    async def test_mixed_blocked_skipped_executed_order(self):
        """Mixed blocked/skipped/executed tools maintain original tool_call order."""
        from matmaster.core.agent import AgentKernel

        registry = ToolRegistry()

        class SimpleTool:
            def __init__(self, name):
                self._name = name
            @property
            def name(self): return self._name
            @property
            def description(self): return "simple"
            @property
            def json_schema(self): return {"type": "object", "properties": {}}
            async def execute(self, arguments):
                return ToolResult(status="success", content=f"{self._name} result")

        for n in ["tool_0", "tool_1", "tool_2", "tool_3", "tool_4"]:
            registry.register(SimpleTool(n), source="test")

        tcs = [
            ToolCallData(id="tc-0", name="tool_0", arguments={}),  # allowed
            ToolCallData(id="tc-1", name="tool_1", arguments={}),  # blocked by guard
            ToolCallData(id="tc-2", name="tool_2", arguments={}),  # skipped by hook
            ToolCallData(id="tc-3", name="tool_3", arguments={}),  # allowed
            ToolCallData(id="tc-4", name="tool_4", arguments={}),  # allowed
        ]

        provider = MultiToolProvider(tool_calls=tcs)
        deny_guard = DenyGuard("tool_1", reason="forbidden")
        skip_hook = SkipHook("tool_2")
        spec = _make_spec(
            provider=provider,
            tool_registry=registry,
            guards=[deny_guard],
            hooks=[skip_hook],
        )
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        tool_msgs = [m for m in result.messages if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 5, f"Expected 5 ToolMessages, got {len(tool_msgs)}"

        # Order MUST match original tool_calls order
        assert tool_msgs[0].tool_call_id == "tc-0"
        assert tool_msgs[0].content == "tool_0 result"  # executed

        assert tool_msgs[1].tool_call_id == "tc-1"
        assert "BLOCKED" in tool_msgs[1].content  # blocked

        assert tool_msgs[2].tool_call_id == "tc-2"
        assert "skipped" in tool_msgs[2].content.lower()  # skipped

        assert tool_msgs[3].tool_call_id == "tc-3"
        assert tool_msgs[3].content == "tool_3 result"  # executed

        assert tool_msgs[4].tool_call_id == "tc-4"
        assert tool_msgs[4].content == "tool_4 result"  # executed

    async def test_single_tool_call_unchanged(self):
        """Single tool_call still works correctly (regression test)."""
        from matmaster.core.agent import AgentKernel

        registry = ToolRegistry()

        class SimpleTool:
            @property
            def name(self): return "my_tool"
            @property
            def description(self): return "simple"
            @property
            def json_schema(self): return {"type": "object", "properties": {}}
            async def execute(self, arguments):
                return ToolResult(status="success", content="single result")

        registry.register(SimpleTool(), source="test")

        tcs = [ToolCallData(id="tc-1", name="my_tool", arguments={})]
        provider = MultiToolProvider(tool_calls=tcs)
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        tool_msgs = [m for m in result.messages if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == "single result"

    async def test_exception_in_closure_not_gather(self):
        """Closure catches exception and returns ToolResult, not BaseException."""
        from matmaster.core.agent import AgentKernel

        registry = ToolRegistry()

        class ErrorTool:
            @property
            def name(self): return "error_tool"
            @property
            def description(self): return "error"
            @property
            def json_schema(self): return {"type": "object", "properties": {}}
            async def execute(self, arguments):
                raise ValueError("test error")

        registry.register(ErrorTool(), source="test")

        tcs = [ToolCallData(id="tc-1", name="error_tool", arguments={})]
        provider = MultiToolProvider(tool_calls=tcs)
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")

        tool_msgs = [m for m in result.messages if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "ValueError" in tool_msgs[0].content
        assert "test error" in tool_msgs[0].content


class TestLLMResponseDegraded:
    def test_degraded_defaults_false(self) -> None:
        """LLMResponse.degraded defaults to False."""
        resp = LLMResponse(content="hello", finish_reason="stop")
        assert resp.degraded is False

    def test_degraded_can_be_set(self) -> None:
        """LLMResponse.degraded can be explicitly set."""
        resp = LLMResponse(content="hello", finish_reason="stop", degraded=True)
        assert resp.degraded is True
