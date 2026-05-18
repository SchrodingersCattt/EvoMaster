from __future__ import annotations

from pathlib import Path

import yaml


def test_skill_docs_do_not_reference_legacy_skill_dispatch_api() -> None:
    """Skill docs should not tell models to call removed Skill action helpers."""
    skills_root = Path("matmaster/skills")
    legacy_markers = (
        "Skill action=",
        "Skill run_script",
        "action=run_script",
        "action=`run_script`",
        "action=get_reference",
        "action=get_info",
        "script_args",
    )
    offenders: list[str] = []
    for path in sorted(skills_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for marker in legacy_markers:
            if marker in text:
                offenders.append(f"{path}: {marker}")
    assert offenders == []


_MAX_DESCRIPTION_LENGTH = 300


def test_skill_description_length_limit() -> None:
    """Skill descriptions must be at most 300 characters to keep routing prompts lean."""
    skills_root = Path("matmaster/skills")
    offenders: list[str] = []
    for path in sorted(skills_root.rglob("SKILL.md")):
        if any(part.startswith("_") for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        try:
            fm = yaml.safe_load(parts[1])
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        desc = fm.get("description", "")
        if len(desc) > _MAX_DESCRIPTION_LENGTH:
            offenders.append(
                f"{path} ({fm.get('name', '?')}): {len(desc)} chars (max {_MAX_DESCRIPTION_LENGTH})"
            )
    assert offenders == [], (
        f"{len(offenders)} skill(s) exceed description limit:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )
