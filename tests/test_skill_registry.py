"""Tests for matmaster.skills.registry — Skill, SkillMetaInfo, SkillRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures: build a temporary skill tree on disk
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


SKILL_MD_CALC = """\
---
name: calculator
description: A calculation skill
mcp_server: calc-server
---
# Calculator

This is the full body of the calculator skill.
"""

SKILL_MD_SEARCH = """\
---
name: search
description: Web search skill
---
Body of search skill.
"""

SKILL_MD_OVERRIDE = """\
---
name: calculator
description: Overridden calculator from root2
---
Overridden body.
"""

SKILL_MD_NESTED = """\
---
name: nested-skill
description: A skill in a nested subdirectory
---
Nested body.
"""

SKILL_MD_NO_FRONTMATTER = "No frontmatter here."


@pytest.fixture()
def skill_tree(tmp_path: Path) -> dict[str, Path]:
    """Create a skill directory tree and return key paths.

    Layout (root1):
        root1/
            calculator/
                SKILL.md          (calculator skill)
                scripts/
                    run.py
                    helper.sh
                    notes.md      (not a script)
                references/
                    api.md
            search/
                SKILL.md          (search skill)
            _common/
                reference/
                    shared.md
                greeting.md
            _internal/
                SKILL.md          (should be skipped — _ prefix)
            category/
                nested-skill/
                    SKILL.md      (nested recursive discovery)

    Layout (root2):
        root2/
            calculator/
                SKILL.md          (overrides root1/calculator)
    """
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"

    # --- root1/calculator ---
    _write(root1 / "calculator" / "SKILL.md", SKILL_MD_CALC)
    _write(root1 / "calculator" / "scripts" / "run.py", "print('run')")
    _write(root1 / "calculator" / "scripts" / "helper.sh", "echo hi")
    _write(root1 / "calculator" / "scripts" / "notes.md", "# not a script")
    _write(root1 / "calculator" / "references" / "api.md", "API reference content")

    # --- root1/search ---
    _write(root1 / "search" / "SKILL.md", SKILL_MD_SEARCH)

    # --- root1/_common (shared references) ---
    _write(root1 / "_common" / "reference" / "shared.md", "Shared reference content")
    _write(root1 / "_common" / "greeting.md", "Hello from _common")

    # --- root1/_internal (should be skipped) ---
    _write(root1 / "_internal" / "SKILL.md", SKILL_MD_SEARCH)

    # --- root1/category/nested-skill (recursive discovery) ---
    _write(root1 / "category" / "nested-skill" / "SKILL.md", SKILL_MD_NESTED)

    # --- root2/calculator (override) ---
    _write(root2 / "calculator" / "SKILL.md", SKILL_MD_OVERRIDE)

    return {"root1": root1, "root2": root2, "tmp": tmp_path}


# ===========================================================================
# Skill tests
# ===========================================================================


class TestSkill:
    """Tests for the Skill class."""

    def test_parse_frontmatter(self, skill_tree: dict[str, Path]) -> None:
        """Frontmatter is parsed into SkillMetaInfo with name, description, extras."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        assert skill.meta_info.name == "calculator"
        assert skill.meta_info.description == "A calculation skill"
        assert skill.meta_info.extras == {"mcp_server": "calc-server"}

    def test_get_full_info_returns_body(self, skill_tree: dict[str, Path]) -> None:
        """get_full_info() returns the markdown body after the frontmatter block."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        body = skill.get_full_info()
        assert "# Calculator" in body
        assert "full body of the calculator skill" in body
        # frontmatter delimiters should NOT appear
        assert "---" not in body

    def test_full_info_is_cached(self, skill_tree: dict[str, Path]) -> None:
        """Calling get_full_info() twice returns the same cached object."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        first = skill.get_full_info()
        second = skill.get_full_info()
        assert first is second

    def test_missing_skill_md_raises(self, tmp_path: Path) -> None:
        """Constructing Skill on a directory without SKILL.md raises FileNotFoundError."""
        from matmaster.skills.registry import Skill

        empty_dir = tmp_path / "no_skill"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            Skill(empty_dir)

    def test_scan_scripts(self, skill_tree: dict[str, Path]) -> None:
        """_scan_scripts finds .py and .sh files but not .md files."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        script_names = sorted(s.name for s in skill.available_scripts)
        assert script_names == ["helper.sh", "run.py"]

    def test_get_script_path(self, skill_tree: dict[str, Path]) -> None:
        """get_script_path returns the Path for a known script, None for unknown."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        assert skill.get_script_path("run.py") is not None
        assert skill.get_script_path("run.py").name == "run.py"
        assert skill.get_script_path("nonexistent.py") is None

    def test_get_reference_in_references_dir(
        self, skill_tree: dict[str, Path]
    ) -> None:
        """get_reference finds a file in the references/ subdirectory."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        content = skill.get_reference("api.md")
        assert content == "API reference content"

    def test_get_reference_fallback_to_common(
        self, skill_tree: dict[str, Path]
    ) -> None:
        """get_reference falls back to _common/reference/ when not found locally."""
        from matmaster.skills.registry import Skill

        # search skill has no local references — should fall back to _common
        skill = Skill(skill_tree["root1"] / "search")
        content = skill.get_reference("shared.md")
        assert content == "Shared reference content"

    def test_get_reference_fallback_to_common_root(
        self, skill_tree: dict[str, Path]
    ) -> None:
        """get_reference falls back to _common/<name> (no reference/ subdir)."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "search")
        content = skill.get_reference("greeting.md")
        assert content == "Hello from _common"

    def test_get_reference_not_found_raises(
        self, skill_tree: dict[str, Path]
    ) -> None:
        """get_reference raises FileNotFoundError when no candidate exists."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        with pytest.raises(FileNotFoundError):
            skill.get_reference("does_not_exist.md")


# ===========================================================================
# SkillRegistry tests
# ===========================================================================


class TestSkillRegistry:
    """Tests for the SkillRegistry class."""

    def test_single_root_scan(self, skill_tree: dict[str, Path]) -> None:
        """Registry discovers skills from a single root directory."""
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry(skill_tree["root1"])
        names = sorted(s.meta_info.name for s in reg.get_all_skills())
        # calculator, search, nested-skill — but NOT _internal
        assert "calculator" in names
        assert "search" in names
        assert "nested-skill" in names

    def test_multi_root_scan(self, skill_tree: dict[str, Path]) -> None:
        """Registry discovers skills from multiple root directories."""
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry([skill_tree["root1"], skill_tree["root2"]])
        names = sorted(s.meta_info.name for s in reg.get_all_skills())
        assert "calculator" in names
        assert "search" in names

    def test_recursive_scan(self, skill_tree: dict[str, Path]) -> None:
        """Registry discovers SKILL.md in nested subdirectories via rglob."""
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry(skill_tree["root1"])
        skill = reg.get_skill("nested-skill")
        assert skill is not None
        assert skill.meta_info.description == "A skill in a nested subdirectory"

    def test_skips_underscore_prefixed_dirs(
        self, skill_tree: dict[str, Path]
    ) -> None:
        """Directories starting with _ (like _common, _internal) are skipped."""
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry(skill_tree["root1"])
        all_names = [s.meta_info.name for s in reg.get_all_skills()]
        # _internal contains SKILL.md with name=search, but it should be skipped
        # so we should have exactly one 'search' (from root1/search), not two
        assert all_names.count("search") == 1

    def test_skips_nested_skill_md_in_scripts(self, tmp_path: Path) -> None:
        """SKILL.md inside a scripts/ subdir of another skill is skipped."""
        root = tmp_path / "root"
        _write(
            root / "parent_skill" / "SKILL.md",
            "---\nname: parent\ndescription: parent skill\n---\nBody.",
        )
        _write(
            root / "parent_skill" / "scripts" / "SKILL.md",
            "---\nname: bogus\ndescription: should be skipped\n---\nNested.",
        )

        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry(root)
        names = [s.meta_info.name for s in reg.get_all_skills()]
        assert "parent" in names
        assert "bogus" not in names

    def test_later_root_overrides_earlier(
        self, skill_tree: dict[str, Path]
    ) -> None:
        """When the same skill name appears in multiple roots, later root wins."""
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry([skill_tree["root1"], skill_tree["root2"]])
        calc = reg.get_skill("calculator")
        assert calc is not None
        assert calc.meta_info.description == "Overridden calculator from root2"

    def test_name_filter(self, skill_tree: dict[str, Path]) -> None:
        """Providing a skills name filter only loads matching skills."""
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry(skill_tree["root1"], skills=["search"])
        names = [s.meta_info.name for s in reg.get_all_skills()]
        assert names == ["search"]

    def test_nonexistent_root_no_error(self, tmp_path: Path) -> None:
        """A non-existent root path does not raise — just yields zero skills."""
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry(tmp_path / "does_not_exist")
        assert reg.get_all_skills() == []

    def test_get_skill_returns_none_for_missing(
        self, skill_tree: dict[str, Path]
    ) -> None:
        """get_skill returns None for a skill name that doesn't exist."""
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry(skill_tree["root1"])
        assert reg.get_skill("nonexistent") is None

    def test_get_meta_info_context(self, skill_tree: dict[str, Path]) -> None:
        """get_meta_info_context returns a string containing all skill info."""
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry(skill_tree["root1"])
        ctx = reg.get_meta_info_context()
        assert "[Skill: calculator]" in ctx
        assert "A calculation skill" in ctx
        assert "[Skill: search]" in ctx
        assert "Web search skill" in ctx
