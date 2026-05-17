from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from matmaster.context.ports import SessionEvent
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.skills import resolve_active_skills
from matmaster.context.sources.tools import (
    SessionToolsSource,
    format_active_mcp,
    resolve_declared_servers,
    resolve_runnable_servers,
)
from matmaster.skills.registry import SkillRegistry


@dataclass(frozen=True)
class _Meta:
    name: str
    description: str = ""
    mcp_server: str | None = None


@dataclass(frozen=True)
class _Skill:
    meta_info: _Meta

    @property
    def name(self) -> str:
        return self.meta_info.name


def _skill(name: str, server: str | None) -> _Skill:
    return _Skill(meta_info=_Meta(name=name, mcp_server=server))


def _write_skill(root: Path, name: str, mcp_server: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} helper\nmcp_server: {mcp_server}\n---\nbody\n",
        encoding="utf-8",
    )


def _skills(root: Path):
    registry = SkillRegistry([root])
    events = (
        SessionEvent(
            id=1, event_type="skill_hit", source=None, content={"skill_name": "pxrd"}
        ),
        SessionEvent(
            id=2, event_type="skill_hit", source=None, content={"skill_name": "sg"}
        ),
    )
    return resolve_active_skills(events, registry)


def test_resolve_declared_servers_dedup() -> None:
    skills = (_skill("a", "srv1"), _skill("b", "srv1"), _skill("c", None))
    assert resolve_declared_servers(skills) == {"srv1"}


def test_resolve_runnable_servers_filters_by_legal_and_schemas() -> None:
    skills = (_skill("a", "srv1"), _skill("b", "srv2"))

    runnable = resolve_runnable_servers(
        skills,
        legal_servers={"srv1"},
        schemas_by_server={"srv1": [{"name": "read"}], "srv2": [{"name": "x"}]},
    )

    assert runnable == {"srv1"}


def test_format_active_mcp_emits_legacy_header() -> None:
    skills = (_skill("a", "srv1"), _skill("b", "srv2"))
    text = format_active_mcp(
        skills,
        legal_servers={"srv1", "srv2"},
        schemas_by_server={
            "srv1": [{"name": "read"}],
            "srv2": [{"name": "write"}, {"name": "list"}],
        },
    )

    assert text.startswith("[Active MCP servers]\n")
    assert "- srv1: available" in text
    assert "  - srv1_read" in text
    assert "- srv2: available" in text
    assert "  - srv2_write" in text
    assert "  - srv2_list" in text


def test_format_active_mcp_marks_unavailable_when_no_schema() -> None:
    skills = (_skill("a", "srv1"),)

    text = format_active_mcp(
        skills,
        legal_servers={"srv1"},
        schemas_by_server={"srv1": []},
    )

    assert "- srv1: unavailable" in text


def test_session_tools_source_to_sections() -> None:
    skills = (_skill("a", "srv1"),)

    source = SessionToolsSource.from_skills(
        skills,
        legal_servers={"srv1"},
        schemas_by_server={"srv1": [{"name": "read"}]},
    )
    sections = source.to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert isinstance(section, ContextSection)
    assert section.key == "session_tools"
    assert section.tag == "active_tools"
    assert section.order == SectionOrder.SESSION_TOOLS
    assert ContextView.RUNTIME in section.views
    assert ContextView.CHECKPOINT in section.views
    assert "srv1_read" in section.content


def test_session_tools_source_empty_when_no_declared_servers() -> None:
    skills = (_skill("a", None),)
    source = SessionToolsSource.from_skills(
        skills,
        legal_servers=None,
        schemas_by_server=None,
    )
    assert source.to_sections() == ()


def test_resolve_declared_servers_uses_skill_metadata_only(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", "mat_xrd")
    _write_skill(root, "sg", "mat_sg")

    assert resolve_declared_servers(_skills(root)) == {"mat_xrd", "mat_sg"}


def test_resolve_runnable_servers_filters_by_legal_servers(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", "mat_xrd")
    _write_skill(root, "sg", "mat_sg")

    assert resolve_runnable_servers(
        _skills(root),
        legal_servers={"mat_sg"},
    ) == {"mat_sg"}


def test_format_active_mcp_marks_unavailable_servers(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", "mat_xrd")

    text = format_active_mcp(
        _skills(root),
        legal_servers=set(),
        schemas_by_server={},
    )

    assert "[Active MCP servers]" in text
    assert "- mat_xrd: unavailable" in text
