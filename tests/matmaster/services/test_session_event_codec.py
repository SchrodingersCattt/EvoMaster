from __future__ import annotations

from datetime import UTC, datetime

import pytest

from matmaster.context.ports import SessionEvent
from src.services.session_event_codec import (
    coerce_created_at_ms,
    coerce_event_id,
    decode_session_events,
    freeze_json_object,
    row_to_event,
)


def test_row_to_event_maps_basic_fields_and_normalizes_strings() -> None:
    event = row_to_event(
        {
            "id": "10",
            "event_type": " query ",
            "source": " User ",
            "content": {"content": "hi", "files": ["a"]},
            "task_id": " task-1 ",
            "invocation_id": " ",
            "spawn_id": "",
            "created_at_ms": "1234",
        }
    )

    assert event == SessionEvent(
        id=10,
        event_type="query",
        source="User",
        content={"content": "hi", "files": ("a",)},
        task_id="task-1",
        invocation_id=None,
        spawn_id=None,
        created_at_ms=1234,
    )


def test_decode_session_events_drops_rows_without_valid_id() -> None:
    events = decode_session_events(
        [
            {"id": None, "type": "query"},
            {"id": True, "type": "query"},
            {"id": "not-an-int", "type": "query"},
            {"id": 9, "type": "query", "content": None},
        ]
    )

    assert len(events) == 1
    assert events[0].id == 9
    assert events[0].content == {"value": None}


def test_decode_session_events_skips_non_mapping_rows() -> None:
    events = decode_session_events(
        [
            ["not", "a", "row"],
            {"id": 1, "type": "query", "content": {"content": "ok"}},
        ]
    )

    assert [event.id for event in events] == [1]


def test_row_to_event_rejects_non_mapping_row() -> None:
    with pytest.raises(TypeError, match="Session event row must be a mapping"):
        row_to_event(["not", "a", "row"])  # type: ignore[arg-type]


def test_row_to_event_rejects_invalid_id() -> None:
    with pytest.raises(ValueError, match="valid id"):
        row_to_event({"id": "bad", "type": "query"})


def test_freeze_json_object_rejects_non_json_schema_drift() -> None:
    with pytest.raises(TypeError, match="Unsupported JSON value type"):
        freeze_json_object({"bad": object()})


def test_freeze_json_object_wraps_non_mapping_content_with_value_key() -> None:
    assert freeze_json_object("") == {"value": ""}
    assert freeze_json_object(None) == {"value": None}


def test_coerce_event_id_rejects_bool() -> None:
    assert coerce_event_id(True) is None
    assert coerce_event_id(False) is None


def test_coerce_created_at_ms_accepts_created_at_ms_and_datetime() -> None:
    assert coerce_created_at_ms({"created_at_ms": "42"}) == 42
    assert coerce_created_at_ms(
        {"created_at": datetime(2026, 1, 1, tzinfo=UTC)}
    ) == 1767225600000


def test_coerce_created_at_ms_ignores_invalid_values() -> None:
    assert coerce_created_at_ms({"created_at_ms": True}) is None
    assert coerce_created_at_ms({"created_at_ms": "bad"}) is None
    assert coerce_created_at_ms({"created_at": "2026-01-01T00:00:00"}) is None
