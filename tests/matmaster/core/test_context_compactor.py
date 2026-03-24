"""Tests for ContextCompactor."""

from __future__ import annotations

import pytest

from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.runtime import CompactionConfig
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


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

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(content=self._summary, finish_reason="stop")

    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None):
        yield StreamChunk(content=self._summary, finish_reason="stop")


class FailingSummaryProvider:
    """Provider that always raises."""

    def chat(self, messages, tools=None):
        raise RuntimeError("LLM unavailable")

    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None):
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
    def test_skip_when_below_threshold(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=128000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        compactor = ContextCompactor(config=config, summary_provider=provider)
        msgs = [SystemMessage(content="sys"), UserMessage(content="task")]
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 1000}, turn=2)
        assert len(provider.calls) == 0

    def test_trigger_when_above_threshold(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)
        assert len(provider.calls) == 1

    def test_cooldown_skips_consecutive_turn(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)
        assert len(provider.calls) == 1

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)
        assert len(provider.calls) == 1


class TestCompactorOutput:
    def test_output_structure(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider(summary="Summarized content.")
        msgs = _build_long_conversation(10)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "You are helpful"
        assert isinstance(msgs[1], SystemMessage)
        assert "[Compacted Context]" in msgs[1].content
        assert "Summarized content." in msgs[1].content
        assert isinstance(msgs[2], UserMessage)
        assert "Analyze this data" in msgs[2].content
        assert len(msgs) < 25

    def test_fallback_on_summary_failure(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = FailingSummaryProvider()
        msgs = _build_long_conversation(10)
        original_len = len(msgs)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

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
    def test_emits_context_compaction_event(self) -> None:
        from matmaster.core.bus import MessageBus
        from matmaster.core.context_compactor import ContextCompactor
        from matmaster.types.events import ContextCompactionEvent

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        bus = MessageBus()
        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider, bus=bus)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        event = bus.get_nowait()
        assert isinstance(event, ContextCompactionEvent)
        assert event.payload["compaction_count"] == 1
        assert event.payload["strategy"] == "summary"
        assert event.payload["trigger_tokens"] > 0

    def test_no_event_when_no_bus(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider, bus=None)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)
