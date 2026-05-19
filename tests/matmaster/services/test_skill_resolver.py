from __future__ import annotations

from pathlib import Path
from typing import Any

from matmaster.context.ports import ActiveSkill, SessionEvent
from src.services.skill_registry_factory import build_skill_registry
from src.services.skill_resolver import SkillRegistryResolver


def _write_skill(root: Path, name: str, mcp_server: str | None = None) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = f"name: {name}\ndescription: {name} desc"
    if mcp_server:
        fm += f"\nmcp_server: {mcp_server}"
    (skill_dir / "SKILL.md").write_text(f"---\n{fm}\n---\nbody", encoding="utf-8")


def _skill_hit(event_id: int, name: str) -> SessionEvent:
    return SessionEvent(
        id=event_id,
        event_type="skill_hit",
        source=None,
        content={"skill_name": name},
    )


def test_resolver_returns_active_skills_for_recorded_hits(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", mcp_server="xrd_srv")
    _write_skill(root, "mlip")
    registry = build_skill_registry(config_roots=(root,), session=None)

    resolver = SkillRegistryResolver(registry)
    events = (_skill_hit(1, "pxrd"), _skill_hit(2, "mlip"))

    assert resolver(events) == (
        ActiveSkill(name="pxrd", description="pxrd desc", mcp_server="xrd_srv"),
        ActiveSkill(name="mlip", description="mlip desc", mcp_server=None),
    )


def test_resolver_silently_drops_unknown_skill_hits(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "alpha")
    registry = build_skill_registry(config_roots=(root,), session=None)

    resolver = SkillRegistryResolver(registry)
    events = (_skill_hit(1, "alpha"), _skill_hit(2, "ghost"))

    assert resolver(events) == (
        ActiveSkill(name="alpha", description="alpha desc", mcp_server=None),
    )


def test_resolver_handles_none_registry() -> None:
    resolver = SkillRegistryResolver(None)

    assert resolver((_skill_hit(1, "x"),)) == ()


def test_resolver_skips_skill_when_registry_lookup_raises() -> None:
    class _BrokenRegistry:
        def get_skill(self, name: str) -> Any:
            if name == "broken":
                raise RuntimeError("simulated lookup failure")
            return None

    resolver = SkillRegistryResolver(_BrokenRegistry())
    events = (_skill_hit(1, "broken"), _skill_hit(2, "ghost"))

    assert resolver(events) == ()
