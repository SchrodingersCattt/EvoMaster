from matmaster.context.sources.attachments import (
    AttachmentEntry,
    filter_entries_after_event_id,
    filter_entries_in_event_range,
    format_entries_text,
    scan_legacy_attachment_entries,
)


def build_available_attachments(events, max_entries: int = 30):
    return list(scan_legacy_attachment_entries(events, max_entries=max_entries))


def format_available_attachments(entries):
    return format_entries_text(entries)


def test_build_available_attachments_reads_top_level_query_metadata() -> None:
    # Production shape returned by ChatEventsTable.get_session_events(): content
    # is unwrapped and attachment metadata is promoted to top-level fields.
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "analyze attachments",
            "files": ["https://oss.example.com/chat/data.csv"],
            "images": ["https://oss.example.com/chat/em.png"],
            "workspace_paths": ["/share/a.cif"],
        }
    ]

    entries = build_available_attachments(events)

    assert entries == [
        AttachmentEntry(
            kind="file",
            label="file_1",
            name="data.csv",
            value="https://oss.example.com/chat/data.csv",
            source_event_id=None,
        ),
        AttachmentEntry(
            kind="image",
            label="image_1",
            name="em.png",
            value="https://oss.example.com/chat/em.png",
            source_event_id=None,
        ),
        AttachmentEntry(
            kind="workspace",
            label="workspace_1",
            name="/share/a.cif",
            value="/share/a.cif",
            source_event_id=None,
        ),
    ]


def test_build_available_attachments_reads_json_content_query_metadata() -> None:
    # Defensive compatibility for raw DB/checkpoint-like rows where content still
    # contains the metadata dict.
    events = [
        {
            "source": "User",
            "type": "query",
            "content": {
                "content": "old turn",
                "files": ["https://oss.example.com/chat/old%20data.csv"],
                "images": ["https://oss.example.com/chat/old-em.webp"],
                "workspace_paths": ["/share/old.cif"],
            },
        }
    ]

    entries = build_available_attachments(events)

    assert [(entry.label, entry.name, entry.value) for entry in entries] == [
        (
            "file_1",
            "old data.csv",
            "https://oss.example.com/chat/old%20data.csv",
        ),
        (
            "image_1",
            "old-em.webp",
            "https://oss.example.com/chat/old-em.webp",
        ),
        ("workspace_1", "/share/old.cif", "/share/old.cif"),
    ]


def test_build_available_attachments_deduplicates_by_kind_and_value() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "first",
            "files": ["https://oss.example.com/chat/data.csv"],
            "images": ["https://oss.example.com/chat/em.png"],
        },
        {
            "source": "User",
            "type": "query",
            "content": {
                "content": "second",
                "files": ["https://oss.example.com/chat/data.csv"],
                "images": ["https://oss.example.com/chat/em.png"],
                "workspace_paths": ["/share/a.cif"],
            },
        },
    ]

    entries = build_available_attachments(events)

    assert [(entry.kind, entry.label, entry.value) for entry in entries] == [
        ("file", "file_1", "https://oss.example.com/chat/data.csv"),
        ("image", "image_1", "https://oss.example.com/chat/em.png"),
        ("workspace", "workspace_1", "/share/a.cif"),
    ]


def test_build_available_attachments_filters_non_user_query_and_invalid_values() -> (
    None
):
    events = [
        {
            "source": "System",
            "type": "query",
            "content": "skip",
            "files": ["https://oss.example.com/chat/system.csv"],
        },
        {
            "source": "User",
            "type": "response",
            "content": "skip",
            "images": ["https://oss.example.com/chat/not-query.png"],
        },
        {
            "source": "User",
            "type": "query",
            "content": "keep",
            "files": [" ", 3, "https://oss.example.com/chat/keep.csv"],
            "images": [None, "https://oss.example.com/chat/keep.png"],
            "workspace_paths": ["", " /share/keep.cif "],
        },
    ]

    entries = build_available_attachments(events)

    assert [(entry.kind, entry.label, entry.value) for entry in entries] == [
        ("file", "file_1", "https://oss.example.com/chat/keep.csv"),
        ("image", "image_1", "https://oss.example.com/chat/keep.png"),
        ("workspace", "workspace_1", "/share/keep.cif"),
    ]


def test_build_available_attachments_normalizes_file_and_image_url_paths() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "raw urls",
            "files": ["https://oss.example.com/chat/raw data.csv"],
            "images": ["https://oss.example.com/chat/显微 图.png"],
        }
    ]

    entries = build_available_attachments(events)

    assert [(entry.label, entry.name, entry.value) for entry in entries] == [
        (
            "file_1",
            "raw data.csv",
            "https://oss.example.com/chat/raw%20data.csv",
        ),
        (
            "image_1",
            "显微 图.png",
            "https://oss.example.com/chat/%E6%98%BE%E5%BE%AE%20%E5%9B%BE.png",
        ),
    ]


def test_dedup_treats_raw_and_percent_encoded_as_same_url() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "t1",
            "files": ["https://oss.example.com/chat/raw data.csv"],
        },
        {
            "source": "User",
            "type": "query",
            "content": "t2",
            "files": ["https://oss.example.com/chat/raw%20data.csv"],
        },
    ]

    entries = build_available_attachments(events)

    assert len(entries) == 1
    assert entries[0].label == "file_1"
    assert entries[0].name == "raw data.csv"
    assert entries[0].value == "https://oss.example.com/chat/raw%20data.csv"


def test_build_available_attachments_uses_simple_total_max_entries_limit() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "many",
            "files": [
                "https://oss.example.com/chat/1.csv",
                "https://oss.example.com/chat/2.csv",
            ],
            "images": ["https://oss.example.com/chat/3.png"],
        }
    ]

    entries = build_available_attachments(events, max_entries=2)

    assert [(entry.kind, entry.label, entry.value) for entry in entries] == [
        ("file", "file_1", "https://oss.example.com/chat/1.csv"),
        ("file", "file_2", "https://oss.example.com/chat/2.csv"),
    ]


def test_build_available_attachments_stops_scanning_after_max_entries() -> None:
    class FailsIfRead(dict):
        def get(self, *args, **kwargs):
            raise AssertionError("event after max_entries should not be inspected")

    entries = build_available_attachments(
        [
            {
                "source": "User",
                "type": "query",
                "content": "first",
                "files": ["https://oss.example.com/chat/1.csv"],
            },
            FailsIfRead(),
        ],
        max_entries=1,
    )

    assert [(entry.kind, entry.label, entry.value) for entry in entries] == [
        ("file", "file_1", "https://oss.example.com/chat/1.csv"),
    ]


def test_format_available_attachments_outputs_compact_block() -> None:
    entries = [
        AttachmentEntry(
            kind="file",
            label="file_1",
            name="data.csv",
            value="https://oss.example.com/chat/data.csv",
            source_event_id=None,
        ),
        AttachmentEntry(
            kind="image",
            label="image_1",
            name="em.png",
            value="https://oss.example.com/chat/em.png",
            source_event_id=None,
        ),
        AttachmentEntry(
            kind="workspace",
            label="workspace_1",
            name="/share/a.cif",
            value="/share/a.cif",
            source_event_id=None,
        ),
    ]

    assert format_available_attachments(entries) == (
        "[Available attachments]\n"
        "file_1 data.csv https://oss.example.com/chat/data.csv\n"
        "image_1 em.png https://oss.example.com/chat/em.png\n"
        "workspace_1 /share/a.cif"
    )


def test_format_available_attachments_returns_empty_string_without_entries() -> None:
    assert format_available_attachments([]) == ""


def test_build_available_attachments_records_source_event_id() -> None:
    events = [
        {
            "id": 10,
            "source": "User",
            "type": "query",
            "content": "first",
            "files": ["https://oss.example.com/chat/a.csv"],
        },
        {
            "id": 11,
            "source": "User",
            "type": "query",
            "content": "second",
            "files": ["https://oss.example.com/chat/b.csv"],
        },
    ]

    entries = build_available_attachments(events)

    assert [(entry.label, entry.source_event_id) for entry in entries] == [
        ("file_1", 10),
        ("file_2", 11),
    ]


def test_filter_entries_after_event_id_preserves_stable_labels() -> None:
    events = [
        {
            "id": 10,
            "source": "User",
            "type": "query",
            "content": "first",
            "files": ["https://oss.example.com/chat/a.csv"],
        },
        {
            "id": 20,
            "source": "User",
            "type": "query",
            "content": "second",
            "files": ["https://oss.example.com/chat/b.csv"],
            "images": ["https://oss.example.com/chat/c.png"],
        },
    ]

    all_entries = build_available_attachments(events)
    delta = filter_entries_after_event_id(all_entries, 10)

    assert [(entry.kind, entry.label, entry.value) for entry in delta] == [
        ("file", "file_2", "https://oss.example.com/chat/b.csv"),
        ("image", "image_1", "https://oss.example.com/chat/c.png"),
    ]


def test_filter_entries_after_event_id_none_keeps_all_entries() -> None:
    entries = [
        AttachmentEntry(
            kind="file",
            label="file_1",
            name="a.csv",
            value="https://oss.example.com/chat/a.csv",
            source_event_id=10,
        )
    ]

    assert filter_entries_after_event_id(entries, None) == tuple(entries)


def test_filter_entries_in_event_range_applies_upper_bound_and_drops_unscoped() -> None:
    entries = [
        AttachmentEntry(
            kind="file",
            label="file_1",
            name="old.csv",
            value="https://oss.example.com/chat/old.csv",
            source_event_id=10,
        ),
        AttachmentEntry(
            kind="file",
            label="file_2",
            name="current.csv",
            value="https://oss.example.com/chat/current.csv",
            source_event_id=20,
        ),
        AttachmentEntry(
            kind="file",
            label="file_3",
            name="unscoped.csv",
            value="https://oss.example.com/chat/unscoped.csv",
            source_event_id=None,
        ),
    ]

    filtered = filter_entries_in_event_range(entries, after_id=None, until_id=10)

    assert [(entry.label, entry.value) for entry in filtered] == [
        ("file_1", "https://oss.example.com/chat/old.csv")
    ]


def test_attachment_module_does_not_expose_prompt_append_shortcut() -> None:
    import matmaster.context.sources.attachments as attachment

    assert not hasattr(attachment, "append_available_attachments")
