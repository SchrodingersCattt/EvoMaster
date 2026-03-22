"""Behavioral tests for ChatHistoryConverter.events_to_messages().

Verifies that DB event dicts are correctly converted to matmaster
engine Message types (UserMessage, AssistantMessage, ToolMessage).
"""

from __future__ import annotations

import pytest

from matmaster.engine.types import (
    AssistantMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
from src.services.chat_history import ChatHistoryConverter


def _user_event(content: str = "hello") -> dict:
    """Build a minimal User/query event dict."""
    return {"source": "User", "type": "query", "content": content}


def _thought_event(content: str = "thinking...") -> dict:
    """Build a MatMaster thought event dict."""
    return {"source": "MatMaster", "type": "thought", "content": content}


def _tool_call_event(
    call_id: str = "tc_1", name: str = "bash", args: dict | None = None
) -> dict:
    """Build a tool_call event dict."""
    return {
        "source": "MatMaster",
        "type": "tool_call",
        "content": {"id": call_id, "name": name, "args": args or {"cmd": "ls"}},
    }


def _tool_result_event(
    call_id: str = "tc_1", name: str = "bash", result: str = "file.txt"
) -> dict:
    """Build a tool_result event dict."""
    return {
        "source": "MatMaster",
        "type": "tool_result",
        "content": {"id": call_id, "name": name, "result": result},
    }


def _finish_event(content: str = "done") -> dict:
    """Build a MatMaster finish event dict."""
    return {"source": "MatMaster", "type": "finish", "content": content}


class TestEventsToMessagesUserEvent:
    """events_to_messages converts user event dict to UserMessage."""

    def test_single_user_event(self):
        events = [_user_event("hello")]
        result = ChatHistoryConverter.events_to_messages(events)
        assert len(result) == 1
        assert isinstance(result[0], UserMessage)
        assert result[0].content == "hello"

    def test_user_event_with_empty_content(self):
        events = [_user_event("")]
        result = ChatHistoryConverter.events_to_messages(events)
        assert len(result) == 1
        assert isinstance(result[0], UserMessage)
        assert result[0].content == ""


class TestEventsToMessagesAssistantWithToolCalls:
    """events_to_messages converts assistant event with tool_calls to AssistantMessage with ToolCallData."""

    def test_thought_then_tool_call_and_result(self):
        events = [
            _user_event("list files"),
            _tool_call_event("tc_1", "bash", {"cmd": "ls"}),
            _tool_result_event("tc_1", "bash", "file.txt"),
            _finish_event("here are your files"),
        ]
        result = ChatHistoryConverter.events_to_messages(events)

        # Should have: UserMessage, AssistantMessage(tool_calls), ToolMessage, AssistantMessage(finish)
        assert len(result) == 4

        # First: UserMessage
        assert isinstance(result[0], UserMessage)

        # Second: AssistantMessage with tool_calls
        assert isinstance(result[1], AssistantMessage)
        assert result[1].tool_calls is not None
        assert len(result[1].tool_calls) == 1
        tc = result[1].tool_calls[0]
        assert isinstance(tc, ToolCallData)
        assert tc.id == "tc_1"
        assert tc.name == "bash"
        assert tc.arguments == {"cmd": "ls"}

        # Third: ToolMessage
        assert isinstance(result[2], ToolMessage)
        assert result[2].tool_call_id == "tc_1"
        assert result[2].tool_name == "bash"

        # Fourth: AssistantMessage (finish)
        assert isinstance(result[3], AssistantMessage)
        assert result[3].content == "here are your files"


class TestEventsToMessagesToolEvent:
    """events_to_messages converts tool result event to ToolMessage."""

    def test_tool_result_fields(self):
        events = [
            _user_event("run something"),
            _tool_call_event("tc_99", "editor", {"file": "a.py"}),
            _tool_result_event("tc_99", "editor", "saved"),
        ]
        result = ChatHistoryConverter.events_to_messages(events)

        tool_msgs = [m for m in result if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_call_id == "tc_99"
        assert tool_msgs[0].tool_name == "editor"


class TestEventsToMessagesEmptyInput:
    """events_to_messages returns empty list for empty input."""

    def test_empty_list(self):
        result = ChatHistoryConverter.events_to_messages([])
        assert result == []


class TestEventsToMessagesPreservesOrder:
    """events_to_messages preserves event ordering (user -> assistant -> tool -> user)."""

    def test_multi_turn_order(self):
        events = [
            # Turn 1
            _user_event("first question"),
            _tool_call_event("tc_1", "bash", {"cmd": "echo hi"}),
            _tool_result_event("tc_1", "bash", "hi"),
            _finish_event("answer 1"),
            # Turn 2
            _user_event("second question"),
            _finish_event("answer 2"),
        ]
        result = ChatHistoryConverter.events_to_messages(events)

        expected_types = [
            UserMessage,       # first question
            AssistantMessage,  # tool_calls
            ToolMessage,       # tool result
            AssistantMessage,  # finish answer 1
            UserMessage,       # second question
            AssistantMessage,  # finish answer 2
        ]
        assert len(result) == len(expected_types)
        for msg, expected_type in zip(result, expected_types):
            assert isinstance(msg, expected_type), (
                f"Expected {expected_type.__name__}, got {type(msg).__name__}"
            )
