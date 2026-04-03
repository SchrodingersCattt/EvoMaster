# Skill Prompt Expansion Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `use_skill` from a 3-action dispatch tool to a single-entry prompt expansion tool, and promote `SkillMetaInfo` extras to structured Pydantic fields.

**Architecture:** `SkillMetaInfo` gains typed fields (`skill_type`, `mcp_server`, `depends_on`) with parsing changes in `_parse_meta_info`. `SkillTool` is rewritten to return expanded SKILL.md content with base directory header and `${SKILL_DIR}` substitution. The 3-action dispatch (`get_info`/`get_reference`/`run_script`) is removed entirely.

**Tech Stack:** Python 3.10+, Pydantic, pytest, asyncio

**Spec:** `docs/superpowers/specs/2026-04-04-skill-prompt-expansion-design.md`

**Note:** `cc-tools/skill_tool.py` and `gpt-tools/gpt_tools/tools/interaction.py` contain
separate SkillTool implementations for claude-code and GPT integrations respectively.
These are unrelated to `matmaster/tools/skill_tool.py` and are NOT modified in this plan.

---

## Chunk 1: Structured Frontmatter

### Task 1: SkillMetaInfo structured fields

**Files:**
- Modify: `matmaster/skills/registry.py:29-86`
- Test: `tests/test_skill_registry.py`
- Test: `tests/matmaster/tools/test_skill_meta_extras.py`

- [ ] **Step 1: Write failing tests for new SkillMetaInfo fields**

Add tests in `tests/test_skill_registry.py` inside `TestSkill`:

```python
def test_parse_frontmatter_mcp_server_as_field(self, skill_tree: dict[str, Path]) -> None:
    """mcp_server is parsed into meta_info.mcp_server, not extras."""
    from matmaster.skills.registry import Skill

    skill = Skill(skill_tree["root1"] / "calculator")
    assert skill.meta_info.mcp_server == "calc-server"
    assert "mcp_server" not in skill.meta_info.extras

def test_parse_frontmatter_skill_type(self, skill_tree: dict[str, Path]) -> None:
    """skill_type is parsed into meta_info.skill_type."""
    from matmaster.skills.registry import Skill

    skill = Skill(skill_tree["root1"] / "calculator")
    # calculator fixture doesn't have skill_type, so it should be None
    assert skill.meta_info.skill_type is None

def test_parse_frontmatter_depends_on(self, tmp_path: Path) -> None:
    """depends_on comma-separated string is parsed into list[str]."""
    from matmaster.skills.registry import Skill

    skill_dir = tmp_path / "workflow"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: workflow\ndescription: test\n"
        "depends_on: mcp-a, mcp-b\n---\nBody\n"
    )
    skill = Skill(skill_dir)
    assert skill.meta_info.depends_on == ["mcp-a", "mcp-b"]
    assert "depends_on" not in skill.meta_info.extras

def test_parse_frontmatter_depends_on_empty(self, skill_tree: dict[str, Path]) -> None:
    """Skills without depends_on have an empty list."""
    from matmaster.skills.registry import Skill

    skill = Skill(skill_tree["root1"] / "search")
    assert skill.meta_info.depends_on == []

def test_parse_frontmatter_skill_type_operator(self, tmp_path: Path) -> None:
    """skill_type is correctly parsed when present."""
    from matmaster.skills.registry import Skill

    skill_dir = tmp_path / "op-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: op-skill\ndescription: test\nskill_type: operator\n---\nBody\n"
    )
    skill = Skill(skill_dir)
    assert skill.meta_info.skill_type == "operator"
```

Also update the existing `test_parse_frontmatter` test -- currently it asserts `skill.meta_info.extras == {"mcp_server": "calc-server"}`. Change to:

```python
def test_parse_frontmatter(self, skill_tree: dict[str, Path]) -> None:
    """Frontmatter is parsed into SkillMetaInfo with name, description, typed fields."""
    from matmaster.skills.registry import Skill

    skill = Skill(skill_tree["root1"] / "calculator")
    assert skill.meta_info.name == "calculator"
    assert skill.meta_info.description == "A calculation skill"
    assert skill.meta_info.mcp_server == "calc-server"
    assert skill.meta_info.extras == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_skill_registry.py::TestSkill -v`
Expected: FAIL -- `mcp_server` is still in extras, `skill_type`/`depends_on` fields don't exist yet.

- [ ] **Step 3: Implement SkillMetaInfo field changes**

In `matmaster/skills/registry.py`, update `SkillMetaInfo`:

```python
from typing import Any, Literal

class SkillMetaInfo(BaseModel):
    """Skill 元信息，从 SKILL.md 的 frontmatter 解析。"""

    name: str = Field(description="技能名称")
    description: str = Field(description="技能描述")
    skill_type: Literal["operator", "mcp-loader", "orchestrator"] | None = Field(
        default=None, description="技能类别"
    )
    mcp_server: str | None = Field(
        default=None, description="MCP server 名称"
    )
    depends_on: list[str] = Field(
        default_factory=list, description="依赖的技能名称列表"
    )
    extras: dict[str, Any] = Field(default_factory=dict, description="扩展字段")
```

Update `_parse_meta_info` in `Skill`:

```python
def _parse_meta_info(self) -> SkillMetaInfo:
    skill_md = self.skill_path / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found in {self.skill_path}")

    content = skill_md.read_text(encoding="utf-8")

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        raise ValueError(f"Invalid SKILL.md: no frontmatter in {skill_md}")

    known_keys = {"name", "description", "skill_type", "mcp_server", "depends_on"}
    data: dict[str, str] = {}
    for line in fm_match.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()

    extras = {k: v for k, v in data.items() if k not in known_keys}

    # Parse depends_on from comma-separated string to list
    depends_on_raw = data.get("depends_on", "")
    depends_on = [d.strip() for d in depends_on_raw.split(",") if d.strip()]

    return SkillMetaInfo(
        name=data.get("name", self.skill_path.name),
        description=data.get("description", ""),
        skill_type=data.get("skill_type"),
        mcp_server=data.get("mcp_server"),
        depends_on=depends_on,
        extras=extras,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_skill_registry.py::TestSkill -v`
Expected: PASS

- [ ] **Step 5: Update test_skill_meta_extras.py**

In `tests/matmaster/tools/test_skill_meta_extras.py`, update tests:

```python
class TestSkillMetaInfoExtras:
    def test_extras_captures_unknown_fields(self):
        info = SkillMetaInfo(
            name="test-skill",
            description="A test skill",
            mcp_server="mat_sg",
            extras={"custom_flag": "true"},
        )
        assert info.mcp_server == "mat_sg"
        assert info.extras["custom_flag"] == "true"
        assert "mcp_server" not in info.extras

    def test_extras_defaults_empty(self):
        info = SkillMetaInfo(name="test", description="desc")
        assert info.extras == {}
        assert info.mcp_server is None
        assert info.skill_type is None
        assert info.depends_on == []

    def test_parse_frontmatter_extras(self, tmp_path):
        """SKILL.md with mcp_server in frontmatter puts it in mcp_server field."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test\nmcp_server: mat_sg\n---\nBody\n"
        )
        skill = Skill(skill_dir)
        assert skill.meta_info.mcp_server == "mat_sg"
        assert "mcp_server" not in skill.meta_info.extras

    def test_parse_frontmatter_no_extras(self, tmp_path):
        """SKILL.md without extra fields has empty extras."""
        skill_dir = tmp_path / "plain-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: plain-skill\ndescription: A plain skill\n---\nBody\n"
        )
        skill = Skill(skill_dir)
        assert skill.meta_info.extras == {}
        assert skill.meta_info.mcp_server is None
```

- [ ] **Step 6: Run updated test_skill_meta_extras**

Run: `python -m pytest tests/matmaster/tools/test_skill_meta_extras.py -v`
Expected: PASS

- [ ] **Step 7: Run full registry test suite**

Run: `python -m pytest tests/test_skill_registry.py -v`
Expected: PASS (SkillRegistry tests should be unaffected -- they don't access extras directly)

- [ ] **Step 8: Commit**

```bash
git add matmaster/skills/registry.py tests/test_skill_registry.py tests/matmaster/tools/test_skill_meta_extras.py
git commit -m "refactor: promote SkillMetaInfo extras to structured fields"
```

---

## Chunk 2: SkillTool Prompt Expansion

### Task 2: Rewrite SkillTool to prompt expansion model

**Files:**
- Modify: `matmaster/tools/skill_tool.py` (rewrite)
- Test: `tests/test_skill_tool.py` (rewrite)

- [ ] **Step 1: Write new test file for prompt expansion SkillTool**

Replace the entire content of `tests/test_skill_tool.py`:

```python
"""Tests for matmaster.tools.skill_tool — prompt expansion model."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(
    root: Path,
    name: str,
    *,
    mcp_server: str | None = None,
    depends_on: str | None = None,
    body: str = "Body",
) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: test\n"
    if mcp_server:
        fm += f"mcp_server: {mcp_server}\n"
    if depends_on:
        fm += f"depends_on: {depends_on}\n"
    fm += f"---\n\n{body}\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# Prompt expansion tests
# ---------------------------------------------------------------------------


class TestPromptExpansion:
    """Tests for the single-entry prompt expansion model."""

    async def test_returns_body_with_base_directory(self, tmp_path: Path) -> None:
        """execute returns skill body with base directory header."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(tmp_path, "calc", body="# Calculator\n\nDo math.")
        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)

        result = await tool.execute({"skill_name": "calc"})
        assert "Base directory for this skill:" in result
        assert str((tmp_path / "calc").resolve()) in result
        assert "# Calculator" in result
        assert "Do math." in result

    async def test_skill_dir_substitution(self, tmp_path: Path) -> None:
        """${SKILL_DIR} in body is replaced with the resolved skill path."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(
            tmp_path, "calc", body="Run: python ${SKILL_DIR}/scripts/run.py"
        )
        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)

        result = await tool.execute({"skill_name": "calc"})
        resolved = str((tmp_path / "calc").resolve())
        assert f"python {resolved}/scripts/run.py" in result
        assert "${SKILL_DIR}" not in result

    async def test_skill_not_found_returns_error(self, tmp_path: Path) -> None:
        """execute for nonexistent skill returns error string."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)

        result = await tool.execute({"skill_name": "nope"})
        assert "Error" in result
        assert "nope" in result


class TestOnSkillHit:
    """Tests for MCP lazy injection triggering."""

    async def test_triggers_on_skill_hit_for_mcp_server(
        self, tmp_path: Path
    ) -> None:
        """execute triggers on_skill_hit with the mcp_server value."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(tmp_path, "calc", mcp_server="calc-server")
        registry = SkillRegistry(tmp_path)
        callback = MagicMock()
        tool = SkillTool(registry, on_skill_hit=callback)

        result = await tool.execute({"skill_name": "calc"})
        callback.assert_called_once_with("calc-server")
        assert "Body" in result

    async def test_no_callback_when_no_mcp_server(self, tmp_path: Path) -> None:
        """execute does not trigger on_skill_hit for skills without mcp_server."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(tmp_path, "plain")
        registry = SkillRegistry(tmp_path)
        callback = MagicMock()
        tool = SkillTool(registry, on_skill_hit=callback)

        await tool.execute({"skill_name": "plain"})
        callback.assert_not_called()

    async def test_depends_on_cascades_to_multiple_servers(
        self, tmp_path: Path
    ) -> None:
        """execute cascades on_skill_hit for each dependency's mcp_server."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(tmp_path, "workflow", depends_on="tool-a,tool-b")
        _make_skill(tmp_path, "tool-a", mcp_server="server-a")
        _make_skill(tmp_path, "tool-b", mcp_server="server-b")
        registry = SkillRegistry(tmp_path)
        callback = MagicMock()
        tool = SkillTool(registry, on_skill_hit=callback)

        await tool.execute({"skill_name": "workflow"})
        calls = [c.args[0] for c in callback.call_args_list]
        assert "server-a" in calls
        assert "server-b" in calls

    async def test_no_callback_registered_is_fine(self, tmp_path: Path) -> None:
        """execute works when no on_skill_hit callback is provided."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(tmp_path, "calc", mcp_server="calc-server")
        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)  # No callback

        result = await tool.execute({"skill_name": "calc"})
        assert "Body" in result

    async def test_missing_dependency_does_not_crash(self, tmp_path: Path) -> None:
        """depends_on referencing a nonexistent skill is silently skipped."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        _make_skill(tmp_path, "wf", depends_on="nonexistent-dep")
        registry = SkillRegistry(tmp_path)
        callback = MagicMock()
        tool = SkillTool(registry, on_skill_hit=callback)

        result = await tool.execute({"skill_name": "wf"})
        callback.assert_not_called()
        assert "Base directory" in result


class TestToolProtocol:
    """Tests that SkillTool conforms to the matmaster Tool Protocol."""

    def test_satisfies_tool_protocol(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool
        from matmaster.tools.tool_registry import Tool

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)
        assert isinstance(tool, Tool)

    def test_name_property(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)
        assert tool.name == "use_skill"

    def test_json_schema_structure(self, tmp_path: Path) -> None:
        """json_schema has required skill_name, no action parameter."""
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)
        schema = tool.json_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "skill_name" in props
        assert "action" not in props
        assert schema["required"] == ["skill_name"]

    def test_metadata_defaults(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.skill_tool import SkillTool
        from matmaster.types.topology import ToolPlane

        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)
        assert tool.plane == ToolPlane.CONTROL_PLANE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_skill_tool.py -v`
Expected: FAIL -- SkillTool still has old constructor signature and 3-action schema.

- [ ] **Step 3: Rewrite SkillTool**

Replace the content of `matmaster/tools/skill_tool.py`:

```python
"""MatMaster-native SkillTool — prompt expansion model.

When invoked, returns the expanded SKILL.md content with base directory
header and ${SKILL_DIR} substitution. Triggers on_skill_hit callback for
lazy MCP schema injection.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, ClassVar

from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

logger = logging.getLogger(__name__)


class SkillTool:
    """Prompt-expansion skill tool.

    Satisfies the matmaster ``Tool`` Protocol (name/description/json_schema/execute).
    """

    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = ()
    capabilities: ClassVar[frozenset[str]] = frozenset({"skill.dispatch"})
    effect_level: ClassVar[str] = "local_mutation"
    fast_path_eligible: ClassVar[bool] = False
    max_result_chars: ClassVar[int] = 0
    plane: ClassVar[ToolPlane] = ToolPlane.CONTROL_PLANE
    state_mode: ClassVar[str] = "stateless"
    stop_mode: ClassVar[str] = "cancellable"
    exposed_to_model: ClassVar[bool] = True

    def __init__(
        self,
        skill_registry: Any,
        on_skill_hit: Callable[[str], None] | None = None,
    ) -> None:
        self._registry = skill_registry
        self._on_skill_hit = on_skill_hit

    # -- Tool Protocol properties -------------------------------------------

    @property
    def name(self) -> str:
        return "use_skill"

    @property
    def description(self) -> str:
        return (
            "Activate a skill by name. Returns the skill's full documentation "
            "and workflow instructions. Follow the returned instructions to "
            "complete the task."
        )

    def describe(self, ctx: ToolDescriptionContext | None = None) -> str:
        return self.description

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        return None

    @property
    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill name in kebab-case (e.g. deep-survey)",
                },
            },
            "required": ["skill_name"],
        }

    # -- Tool Protocol execute ----------------------------------------------

    async def execute(self, arguments: dict[str, Any]) -> str:
        return await asyncio.to_thread(self._execute_sync, arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str:
        return await self.execute(arguments)

    def _execute_sync(self, arguments: dict[str, Any]) -> str:
        try:
            skill_name = arguments["skill_name"]
            skill = self._registry.get_skill(skill_name)
            if skill is None:
                return f"Error: Skill '{skill_name}' not found"

            logger.info("Skill expansion: skill_name=%s", skill_name)

            # 1. Get body
            body = skill.get_full_info()

            # 2. Resolve skill directory
            skill_dir = str(skill.skill_path.resolve())

            # 3. ${SKILL_DIR} substitution
            body = body.replace("${SKILL_DIR}", skill_dir)

            # 4. Trigger on_skill_hit (MCP lazy injection)
            if skill.meta_info.mcp_server and self._on_skill_hit:
                self._on_skill_hit(skill.meta_info.mcp_server)

            for dep_name in skill.meta_info.depends_on:
                dep_skill = self._registry.get_skill(dep_name)
                if dep_skill and dep_skill.meta_info.mcp_server and self._on_skill_hit:
                    self._on_skill_hit(dep_skill.meta_info.mcp_server)

            # 5. Return expanded content with base directory header
            header = f"Base directory for this skill: {skill_dir}"
            return f"{header}\n\n{body}"

        except Exception as e:
            logger.error("Skill tool execution failed: %s", e, exc_info=True)
            return f"Error: {e}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_skill_tool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/skill_tool.py tests/test_skill_tool.py
git commit -m "refactor: rewrite SkillTool to prompt expansion model"
```

### Task 3: Update callback tests and Exp call site

**Files:**
- Modify: `tests/matmaster/tools/test_skill_tool_callback.py`
- Modify: `matmaster/core/exp.py:633-634`

- [ ] **Step 1: Update test_skill_tool_callback.py**

```python
from __future__ import annotations

from unittest.mock import MagicMock

from matmaster.skills.registry import SkillRegistry
from matmaster.tools.skill_tool import SkillTool


class TestSkillToolCallback:
    def _make_skill_dir(self, tmp_path, name, mcp_server=None):
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        lines = [f"---\nname: {name}\ndescription: Test skill\n"]
        if mcp_server:
            lines.append(f"mcp_server: {mcp_server}\n")
        lines.append("---\nSkill body\n")
        (skill_dir / "SKILL.md").write_text("".join(lines))
        return skill_dir

    async def test_callback_invoked_with_mcp_server(self, tmp_path):
        self._make_skill_dir(tmp_path, "test-skill", mcp_server="mat_sg")
        registry = SkillRegistry(tmp_path)
        hit_servers = []
        tool = SkillTool(registry, on_skill_hit=lambda s: hit_servers.append(s))

        await tool.execute({"skill_name": "test-skill"})
        assert hit_servers == ["mat_sg"]

    async def test_callback_not_invoked_without_mcp_server(self, tmp_path):
        self._make_skill_dir(tmp_path, "plain-skill")
        registry = SkillRegistry(tmp_path)
        hit_servers = []
        tool = SkillTool(registry, on_skill_hit=lambda s: hit_servers.append(s))

        await tool.execute({"skill_name": "plain-skill"})
        assert hit_servers == []

    async def test_no_callback_is_fine(self, tmp_path):
        self._make_skill_dir(tmp_path, "test-skill", mcp_server="mat_sg")
        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)  # No callback

        result = await tool.execute({"skill_name": "test-skill"})
        assert "Skill body" in result
```

- [ ] **Step 2: Update Exp._init_skill_tools call site**

In `matmaster/core/exp.py`, change line 633-635 from:

```python
        skill_tool = SkillTool(
            skill_registry, session=ctx.session, on_skill_hit=on_skill_hit
        )
```

to:

```python
        skill_tool = SkillTool(skill_registry, on_skill_hit=on_skill_hit)
```

- [ ] **Step 3: Run all skill-related tests**

Run: `python -m pytest tests/test_skill_registry.py tests/test_skill_tool.py tests/matmaster/tools/test_skill_meta_extras.py tests/matmaster/tools/test_skill_tool_callback.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/matmaster/tools/test_skill_tool_callback.py matmaster/core/exp.py
git commit -m "refactor: update SkillTool call site and callback tests"
```

### Task 4: Final verification

**Files:** (none -- verification only)

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --timeout=60`
Expected: PASS. No regressions in other tests that might import or reference SkillTool.

- [ ] **Step 2: Grep for stale references**

Check that no remaining code references the old 3-action interface:

```bash
grep -r "action.*get_info\|action.*get_reference\|action.*run_script" matmaster/ --include="*.py" | grep -v __pycache__
grep -r "_get_info\|_get_reference\|_run_script\|_build_command\|_find_project_root\|_get_co_template_hint" matmaster/ --include="*.py" | grep -v __pycache__
```

Expected: No matches in production code (test files and spec files are OK).

- [ ] **Step 3: Commit any final cleanups if needed**
