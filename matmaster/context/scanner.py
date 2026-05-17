from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from matmaster.context.ports import JsonValue, SessionEvent


@dataclass(frozen=True)
class SkillHitRecord:
    skill_name: str
    event_id: int | None = None
    created_at_ms: int | None = None


def _skill_name_from_content(content: JsonValue) -> str:
    if isinstance(content, Mapping):
        raw = content.get("skill_name") or content.get("value") or content.get(
            "content"
        )
        return str(raw or "").strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def scan_skill_hits(events: Iterable[SessionEvent]) -> tuple[SkillHitRecord, ...]:
    seen: set[str] = set()
    records: list[SkillHitRecord] = []
    for event in events:
        if event.event_type != "skill_hit":
            continue
        name = _skill_name_from_content(event.content)
        if not name or name in seen:
            continue
        seen.add(name)
        records.append(
            SkillHitRecord(
                skill_name=name,
                event_id=event.id,
                created_at_ms=event.created_at_ms,
            )
        )
    return tuple(records)
