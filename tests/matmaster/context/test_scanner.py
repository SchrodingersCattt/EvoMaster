from __future__ import annotations

from matmaster.context.ports import SessionEvent
from matmaster.context.scanner import SkillHitRecord, scan_skill_hits


def test_scan_skill_hits_accepts_session_events() -> None:
    events = (
        SessionEvent(id=1, event_type="query", source=None, content={"value": "skip"}),
        SessionEvent(
            id=2,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "pxrd"},
            created_at_ms=1767225600000,
        ),
        SessionEvent(
            id=3,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "mlip"},
        ),
        SessionEvent(
            id=4,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "pxrd"},
        ),
        SessionEvent(
            id=5,
            event_type="skill_hit",
            source=None,
            content={"skill_name": ""},
        ),
    )

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(
            skill_name="pxrd",
            event_id=2,
            created_at_ms=1767225600000,
        ),
        SkillHitRecord(skill_name="mlip", event_id=3, created_at_ms=None),
    )


def test_scan_skill_hits_accepts_value_wrapped_legacy_string_content() -> None:
    events = (
        SessionEvent(
            id=7,
            event_type="skill_hit",
            source=None,
            content={"value": "search"},
        ),
    )

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(skill_name="search", event_id=7, created_at_ms=None),
    )


def test_scan_skill_hits_accepts_content_wrapped_migration_rows() -> None:
    events = (
        SessionEvent(
            id=8,
            event_type="skill_hit",
            source=None,
            content={"content": "legacy-search"},
        ),
    )

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(skill_name="legacy-search", event_id=8, created_at_ms=None),
    )
