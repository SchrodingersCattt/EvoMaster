from pathlib import Path

import pytest

from matmaster.manifests.rehydrator import CompactionRehydrator
from matmaster.skills.registry import SkillRegistry
from matmaster.types.context import PlaygroundContext


def _registry(tmp_path: Path) -> SkillRegistry:
    root = tmp_path / "skills"
    skill_dir = root / "pxrd"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pxrd\ndescription: PXRD helper\nmcp_server: mat_xrd\n---\nbody\n",
        encoding="utf-8",
    )
    return SkillRegistry([root])


@pytest.mark.asyncio
async def test_rehydrator_builds_attachment_skill_and_mcp_sections(
    tmp_path: Path,
) -> None:
    events = [
        {
            "id": 10,
            "source": "User",
            "type": "query",
            "content": "upload",
            "files": ["https://oss.example.com/chat/a.csv"],
        },
        {"id": 11, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
    ]
    rehydrator = CompactionRehydrator(
        get_query_events=lambda: events,
        get_all_events=lambda: events,
        get_latest_checkpoint_covered_until_event_id=lambda: None,
        skill_registry=_registry(tmp_path),
        playground_ctx=PlaygroundContext(
            workdir=tmp_path,
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read", "description": "Read"}]},
    )

    text = await rehydrator.build()

    assert "<attachments>" in text
    assert "file_1 a.csv https://oss.example.com/chat/a.csv" in text
    assert "<loaded_skills>" in text
    assert "- pxrd: PXRD helper" in text
    assert "<active_tools>" in text
    assert "- mat_xrd: available" in text
    assert "mat_xrd_read" in text


@pytest.mark.asyncio
async def test_rehydrator_filters_attachment_delta_after_checkpoint(
    tmp_path: Path,
) -> None:
    events = [
        {
            "id": 10,
            "source": "User",
            "type": "query",
            "content": "old",
            "files": ["https://oss.example.com/chat/a.csv"],
        },
        {
            "id": 20,
            "source": "User",
            "type": "query",
            "content": "new",
            "files": ["https://oss.example.com/chat/b.csv"],
        },
    ]
    rehydrator = CompactionRehydrator(
        get_query_events=lambda: events,
        get_all_events=lambda: events,
        get_latest_checkpoint_covered_until_event_id=lambda: 10,
        skill_registry=_registry(tmp_path),
        playground_ctx=PlaygroundContext(
            workdir=tmp_path,
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
    )

    text = await rehydrator.build()

    assert "file_1 a.csv" not in text
    assert "file_2 b.csv" in text


@pytest.mark.asyncio
async def test_rehydrator_preserves_current_query_attachment_after_pre_query_checkpoint(
    tmp_path: Path,
) -> None:
    events = [
        {
            "id": 10,
            "source": "User",
            "type": "query",
            "content": "old",
            "files": ["https://oss.example.com/chat/old.cif"],
        },
        {
            "id": 20,
            "source": "User",
            "type": "query",
            "content": "current",
            "files": ["https://oss.example.com/chat/current.cif"],
        },
    ]
    rehydrator = CompactionRehydrator(
        get_query_events=lambda: events,
        get_all_events=lambda: events,
        get_latest_checkpoint_covered_until_event_id=lambda: 10,
        skill_registry=_registry(tmp_path),
        playground_ctx=PlaygroundContext(
            workdir=tmp_path,
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
    )

    text = await rehydrator.build()

    assert "old.cif" not in text
    assert "file_2 current.cif https://oss.example.com/chat/current.cif" in text


@pytest.mark.asyncio
async def test_rehydrator_returns_other_sections_when_one_manifest_fails(
    tmp_path: Path,
    caplog,
) -> None:
    rehydrator = CompactionRehydrator(
        get_query_events=lambda: (_ for _ in ()).throw(RuntimeError("query down")),
        get_all_events=lambda: [
            {"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}}
        ],
        skill_registry=_registry(tmp_path),
        playground_ctx=PlaygroundContext(
            workdir=tmp_path,
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
    )

    text = await rehydrator.build()

    assert "<loaded_skills>" in text
    assert "PXRD helper" in text
    assert "query down" in caplog.text
