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

from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.events import FinishEvent
from matmaster.types.guards import Guard, GuardContext, GuardResult
from matmaster.types.runtime import AgentRuntimeSpec
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

    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
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

        assert isinstance(result, FinishEvent)
        assert result.reason == "natural"
        assert result.final_content == "Hello"

    def test_natural_finish_messages(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="Hello"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = kernel.run(spec, "test task")

        assert result.reason == "natural"


class TestMaxTurns:
    """LLM always returns tool_calls, max_turns reached."""

    def test_max_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="some_tool", arguments={"x": 1})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=999)
        spec = _make_spec(provider=provider, max_turns=2)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert isinstance(result, FinishEvent)
        assert result.reason == "max_turns"


class TestExternalCancel:
    """stop_event.set() -> FinishEvent(reason='cancelled')."""

    def test_cancel_before_run(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()
        stop_event.set()
        spec = _make_spec()
        kernel = AgentKernel()
        result = kernel.run(spec, "test", stop_event=stop_event)

        assert isinstance(result, FinishEvent)
        assert result.reason == "cancelled"

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

        assert isinstance(result, FinishEvent)
        assert result.reason == "cancelled"


class TestHookStopped:
    """should_continue returns False -> FinishEvent(reason='hook_stopped')."""

    def test_hook_stopped(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec(hooks=[StopHook()])
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert isinstance(result, FinishEvent)
        assert result.reason == "hook_stopped"


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

        assert result.reason == "natural"
        assert result.final_content == "Hello"
        assert len(chunk_hook.chunks) == 3  # all 3 chunks forwarded


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

        assert result.reason == "natural"
        assert result.final_content == "final answer"
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

        assert result.reason == "natural"
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

        assert result.reason == "natural"
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

        assert result.reason == "natural"
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
