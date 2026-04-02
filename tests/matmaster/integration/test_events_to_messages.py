"""Behavioral tests for ChatHistoryConverter.events_to_messages().

Verifies that DB event dicts are correctly converted to matmaster
types Message types (UserMessage, AssistantMessage, ToolMessage).
"""

from __future__ import annotations

import pytest

from matmaster.types.messages import (
    AssistantMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)

ChatHistoryConverter = pytest.importorskip(
    "src.services.chat_history",
    reason="src not available (isolation test)",
).ChatHistoryConverter


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


def _run_result_event(content: str = "done") -> dict:
    """Build a MatMaster run_result event dict."""
    return {"source": "MatMaster", "type": "run_result", "content": content}


def _response_event(content: str = "done") -> dict:
    """Build a MatMaster response event dict."""
    return {"source": "MatMaster", "type": "response", "content": content}


def _assistant_state_event(
    *,
    content: str = "",
    reasoning_content: str | None = None,
    tool_calls: list[dict] | None = None,
) -> dict:
    """Build a MatMaster assistant_state event dict."""
    payload: dict = {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls or [],
    }
    if reasoning_content is not None:
        payload["reasoning_content"] = reasoning_content
    return {"source": "MatMaster", "type": "assistant_state", "content": payload}


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
            _run_result_event("here are your files"),
        ]
        result = ChatHistoryConverter.events_to_messages(events)

        # Should have: UserMessage, AssistantMessage(tool_calls), ToolMessage, AssistantMessage(run_result)
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

        # Fourth: AssistantMessage (run_result)
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
            _run_result_event("answer 1"),
            # Turn 2
            _user_event("second question"),
            _run_result_event("answer 2"),
        ]
        result = ChatHistoryConverter.events_to_messages(events)

        expected_types = [
            UserMessage,  # first question
            AssistantMessage,  # tool_calls
            ToolMessage,  # tool result
            AssistantMessage,  # run_result answer 1
            UserMessage,  # second question
            AssistantMessage,  # run_result answer 2
        ]
        assert len(result) == len(expected_types)
        for msg, expected_type in zip(result, expected_types):
            assert isinstance(
                msg, expected_type
            ), f"Expected {expected_type.__name__}, got {type(msg).__name__}"

    def test_legacy_finish_events_still_map_to_assistant_messages(self):
        events = [
            _user_event("legacy question"),
            {"source": "MatMaster", "type": "finish", "content": "legacy answer"},
        ]

        result = ChatHistoryConverter.events_to_messages(events)

        assert len(result) == 2
        assert isinstance(result[1], AssistantMessage)
        assert result[1].content == "legacy answer"

    def test_response_event_becomes_assistant_message(self):
        events = [_user_event("q"), _response_event("answer")]

        result = ChatHistoryConverter.events_to_messages(events)

        assert len(result) == 2
        assert isinstance(result[-1], AssistantMessage)
        assert result[-1].content == "answer"

    def test_thought_and_response_merge_into_single_assistant_message(self):
        events = [
            _user_event("q"),
            _thought_event("thinking first"),
            _response_event("answer"),
            _run_result_event("answer"),
        ]

        result = ChatHistoryConverter.events_to_messages(events)

        assert len(result) == 2
        assert isinstance(result[-1], AssistantMessage)
        assert result[-1].content == "answer"
        assert result[-1].reasoning_content == "thinking first"

    def test_run_result_is_only_legacy_fallback_when_response_missing(self):
        events = [_run_result_event("legacy answer")]

        result = ChatHistoryConverter.events_to_messages(events)

        assert len(result) == 1
        assert isinstance(result[-1], AssistantMessage)
        assert result[-1].content == "legacy answer"

    def test_assistant_state_reasoning_round_trips_to_matmaster_messages(self):
        events = [
            _user_event("q"),
            _assistant_state_event(
                content="answer",
                reasoning_content="hidden reasoning",
            ),
        ]

        result = ChatHistoryConverter.events_to_messages(events)

        assert len(result) == 2
        assert isinstance(result[-1], AssistantMessage)
        assert result[-1].content == "answer"
        assert result[-1].reasoning_content == "hidden reasoning"


class TestEventsToMessagesPersistenceRoundTrip:
    """Persisted public content shape remains readable by ChatHistoryConverter."""

    def test_tool_call_public_shape_round_trips(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event
        from matmaster.types.events import ToolCallEvent

        event = ToolCallEvent(
            source="Agent",
            call_id="tc_1",
            tool_name="bash",
            arguments={"cmd": "ls"},
        )
        persisted = _public_content_for_event(
            "tool_call", event.model_dump(mode="json")
        )
        db_event = {"source": "MatMaster", "type": "tool_call", "content": persisted}

        result = ChatHistoryConverter.events_to_messages(
            [_user_event("run"), db_event, _tool_result_event("tc_1", "bash", "ok")]
        )

        assistant_msgs = [m for m in result if isinstance(m, AssistantMessage)]
        assert len(assistant_msgs) >= 1
        tc = assistant_msgs[0].tool_calls[0]
        assert tc.id == "tc_1"
        assert tc.name == "bash"
        assert tc.arguments == {"cmd": "ls"}

    def test_tool_result_public_shape_round_trips(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event
        from matmaster.types.events import ToolResultEvent

        event = ToolResultEvent(
            source="Agent",
            call_id="tc_1",
            tool_name="bash",
            result="file.txt",
        )
        persisted = _public_content_for_event(
            "tool_result", event.model_dump(mode="json")
        )
        db_event = {"source": "MatMaster", "type": "tool_result", "content": persisted}

        result = ChatHistoryConverter.events_to_messages(
            [_user_event("run"), _tool_call_event("tc_1", "bash"), db_event]
        )

        tool_msgs = [m for m in result if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_call_id == "tc_1"
        assert tool_msgs[0].content == "file.txt"

    def test_run_result_dict_shape_round_trips(self) -> None:
        from matmaster.integration.event_payloads import _public_content_for_event
        from matmaster.types.events import RunResultEvent

        event = RunResultEvent(
            source="Agent",
            status="completed",
            reason="natural",
            final_content="here are your files",
        )
        persisted = _public_content_for_event(
            "run_result", event.model_dump(mode="json")
        )
        db_event = {"source": "MatMaster", "type": "run_result", "content": persisted}

        result = ChatHistoryConverter.events_to_messages(
            [_user_event("list files"), db_event]
        )

        assistant_msgs = [m for m in result if isinstance(m, AssistantMessage)]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].content == "here are your files"

    def test_legacy_run_result_string_still_round_trips(self) -> None:
        db_event = {
            "source": "MatMaster",
            "type": "run_result",
            "content": "legacy string answer",
        }

        result = ChatHistoryConverter.events_to_messages(
            [_user_event("question"), db_event]
        )

        assistant_msgs = [m for m in result if isinstance(m, AssistantMessage)]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].content == "legacy string answer"


class TestExcludeSpawnForParentDialog:
    """Parent LLM history must ignore persisted sub-agent rows (spawn_id set)."""

    def test_exclude_spawn_events_drops_subagent_rows(self) -> None:
        events = [
            _user_event("hello"),
            {
                **_response_event("parent"),
                "task_id": "t1",
                "spawn_id": None,
            },
            {
                **_response_event("subagent only"),
                "task_id": "t1",
                "spawn_id": "sp-1",
            },
        ]
        filtered = ChatHistoryConverter.exclude_spawn_events(events)
        assert len(filtered) == 2
        assert all(ev.get("spawn_id") is None for ev in filtered)
        msgs = ChatHistoryConverter.events_to_messages(filtered)
        assistant = [m for m in msgs if isinstance(m, AssistantMessage)]
        assert len(assistant) == 1
        assert assistant[0].content == "parent"

    def test_agent_run_style_pipeline_excludes_spawn_before_task_filter(self) -> None:
        """Mirrors agent_run_service: exclude_spawn -> exclude_task_events -> events_to_messages."""
        raw = [
            _user_event("q"),
            {**_response_event("current turn"), "task_id": "t-new", "spawn_id": None},
            {**_response_event("sub"), "task_id": "t-new", "spawn_id": "s1"},
        ]
        step1 = ChatHistoryConverter.exclude_spawn_events(raw)
        step2 = ChatHistoryConverter.exclude_task_events(step1, "t-new")
        msgs = ChatHistoryConverter.events_to_messages(step2)
        assert len(msgs) == 1
        assert isinstance(msgs[0], UserMessage)
        assert msgs[0].content == "q"
