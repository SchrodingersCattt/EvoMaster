"""Tests for ContextCompactor."""

from __future__ import annotations

from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
from matmaster.types.runtime import CompactionConfig


class TestEstimateTokens:
    """Token estimation for messages."""

    def test_empty_list(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        assert estimate_tokens([]) == 0

    def test_single_message(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        msgs = [UserMessage(content="hello world")]
        tokens = estimate_tokens(msgs)
        assert tokens > 0
        assert tokens < 20

    def test_multiple_messages(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        msgs = [
            SystemMessage(content="You are helpful"),
            UserMessage(content="What is 2+2?"),
            AssistantMessage(content="4"),
        ]
        total = estimate_tokens(msgs)
        single = estimate_tokens([msgs[0]])
        assert total > single

    def test_tool_message_content(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        long_result = "x" * 4000
        msgs = [ToolMessage(content=long_result, tool_call_id="tc-1", tool_name="t")]
        tokens = estimate_tokens(msgs)
        assert tokens > 500


class TestParseTurns:
    """Turn boundary detection for retention rule."""

    def test_simple_assistant_tool_turn(self) -> None:
        from matmaster.core.context_compactor import parse_turns

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="thinking",
                tool_calls=[ToolCallData(id="tc1", name="bash", arguments={})],
            ),
            ToolMessage(content="result", tool_call_id="tc1", tool_name="bash"),
            AssistantMessage(content="done"),
        ]
        turns = parse_turns(msgs)
        assert len(turns) == 2
        assert len(turns[0]) == 2
        assert len(turns[1]) == 1

    def test_turn_includes_preceding_user_message(self) -> None:
        from matmaster.core.context_compactor import parse_turns

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task1"),
            AssistantMessage(content="reply1"),
            UserMessage(content="task2"),
            AssistantMessage(content="reply2"),
        ]
        turns = parse_turns(msgs)
        assert len(turns) == 2
        assert len(turns[0]) == 1
        assert isinstance(turns[0][0], AssistantMessage)
        assert len(turns[1]) == 2
        assert isinstance(turns[1][0], UserMessage)
        assert isinstance(turns[1][1], AssistantMessage)

    def test_empty_after_immutable(self) -> None:
        from matmaster.core.context_compactor import parse_turns

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
        ]
        turns = parse_turns(msgs)
        assert len(turns) == 0

    def test_multi_tool_calls_in_one_turn(self) -> None:
        from matmaster.core.context_compactor import parse_turns

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCallData(id="tc1", name="a", arguments={}),
                    ToolCallData(id="tc2", name="b", arguments={}),
                ],
            ),
            ToolMessage(content="r1", tool_call_id="tc1", tool_name="a"),
            ToolMessage(content="r2", tool_call_id="tc2", tool_name="b"),
        ]
        turns = parse_turns(msgs)
        assert len(turns) == 1
        assert len(turns[0]) == 3


class MockSummaryProvider:
    """Provider that returns a fixed summary."""

    def __init__(self, summary: str = "Summary of old conversation.") -> None:
        self._summary = summary
        self.calls: list[list[dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(content=self._summary, finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content=self._summary, finish_reason="stop")


class FailingSummaryProvider:
    """Provider that always raises."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        raise RuntimeError("LLM unavailable")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="", finish_reason="stop")


def _build_long_conversation(n_turns: int = 10) -> list:
    """Build a conversation with SystemMessage + UserMessage + N turns."""
    msgs = [
        SystemMessage(content="You are helpful"),
        UserMessage(content="Analyze this data: " + "x" * 200),
    ]
    for i in range(n_turns):
        msgs.append(
            AssistantMessage(
                content=f"Turn {i} thinking",
                tool_calls=[
                    ToolCallData(
                        id=f"tc-{i}",
                        name="bash",
                        arguments={"cmd": "ls"},
                    )
                ],
            )
        )
        msgs.append(
            ToolMessage(
                content="large result " + "y" * 500,
                tool_call_id=f"tc-{i}",
                tool_name="bash",
            )
        )
    return msgs


class TestCompactorThreshold:
    async def test_skip_when_below_threshold(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=128000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        compactor = ContextCompactor(config=config, summary_provider=provider)
        msgs = [SystemMessage(content="sys"), UserMessage(content="task")]
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 1000}, turn=2)
        assert len(provider.calls) == 0

    async def test_trigger_when_above_threshold(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)
        assert len(provider.calls) == 1

    async def test_cooldown_skips_consecutive_turn(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)
        assert len(provider.calls) == 1

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)
        assert len(provider.calls) == 1


class TestCompactorOutput:
    async def test_output_structure(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider(summary="Summarized content.")
        msgs = _build_long_conversation(10)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "You are helpful"
        assert isinstance(msgs[1], SystemMessage)
        assert "[Compacted Context]" in msgs[1].content
        assert "Summarized content." in msgs[1].content
        assert isinstance(msgs[2], UserMessage)
        assert "Analyze this data" in msgs[2].content
        assert len(msgs) < 25

    async def test_fallback_on_summary_failure(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = FailingSummaryProvider()
        msgs = _build_long_conversation(10)
        original_len = len(msgs)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        assert len(msgs) < original_len
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], UserMessage)
        assert "[Compacted Context]" not in (msgs[0].content or "")


class TestCompactorMessageCount:
    def test_update_message_count(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(enabled=True, context_window_tokens=128000)
        provider = MockSummaryProvider()
        compactor = ContextCompactor(config=config, summary_provider=provider)

        compactor.update_message_count(5)
        assert compactor._last_llm_message_count == 5

        compactor.update_message_count(8)
        assert compactor._last_llm_message_count == 8


class TestCompactorEventEmission:
    async def test_emits_context_compaction_event_via_sink(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor
        from matmaster.types.events import ContextCompactionEvent

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        received: list = []

        async def sink(event):
            received.append(event)

        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(
            config=config, summary_provider=provider, event_sink=sink
        )
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        assert len(received) == 1
        event = received[0]
        assert isinstance(event, ContextCompactionEvent)
        assert event.payload["compaction_count"] == 1
        assert event.payload["strategy"] == "summary"
        assert event.payload["trigger_tokens"] > 0

    async def test_no_event_when_no_sink(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(
            config=config, summary_provider=provider, event_sink=None
        )
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)


class TestToolTruncationFallback:
    """Tool result truncation when no old turns to compress."""

    async def test_truncates_when_no_compressible_turns(self) -> None:
        """1 turn with huge tool results -> falls back to tool_truncation."""
        from matmaster.core.context_compactor import ContextCompactor
        from matmaster.types.events import ContextCompactionEvent

        config = CompactionConfig(
            enabled=True, context_window_tokens=500, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        received: list = []

        async def sink(event):
            received.append(event)

        # 1 turn: Assistant with 3 tool calls + 3 large ToolMessages
        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="calling tools",
                tool_calls=[
                    ToolCallData(id=f"tc-{i}", name="bash", arguments={})
                    for i in range(3)
                ],
            ),
            ToolMessage(
                content="big result " + "A" * 2000,
                tool_call_id="tc-0",
                tool_name="bash",
            ),
            ToolMessage(
                content="big result " + "B" * 2000,
                tool_call_id="tc-1",
                tool_name="bash",
            ),
            ToolMessage(
                content="big result " + "C" * 2000,
                tool_call_id="tc-2",
                tool_name="bash",
            ),
        ]

        compactor = ContextCompactor(
            config=config, summary_provider=provider, event_sink=sink
        )
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 600}, turn=3)

        # summary provider should NOT be called (no old messages to summarize)
        assert len(provider.calls) == 0

        # But truncation should have happened
        assert compactor._compaction_count == 1

        # At least one ToolMessage should have been truncated
        truncated_msgs = [
            m
            for m in msgs
            if isinstance(m, ToolMessage) and "truncated" in (m.content or "")
        ]
        assert len(truncated_msgs) > 0

        # Event should have strategy=tool_truncation
        assert len(received) == 1
        event = received[0]
        assert isinstance(event, ContextCompactionEvent)
        assert event.payload["strategy"] == "tool_truncation"

    async def test_no_truncation_for_small_tool_results(self) -> None:
        """Small tool results (< 500 chars) are not truncated."""
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=500, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCallData(id="tc-0", name="t", arguments={})],
            ),
            ToolMessage(content="short result", tool_call_id="tc-0", tool_name="t"),
        ]

        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 600}, turn=3)

        # Small content -> not truncated (truncation skips content < 500 chars)
        assert "truncated" not in (msgs[3].content or "")

    async def test_truncation_preserves_head_and_tail(self) -> None:
        """Truncated content keeps head 200 + tail 100 chars."""
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=200, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        original_content = "HEAD_MARKER_" + "x" * 2000 + "_TAIL_MARKER"

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCallData(id="tc-0", name="t", arguments={})],
            ),
            ToolMessage(content=original_content, tool_call_id="tc-0", tool_name="t"),
        ]

        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 300}, turn=3)

        result_content = msgs[3].content or ""
        assert "HEAD_MARKER_" in result_content  # head preserved
        assert "_TAIL_MARKER" in result_content  # tail preserved
        assert "truncated" in result_content  # marker present
        assert len(result_content) < len(original_content)


class TestCompactorEventSink:
    """Phase 34: event_sink callback replaces bus dependency."""

    async def test_event_sink_receives_compaction_event(self) -> None:
        """Compactor with event_sink calls sink with ContextCompactionEvent."""
        from matmaster.core.context_compactor import ContextCompactor
        from matmaster.types.events import ContextCompactionEvent

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        received_events: list = []

        async def sink(event):
            received_events.append(event)

        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(
            config=config, summary_provider=provider, event_sink=sink
        )
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        assert len(received_events) == 1
        event = received_events[0]
        assert isinstance(event, ContextCompactionEvent)
        assert event.payload["compaction_count"] == 1
        assert event.payload["strategy"] == "summary"

    async def test_no_event_when_no_sink(self) -> None:
        """Compactor with event_sink=None does not error."""
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(
            config=config, summary_provider=provider, event_sink=None
        )
        compactor.update_message_count(len(msgs))

        # Should not raise
        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

    async def test_no_bus_import_in_compactor(self) -> None:
        """ContextCompactor module should not import MessageBus."""
        import ast
        import inspect

        from matmaster.core import context_compactor

        source = inspect.getsource(context_compactor)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "bus" in node.module:
                    # Check it's specifically importing MessageBus
                    for alias in node.names:
                        assert (
                            alias.name != "MessageBus"
                        ), "ContextCompactor should not import MessageBus"
