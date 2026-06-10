"""Tests for matmaster.skills.registry — Skill, SkillMetaInfo, SkillRegistry."""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath

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

SKILL_MD_MEMBER = """\
---
name: member-skill
description: Plugin member skill
---
Member body.
"""


class FakeRemoteSkillSession:
    def __init__(
        self,
        files: dict[str, str],
        errors: dict[str, str] | None = None,
    ) -> None:
        self._files = files
        self._errors = errors or {}
        self.exec_calls: list[str] = []
        self.read_calls: list[str] = []

    def _all_paths(self) -> list[str]:
        return sorted([*self._files, *self._errors])

    def path_exists(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(
            candidate == path or candidate.startswith(prefix)
            for candidate in self._all_paths()
        )

    def exec_bash(self, command: str, timeout: int | None = None) -> dict[str, object]:
        self.exec_calls.append(command)
        root = shlex.split(command)[-1].rstrip("/")
        prefix = root + "/"
        payload: list[dict[str, str]] = []
        for path in self._all_paths():
            if not path.startswith(prefix):
                continue
            if not path.endswith(("/SKILL.md", "/plugin.yaml")):
                continue
            if path in self._errors:
                payload.append({"path": path, "error": self._errors[path]})
            else:
                payload.append({"path": path, "content": self._files[path]})
        return {"exit_code": 0, "stdout": json.dumps(payload)}

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        self.read_calls.append(path)
        return self._files[path]


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


def test_remote_scan_script_collects_skills_plugins_and_errors(
    tmp_path: Path,
) -> None:
    """真实扫描脚本：收集 SKILL.md 与 plugin.yaml，读失败产出 error 条目。"""
    from matmaster.skills.registry import _REMOTE_SKILL_SCAN_SCRIPT

    root = tmp_path / "plugins"
    _write(root / "chem-pack" / "plugin.yaml", "name: chem-pack\n")
    _write(root / "chem-pack" / "skills" / "calc" / "SKILL.md", SKILL_MD_CALC)
    _write(root / "notes" / "README.md", "ignored")
    broken = root / "broken" / "SKILL.md"
    broken.parent.mkdir(parents=True)
    broken.symlink_to(tmp_path / "missing-target")

    result = subprocess.run(
        [sys.executable, "-c", _REMOTE_SKILL_SCAN_SCRIPT, str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    items = {item["path"]: item for item in json.loads(result.stdout)}

    manifest = str(root / "chem-pack" / "plugin.yaml")
    skill_md = str(root / "chem-pack" / "skills" / "calc" / "SKILL.md")
    assert set(items) == {manifest, skill_md, str(broken)}
    assert items[manifest]["content"] == "name: chem-pack\n"
    assert "full body of the calculator skill" in items[skill_md]["content"]
    assert items[str(broken)]["error"]
    assert "content" not in items[str(broken)]


# ===========================================================================
# Skill tests
# ===========================================================================


class TestSkill:
    """Tests for the Skill class."""

    def test_parse_frontmatter(self, skill_tree: dict[str, Path]) -> None:
        """Frontmatter is parsed into SkillMetaInfo with name, description, mcp_server."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        assert skill.meta_info.name == "calculator"
        assert skill.meta_info.description == "A calculation skill"
        assert skill.meta_info.mcp_server == "calc-server"
        assert skill.meta_info.extras == {}

    def test_parse_frontmatter_mcp_server_as_field(
        self, skill_tree: dict[str, Path]
    ) -> None:
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        assert skill.meta_info.mcp_server == "calc-server"
        assert "mcp_server" not in skill.meta_info.extras

    def test_parse_frontmatter_depends_on(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import Skill

        skill_dir = tmp_path / "deps-skill"
        _write(
            skill_dir / "SKILL.md",
            "---\n"
            "name: deps-skill\n"
            "description: Depends on MCPs\n"
            "depends_on: mcp-a, mcp-b\n"
            "---\n"
            "Body.\n",
        )
        skill = Skill(skill_dir)
        assert skill.meta_info.depends_on == ["mcp-a", "mcp-b"]
        assert "depends_on" not in skill.meta_info.extras

    def test_parse_frontmatter_depends_on_empty(
        self, skill_tree: dict[str, Path]
    ) -> None:
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "search")
        assert skill.meta_info.depends_on == []

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

    def test_skill_exposes_only_active_runtime_helpers(
        self, skill_tree: dict[str, Path]
    ) -> None:
        """Skill no longer exposes legacy reference/script dispatch helpers."""
        from matmaster.skills.registry import Skill

        skill = Skill(skill_tree["root1"] / "calculator")
        assert not hasattr(skill, "available_scripts")
        assert not hasattr(skill, "get_script_path")
        assert not hasattr(skill, "get_reference")


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

    def test_skips_underscore_prefixed_dirs(self, skill_tree: dict[str, Path]) -> None:
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

    def test_later_root_overrides_earlier(self, skill_tree: dict[str, Path]) -> None:
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

    def test_remote_root_scan_via_session(self) -> None:
        """Registry discovers SKILL.md files from session-backed remote roots."""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                "/personal/.matmaster/skills/user-skill/SKILL.md": (
                    "---\n"
                    "name: user-skill\n"
                    "description: User remote skill\n"
                    "---\n"
                    "Remote body\n"
                ),
                "/personal/.matmaster/skills/_internal/SKILL.md": (
                    "---\n"
                    "name: hidden\n"
                    "description: Hidden\n"
                    "---\n"
                    "Hidden body\n"
                ),
                "/personal/.matmaster/skills/parent/SKILL.md": (
                    "---\n"
                    "name: parent\n"
                    "description: Parent\n"
                    "---\n"
                    "Parent body\n"
                ),
                "/personal/.matmaster/skills/parent/scripts/SKILL.md": (
                    "---\n"
                    "name: nested\n"
                    "description: Nested\n"
                    "---\n"
                    "Nested body\n"
                ),
            }
        )

        reg = SkillRegistry(
            [],
            remote_session=session,
            remote_roots=["/personal/.matmaster/skills"],
        )

        names = sorted(skill.meta_info.name for skill in reg.get_all_skills())
        assert names == ["parent", "user-skill"]
        skill = reg.get_skill("user-skill")
        assert skill is not None
        assert skill.skill_path == PurePosixPath(
            "/personal/.matmaster/skills/user-skill"
        )
        assert skill.get_full_info() == "Remote body"
        assert session.read_calls == []

    def test_remote_skill_over_local_fallback_is_not_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Remote skill replacing the local fallback is expected precedence."""
        from matmaster.skills.registry import SkillRegistry

        local_root = tmp_path / "local"
        _write(
            local_root / "calculator" / "SKILL.md",
            "---\n"
            "name: calculator\n"
            "description: Local fallback calculator\n"
            "---\n"
            "Local body\n",
        )
        session = FakeRemoteSkillSession(
            {
                "/personal/.matmaster/skills/calculator/SKILL.md": (
                    "---\n"
                    "name: calculator\n"
                    "description: Remote calculator\n"
                    "---\n"
                    "Remote body\n"
                )
            }
        )

        with caplog.at_level(logging.INFO, logger="matmaster.skills.registry"):
            reg = SkillRegistry(
                local_root,
                remote_session=session,
                remote_roots=["/personal/.matmaster/skills"],
            )

        calculator = reg.get_skill("calculator")
        assert calculator is not None
        assert calculator.meta_info.description == "Remote calculator"
        assert not [
            record
            for record in caplog.records
            if record.levelno >= logging.WARNING and "overridden" in record.message
        ]
        assert "Skill registry built:" in caplog.text
        assert "remote_over_local=1" in caplog.text
        assert "local_fallback=0" in caplog.text

    def test_remote_roots_are_normalized_before_scan(self) -> None:
        """Equivalent remote root spellings are scanned only once."""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                "/personal/.matmaster/skills/user-skill/SKILL.md": (
                    "---\n"
                    "name: user-skill\n"
                    "description: User remote skill\n"
                    "---\n"
                    "Remote body\n"
                )
            }
        )

        reg = SkillRegistry(
            [],
            remote_session=session,
            remote_roots=[
                "/personal/.matmaster/skills",
                "/personal/.matmaster/skills/",
            ],
        )

        assert reg.get_skill("user-skill") is not None
        assert len(session.exec_calls) == 1


class TestRemotePluginAttribution:
    """远端 plugin.yaml 归属：挂载、祖先匹配、root 边界、失败语义、覆盖顺序。"""

    ROOT = "/personal/.matmaster/plugins"

    def test_remote_member_mounts_nearest_plugin(self) -> None:
        """成员挂最近祖先 PluginInfo；name 缺省回退目录名；散装不受影响。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/outer/plugin.yaml": "description: Outer pack\n",
                f"{self.ROOT}/outer/inner/plugin.yaml": (
                    "name: inner-pack\ndescription: Inner\n"
                ),
                f"{self.ROOT}/outer/inner/skills/member/SKILL.md": SKILL_MD_MEMBER,
                f"{self.ROOT}/outer/skills/outer-member/SKILL.md": (
                    "---\nname: outer-member\ndescription: Outer member\n---\nBody\n"
                ),
                f"{self.ROOT}/loose/SKILL.md": (
                    "---\nname: loose-skill\ndescription: Loose\n---\nBody\n"
                ),
            }
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])

        member = reg.get_skill("member-skill")
        assert member is not None
        assert member.plugin is not None
        assert member.plugin.name == "inner-pack"
        assert member.plugin.description == "Inner"
        assert member.plugin_dir == PurePosixPath(f"{self.ROOT}/outer/inner")
        outer_member = reg.get_skill("outer-member")
        assert outer_member is not None
        assert outer_member.plugin is not None
        assert outer_member.plugin.name == "outer"
        assert outer_member.plugin.description == "Outer pack"
        loose = reg.get_skill("loose-skill")
        assert loose is not None
        assert loose.plugin is None
        assert loose.plugin_dir is None

    def test_plugin_yaml_at_root_is_ignored(self) -> None:
        """远端根自身不作 plugin 根：根下 plugin.yaml 不产生归属。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/plugin.yaml": "name: root-pack\n",
                f"{self.ROOT}/some-skill/SKILL.md": (
                    "---\nname: some-skill\ndescription: S\n---\nBody\n"
                ),
            }
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])

        skill = reg.get_skill("some-skill")
        assert skill is not None
        assert skill.plugin is None

    def test_invalid_plugin_manifest_fails_members(self) -> None:
        """plugin.yaml 读失败或坏 YAML：成员加载失败；他组不受影响。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/bad-yaml/plugin.yaml": "{ name: [unbalanced",
                f"{self.ROOT}/bad-yaml/skills/y/SKILL.md": (
                    "---\nname: y-skill\ndescription: Y\n---\nBody\n"
                ),
                f"{self.ROOT}/unreadable/skills/z/SKILL.md": (
                    "---\nname: z-skill\ndescription: Z\n---\nBody\n"
                ),
                f"{self.ROOT}/ok/plugin.yaml": "name: ok-pack\n",
                f"{self.ROOT}/ok/skills/w/SKILL.md": (
                    "---\nname: w-skill\ndescription: W\n---\nBody\n"
                ),
            },
            errors={
                f"{self.ROOT}/unreadable/plugin.yaml": "Permission denied",
            },
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])

        assert reg.get_skill("y-skill") is None
        assert reg.get_skill("z-skill") is None
        assert reg.get_skill("w-skill") is not None

    def test_skill_error_entry_skipped(self) -> None:
        """SKILL.md error 条目：跳过该 skill，不影响同根其余条目。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/ok/plugin.yaml": "name: ok-pack\n",
                f"{self.ROOT}/ok/skills/good/SKILL.md": (
                    "---\nname: good-skill\ndescription: G\n---\nBody\n"
                ),
            },
            errors={
                f"{self.ROOT}/ok/skills/broken/SKILL.md": "I/O error",
            },
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])

        names = {s.meta_info.name for s in reg.get_all_skills()}
        assert names == {"good-skill"}

    def test_skills_root_overrides_plugins_root_member(self) -> None:
        """根顺序覆盖：散装个人 skill（skills 根，后扫描）覆盖同名 plugin 成员。"""
        from matmaster.skills.registry import SkillRegistry

        skills_root = "/personal/.matmaster/skills"
        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/pack/plugin.yaml": "name: pack\n",
                f"{self.ROOT}/pack/skills/dup/SKILL.md": (
                    "---\nname: dup-skill\ndescription: Member version\n---\nBody\n"
                ),
                f"{skills_root}/dup/SKILL.md": (
                    "---\nname: dup-skill\ndescription: Loose version\n---\nBody\n"
                ),
            }
        )

        reg = SkillRegistry(
            [],
            remote_session=session,
            remote_roots=[self.ROOT, skills_root],
        )

        dup = reg.get_skill("dup-skill")
        assert dup is not None
        assert dup.meta_info.description == "Loose version"
        assert dup.plugin is None

    def test_meta_info_context_groups_remote_members(self) -> None:
        """提示词分组：远端成员归组在 [Plugin: ...] 名下。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/chem-pack/plugin.yaml": (
                    "name: chem-pack\ndescription: Chemistry toolkit\n"
                ),
                f"{self.ROOT}/chem-pack/skills/member/SKILL.md": SKILL_MD_MEMBER,
            }
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])
        ctx = reg.get_meta_info_context()

        assert "[Plugin: chem-pack] Chemistry toolkit" in ctx
        assert "  [Skill: member-skill] Plugin member skill" in ctx


class TestSkillRegistryCache:
    def test_cache_hit_reuses_same_registry_instance(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import SkillRegistry, SkillRegistryCache

        cache = SkillRegistryCache()
        calls = 0

        def build() -> SkillRegistry:
            nonlocal calls
            calls += 1
            return SkillRegistry(tmp_path / "missing")

        key = ((str(tmp_path / "missing"),), (), ())
        first = cache.get_or_build(key, build)
        second = cache.get_or_build(key, build)

        assert first is second
        assert calls == 1

    def test_cache_key_isolates_different_signatures(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import SkillRegistry, SkillRegistryCache

        cache = SkillRegistryCache()
        first = cache.get_or_build(
            ((str(tmp_path / "a"),), (), ()),
            lambda: SkillRegistry(tmp_path / "a"),
        )
        second = cache.get_or_build(
            ((str(tmp_path / "b"),), (), ()),
            lambda: SkillRegistry(tmp_path / "b"),
        )

        assert first is not second

    @pytest.mark.asyncio
    async def test_skill_consumers_do_not_mutate_registry_membership(
        self,
        tmp_path: Path,
    ) -> None:
        from matmaster.context.ports import SessionEvent
        from matmaster.context.skill_resolver import SkillRegistryResolver
        from matmaster.tools.builtin.skill_tool import SkillTool

        skill_dir = tmp_path / "stable-skill"
        _write(
            skill_dir / "SKILL.md",
            "---\n"
            "name: stable-skill\n"
            "description: Stable skill\n"
            "---\n"
            "Stable body\n",
        )

        class GuardedRegistry:
            def __init__(self) -> None:
                from matmaster.skills.registry import Skill

                self._skills = {"stable-skill": Skill(skill_dir)}
                self.removed = False

            def get_skill(self, name: str):
                return self._skills.get(name)

            def get_all_skills(self):
                return list(self._skills.values())

            def get_meta_info_context(self) -> str:
                return "\n".join(
                    f"[Skill: {skill.meta_info.name}] {skill.meta_info.description}"
                    for skill in self._skills.values()
                )

            def remove_skills(self, names: set[str]) -> None:
                self.removed = True
                raise AssertionError("runtime consumer must not change membership")

        registry = GuardedRegistry()
        tool = SkillTool(skill_registry=registry)
        result = await tool.execute({"skill": "stable-skill"})
        assert "Stable body" in result

        resolver = SkillRegistryResolver(registry)
        resolved = resolver(
            (
                SessionEvent(
                    id=1,
                    event_type="skill_hit",
                    source="agent",
                    content={"skill_name": "stable-skill"},
                ),
            )
        )
        assert resolved[0].name == "stable-skill"
        assert registry.removed is False
