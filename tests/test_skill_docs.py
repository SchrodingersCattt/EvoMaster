from __future__ import annotations

from pathlib import Path


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
