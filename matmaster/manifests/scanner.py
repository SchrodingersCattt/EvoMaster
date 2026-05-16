"""Phase 2B shim delegating to matmaster.context.scanner."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.context.scanner import SkillHitRecord

__all__ = ["SkillHitRecord", "scan_skill_hits"]


def _legacy_event_id(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _legacy_skill_name(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("skill_name") or "").strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def scan_skill_hits(events: Iterable[dict[str, Any]]) -> list[SkillHitRecord]:
    seen: set[str] = set()
    records: list[SkillHitRecord] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "skill_hit":
            continue
        name = _legacy_skill_name(event.get("content"))
        if not name or name in seen:
            continue
        seen.add(name)
        timestamp = event.get("created_at")
        records.append(
            SkillHitRecord(
                skill_name=name,
                event_id=_legacy_event_id(event.get("id")),
                timestamp=str(timestamp) if timestamp is not None else None,
            )
        )
    return records
