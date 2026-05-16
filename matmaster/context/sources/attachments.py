from __future__ import annotations

import posixpath
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, unquote, urlparse, urlunparse

from matmaster.context.ports import SessionEvent
from matmaster.context.scanner import coerce_event_id
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder
from matmaster.utils.event_source import normalize_event_source

AttachmentKind = Literal["file", "image", "workspace"]


@dataclass(frozen=True)
class AttachmentEntry:
    kind: AttachmentKind
    label: str
    name: str
    value: str
    source_event_id: int | None = None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                out.append(stripped)
    return tuple(out)


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


def _normalize_value(kind: AttachmentKind, value: str) -> str:
    if kind in {"file", "image"}:
        return _normalize_url(value)
    return value


def _query_payload(event: SessionEvent) -> Mapping[str, object]:
    content = event.content
    if isinstance(content, Mapping):
        return content
    return {}


def _legacy_query_payload(row: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    content = row.get("content")
    if isinstance(content, Mapping):
        payload.update(content)
    for key in ("files", "images", "workspace_paths"):
        if key in row:
            payload[key] = row.get(key)
    return payload


def _scan_payloads(
    payloads: Iterable[tuple[Mapping[str, object], int | None]],
    *,
    max_entries: int,
) -> tuple[AttachmentEntry, ...]:
    if max_entries <= 0:
        return ()

    counters: dict[AttachmentKind, int] = {"file": 0, "image": 0, "workspace": 0}
    seen: set[tuple[AttachmentKind, str]] = set()
    entries: list[AttachmentEntry] = []

    def add(kind: AttachmentKind, value: str, source_event_id: int | None) -> None:
        if len(entries) >= max_entries:
            return
        normalized = _normalize_value(kind, value)
        key = (kind, normalized)
        if key in seen:
            return
        seen.add(key)
        counters[kind] += 1
        entries.append(
            AttachmentEntry(
                kind=kind,
                label=f"{kind}_{counters[kind]}",
                name=_entry_name(kind, normalized),
                value=normalized,
                source_event_id=source_event_id,
            )
        )

    for payload, source_event_id in payloads:
        for value in _string_tuple(payload.get("files")):
            add("file", value, source_event_id)
        for value in _string_tuple(payload.get("images")):
            add("image", value, source_event_id)
        for value in _string_tuple(payload.get("workspace_paths")):
            add("workspace", value, source_event_id)
        if len(entries) >= max_entries:
            break

    return tuple(entries)


def scan_attachment_entries(
    events: Iterable[SessionEvent],
    *,
    max_entries: int = 30,
) -> tuple[AttachmentEntry, ...]:
    def payloads() -> Iterable[tuple[Mapping[str, object], int | None]]:
        for event in events:
            if event.source != "User":
                continue
            if event.event_type != "query":
                continue
            yield (_query_payload(event), event.id)

    return _scan_payloads(payloads(), max_entries=max_entries)


def scan_legacy_attachment_entries(
    rows: Iterable[Mapping[str, object]],
    *,
    max_entries: int = 30,
) -> tuple[AttachmentEntry, ...]:
    def payloads() -> Iterable[tuple[Mapping[str, object], int | None]]:
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if normalize_event_source(row.get("source")) != "User":
                continue
            if str(row.get("type") or "").strip() != "query":
                continue
            yield (_legacy_query_payload(row), coerce_event_id(row.get("id")))

    return _scan_payloads(payloads(), max_entries=max_entries)


def filter_entries_in_event_range(
    entries: Iterable[AttachmentEntry],
    *,
    after_id: int | None,
    until_id: int | None,
) -> tuple[AttachmentEntry, ...]:
    if after_id is None and until_id is None:
        return tuple(entries)
    return tuple(
        entry
        for entry in entries
        if entry.source_event_id is not None
        and (after_id is None or entry.source_event_id > after_id)
        and (until_id is None or entry.source_event_id <= until_id)
    )


def filter_entries_after_event_id(
    entries: Iterable[AttachmentEntry],
    after_id: int | None,
) -> tuple[AttachmentEntry, ...]:
    return filter_entries_in_event_range(entries, after_id=after_id, until_id=None)


def format_entries_text(entries: Iterable[AttachmentEntry]) -> str:
    seq = tuple(entries)
    if not seq:
        return ""
    lines = ["[Available attachments]"]
    for entry in seq:
        if entry.kind == "workspace":
            lines.append(f"{entry.label} {entry.value}")
        else:
            lines.append(f"{entry.label} {entry.name} {entry.value}")
    return "\n".join(lines)


@dataclass(frozen=True)
class SessionAttachmentsSource:
    entries: tuple[AttachmentEntry, ...] = ()

    @classmethod
    def from_events(
        cls,
        events: Iterable[SessionEvent],
        *,
        until_event_id: int | None = None,
        after_id: int | None = None,
        max_entries: int = 30,
    ) -> SessionAttachmentsSource:
        raw_entries = scan_attachment_entries(events, max_entries=max_entries)
        scoped = filter_entries_in_event_range(
            raw_entries,
            after_id=after_id,
            until_id=until_event_id,
        )
        return cls(entries=scoped)

    def with_added(
        self,
        extra: Iterable[AttachmentEntry],
    ) -> SessionAttachmentsSource:
        added = tuple(extra)
        if not added:
            return self
        return SessionAttachmentsSource(entries=(*self.entries, *added))

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = format_entries_text(self.entries)
        if not text:
            return ()
        return (
            ContextSection(
                key="session_attachments",
                tag="attachments",
                content=text,
                order=SectionOrder.SESSION_ATTACHMENTS,
                views=ALL_VIEWS,
            ),
        )
