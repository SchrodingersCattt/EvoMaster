"""Phase 2B shim delegating to matmaster.context.sources.attachments."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.context.sources.attachments import (
    AttachmentEntry,
    AttachmentKind,
)
from matmaster.context.sources.attachments import (
    filter_entries_after_event_id as _typed_filter_after,
)
from matmaster.context.sources.attachments import (
    filter_entries_in_event_range as _typed_filter_range,
)
from matmaster.context.sources.attachments import format_entries_text as _typed_format
from matmaster.context.sources.attachments import (
    scan_legacy_attachment_entries as _legacy_scan,
)

__all__ = [
    "AttachmentEntry",
    "AttachmentKind",
    "build_available_attachments",
    "filter_entries_after_event_id",
    "filter_entries_in_event_range",
    "format_available_attachments",
]


def build_available_attachments(
    events: Iterable[dict[str, Any]],
    *,
    max_entries: int = 30,
) -> list[AttachmentEntry]:
    return list(_legacy_scan(events, max_entries=max_entries))


def filter_entries_after_event_id(
    entries: Iterable[AttachmentEntry],
    after_id: int | None,
) -> list[AttachmentEntry]:
    return list(_typed_filter_after(entries, after_id))


def filter_entries_in_event_range(
    entries: Iterable[AttachmentEntry],
    *,
    after_id: int | None,
    until_id: int | None,
) -> list[AttachmentEntry]:
    return list(_typed_filter_range(entries, after_id=after_id, until_id=until_id))


def format_available_attachments(entries: Iterable[AttachmentEntry]) -> str:
    return _typed_format(entries)
