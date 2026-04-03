"""Tests for matmaster.core.hooks -- Hook Protocol, BaseHook, HookAction, run_* helpers."""

from __future__ import annotations

import pytest

from matmaster.core.hooks import (
    BaseHook,
    Hook,
    HookAction,
    run_on_stream_chunk,
    run_post_tool_call,
    run_pre_llm_call,
    run_pre_tool_call,
    run_should_continue,
)
from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import (
    Message,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    UserMessage,
)

# ── Fixtures ──────────────────────────────────────────


@pytest.fixture
def sample_tool_call() -> ToolCallData:
    return ToolCallData(id="tc-1", name="test_tool", arguments={"key": "value"})


@pytest.fixture
def sample_messages() -> list[Message]:
    return [
        SystemMessage(content="You are a test agent"),
        UserMessage(content="hello"),
    ]


@pytest.fixture
def sample_chunk() -> StreamChunk:
    return StreamChunk(
        content="hello",
        stream_state="streaming",
        stream_id="s1",
        reasoning_content="thinking",
    )


# ── Hook Protocol conformance ────────────────────────


class TestHookProtocol:
    def test_base_hook_satisfies_protocol(self) -> None:
        hook = BaseHook()
        assert isinstance(hook, Hook)

    async def test_base_hook_pre_tool_call_default(
        self, sample_tool_call: ToolCallData
    ) -> None:
        hook = BaseHook()
        result = await hook.pre_tool_call(sample_tool_call)
        assert result == HookAction.CONTINUE

    async def test_base_hook_post_tool_call_default(
        self, sample_tool_call: ToolCallData
    ) -> None:
        hook = BaseHook()
        result = await hook.post_tool_call(
            sample_tool_call, ToolResult(content="result")
        )
        assert result is None

    async def test_base_hook_pre_llm_call_default(
        self, sample_messages: list[Message]
    ) -> None:
        hook = BaseHook()
        result = await hook.pre_llm_call(sample_messages, 1)
        assert result is None

    async def test_base_hook_should_continue_default(
        self, sample_messages: list[Message]
    ) -> None:
        hook = BaseHook()
        result = await hook.should_continue(sample_messages, 1)
        assert result is True

    async def test_base_hook_on_stream_chunk_default(
        self, sample_chunk: StreamChunk
    ) -> None:
        hook = BaseHook()
        result = await hook.on_stream_chunk(sample_chunk)
        assert result is None

    async def test_base_hook_on_segment_complete_default(self) -> None:
        hook = BaseHook()
        result = await hook.on_segment_complete("thought", "done", "s1")
        assert result is None


# ── Custom hook overrides ─────────────────────────────


class SkipHook(BaseHook):
    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        return HookAction.SKIP


class StopHook(BaseHook):
    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        return False


class TestCustomHookOverrides:
    async def test_pre_tool_call_skip(self, sample_tool_call: ToolCallData) -> None:
        hook = SkipHook()
        assert await hook.pre_tool_call(sample_tool_call) == HookAction.SKIP

    async def test_should_continue_false(self, sample_messages: list[Message]) -> None:
        hook = StopHook()
        assert await hook.should_continue(sample_messages, 1) is False


# ── Hook short-circuit helpers ────────────────────────


class TrackingHook(BaseHook):
    """Hook that tracks whether each method was called."""

    def __init__(self) -> None:
        self.pre_tool_call_called = False
        self.post_tool_call_called = False
        self.pre_llm_call_called = False
        self.should_continue_called = False
        self.on_stream_chunk_called = False

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self.pre_tool_call_called = True
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        self.post_tool_call_called = True

    async def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        self.pre_llm_call_called = True

    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        self.should_continue_called = True
        return True

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        self.on_stream_chunk_called = True


class TrackingSkipHook(TrackingHook):
    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self.pre_tool_call_called = True
        return HookAction.SKIP


class TrackingStopHook(TrackingHook):
    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        self.should_continue_called = True
        return False


class TestHookShortCircuit:
    async def test_pre_tool_call_skip_first(
        self, sample_tool_call: ToolCallData
    ) -> None:
        """First hook skips -> second hook not called."""
        skip_hook = TrackingSkipHook()
        continue_hook = TrackingHook()
        result = await run_pre_tool_call([skip_hook, continue_hook], sample_tool_call)
        assert result == HookAction.SKIP
        assert skip_hook.pre_tool_call_called is True
        assert continue_hook.pre_tool_call_called is False

    async def test_pre_tool_call_skip_second(
        self, sample_tool_call: ToolCallData
    ) -> None:
        """First continues, second skips -> returns SKIP."""
        continue_hook = TrackingHook()
        skip_hook = TrackingSkipHook()
        result = await run_pre_tool_call([continue_hook, skip_hook], sample_tool_call)
        assert result == HookAction.SKIP
        assert continue_hook.pre_tool_call_called is True
        assert skip_hook.pre_tool_call_called is True

    async def test_pre_tool_call_all_continue(
        self, sample_tool_call: ToolCallData
    ) -> None:
        """All hooks continue -> returns CONTINUE."""
        h1 = TrackingHook()
        h2 = TrackingHook()
        result = await run_pre_tool_call([h1, h2], sample_tool_call)
        assert result == HookAction.CONTINUE

    async def test_should_continue_false_first(
        self, sample_messages: list[Message]
    ) -> None:
        """First hook returns False -> second not called."""
        stop_hook = TrackingStopHook()
        continue_hook = TrackingHook()
        result = await run_should_continue(
            [stop_hook, continue_hook], sample_messages, 1
        )
        assert result is False
        assert stop_hook.should_continue_called is True
        assert continue_hook.should_continue_called is False

    async def test_post_tool_call_calls_all(
        self, sample_tool_call: ToolCallData
    ) -> None:
        """Observation hook -- both hooks called (no short-circuit)."""
        h1 = TrackingHook()
        h2 = TrackingHook()
        await run_post_tool_call(
            [h1, h2], sample_tool_call, ToolResult(content="result")
        )
        assert h1.post_tool_call_called is True
        assert h2.post_tool_call_called is True

    async def test_post_tool_call_returns_rewritten_result(
        self, sample_tool_call: ToolCallData
    ) -> None:
        """post_tool_call hook can rewrite the ToolResult."""

        class RewritingHook(BaseHook):
            async def post_tool_call(
                self, tool_call: ToolCallData, result: ToolResult
            ) -> ToolResult | None:
                return ToolResult(content=result.content + " :: rewritten")

        rewritten = await run_post_tool_call(
            [RewritingHook()], sample_tool_call, ToolResult(content="raw")
        )
        assert rewritten.content == "raw :: rewritten"

    async def test_post_tool_call_chain_rewrite(
        self, sample_tool_call: ToolCallData
    ) -> None:
        """Multiple hooks chain rewrites sequentially."""

        class AppendHook(BaseHook):
            def __init__(self, suffix: str) -> None:
                self._suffix = suffix

            async def post_tool_call(
                self, tool_call: ToolCallData, result: ToolResult
            ) -> ToolResult | None:
                return ToolResult(content=result.content + self._suffix)

        result = await run_post_tool_call(
            [AppendHook(" A"), AppendHook(" B")],
            sample_tool_call,
            ToolResult(content="raw"),
        )
        assert result.content == "raw A B"

    async def test_post_tool_call_none_preserves_result(
        self, sample_tool_call: ToolCallData
    ) -> None:
        """Hook returning None preserves the current result."""
        result = await run_post_tool_call(
            [BaseHook()], sample_tool_call, ToolResult(content="unchanged")
        )
        assert result.content == "unchanged"

    async def test_pre_llm_call_calls_all(self, sample_messages: list[Message]) -> None:
        """Observation hook -- both hooks called (no short-circuit)."""
        h1 = TrackingHook()
        h2 = TrackingHook()
        await run_pre_llm_call([h1, h2], sample_messages, 1)
        assert h1.pre_llm_call_called is True
        assert h2.pre_llm_call_called is True

    async def test_on_stream_chunk_calls_all(self, sample_chunk: StreamChunk) -> None:
        """Observation hook -- both hooks called (no short-circuit)."""
        h1 = TrackingHook()
        h2 = TrackingHook()
        await run_on_stream_chunk([h1, h2], sample_chunk)
        assert h1.on_stream_chunk_called is True
        assert h2.on_stream_chunk_called is True
