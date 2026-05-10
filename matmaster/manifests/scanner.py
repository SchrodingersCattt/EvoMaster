from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SkillHitRecord:
    skill_name: str
    event_id: int | None = None
    timestamp: str | None = None


def _event_id(event: dict[str, Any]) -> int | None:
    raw = event.get("id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _skill_name(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("skill_name") or "").strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def scan_skill_hits(events: list[dict[str, Any]]) -> list[SkillHitRecord]:
    seen: set[str] = set()
    records: list[SkillHitRecord] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "skill_hit":
            continue
        name = _skill_name(event.get("content"))
        if not name or name in seen:
            continue
        seen.add(name)
        timestamp = event.get("created_at")
        records.append(
            SkillHitRecord(
                skill_name=name,
                event_id=_event_id(event),
                timestamp=str(timestamp) if timestamp is not None else None,
            )
        )
    return records

