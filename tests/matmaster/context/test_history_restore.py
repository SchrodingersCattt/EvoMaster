from __future__ import annotations

from typing import Any

from matmaster.context.history_restore import ModelHistoryRestorer
from matmaster.types.messages import (
    AssistantMessage,
    Message,
    ToolMessage,
    UserMessage,
)


def _build(
    *,
    checkpoint: dict[str, Any] | None = None,
    events_after: list[dict[str, Any]] | None = None,
    has_utc: bool = False,
    legacy: list[Message] | None = None,
):
    calls: dict[str, list] = {
        "checkpoint": [],
        "events_after": [],
        "has_utc": [],
        "legacy": [],
    }

    def get_latest_checkpoint(session_id: str, spawn_id: str | None) -> dict | None:
        calls["checkpoint"].append((session_id, spawn_id))
        return checkpoint

    def get_events_after(
        session_id: str,
        after_id: int | None,
        spawn_id: str | None,
    ) -> list[dict]:
        calls["events_after"].append((session_id, after_id, spawn_id))
        return events_after or []

    def has_user_turn_context(session_id: str, spawn_id: str | None) -> bool:
        calls["has_utc"].append((session_id, spawn_id))
        return has_utc

    def legacy_restore(session_id: str, spawn_id: str | None) -> list[Message]:
        calls["legacy"].append((session_id, spawn_id))
        return legacy or []

    def deserialize_base_messages(raw: list[dict[str, Any]]) -> list[Message]:
        return [
            UserMessage.model_validate(item)
            if item.get("role") == "user"
            else AssistantMessage.model_validate(item)
            for item in raw
            if item.get("role") in {"user", "assistant"}
        ]

    def events_to_messages(events: list[dict[str, Any]]) -> list[Message]:
        messages: list[Message] = []
        for event in events:
            etype = event.get("type")
            payload = event.get("content") or {}
            if event.get("source") == "User" and etype == "query":
                messages.append(UserMessage(content=str(payload.get("content") or "")))
            elif etype in {"response", "run_result", "finish"}:
                messages.append(
                    AssistantMessage(content=str(payload.get("content") or ""))
                )
            elif etype == "assistant_state":
                state = payload.get("state") or payload
                messages.append(AssistantMessage.model_validate(state))
            elif etype == "tool_result":
                messages.append(
                    ToolMessage(
                        content=str(payload.get("result", "")),
                        tool_call_id=str(
                            payload.get("id") or payload.get("call_id") or ""
                        ),
                        tool_name=str(
                            payload.get("name") or payload.get("tool_name") or ""
                        ),
                    )
                )
        return messages

    def normalize_tool_result_event(event: dict[str, Any]) -> dict[str, Any]:
        content = dict(event.get("content") or {})
        if "id" not in content and content.get("call_id"):
            content["id"] = content["call_id"]
        if "name" not in content and content.get("tool_name"):
            content["name"] = content["tool_name"]
        return {**event, "content": content}

    restorer = ModelHistoryRestorer(
        get_latest_checkpoint=get_latest_checkpoint,
        get_events_after=get_events_after,
        has_user_turn_context=has_user_turn_context,
        legacy_restore=legacy_restore,
        deserialize_base_messages=deserialize_base_messages,
        events_to_messages=events_to_messages,
        normalize_tool_result_event=normalize_tool_result_event,
    )
    return restorer, calls


def test_restore_pure_v0_delegates_to_legacy() -> None:
    restorer, calls = _build(
        checkpoint=None,
        has_utc=False,
        legacy=[UserMessage(content="legacy")],
    )

    result = restorer.restore("sess-1")

    assert len(result) == 1
    assert isinstance(result[0], UserMessage)
    assert result[0].content == "legacy"
    assert calls["legacy"] == [("sess-1", None)]
    assert calls["events_after"] == []


def test_restore_v0_checkpoint_falls_back_to_legacy() -> None:
    restorer, calls = _build(
        checkpoint={"content": {"schema_version": "checkpoint.v0"}, "id": 99},
        has_utc=False,
        legacy=[UserMessage(content="legacy")],
    )

    result = restorer.restore("sess-1")

    assert len(result) == 1
    assert calls["legacy"] == [("sess-1", None)]


def test_restore_hybrid_v1_consumes_uncovered_user_query() -> None:
    events = [
        {
            "id": 5,
            "type": "query",
            "source": "User",
            "content": {"content": "pre-Phase-1 turn"},
            "invocation_id": "inv-old",
        },
        {
            "id": 6,
            "type": "response",
            "content": {"content": "old response"},
        },
        {
            "id": 7,
            "type": "user_turn_context",
            "invocation_id": "inv-new",
            "content": {
                "message": {
                    "role": "user",
                    "content": "rendered new turn",
                }
            },
        },
    ]
    restorer, calls = _build(checkpoint=None, events_after=events, has_utc=True)

    result = restorer.restore("sess-1")

    contents = [m.content for m in result]
    assert "pre-Phase-1 turn" in contents
    assert "old response" in contents
    assert "rendered new turn" in contents
    assert calls["legacy"] == []


def test_restore_hybrid_v1_skips_user_query_covered_by_utc() -> None:
    events = [
        {
            "id": 5,
            "type": "query",
            "source": "User",
            "content": {"content": "old raw"},
            "invocation_id": "inv-1",
        },
        {
            "id": 6,
            "type": "user_turn_context",
            "invocation_id": "inv-1",
            "content": {
                "message": {"role": "user", "content": "rendered with anchor"},
            },
        },
    ]
    restorer, _ = _build(checkpoint=None, events_after=events, has_utc=True)

    result = restorer.restore("sess-1")

    contents = [m.content for m in result]
    assert "old raw" not in contents
    assert "rendered with anchor" in contents


def test_restore_pure_v1_loads_base_messages_and_skips_user_query() -> None:
    checkpoint = {
        "id": 99,
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": 50,
            "base_messages": [
                {"role": "user", "content": "summary as user"},
            ],
        },
    }
    events_after = [
        {
            "id": 51,
            "type": "query",
            "source": "User",
            "content": {"content": "should be skipped"},
            "invocation_id": "inv-after-checkpoint",
        },
        {
            "id": 52,
            "type": "user_turn_context",
            "invocation_id": "inv-after-checkpoint",
            "content": {
                "message": {"role": "user", "content": "rendered after checkpoint"},
            },
        },
        {
            "id": 53,
            "type": "tool_result",
            "content": {"result": "tool out", "call_id": "c1", "tool_name": "t"},
        },
    ]
    restorer, calls = _build(checkpoint=checkpoint, events_after=events_after)

    result = restorer.restore("sess-1")

    assert calls["legacy"] == []
    assert calls["events_after"][0] == ("sess-1", 50, None)
    contents = [m.content for m in result]
    assert contents[0] == "summary as user"
    assert "should be skipped" not in contents
    assert "rendered after checkpoint" in contents
    assert any(isinstance(m, ToolMessage) and m.content == "tool out" for m in result)


def test_restore_pure_v1_with_null_covered_until_falls_back_to_legacy() -> None:
    checkpoint = {
        "id": 99,
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": None,
            "base_messages": [],
        },
    }
    restorer, calls = _build(
        checkpoint=checkpoint,
        legacy=[UserMessage(content="from legacy")],
    )

    result = restorer.restore("sess-1")

    assert len(result) == 1
    assert result[0].content == "from legacy"
    assert calls["legacy"] == [("sess-1", None)]


def test_restore_pure_v1_consumes_assistant_state_and_response() -> None:
    checkpoint = {
        "id": 99,
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": 0,
            "base_messages": [],
        },
    }
    events_after = [
        {
            "id": 1,
            "type": "user_turn_context",
            "invocation_id": "inv-1",
            "content": {
                "message": {"role": "user", "content": "ask"},
            },
        },
        {
            "id": 2,
            "type": "response",
            "content": {"content": "natural reply"},
        },
        {
            "id": 3,
            "type": "user_turn_context",
            "invocation_id": "inv-2",
            "content": {"message": {"role": "user", "content": "next ask"}},
        },
        {
            "id": 4,
            "type": "assistant_state",
            "content": {
                "state": {
                    "role": "assistant",
                    "content": "calling tool",
                    "tool_calls": [],
                }
            },
        },
    ]
    restorer, _ = _build(checkpoint=checkpoint, events_after=events_after)

    result = restorer.restore("sess-1")

    user_msgs = [m for m in result if isinstance(m, UserMessage)]
    asst_msgs = [m for m in result if isinstance(m, AssistantMessage)]

    assert [m.content for m in user_msgs] == ["ask", "next ask"]
    assert [m.content for m in asst_msgs] == ["natural reply", "calling tool"]


def test_restore_passes_spawn_id_to_callbacks() -> None:
    restorer, calls = _build(
        checkpoint=None,
        has_utc=False,
        legacy=[],
    )

    restorer.restore("sess-1", spawn_id="spawn-A")

    assert calls["legacy"] == [("sess-1", "spawn-A")]
    assert calls["has_utc"] == [("sess-1", "spawn-A")]
