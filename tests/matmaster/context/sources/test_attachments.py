from __future__ import annotations

from matmaster.context.ports import SessionEvent
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.attachments import (
    AttachmentEntry,
    SessionAttachmentsSource,
    filter_entries_in_event_range,
    format_entries_text,
    scan_attachment_entries,
)

_QUERY_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {
            "files": ["https://oss.example.com/chat/a.csv"],
            "images": ["https://img.example.com/x.png"],
            "workspace_paths": ["/ws/data.csv"],
        },
    },
    {
        "id": 20,
        "source": "User",
        "type": "query",
        "content": {
            "files": ["https://oss.example.com/chat/b.csv"],
        },
    },
    {
        "id": 30,
        "source": "Assistant",
        "type": "response",
        "content": {"text": "ignored"},
    },
]


def _session_events(rows: list[dict]) -> tuple[SessionEvent, ...]:
    events: list[SessionEvent] = []
    for row in rows:
        events.append(
            SessionEvent(
                id=int(row["id"]),
                source=row.get("source"),
                event_type=str(row.get("type") or ""),
                content=row.get("content") or {"value": None},
            )
        )
    return tuple(events)


def test_scan_attachment_entries_dedup_and_label() -> None:
    events = _session_events(_QUERY_EVENTS)

    entries = scan_attachment_entries(events)

    assert entries == (
        AttachmentEntry(
            kind="file",
            label="file_1",
            name="a.csv",
            value="https://oss.example.com/chat/a.csv",
            source_event_id=10,
        ),
        AttachmentEntry(
            kind="image",
            label="image_1",
            name="x.png",
            value="https://img.example.com/x.png",
            source_event_id=10,
        ),
        AttachmentEntry(
            kind="workspace",
            label="workspace_1",
            name="/ws/data.csv",
            value="/ws/data.csv",
            source_event_id=10,
        ),
        AttachmentEntry(
            kind="file",
            label="file_2",
            name="b.csv",
            value="https://oss.example.com/chat/b.csv",
            source_event_id=20,
        ),
    )


def test_filter_entries_in_event_range_window() -> None:
    events = _session_events(_QUERY_EVENTS)
    entries = scan_attachment_entries(events)

    filtered = filter_entries_in_event_range(entries, after_id=10, until_id=None)

    assert tuple(entry.label for entry in filtered) == ("file_2",)


def test_format_entries_text_matches_legacy_shape() -> None:
    events = _session_events(_QUERY_EVENTS)
    entries = scan_attachment_entries(events)

    text = format_entries_text(entries)

    assert text.startswith("[Available attachments]\n")
    assert "file_1 a.csv https://oss.example.com/chat/a.csv" in text
    assert "image_1 x.png https://img.example.com/x.png" in text
    assert "workspace_1 /ws/data.csv" in text


def test_format_entries_text_empty() -> None:
    assert format_entries_text(()) == ""


def test_source_to_sections_emits_runtime_plus_checkpoint() -> None:
    events = _session_events(_QUERY_EVENTS)

    source = SessionAttachmentsSource.from_events(events)
    sections = source.to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert isinstance(section, ContextSection)
    assert section.key == "session-attachments"
    assert section.tag == "attachments"
    assert section.order == SectionOrder.SESSION_ATTACHMENTS
    assert ContextView.RUNTIME in section.views
    assert ContextView.CHECKPOINT in section.views
    assert "[Available attachments]" in section.content


def test_source_to_sections_empty_returns_no_section() -> None:
    source = SessionAttachmentsSource.from_events(())
    assert source.to_sections() == ()


def test_source_from_events_respects_until_event_id() -> None:
    events = _session_events(_QUERY_EVENTS)

    source = SessionAttachmentsSource.from_events(events, until_event_id=10)
    text = source.to_sections()[0].content

    assert "file_1 a.csv" in text
    assert "b.csv" not in text


def test_source_with_added_appends_entries_idempotently() -> None:
    base = SessionAttachmentsSource(
        entries=(
            AttachmentEntry(
                kind="file",
                label="file_1",
                name="a.csv",
                value="https://oss.example.com/chat/a.csv",
                source_event_id=10,
            ),
        )
    )
    extra = AttachmentEntry(
        kind="file",
        label="file_2",
        name="b.csv",
        value="https://oss.example.com/chat/b.csv",
        source_event_id=20,
    )

    extended = base.with_added((extra,))

    assert extended.entries == (base.entries[0], extra)
    assert extended is not base
