"""Tests for AgentKernel execution loop.

Covers all termination paths (natural, max_turns, cancelled, hook_stopped),
guard blocking, hook SKIP, streaming accumulation, tool call delta
reassembly, full cycle, and execution order verification.
"""

from __future__ import annotations

import threading
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from matmaster.tools.tool_result import ToolResult
from matmaster.tools.tool_registry import ToolRegistry
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

    def execute(self, arguments: dict[str, Any]) -> str:
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

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="not used", finish_reason="stop")

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse:
        return self.chat(messages, tools)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]:
        yield from self._chunks


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

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="not used", finish_reason="stop")

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse:
        return self.chat(messages, tools)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]:
        self._call_count += 1
        if self._call_count <= self._max_tool_turns:
            for tc in self._tool_calls:
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": str(tc.arguments).replace("'", '"'),
                        }
                    ],
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

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        if tool_call.name == self._skip_name:
            return HookAction.SKIP
        return HookAction.CONTINUE


class StopHook(BaseHook):
    """Hook that returns False from should_continue."""

    def should_continue(self, messages: list[Message], turn: int) -> bool:
        return False


class RecordingHook(BaseHook):
    """Hook that records all method calls in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self.calls.append("pre_tool_call")
        return HookAction.CONTINUE

    def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        self.calls.append("post_tool_call")

    def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        self.calls.append("pre_llm_call")

    def should_continue(self, messages: list[Message], turn: int) -> bool:
        self.calls.append("should_continue")
        return True

    def on_stream_chunk(self, chunk: StreamChunk) -> None:
        self.calls.append("on_stream_chunk")


class ChunkRecordingHook(BaseHook):
    """Hook that records stream chunks."""

    def __init__(self) -> None:
        self.chunks: list[StreamChunk] = []

    def on_stream_chunk(self, chunk: StreamChunk) -> None:
        self.chunks.append(chunk)


class SegmentRecordingHook(BaseHook):
    """Hook that records completed logical segments."""

    def __init__(self) -> None:
        self.segments: list[tuple[str, str, str | None]] = []

    def on_segment_complete(
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

    def test_natural_finish(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="Hello"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = kernel.run(spec, "test task")

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "natural"
        assert result.result.final_content == "Hello"

    def test_natural_finish_messages(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="Hello"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = kernel.run(spec, "test task")

        assert result.result.reason == "natural"


class TestMaxTurns:
    """LLM always returns tool_calls, max_turns reached."""

    def test_max_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="some_tool", arguments={"x": 1})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=999)
        spec = _make_spec(provider=provider, max_turns=2)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "max_turns"


class TestExternalCancel:
    """stop_event.set() -> FinishEvent(reason='cancelled')."""

    def test_cancel_before_run(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()
        stop_event.set()
        spec = _make_spec()
        kernel = AgentKernel()
        result = kernel.run(spec, "test", stop_event=stop_event)

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "cancelled"

    def test_cancel_during_run(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()

        class CancelAfterFirstTurnProvider:
            def __init__(self) -> None:
                self._call_count = 0

            def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            def chat_with_retry(
                self,
                messages: list,
                tools: list | None = None,
                *,
                max_retries: int = 3,
                retry_delay: float = 1.0,
            ) -> LLMResponse:
                return self.chat(messages, tools)

            def chat_stream(
                self,
                messages: list,
                tools: list | None = None,
            ) -> Iterator[StreamChunk]:
                self._call_count += 1
                if self._call_count == 1:
                    tc = ToolCallData(id="tc-1", name="tool", arguments={})
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
        result = kernel.run(spec, "test", stop_event=stop_event)

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "cancelled"


class TestHookStopped:
    """should_continue returns False -> FinishEvent(reason='hook_stopped')."""

    def test_hook_stopped(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec(hooks=[StopHook()])
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "hook_stopped"


class TestGuardBlocks:
    """Guard blocks tool call -> BLOCKED message, hooks NOT triggered."""

    def test_guard_blocks(self) -> None:
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
        result = kernel.run(spec, "test")

        # Tool should NOT have been executed
        bad_tool = tools[0]
        assert len(bad_tool.calls) == 0
        # Hooks pre_tool_call/post_tool_call should NOT be called for blocked tool
        assert "pre_tool_call" not in recording.calls
        assert "post_tool_call" not in recording.calls

    def test_guard_block_triggers_hook(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.core.hooks import BaseHook
        from matmaster.types.guards import GuardResult

        class GuardBlockRecorder(BaseHook):
            def __init__(self) -> None:
                self.blocked: list[tuple[str, str | None]] = []

            def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None:
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
        kernel.run(spec, "test")

        assert len(recorder.blocked) == 1
        assert recorder.blocked[0] == ("bad_tool", "no access")


class TestHookSkip:
    """Hook SKIP -> tool NOT executed, ToolMessage with 'skipped by hook'."""

    def test_hook_skip(self) -> None:
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
        result = kernel.run(spec, "test")

        # Tool should NOT have been executed
        skip_tool = tools[0]
        assert len(skip_tool.calls) == 0


class TestStreamingAccumulation:
    """Provider yields chunks, kernel accumulates to LLMResponse."""

    def test_streaming_accumulation(self) -> None:
        from matmaster.core.agent import AgentKernel

        chunk_hook = ChunkRecordingHook()
        provider = StreamingProvider([
            StreamChunk(content="He"),
            StreamChunk(content="llo"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider, hooks=[chunk_hook])
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

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

    def test_non_stop_finish_reason_does_not_commit_natural_finish(self) -> None:
        from matmaster.core.agent import AgentKernel

        chunk_hook = ChunkRecordingHook()
        provider = StreamingProvider([
            StreamChunk(content="partial"),
            StreamChunk(finish_reason="length"),
        ])
        spec = _make_spec(provider=provider, hooks=[chunk_hook])
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

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

    def test_tool_call_delta(self) -> None:
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

            def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            def chat_with_retry(
                self,
                messages: list,
                tools: list | None = None,
                *,
                max_retries: int = 3,
                retry_delay: float = 1.0,
            ) -> LLMResponse:
                return self.chat(messages, tools)

            def chat_stream(
                self, messages: list, tools: list | None = None
            ) -> Iterator[StreamChunk]:
                self._call_count += 1
                if self._call_count == 1:
                    yield from chunks
                else:
                    yield StreamChunk(content="done", finish_reason="stop")

        tool_reg, tools = _make_tool_registry(["fn"])
        spec = _make_spec(provider=TwoPhaseProvider(), tool_registry=tool_reg)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        # Tool should have been called with parsed arguments
        fn_tool = tools[0]
        assert len(fn_tool.calls) == 1
        assert fn_tool.calls[0][0] == "fn"
        assert fn_tool.calls[0][1] == {"a": 1}


class TestFullCycle:
    """Turn 1: tool_call -> execute. Turn 2: natural finish."""

    def test_full_cycle(self) -> None:
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
        result = kernel.run(spec, "test")

        assert result.result.reason == "natural"
        assert result.result.final_content == "final answer"
        my_tool = tools[0]
        assert len(my_tool.calls) == 1
        assert my_tool.calls[0][0] == "my_tool"


class TestHistoryParameter:
    """AgentKernel.run() with history parameter."""

    def test_history_inserts_between_system_and_user(self) -> None:
        """history messages are placed between SystemMessage and UserMessage(task)."""
        from matmaster.core.agent import AgentKernel

        captured_messages: list[list[dict[str, Any]]] = []

        class CapturingProvider:
            def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            def chat_with_retry(self, messages: list, tools: list | None = None, *, max_retries: int = 3, retry_delay: float = 1.0) -> LLMResponse:
                return self.chat(messages, tools)

            def chat_stream(self, messages: list, tools: list | None = None) -> Iterator[StreamChunk]:
                captured_messages.append(messages)
                yield StreamChunk(content="ok", finish_reason="stop")

        history = [
            UserMessage(content="hi"),
            AssistantMessage(content="hello"),
        ]
        spec = _make_spec(provider=CapturingProvider())
        kernel = AgentKernel()
        result = kernel.run(spec, "new question", history=history)

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

    def test_history_none_is_backward_compatible(self) -> None:
        """history=None produces [SystemMessage, UserMessage(task)]."""
        from matmaster.core.agent import AgentKernel

        captured_messages: list[list[dict[str, Any]]] = []

        class CapturingProvider:
            def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            def chat_with_retry(self, messages: list, tools: list | None = None, *, max_retries: int = 3, retry_delay: float = 1.0) -> LLMResponse:
                return self.chat(messages, tools)

            def chat_stream(self, messages: list, tools: list | None = None) -> Iterator[StreamChunk]:
                captured_messages.append(messages)
                yield StreamChunk(content="ok", finish_reason="stop")

        spec = _make_spec(provider=CapturingProvider())
        kernel = AgentKernel()
        result = kernel.run(spec, "test task", history=None)

        assert result.result.reason == "natural"
        msgs = captured_messages[0]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "test task"

    def test_empty_history_is_backward_compatible(self) -> None:
        """history=[] produces [SystemMessage, UserMessage(task)]."""
        from matmaster.core.agent import AgentKernel

        captured_messages: list[list[dict[str, Any]]] = []

        class CapturingProvider:
            def chat(self, messages: list, tools: list | None = None) -> LLMResponse:
                return LLMResponse(content="unused", finish_reason="stop")

            def chat_with_retry(self, messages: list, tools: list | None = None, *, max_retries: int = 3, retry_delay: float = 1.0) -> LLMResponse:
                return self.chat(messages, tools)

            def chat_stream(self, messages: list, tools: list | None = None) -> Iterator[StreamChunk]:
                captured_messages.append(messages)
                yield StreamChunk(content="ok", finish_reason="stop")

        spec = _make_spec(provider=CapturingProvider())
        kernel = AgentKernel()
        result = kernel.run(spec, "test task", history=[])

        assert result.result.reason == "natural"
        msgs = captured_messages[0]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "test task"


class TestExecutionOrder:
    """Recording hook tracks correct call order."""

    def test_execution_order(self) -> None:
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
        kernel.run(spec, "test")

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

    def test_natural_finish_returns_messages(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.types.runtime import KernelRunResult

        provider = StreamingProvider([
            StreamChunk(content="Hello"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = kernel.run(spec, "test task")

        assert isinstance(result, KernelRunResult)
        assert result.result.reason == "natural"
        # Messages: [SystemMessage, UserMessage, AssistantMessage]
        assert len(result.messages) == 3
        assert isinstance(result.messages[0], SystemMessage)
        assert isinstance(result.messages[1], UserMessage)
        assert isinstance(result.messages[2], AssistantMessage)
        assert result.messages[2].content == "Hello"

    def test_tool_cycle_returns_all_messages(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.types.runtime import KernelRunResult

        tc = ToolCallData(id="tc-1", name="my_tool", arguments={"key": "val"})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="final"
        )
        tool_reg, _ = _make_tool_registry(["my_tool"], result="tool output")
        spec = _make_spec(provider=provider, tool_registry=tool_reg, max_turns=10)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

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

    def test_tool_exception_becomes_error_message(self) -> None:
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

            def execute(self, arguments: dict[str, Any]) -> str:
                raise RuntimeError("kaboom!")

        registry = ToolRegistry()
        registry.register(ExplodingTool(), source="test")

        tc = ToolCallData(id="tc-1", name="boom", arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="recovered"
        )
        spec = _make_spec(provider=provider, tool_registry=registry, max_turns=5)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert result.result.reason == "natural"
        assert result.result.final_content == "recovered"


class TestCallLlmUsageCapture:
    """_call_llm captures usage from StreamChunk into LLMResponse."""

    def test_usage_captured_from_stream(self) -> None:
        from matmaster.core.agent import AgentKernel

        usage_data = {
            "prompt_tokens": 500,
            "completion_tokens": 100,
            "total_tokens": 600,
        }

        class UsageProvider:
            def chat(self, messages, tools=None):
                return LLMResponse(content="unused", finish_reason="stop")

            def chat_with_retry(
                self,
                messages,
                tools=None,
                *,
                max_retries=3,
                retry_delay=1.0,
            ):
                return self.chat(messages, tools)

            def chat_stream(self, messages, tools=None):
                yield StreamChunk(content="hello")
                yield StreamChunk(finish_reason="stop", usage=usage_data)

        spec = _make_spec(provider=UsageProvider())
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert result.result.reason == "natural"
        response = kernel._call_llm(spec, [UserMessage(content="test")])
        assert response.usage == usage_data

    def test_segment_complete_hooks_run_for_reasoning_and_response(self) -> None:
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

        response = kernel._call_llm(spec, [UserMessage(content="test")])

        assert response.reasoning_content == "think "
        assert response.content == "answer"
        assert segment_hook.segments == [
            ("thought", "think ", "turn-1"),
            ("response", "answer", "turn-1"),
        ]


class TestCompactorIntegration:
    """Kernel calls compactor.compact_if_needed and update_message_count."""

    def test_compactor_called_each_turn(self) -> None:
        from matmaster.core.agent import AgentKernel

        call_log: list[tuple[int, int]] = []

        class SpyCompactor:
            _last_llm_message_count = 0

            def compact_if_needed(self, messages, last_usage, turn):
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
        result = kernel.run(spec, "test")

        assert result.result.reason == "natural"
        assert len(call_log) == 3
        assert [turn for _, turn in call_log] == [1, 2, 3]

    def test_last_usage_passed_to_compactor(self) -> None:
        from matmaster.core.agent import AgentKernel

        usage_log: list[dict] = []

        class UsageSpyCompactor:
            _last_llm_message_count = 0

            def compact_if_needed(self, messages, last_usage, turn):
                usage_log.append(dict(last_usage))

            def update_message_count(self, count):
                self._last_llm_message_count = count

        usage_data = {
            "prompt_tokens": 500,
            "completion_tokens": 100,
            "total_tokens": 600,
        }

        class UsageTrackingProvider:
            def chat(self, messages, tools=None):
                return LLMResponse(content="unused", finish_reason="stop")

            def chat_with_retry(
                self,
                messages,
                tools=None,
                *,
                max_retries=3,
                retry_delay=1.0,
            ):
                return self.chat(messages, tools)

            def chat_stream(self, messages, tools=None):
                yield StreamChunk(
                    content="done", finish_reason="stop", usage=usage_data
                )

        spec = _make_spec(provider=UsageTrackingProvider())
        spec = spec.model_copy(update={"compactor": UsageSpyCompactor()})

        kernel = AgentKernel()
        kernel.run(spec, "test")

        assert usage_log[0] == {}

    def test_no_compactor_no_error(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec()
        assert spec.compactor is None
        kernel = AgentKernel()
        result = kernel.run(spec, "test")
        assert result.result.reason == "natural"


class TestKernelResultFields:
    """KernelResult carries num_turns, stop_reason, and accumulated usage."""

    def test_natural_finish_has_num_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="Hello", finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert result.result.num_turns == 1
        assert result.result.stop_reason == "stop"

    def test_multi_turn_accumulates_usage(self) -> None:
        from matmaster.core.agent import AgentKernel

        class UsageTrackingToolProvider:
            def __init__(self) -> None:
                self._call_count = 0

            def chat(self, messages, tools=None):
                return LLMResponse(content="unused", finish_reason="stop")

            def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
                return self.chat(messages, tools)

            def chat_stream(self, messages, tools=None):
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
        result = kernel.run(spec, "test")

        assert result.result.num_turns == 2
        assert result.result.usage["prompt_tokens"] == 300
        assert result.result.usage["completion_tokens"] == 80

    def test_max_turns_has_correct_num_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="some_tool", arguments={"x": 1})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=999)
        spec = _make_spec(provider=provider, max_turns=3)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert result.result.reason == "max_turns"
        assert result.result.num_turns == 3

    def test_cancelled_has_zero_turns_when_immediate(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()
        stop_event.set()
        spec = _make_spec()
        kernel = AgentKernel()
        result = kernel.run(spec, "test", stop_event=stop_event)

        assert result.result.reason == "cancelled"
        assert result.result.num_turns == 0

    def test_hook_stopped_has_correct_num_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec(hooks=[StopHook()])
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert result.result.reason == "hook_stopped"
        # hook_stopped fires after turn += 1 but before LLM call
        assert result.result.num_turns == 0

    def test_invalid_finish_has_correct_fields(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="partial"),
            StreamChunk(finish_reason="length"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert result.result.reason == "invalid_finish"
        assert result.result.num_turns == 1
        assert result.result.stop_reason == "length"
