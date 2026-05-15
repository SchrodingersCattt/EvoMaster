from __future__ import annotations

from typing import Any

from matmaster.types.message_normalization import normalize_and_validate_openai_messages
from matmaster.types.messages import (
    AssistantMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
from src.services.history_checkpoint_codec import serialize_base_messages
from src.services.model_history_restore_service import ModelHistoryRestoreService


def _utc(
    content: str,
    *,
    event_id: int,
    invocation_id: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "session_id": "sess-1",
        "source": "MatMaster",
        "type": "user_turn_context",
        "content": {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(content=content).model_dump(mode="json"),
            "user_instructions_hash": "sha256:abc",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
        "task_id": task_id,
        "invocation_id": invocation_id,
        "spawn_id": None,
    }


def _assistant_state(
    content: str,
    *,
    event_id: int,
    call_id: str = "call-1",
    task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "session_id": "sess-1",
        "source": "MatMaster",
        "type": "assistant_state",
        "content": {
            "state": AssistantMessage(
                content=content,
                tool_calls=[
                    ToolCallData(
                        id=call_id,
                        name="search_materials",
                        arguments={"formula": "Si"},
                    )
                ],
            ).model_dump(mode="json")
        },
        "task_id": task_id,
        "invocation_id": "inv-assistant",
        "spawn_id": None,
    }


def _tool_result(
    result: Any,
    *,
    event_id: int,
    call_id: str = "call-1",
    tool_name: str = "search_materials",
    task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "session_id": "sess-1",
        "source": "Tool",
        "type": "tool_result",
        "content": {
            "call_id": call_id,
            "tool_name": tool_name,
            "result": result,
        },
        "task_id": task_id,
        "invocation_id": "inv-tool",
        "spawn_id": None,
    }


def _tool_call(
    *,
    event_id: int,
    call_id: str = "call-1",
    tool_name: str = "search_materials",
    task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "session_id": "sess-1",
        "source": "MatMaster",
        "type": "tool_call",
        "content": {
            "id": call_id,
            "name": tool_name,
            "args": {"formula": "Si"},
        },
        "task_id": task_id,
        "invocation_id": "inv-tool-call",
        "spawn_id": None,
    }


def _response(
    content: str,
    *,
    event_id: int,
    task_id: str | None = None,
    reasoning_content: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": content}
    if reasoning_content is not None:
        payload["reasoning_content"] = reasoning_content
    return {
        "id": event_id,
        "session_id": "sess-1",
        "source": "MatMaster",
        "type": "response",
        "content": payload,
        "task_id": task_id,
        "invocation_id": "inv-response",
        "spawn_id": None,
    }


def _run_result(
    content: str,
    *,
    event_id: int,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "session_id": "sess-1",
        "source": "MatMaster",
        "type": "run_result",
        "content": {"content": content},
        "task_id": task_id,
        "invocation_id": "inv-run-result",
        "spawn_id": None,
    }


def _bad_assistant_state(*, event_id: int) -> dict[str, Any]:
    return {
        "id": event_id,
        "session_id": "sess-1",
        "source": "MatMaster",
        "type": "assistant_state",
        "content": {
            "state": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "bad-call"}],
            }
        },
        "task_id": "task-old",
        "invocation_id": "inv-bad-assistant",
        "spawn_id": None,
    }


def _raw_user(
    content: str,
    *,
    event_id: int,
    invocation_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "session_id": "sess-1",
        "source": "User",
        "type": "query",
        "content": content,
        "task_id": task_id,
        "invocation_id": invocation_id,
        "spawn_id": None,
    }


def _v1_checkpoint(
    *,
    checkpoint_id: int = 10,
    covered_until_event_id: int | None = 10,
    summary: str = "checkpoint summary",
) -> dict[str, Any]:
    return {
        "id": checkpoint_id,
        "session_id": "sess-1",
        "source": "System",
        "type": "history_checkpoint",
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": covered_until_event_id,
            "base_messages": serialize_base_messages(
                [
                    UserMessage(
                        content=(
                            "<compacted_history>\n"
                            f"{summary}\n"
                            "</compacted_history>"
                        )
                    )
                ]
            ),
        },
        "task_id": "task-checkpoint",
        "invocation_id": "inv-checkpoint",
        "spawn_id": None,
    }


class FakeEventsTable:
    def __init__(
        self,
        *,
        checkpoints: list[dict[str, Any]] | None = None,
        scope_events: list[dict[str, Any]] | None = None,
        session_events: list[dict[str, Any]] | None = None,
        has_utc: bool = False,
    ) -> None:
        self.checkpoints = checkpoints or []
        self.scope_events = scope_events or []
        self.session_events = session_events or []
        self.has_utc = has_utc
        self.calls: list[tuple[Any, ...]] = []

    def get_history_checkpoints(
        self, session_id: str, spawn_id: str | None, limit: int = 5
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_history_checkpoints", session_id, spawn_id, limit))
        return list(self.checkpoints)

    def get_scope_events_after_id(
        self,
        session_id: str,
        spawn_id: str | None,
        after_id: int | None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            ("get_scope_events_after_id", session_id, spawn_id, after_id, limit)
        )
        return list(self.scope_events)

    def get_session_events(
        self,
        session_id: str,
        limit: int | None = None,
        include_spawn: bool = False,
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_session_events", session_id, limit, include_spawn))
        return list(self.session_events)

    def has_user_turn_context(
        self,
        session_id: str,
        spawn_id: str | None,
    ) -> bool:
        self.calls.append(("has_user_turn_context", session_id, spawn_id))
        return self.has_utc


def test_no_checkpoint_without_user_turn_context_uses_legacy_restore() -> None:
    events_table = FakeEventsTable(
        session_events=[
            _raw_user("legacy question", event_id=1, invocation_id="inv-1"),
            _response("legacy answer", event_id=2),
        ],
        has_utc=False,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
        raw_limit=50,
    )

    assert [message.role for message in history] == ["user", "assistant"]
    assert [message.content for message in history] == [
        "legacy question",
        "legacy answer",
    ]
    assert ("has_user_turn_context", "sess-1", None) in events_table.calls
    assert not any(
        call[0] == "get_scope_events_after_id" for call in events_table.calls
    )


def test_no_checkpoint_with_user_turn_context_uses_v1_restore() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _utc("context question", event_id=1, invocation_id="inv-1"),
            _response("context answer", event_id=2),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [type(message) for message in history] == [UserMessage, AssistantMessage]
    assert [message.content for message in history] == [
        "context question",
        "context answer",
    ]
    assert (
        "get_scope_events_after_id",
        "sess-1",
        None,
        None,
        None,
    ) in events_table.calls
    assert not any(call[0] == "get_session_events" for call in events_table.calls)


def test_v1_checkpoint_restores_base_messages_then_tail_events() -> None:
    events_table = FakeEventsTable(
        checkpoints=[_v1_checkpoint(covered_until_event_id=5, summary="base")],
        scope_events=[
            _utc("tail question", event_id=6, invocation_id="inv-tail"),
            _response("tail answer", event_id=7),
        ],
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [type(message) for message in history] == [
        UserMessage,
        UserMessage,
        AssistantMessage,
    ]
    assert "base" in (history[0].content or "")
    assert [message.content for message in history[1:]] == [
        "tail question",
        "tail answer",
    ]
    assert ("get_scope_events_after_id", "sess-1", None, 5, None) in events_table.calls


def test_v1_restore_consumes_assistant_state_and_tool_result() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _utc("find silicon", event_id=1, invocation_id="inv-1"),
            _assistant_state("I will search.", event_id=2),
            _tool_result({"matches": 3}, event_id=3),
            _response("found results", event_id=4),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [type(message) for message in history] == [
        UserMessage,
        AssistantMessage,
        ToolMessage,
        AssistantMessage,
    ]
    assistant = history[1]
    tool = history[2]
    assert isinstance(assistant, AssistantMessage)
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0].id == "call-1"
    assert isinstance(tool, ToolMessage)
    assert tool.tool_call_id == "call-1"
    assert tool.tool_name == "search_materials"
    assert tool.content == '{"matches": 3}'
    normalize_and_validate_openai_messages(history)


def test_v1_restore_does_not_duplicate_response_and_run_result() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _utc("question", event_id=1, invocation_id="inv-1"),
            _response("answer", event_id=2),
            _run_result("answer", event_id=3),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [type(message) for message in history] == [UserMessage, AssistantMessage]
    assert [message.content for message in history] == ["question", "answer"]


def test_v1_restore_pairs_tool_call_and_public_tool_result_payload() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _utc("find silicon", event_id=1, invocation_id="inv-1"),
            _tool_call(event_id=2),
            _tool_result({"matches": 3}, event_id=3),
            _response("found results", event_id=4),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [type(message) for message in history] == [
        UserMessage,
        AssistantMessage,
        ToolMessage,
        AssistantMessage,
    ]
    assistant = history[1]
    tool = history[2]
    assert isinstance(assistant, AssistantMessage)
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0].id == "call-1"
    assert isinstance(tool, ToolMessage)
    assert tool.tool_call_id == "call-1"
    assert tool.tool_name == "search_materials"
    assert tool.content == '{"matches": 3}'
    normalize_and_validate_openai_messages(history)


def test_v1_restore_skips_orphan_tool_result() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _utc("find silicon", event_id=1, invocation_id="inv-1"),
            _tool_result("orphan", event_id=2, call_id="missing"),
            _response("done", event_id=3),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [type(message) for message in history] == [UserMessage, AssistantMessage]
    assert [message.content for message in history] == ["find silicon", "done"]
    normalize_and_validate_openai_messages(history)


def test_hybrid_v1_skips_bad_assistant_state_and_continues() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _utc("question", event_id=1, invocation_id="inv-1"),
            _bad_assistant_state(event_id=2),
            _response("answer after bad state", event_id=3),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [type(message) for message in history] == [UserMessage, AssistantMessage]
    assert [message.content for message in history] == [
        "question",
        "answer after bad state",
    ]


def test_v1_restore_excludes_current_task_events() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _utc("previous question", event_id=1, invocation_id="inv-prev"),
            _response("previous answer", event_id=2),
            _utc(
                "current question",
                event_id=3,
                invocation_id="inv-current",
                task_id="task-current",
            ),
            _response("current answer", event_id=4, task_id="task-current"),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id="task-current",
    )

    assert [message.content for message in history] == [
        "previous question",
        "previous answer",
    ]


def test_v1_checkpoint_with_null_boundary_falls_back_to_legacy() -> None:
    events_table = FakeEventsTable(
        checkpoints=[_v1_checkpoint(covered_until_event_id=None)],
        session_events=[
            _raw_user("legacy fallback question", event_id=1),
            _response("legacy fallback answer", event_id=2),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [message.content for message in history] == [
        "legacy fallback question",
        "legacy fallback answer",
    ]
    assert any(call[0] == "get_session_events" for call in events_table.calls)


def test_hybrid_v1_keeps_pre_phase1_user_query_without_utc() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _raw_user("pre phase 1 question", event_id=1, invocation_id="legacy-inv"),
            _response("pre phase 1 answer", event_id=2),
            _utc("phase 1 question", event_id=3, invocation_id="new-inv"),
            _response("phase 1 answer", event_id=4),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [message.content for message in history] == [
        "pre phase 1 question",
        "pre phase 1 answer",
        "phase 1 question",
        "phase 1 answer",
    ]


def test_hybrid_v1_skips_covered_user_query() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _raw_user("raw duplicate", event_id=1, invocation_id="covered-inv"),
            _utc("canonical user context", event_id=2, invocation_id="covered-inv"),
            _response("answer", event_id=3),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [message.content for message in history] == [
        "canonical user context",
        "answer",
    ]


def test_hybrid_v1_keeps_pre_phase1_user_query_without_invocation_id() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _raw_user("old query without invocation", event_id=1),
            _utc("new context", event_id=2, invocation_id="new-inv"),
        ],
        has_utc=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert [message.content for message in history] == [
        "old query without invocation",
        "new context",
    ]
