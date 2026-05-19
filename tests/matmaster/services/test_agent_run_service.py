from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from matmaster.context.ports import SessionEvent
from matmaster.core.runtime_context_assembly import empty_skill_resolver
from src.services.agent_run_service import AgentRunService


def _service() -> AgentRunService:
    return AgentRunService(sessions_service=MagicMock())


def _skills_config(enabled: bool, root: str | None = "/tmp/skills") -> Any:
    return SimpleNamespace(
        enabled=enabled,
        skills_root=root,
        disabled_skill_names=(),
    )


def _skill_hit(name: str) -> tuple[SessionEvent, ...]:
    return (
        SessionEvent(
            id=1,
            event_type="skill_hit",
            source=None,
            content={"skill_name": name},
        ),
    )


def test_build_skill_resolver_skips_registry_when_skills_disabled() -> None:
    svc = _service()
    exp_config = SimpleNamespace(skills=_skills_config(enabled=False))

    with patch(
        "src.services.agent_run_service.build_skill_registry",
        side_effect=AssertionError(
            "build_skill_registry must not be called when skills.enabled=False"
        ),
    ):
        resolver = svc._build_skill_resolver(exp_config, session=None)

    assert resolver is empty_skill_resolver


def test_build_skill_resolver_skips_registry_when_skills_config_missing() -> None:
    svc = _service()
    exp_config = SimpleNamespace(skills=None)

    with patch(
        "src.services.agent_run_service.build_skill_registry",
        side_effect=AssertionError("must not be called when skills config is None"),
    ):
        resolver = svc._build_skill_resolver(exp_config, session=None)

    assert resolver is empty_skill_resolver


def test_build_skill_resolver_constructs_registry_when_enabled(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: a\n---\nbody",
        encoding="utf-8",
    )

    svc = _service()
    exp_config = SimpleNamespace(skills=_skills_config(enabled=True, root=str(root)))

    resolver = svc._build_skill_resolver(exp_config, session=None)

    skills = resolver(_skill_hit("alpha"))
    assert [skill.name for skill in skills] == ["alpha"]
