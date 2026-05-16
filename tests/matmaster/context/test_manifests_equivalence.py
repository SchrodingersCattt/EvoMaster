from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.context.scanner import coerce_session_events, scan_skill_hits
from matmaster.context.session import SessionContextBuilder
from matmaster.context.sources.attachments import (
    SessionAttachmentsSource,
    filter_entries_in_event_range,
    format_entries_text,
    scan_attachment_entries,
)
from matmaster.context.sources.skills import (
    format_loaded_skills,
    resolve_active_skills,
)
from matmaster.context.sources.tools import format_active_mcp
from matmaster.manifests import attachment as legacy_attachment
from matmaster.manifests import mcp as legacy_mcp
from matmaster.manifests import skill as legacy_skill
from matmaster.manifests.rehydrator import CompactionRehydrator
from matmaster.manifests.scanner import scan_skill_hits as legacy_scan_skill_hits
from matmaster.skills.registry import SkillRegistry
from matmaster.types.context import PlaygroundContext


def _registry(
    tmp_path: Path,
    skills: tuple[tuple[str, str, str | None], ...],
) -> SkillRegistry:
    root = tmp_path / "skills"
    for name, description, server in skills:
        skill_dir = root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        meta = ["---", f"name: {name}", f"description: {description}"]
        if server:
            meta.append(f"mcp_server: {server}")
        meta.extend(["---", "body"])
        (skill_dir / "SKILL.md").write_text("\n".join(meta), encoding="utf-8")
    return SkillRegistry([root])


SINGLE_TURN_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {
            "content": "upload",
            "files": ["https://oss.example.com/a.csv"],
            "images": ["https://img.example.com/x.png"],
            "workspace_paths": ["/ws/data"],
        },
    },
]

MULTI_TURN_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/a.csv"]},
    },
    {
        "id": 20,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/b.csv"]},
    },
    {
        "id": 30,
        "source": "User",
        "type": "query",
        "content": {"images": ["https://img.example.com/c.png"]},
    },
]

SKILL_EVOLUTION_EVENTS = [
    {"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
    {"id": 2, "type": "skill_hit", "content": {"skill_name": "mlip"}},
    {"id": 3, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
]

TOOL_CATALOG_EVENTS = [
    {"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
    {"id": 2, "type": "skill_hit", "content": {"skill_name": "mlip"}},
    {"id": 3, "type": "skill_hit", "content": {"skill_name": "deprecated"}},
]

CHECKPOINT_MIXED_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/pre.csv"]},
    },
    {"id": 15, "type": "history_checkpoint", "content": {"covered_until_event_id": 14}},
    {
        "id": 20,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/post.csv"]},
    },
]

HASH_ANCHOR_EVENTS = [
    {
        "id": 5,
        "type": "user_turn_context",
        "content": {
            "message": {"role": "user", "content": "anchor turn"},
            "user_instructions_hash": "sha256:aaa",
        },
    },
    {"id": 10, "type": "history_checkpoint", "content": {"covered_until_event_id": 9}},
    {
        "id": 12,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/post-anchor.csv"]},
    },
]


@pytest.mark.parametrize(
    "events",
    [SINGLE_TURN_EVENTS, MULTI_TURN_EVENTS, CHECKPOINT_MIXED_EVENTS, HASH_ANCHOR_EVENTS],
)
def test_attachment_entries_equivalence(events) -> None:
    legacy_entries = legacy_attachment.build_available_attachments(events)
    typed = coerce_session_events(events)
    typed_entries = list(scan_attachment_entries(typed))

    assert legacy_entries == typed_entries


def test_legacy_top_level_attachment_shape_stays_supported() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "display-flattened production row",
            "files": ["https://oss.example.com/chat/data.csv"],
            "images": ["https://oss.example.com/chat/em.png"],
            "workspace_paths": ["/share/a.cif"],
        }
    ]

    legacy_entries = legacy_attachment.build_available_attachments(events)

    assert [entry.label for entry in legacy_entries] == [
        "file_1",
        "image_1",
        "workspace_1",
    ]
    assert legacy_entries[0].source_event_id is None


@pytest.mark.parametrize("events", [MULTI_TURN_EVENTS, CHECKPOINT_MIXED_EVENTS])
def test_attachment_format_equivalence(events) -> None:
    legacy_entries = legacy_attachment.build_available_attachments(events)
    legacy_text = legacy_attachment.format_available_attachments(legacy_entries)

    typed = coerce_session_events(events)
    typed_entries = scan_attachment_entries(typed)
    typed_text = format_entries_text(typed_entries)

    assert legacy_text == typed_text


def test_attachment_filter_after_checkpoint_equivalence() -> None:
    legacy_entries = legacy_attachment.build_available_attachments(
        CHECKPOINT_MIXED_EVENTS
    )
    legacy_filtered = legacy_attachment.filter_entries_in_event_range(
        legacy_entries,
        after_id=14,
        until_id=None,
    )

    typed = coerce_session_events(CHECKPOINT_MIXED_EVENTS)
    typed_entries = scan_attachment_entries(typed)
    typed_filtered = list(
        filter_entries_in_event_range(typed_entries, after_id=14, until_id=None)
    )

    assert legacy_filtered == typed_filtered


def test_attachment_until_event_id_boundary_equivalence() -> None:
    legacy_entries = legacy_attachment.build_available_attachments(MULTI_TURN_EVENTS)
    legacy_clipped = legacy_attachment.filter_entries_in_event_range(
        legacy_entries,
        after_id=None,
        until_id=20,
    )

    typed = coerce_session_events(MULTI_TURN_EVENTS)
    source = SessionAttachmentsSource.from_events(typed, until_event_id=20)

    assert format_entries_text(
        source.entries
    ) == legacy_attachment.format_available_attachments(legacy_clipped)


def test_skill_equivalence(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        (
            ("pxrd", "PXRD helper", "mat_xrd"),
            ("mlip", "MLIP runner", "mat_mlip"),
        ),
    )

    legacy_skills = legacy_skill.resolve_active_skills(SKILL_EVOLUTION_EVENTS, registry)
    legacy_text = legacy_skill.format_loaded_skills(legacy_skills)

    typed = coerce_session_events(SKILL_EVOLUTION_EVENTS)
    typed_skills = resolve_active_skills(typed, registry)
    typed_text = format_loaded_skills(typed_skills)

    assert [legacy_skill.skill_name(skill) for skill in legacy_skills] == [
        legacy_skill.skill_name(skill) for skill in typed_skills
    ]
    assert legacy_text == typed_text


def test_skill_hit_timestamp_bridge_equivalence() -> None:
    events = [
        {
            "id": 1,
            "type": "skill_hit",
            "content": {"skill_name": "pxrd"},
            "created_at": "2026-01-01T00:00:00",
        }
    ]

    legacy_records = legacy_scan_skill_hits(events)
    typed_records = scan_skill_hits(coerce_session_events(events))

    assert typed_records == tuple(legacy_records)
    assert typed_records[0].timestamp == "2026-01-01T00:00:00"


def test_active_mcp_equivalence(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        (
            ("pxrd", "PXRD helper", "mat_xrd"),
            ("mlip", "MLIP runner", "mat_mlip"),
            ("deprecated", "old", "mat_dead"),
        ),
    )
    schemas = {
        "mat_xrd": [{"name": "read"}, {"name": "write"}],
        "mat_mlip": [{"name": "run"}],
        "mat_dead": [],
    }
    legal = {"mat_xrd", "mat_mlip"}

    typed = coerce_session_events(TOOL_CATALOG_EVENTS)
    typed_skills = resolve_active_skills(typed, registry)
    legacy_text = legacy_mcp.format_active_mcp(
        list(typed_skills),
        legal_servers=legal,
        schemas_by_server=schemas,
    )
    typed_text = format_active_mcp(
        typed_skills,
        legal_servers=legal,
        schemas_by_server=schemas,
    )

    assert legacy_text == typed_text


@pytest.mark.asyncio
async def test_compaction_rehydrator_vs_session_builder(tmp_path: Path) -> None:
    registry = _registry(tmp_path, (("pxrd", "PXRD helper", "mat_xrd"),))
    schemas = {"mat_xrd": [{"name": "read"}]}
    legal = {"mat_xrd"}
    events = MULTI_TURN_EVENTS + SKILL_EVOLUTION_EVENTS

    rehydrator = CompactionRehydrator(
        get_query_events=lambda: events,
        get_all_events=lambda: events,
        get_latest_checkpoint_covered_until_event_id=lambda: None,
        skill_registry=registry,
        playground_ctx=PlaygroundContext(
            workdir=tmp_path,
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        legal_mcp_servers=legal,
        schemas_by_server=schemas,
    )
    legacy_text = await rehydrator.build()

    typed = coerce_session_events(events)
    builder = SessionContextBuilder(
        events=typed,
        skill_registry=registry,
        legal_mcp_servers=legal,
        schemas_by_server=schemas,
    )
    sections = builder.build_sections(until_event_id=None, include_attachments=True)

    def _wrap(tag: str, content: str) -> str:
        text = (content or "").strip()
        return f"<{tag}>\n{text}\n</{tag}>" if text else ""

    tag_map = {
        "session_attachments": "attachments",
        "session_skills": "loaded_skills",
        "session_tools": "active_tools",
    }
    legacy_order = (
        "session_attachments",
        "session_skills",
        "session_tools",
    )
    section_by_key = {section.key: section for section in sections}
    composed = "\n\n".join(
        _wrap(tag_map[key], section_by_key[key].content)
        for key in legacy_order
        if key in section_by_key and section_by_key[key].content.strip()
    )

    assert legacy_text == composed


def test_spawn_id_filtering_lives_in_caller_not_in_source() -> None:
    typed = coerce_session_events(
        [
            {
                "id": 1,
                "source": "User",
                "type": "query",
                "content": {"files": ["https://oss.example.com/main.csv"]},
                "spawn_id": None,
            },
            {
                "id": 2,
                "source": "User",
                "type": "query",
                "content": {"files": ["https://oss.example.com/spawn.csv"]},
                "spawn_id": "spawn-A",
            },
        ]
    )
    entries = scan_attachment_entries(typed)
    assert {entry.name for entry in entries} == {"main.csv", "spawn.csv"}
