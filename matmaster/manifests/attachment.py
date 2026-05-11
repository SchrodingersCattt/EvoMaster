from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, unquote, urlparse, urlunparse

from matmaster.utils.event_source import normalize_event_source

AttachmentKind = Literal["file", "image", "workspace"]


@dataclass(frozen=True)
class AttachmentEntry:
    kind: AttachmentKind
    label: str
    name: str
    value: str
    source_event_id: int | None = None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _query_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    content = event.get("content")
    if isinstance(content, dict):
        payload.update(content)
    for key in ("files", "images", "workspace_paths"):
        if key in event:
            payload[key] = event.get(key)
    return payload


def _event_id(event: dict[str, Any]) -> int | None:
    raw = event.get("id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _name_from_url(value: str, fallback: str) -> str:
    parsed = urlparse(value)
    path = unquote(parsed.path or "")
    basename = posixpath.basename(path)
    return basename or fallback


def _entry_name(kind: AttachmentKind, value: str) -> str:
    if kind == "file":
        return _name_from_url(value, "file")
    if kind == "image":
        return _name_from_url(value, "image")
    return value


def _normalize_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return urlunparse(parsed._replace(path=quote(unquote(parsed.path or ""), safe="/")))


def _normalize_attachment_value(kind: AttachmentKind, value: str) -> str:
    if kind in {"file", "image"}:
        return _normalize_url(value)
    return value


def build_available_attachments(
    events: list[dict[str, Any]],
    *,
    max_entries: int = 30,
) -> list[AttachmentEntry]:
    counters: dict[AttachmentKind, int] = {
        "file": 0,
        "image": 0,
        "workspace": 0,
    }
    seen: set[tuple[AttachmentKind, str]] = set()
    entries: list[AttachmentEntry] = []

    def add(kind: AttachmentKind, value: str, source_event_id: int | None) -> None:
        if len(entries) >= max_entries:
            return
        normalized_value = _normalize_attachment_value(kind, value)
        key = (kind, normalized_value)
        if key in seen:
            return
        seen.add(key)
        counters[kind] += 1
        entries.append(
            AttachmentEntry(
                kind=kind,
                label=f"{kind}_{counters[kind]}",
                name=_entry_name(kind, normalized_value),
                value=normalized_value,
                source_event_id=source_event_id,
            )
        )

    for event in events:
        if len(entries) >= max_entries:
            break
        if normalize_event_source(event.get("source")) != "User":
            continue
        if (event.get("type") or "").strip() != "query":
            continue
        payload = _query_payload(event)
        source_event_id = _event_id(event)
        for value in _string_list(payload.get("files")):
            add("file", value, source_event_id)
        for value in _string_list(payload.get("images")):
            add("image", value, source_event_id)
        for value in _string_list(payload.get("workspace_paths")):
            add("workspace", value, source_event_id)

    return entries


def filter_entries_after_event_id(
    entries: list[AttachmentEntry],
    after_id: int | None,
) -> list[AttachmentEntry]:
    if after_id is None:
        return list(entries)
    return [
        entry
        for entry in entries
        if entry.source_event_id is not None and entry.source_event_id > after_id
    ]


def format_available_attachments(entries: list[AttachmentEntry]) -> str:
    if not entries:
        return ""
    lines = ["[Available attachments]"]
    for entry in entries:
        if entry.kind == "workspace":
            lines.append(f"{entry.label} {entry.value}")
        else:
            lines.append(f"{entry.label} {entry.name} {entry.value}")
    return "\n".join(lines)
