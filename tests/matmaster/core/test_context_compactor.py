"""Tests for ContextCompactor."""

from __future__ import annotations

import pytest

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
