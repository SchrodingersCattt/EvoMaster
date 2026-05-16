from __future__ import annotations

from matmaster.context.ports import SessionEvent
from matmaster.context.scanner import (
    SkillHitRecord,
    coerce_session_events,
    scan_skill_hits,
)


def test_coerce_session_events_maps_basic_fields() -> None:
    rows = [
        {
            "id": 10,
            "type": "query",
            "source": "User",
            "content": {"content": "hi", "files": ["a"]},
            "invocation_id": "inv-1",
            "spawn_id": None,
            "task_id": "task-1",
        },
        {
            "id": 11,
            "type": "skill_hit",
            "source": "System",
            "content": {"skill_name": "pxrd"},
        },
    ]

    events = coerce_session_events(rows)

    assert isinstance(events, tuple)
    assert len(events) == 2
    assert events[0] == SessionEvent(
        id=10,
        event_type="query",
        source="User",
        content={"content": "hi", "files": ("a",)},
        invocation_id="inv-1",
        spawn_id=None,
        task_id="task-1",
    )
    assert events[1].invocation_id is None
    assert events[1].content["skill_name"] == "pxrd"


def test_coerce_session_events_freezes_nested_lists_into_tuples() -> None:
    rows = [
        {
            "id": 7,
            "type": "query",
            "source": "User",
            "content": {
                "files": ["a", "b"],
                "images": ["c"],
                "nested": {"deep": ["x"]},
            },
        }
    ]

    events = coerce_session_events(rows)

    assert events[0].content["files"] == ("a", "b")
    assert events[0].content["images"] == ("c",)
    assert events[0].content["nested"]["deep"] == ("x",)


def test_coerce_session_events_drops_rows_without_int_id() -> None:
    rows = [
        {"id": None, "type": "query"},
        {"id": "not-an-int", "type": "query"},
        {"id": 9, "type": "query", "content": None, "source": None},
    ]

    events = coerce_session_events(rows)

    assert len(events) == 1
    assert events[0].id == 9
    assert events[0].source is None
    assert events[0].content == {}


def test_scan_skill_hits_accepts_session_events() -> None:
    events = coerce_session_events(
        [
            {"id": 1, "type": "query", "content": "skip"},
            {
                "id": 2,
                "type": "skill_hit",
                "content": {"skill_name": "pxrd"},
                "created_at": "2026-01-01T00:00:00",
            },
            {"id": 3, "type": "skill_hit", "content": {"skill_name": "mlip"}},
            {"id": 4, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
            {"id": 5, "type": "skill_hit", "content": {"skill_name": ""}},
        ]
    )

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(skill_name="pxrd", event_id=2, timestamp="2026-01-01T00:00:00"),
        SkillHitRecord(skill_name="mlip", event_id=3, timestamp=None),
    )


def test_scan_skill_hits_accepts_legacy_string_content_via_coerce() -> None:
    events = coerce_session_events([{"id": 7, "type": "skill_hit", "content": "search"}])

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(skill_name="search", event_id=7, timestamp=None),
    )
