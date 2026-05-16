from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from matmaster.context.ports import JsonObject, JsonValue, SessionEvent


@dataclass(frozen=True)
class SkillHitRecord:
    skill_name: str
    event_id: int | None = None
    timestamp: str | None = None


def _freeze_json_value(value: Any) -> JsonValue:
    """Convert raw DAO row payload into the restricted ports.JsonValue tree."""
    if isinstance(value, Mapping):
        return {str(k): _freeze_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(v) for v in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _coerce_content(value: Any) -> JsonObject:
    if isinstance(value, Mapping):
        return {str(k): _freeze_json_value(v) for k, v in value.items()}
    if value is None:
        return {}
    return {"content": _freeze_json_value(value)}


def _coerce_event_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def coerce_session_events(rows: Iterable[Mapping[str, Any]]) -> tuple[SessionEvent, ...]:
    """Translate raw DAO event rows into a typed SessionEvent tuple."""
    events: list[SessionEvent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        event_id = _coerce_event_id(row.get("id"))
        if event_id is None:
            continue
        content = _coerce_content(row.get("content"))
        if (
            isinstance(content, dict)
            and "created_at" not in content
            and row.get("created_at") is not None
        ):
            content = {**content, "created_at": _freeze_json_value(row.get("created_at"))}
        events.append(
            SessionEvent(
                id=event_id,
                event_type=str(row.get("type") or "").strip(),
                source=_coerce_optional_str(row.get("source")),
                content=content,
                task_id=_coerce_optional_str(row.get("task_id")),
                invocation_id=_coerce_optional_str(row.get("invocation_id")),
                spawn_id=_coerce_optional_str(row.get("spawn_id")),
            )
        )
    return tuple(events)


def _skill_name_from_content(content: JsonValue) -> str:
    if isinstance(content, Mapping):
        raw = content.get("skill_name") or content.get("content")
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
        timestamp_raw = (
            event.content.get("created_at")
            if isinstance(event.content, Mapping)
            else None
        )
        timestamp = (
            str(timestamp_raw) if isinstance(timestamp_raw, str) and timestamp_raw else None
        )
        records.append(
            SkillHitRecord(
                skill_name=name,
                event_id=event.id,
                timestamp=timestamp,
            )
        )
    return tuple(records)
