from __future__ import annotations

import json
import shlex
from pathlib import Path

from matmaster.config.exp import ExpSkillsConfig
from matmaster.core.skill_registry_cache import (
    build_cached_skill_registry,
    skill_registry_cache_key,
)
from matmaster.skills.registry import SkillRegistryCache


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _skill_body(name: str, description: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n"
        f"{description} body\n"
    )


class FakeRemoteSkillSession:
    def __init__(self, root: str, files: dict[str, str]) -> None:
        self.remote_user_skills_root = root
        self.remote_skill_roots: list[str] = []
        self.local_user_skills_root: str | None = None
        self._files = files
        self.exec_calls: list[str] = []
        self.read_calls: list[str] = []

    def path_exists(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(
            candidate == path or candidate.startswith(prefix)
            for candidate in self._files
        )

    def exec_bash(self, command: str, timeout: int | None = None) -> dict[str, object]:
        self.exec_calls.append(command)
        root = shlex.split(command)[-1].rstrip("/")
        prefix = root + "/"
        payload = [
            {"path": path, "content": self._files[path]}
            for path in sorted(self._files)
            if path.endswith("/SKILL.md") and path.startswith(prefix)
        ]
        return {"exit_code": 0, "stdout": json.dumps(payload)}

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        self.read_calls.append(path)
        return self._files[path]


def test_cache_key_preserves_local_root_order_and_normalizes_remote_roots(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"

    key_ab = skill_registry_cache_key(
        local_roots=[root_a, root_b],
        remote_roots=["/personal/.matmaster/skills", "/personal/.matmaster/skills/"],
        config_disabled_skill_names=["zeta", "alpha"],
    )
    key_ba = skill_registry_cache_key(
        local_roots=[root_b, root_a],
        remote_roots=["/personal/.matmaster/skills/"],
        config_disabled_skill_names=["alpha", "zeta"],
    )

    assert key_ab[0] == (str(root_a), str(root_b))
    assert key_ab[1] == ("/personal/.matmaster/skills",)
    assert key_ab[2] == ("alpha", "zeta")
    assert key_ba[0] == (str(root_b), str(root_a))
    assert key_ab != key_ba


def test_build_cached_skill_registry_reuses_remote_scan_with_same_signature(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "local"
    _write(local_root / "local-skill" / "SKILL.md", _skill_body("local-skill", "Local"))
    remote_root = "/personal/.matmaster/skills"
    session = FakeRemoteSkillSession(
        remote_root,
        {
            f"{remote_root}/remote-skill/SKILL.md": _skill_body(
                "remote-skill",
                "Remote",
            )
        },
    )
    skills_cfg = ExpSkillsConfig(
        enabled=True,
        skills_root=[str(local_root)],
    )
    cache = SkillRegistryCache()

    first = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=session,
        skill_cache=cache,
    )
    second = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=session,
        skill_cache=cache,
    )

    assert first is second
    assert first is not None
    assert first.get_skill("remote-skill") is not None
    assert len(session.exec_calls) == 1


def test_build_cached_skill_registry_isolates_config_disabled_names(
    tmp_path: Path,
) -> None:
    remote_root = "/personal/.matmaster/skills"
    session = FakeRemoteSkillSession(
        remote_root,
        {
            f"{remote_root}/remote-skill/SKILL.md": _skill_body(
                "remote-skill",
                "Remote",
            )
        },
    )
    cache = SkillRegistryCache()
    enabled_cfg = ExpSkillsConfig(enabled=True, skills_root=[])
    disabled_cfg = ExpSkillsConfig(
        enabled=True,
        skills_root=[],
        disabled_skill_names=["remote-skill"],
    )

    visible = build_cached_skill_registry(
        skills_cfg=enabled_cfg,
        session=session,
        skill_cache=cache,
    )
    hidden = build_cached_skill_registry(
        skills_cfg=disabled_cfg,
        session=session,
        skill_cache=cache,
    )

    assert visible is not hidden
    assert visible is not None
    assert hidden is not None
    assert visible.get_skill("remote-skill") is not None
    assert hidden.get_skill("remote-skill") is None
    assert len(session.exec_calls) == 2


def test_new_query_cache_rebuilds_registry_after_remote_skill_change(
    tmp_path: Path,
) -> None:
    remote_root = "/personal/.matmaster/skills"
    session = FakeRemoteSkillSession(
        remote_root,
        {
            f"{remote_root}/remote-skill/SKILL.md": _skill_body(
                "remote-skill",
                "Old Remote",
            )
        },
    )
    skills_cfg = ExpSkillsConfig(enabled=True, skills_root=[])

    old_registry = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=session,
        skill_cache=SkillRegistryCache(),
    )
    session._files[f"{remote_root}/remote-skill/SKILL.md"] = _skill_body(
        "remote-skill",
        "New Remote",
    )
    new_registry = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=session,
        skill_cache=SkillRegistryCache(),
    )

    assert old_registry is not None
    assert new_registry is not None
    assert old_registry.get_skill("remote-skill").meta_info.description == "Old Remote"
    assert new_registry.get_skill("remote-skill").meta_info.description == "New Remote"
    assert len(session.exec_calls) == 2
