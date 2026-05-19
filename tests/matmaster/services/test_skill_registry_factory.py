from __future__ import annotations

import json
from pathlib import Path

from src.services.skill_registry_factory import build_skill_registry


def _write_skill(root: Path, name: str, mcp_server: str | None = None) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = f"name: {name}\ndescription: {name} description"
    if mcp_server:
        fm += f"\nmcp_server: {mcp_server}"
    (skill_dir / "SKILL.md").write_text(f"---\n{fm}\n---\nbody", encoding="utf-8")


class _Session:
    def __init__(
        self,
        local_user_skills_root: str | None = None,
        remote_skill_roots: tuple[str, ...] = (),
        remote_user_skills_root: str | None = None,
    ) -> None:
        self.local_user_skills_root = local_user_skills_root
        self.remote_skill_roots = list(remote_skill_roots)
        self.remote_user_skills_root = remote_user_skills_root


def test_build_skill_registry_returns_none_when_no_roots() -> None:
    assert (
        build_skill_registry(
            config_roots=(),
            session=_Session(),
            config_disabled=(),
        )
        is None
    )


def test_build_skill_registry_appends_local_user_skills_root(tmp_path: Path) -> None:
    config_root = tmp_path / "config_skills"
    user_root = tmp_path / "user_skills"
    _write_skill(config_root, "alpha")
    _write_skill(user_root, "beta")

    registry = build_skill_registry(
        config_roots=(config_root,),
        session=_Session(local_user_skills_root=str(user_root)),
        config_disabled=(),
    )

    assert registry is not None
    assert {skill.meta_info.name for skill in registry.get_all_skills()} == {
        "alpha",
        "beta",
    }


def test_build_skill_registry_applies_config_and_settings_disable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "alpha")
    _write_skill(root, "beta")
    _write_skill(root, "gamma")
    (root / ".settings.json").write_text(
        json.dumps({"disabled": ["gamma"]}),
        encoding="utf-8",
    )

    registry = build_skill_registry(
        config_roots=(root,),
        session=_Session(),
        config_disabled=("beta",),
    )

    assert registry is not None
    assert {skill.meta_info.name for skill in registry.get_all_skills()} == {"alpha"}
