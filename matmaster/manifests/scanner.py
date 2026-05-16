"""Phase 2B shim delegating to matmaster.context.scanner."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.context.scanner import (
    SkillHitRecord,
    coerce_session_events,
)
from matmaster.context.scanner import scan_skill_hits as _typed_scan_skill_hits

__all__ = ["SkillHitRecord", "scan_skill_hits"]

_SYNTHETIC_ID_START = -1_000_000_000


def _legacy_event_id(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def scan_skill_hits(events: Iterable[dict[str, Any]]) -> list[SkillHitRecord]:
    rows: list[dict[str, Any]] = []
    synthetic_ids: set[int] = set()
    next_synthetic_id = _SYNTHETIC_ID_START

    for event in events:
        if not isinstance(event, dict):
            continue
        adapted = dict(event)
        content = adapted.get("content")
        if isinstance(content, dict) and event.get("created_at") is not None:
            merged = dict(content)
            merged["created_at"] = event.get("created_at")
            adapted["content"] = merged
        if _legacy_event_id(adapted.get("id")) is None:
            adapted["id"] = next_synthetic_id
            synthetic_ids.add(next_synthetic_id)
            next_synthetic_id -= 1
        rows.append(adapted)

    typed = coerce_session_events(rows)
    records: list[SkillHitRecord] = []
    for record in _typed_scan_skill_hits(typed):
        records.append(
            SkillHitRecord(
                skill_name=record.skill_name,
                event_id=None if record.event_id in synthetic_ids else record.event_id,
                timestamp=record.timestamp,
            )
        )
    return records
