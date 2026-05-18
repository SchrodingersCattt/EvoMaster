from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from matmaster.context.ports import JsonObject, JsonValue, SessionEvent


def freeze_json_value(value: Any) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): freeze_json_value(inner) for key, inner in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(freeze_json_value(inner) for inner in value)
    raise TypeError(
        f"Unsupported JSON value type in context event payload: {type(value)!r}"
    )


def freeze_json_object(value: Any) -> JsonObject:
    if not isinstance(value, Mapping):
        return {"value": freeze_json_value(value)}
    return {str(key): freeze_json_value(inner) for key, inner in value.items()}


def coerce_event_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def coerce_created_at_ms(row: Mapping[str, Any]) -> int | None:
    raw_ms = row.get("created_at_ms")
    if raw_ms is not None and not isinstance(raw_ms, bool):
        try:
            return int(raw_ms)
        except (TypeError, ValueError):
            return None

    raw_created_at = row.get("created_at")
    timestamp = getattr(raw_created_at, "timestamp", None)
    if callable(timestamp):
        try:
            return int(timestamp() * 1000)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    return None


def _row_to_event(row: Mapping[str, Any], event_id: int) -> SessionEvent:
    raw_content = row["content"] if "content" in row else None
    return SessionEvent(
        id=event_id,
        event_type=str(row.get("type") or row.get("event_type") or "").strip(),
        source=coerce_optional_str(row.get("source")),
        content=freeze_json_object(raw_content),
        task_id=coerce_optional_str(row.get("task_id")),
        invocation_id=coerce_optional_str(row.get("invocation_id")),
        spawn_id=coerce_optional_str(row.get("spawn_id")),
        created_at_ms=coerce_created_at_ms(row),
    )


def row_to_event(row: Mapping[str, Any]) -> SessionEvent:
    if not isinstance(row, Mapping):
        raise TypeError("Session event row must be a mapping")
    event_id = coerce_event_id(row.get("id"))
    if event_id is None:
        raise ValueError("Session event row must contain a valid id")
    return _row_to_event(row, event_id)


def decode_session_events(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[SessionEvent, ...]:
    events: list[SessionEvent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        event_id = coerce_event_id(row.get("id"))
        if event_id is None:
            continue
        events.append(_row_to_event(row, event_id))
    return tuple(events)
