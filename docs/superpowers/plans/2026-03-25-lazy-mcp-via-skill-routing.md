# Lazy MCP Loading via Skill Routing — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace eager MCP connection (all 11 servers at startup) with on-demand loading through skill routing, reducing system prompt from ~5000 to ~800 tokens.

**Architecture:** Skills declare their MCP server dependency via SKILL.md frontmatter. At startup only a lightweight skill routing table is injected. When LLM triggers a skill via `use_skill(get_info)`, cached tool schemas are dynamically registered as LazyMCPTool placeholders. First actual tool execution lazily connects the MCP server.

**Tech Stack:** Python 3.10+, Pydantic v2, asyncio (event loop bridging), MCPToolManager (existing)

**Spec:** `docs/superpowers/specs/2026-03-25-lazy-mcp-via-skill-routing-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|---|---|
| `matmaster/tools/schema_cache.py` | ToolSchemaCache — load cached MCP tool schemas from local JSON |
| `matmaster/tools/lazy_mcp.py` | LazyMCPTool (Tool Protocol) + LazyMCPConnector + configure_mcp_manager |
| `matmaster/tools/cache_mcp_schemas.py` | CLI script to generate cached schemas via full MCPToolManager pipeline |
| `tests/matmaster/tools/test_schema_cache.py` | Tests for ToolSchemaCache |
| `tests/matmaster/tools/test_lazy_mcp.py` | Tests for LazyMCPTool + LazyMCPConnector |

### Modified Files

| File | Change |
|---|---|
| `evomaster/skills/base.py:20-93` | SkillMetaInfo: add `extras` field + update `_parse_meta_info` |
| `evomaster/agent/tools/skill.py:49-68` | SkillTool: add `on_skill_hit` callback param |
| `matmaster/config/exp.py` | Add ExpSkillsConfig model |
| `matmaster/core/exp.py:117-121,248-254` | Fill `_init_skill_tools` stub + pass skill_registry to ContextBuilder |
| `matmaster/exps/direct.toml` | Add `[skills]` section |
| `.gitignore` | Add `matmaster/cache/` |
| `tests/matmaster/config/test_exp.py` | Add ExpSkillsConfig tests |
| `tests/matmaster/core/test_exp.py` | Update build_runtime tests for skill injection |

---

## Chunk 1: Foundation — SkillMetaInfo extras + ExpSkillsConfig

### Task 1: SkillMetaInfo extras field

**Files:**
- Modify: `evomaster/skills/base.py:20-93`
- Test: `tests/matmaster/tools/test_schema_cache.py` (SkillMetaInfo tests added here for now)

- [ ] **Step 1: Write test for extras parsing**

```python
# tests/matmaster/tools/test_skill_meta_extras.py
from pathlib import Path
from evomaster.skills.base import SkillMetaInfo, BaseSkill, Skill


class TestSkillMetaInfoExtras:
    def test_extras_captures_unknown_fields(self):
        info = SkillMetaInfo(
            name="test-skill",
            description="A test skill",
            extras={"mcp_server": "mat_sg", "custom_flag": "true"},
        )
        assert info.extras["mcp_server"] == "mat_sg"
        assert info.extras["custom_flag"] == "true"

    def test_extras_defaults_empty(self):
        info = SkillMetaInfo(name="test", description="desc")
        assert info.extras == {}

    def test_parse_frontmatter_extras(self, tmp_path):
        """SKILL.md with mcp_server in frontmatter puts it in extras."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test\nmcp_server: mat_sg\n---\nBody\n"
        )
        skill = Skill(skill_dir)
        assert skill.meta_info.extras.get("mcp_server") == "mat_sg"

    def test_parse_frontmatter_no_extras(self, tmp_path):
        """SKILL.md without extra fields has empty extras."""
        skill_dir = tmp_path / "plain-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: plain-skill\ndescription: A plain skill\n---\nBody\n"
        )
        skill = Skill(skill_dir)
        assert skill.meta_info.extras == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_skill_meta_extras.py -v`
Expected: FAIL — SkillMetaInfo has no `extras` field

- [ ] **Step 3: Implement SkillMetaInfo extras + update _parse_meta_info**

In `evomaster/skills/base.py`:

1. Add `extras` field to SkillMetaInfo (line 20-29):

```python
class SkillMetaInfo(BaseModel):
    name: str = Field(description='技能名称')
    description: str = Field(description='技能描述，包含使用场景和触发条件')
    license: str | None = Field(default=None, description='许可证信息')
    extras: dict[str, Any] = Field(default_factory=dict, description='扩展字段')
```

Add `from typing import Any` to imports if not present.

2. Update `_parse_meta_info` (line 90-93) to collect extras:

```python
        known_keys = {'name', 'description', 'license'}
        extras = {k: v for k, v in frontmatter_data.items() if k not in known_keys}

        return SkillMetaInfo(
            name=frontmatter_data.get('name', self.skill_path.name),
            description=frontmatter_data.get('description', ''),
            license=frontmatter_data.get('license'),
            extras=extras,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_skill_meta_extras.py -v`
Expected: PASS

- [ ] **Step 5: Run existing skill tests to verify no regression**

Run: `uv run pytest tests/ -k "skill" -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add evomaster/skills/base.py tests/matmaster/tools/test_skill_meta_extras.py
git commit -m "feat: add extras field to SkillMetaInfo for frontmatter extensions"
```

---

### Task 2: ExpSkillsConfig

**Files:**
- Modify: `matmaster/config/exp.py`
- Modify: `matmaster/exps/direct.toml`
- Modify: `tests/matmaster/config/test_exp.py`

- [ ] **Step 1: Write test for ExpSkillsConfig**

```python
# Append to tests/matmaster/config/test_exp.py
from matmaster.config.exp import ExpSkillsConfig


class TestExpSkillsConfig:
    def test_defaults(self):
        cfg = ExpSkillsConfig()
        assert cfg.enabled is False
        assert cfg.skills_root == ""
        assert cfg.cache_dir == ""
        assert cfg.config_dir == ""
        assert cfg.mcp_config_file == ""

    def test_from_dict(self):
        cfg = ExpSkillsConfig(
            enabled=True,
            skills_root="playground/mat_master/skills",
            cache_dir="matmaster/cache",
            config_dir="configs/mat_master",
            mcp_config_file="mcp_config.json",
        )
        assert cfg.enabled is True
        assert cfg.skills_root == "playground/mat_master/skills"


class TestExpConfigWithSkills:
    def test_exp_config_includes_skills(self):
        data = {
            "name": "direct",
            "skills": {
                "enabled": True,
                "skills_root": "playground/mat_master/skills",
                "cache_dir": "matmaster/cache",
                "config_dir": "configs/mat_master",
                "mcp_config_file": "mcp_config.json",
            },
        }
        cfg = ExpConfig.model_validate(data)
        assert cfg.skills.enabled is True
        assert cfg.skills.cache_dir == "matmaster/cache"

    def test_exp_config_skills_defaults_when_absent(self):
        cfg = ExpConfig.model_validate({"name": "direct"})
        assert cfg.skills.enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/config/test_exp.py::TestExpSkillsConfig -v`
Expected: FAIL — ExpSkillsConfig not found

- [ ] **Step 3: Implement ExpSkillsConfig**

In `matmaster/config/exp.py`, add before `ExpConfig`:

```python
class ExpSkillsConfig(BaseModel):
    """Skill registration and lazy MCP loading settings."""

    enabled: bool = False
    skills_root: str = ""
    cache_dir: str = ""
    config_dir: str = ""
    mcp_config_file: str = ""
```

Add `skills` field to `ExpConfig`:

```python
    skills: ExpSkillsConfig = Field(default_factory=ExpSkillsConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/config/test_exp.py -v`
Expected: All PASS

- [ ] **Step 5: Update direct.toml**

Add to `matmaster/exps/direct.toml`:

```toml
[skills]
enabled = true
skills_root = "playground/mat_master/skills"
cache_dir = "matmaster/cache"
config_dir = "configs/mat_master"
mcp_config_file = "mcp_config.json"
```

- [ ] **Step 6: Run config loader test to verify toml parses**

Run: `uv run pytest tests/matmaster/core/test_config_loader.py -v`
Expected: PASS (extra="ignore" allows new fields)

- [ ] **Step 7: Add matmaster/cache/ to .gitignore**

Append `matmaster/cache/` to `.gitignore`.

- [ ] **Step 8: Commit**

```bash
git add matmaster/config/exp.py matmaster/exps/direct.toml tests/matmaster/config/test_exp.py .gitignore
git commit -m "feat: add ExpSkillsConfig for lazy MCP skill routing"
```

---

## Chunk 2: ToolSchemaCache + LazyMCPTool

### Task 3: ToolSchemaCache

**Files:**
- Create: `matmaster/tools/schema_cache.py`
- Create: `tests/matmaster/tools/test_schema_cache.py`

- [ ] **Step 1: Write tests for ToolSchemaCache**

```python
# tests/matmaster/tools/test_schema_cache.py
import json
from pathlib import Path

from matmaster.tools.schema_cache import ToolSchemaCache


class TestToolSchemaCache:
    def test_load_existing_cache(self, tmp_path):
        schemas = [
            {"name": "build_bulk", "description": "Build bulk", "input_schema": {}},
            {"name": "build_surface", "description": "Build surface", "input_schema": {}},
        ]
        (tmp_path / "mat_sg.json").write_text(json.dumps(schemas))
        cache = ToolSchemaCache(tmp_path)
        result = cache.load("mat_sg")
        assert result is not None
        assert len(result) == 2
        assert result[0]["name"] == "build_bulk"

    def test_load_missing_cache(self, tmp_path):
        cache = ToolSchemaCache(tmp_path)
        result = cache.load("nonexistent")
        assert result is None

    def test_load_empty_dir(self, tmp_path):
        cache = ToolSchemaCache(tmp_path)
        result = cache.load("any_server")
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_schema_cache.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement ToolSchemaCache**

```python
# matmaster/tools/schema_cache.py
"""ToolSchemaCache -- load pre-cached MCP tool schemas from local JSON files.

Cache files are generated by `matmaster.tools.cache_mcp_schemas` CLI tool
and stored in matmaster/cache/<server_name>.json. Each file contains the
filtered tool list (post tool_include_only, sync_tools, dedup).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ToolSchemaCache:
    """Read-only cache of MCP tool schemas. No TTL, no auto-sync."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)

    def load(self, server_name: str) -> list[dict] | None:
        """Load all tool schemas for a server. Returns None if cache miss."""
        path = self.cache_dir / f"{server_name}.json"
        if not path.exists():
            logger.debug("Cache miss for MCP server '%s' at %s", server_name, path)
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read cache for '%s': %s", server_name, e)
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_schema_cache.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/schema_cache.py tests/matmaster/tools/test_schema_cache.py
git commit -m "feat: add ToolSchemaCache for local MCP schema loading"
```

---

### Task 4: LazyMCPTool (Tool Protocol implementation)

**Files:**
- Create: `matmaster/tools/lazy_mcp.py`
- Create: `tests/matmaster/tools/test_lazy_mcp.py`

- [ ] **Step 1: Write tests for LazyMCPTool Protocol conformance + lazy execute**

```python
# tests/matmaster/tools/test_lazy_mcp.py
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from matmaster.tools.tool_registry import Tool
from matmaster.tools.lazy_mcp import LazyMCPTool


class FakeConnector:
    """Fake LazyMCPConnector for testing."""

    def __init__(self):
        self.session = MagicMock()
        self.connect_calls: list[tuple[str, str]] = []
        self._fake_tool = MagicMock()
        self._fake_tool.execute.return_value = ("result_text", {"success": True})

    def connect_and_get_tool(self, server_name: str, remote_tool_name: str):
        self.connect_calls.append((server_name, remote_tool_name))
        return self._fake_tool


class TestLazyMCPToolProtocol:
    def test_satisfies_tool_protocol(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="Build bulk structure",
            input_schema={"type": "object", "properties": {}},
            connector=connector,
        )
        assert isinstance(tool, Tool)

    def test_properties(self):
        connector = FakeConnector()
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="Build bulk structure",
            input_schema=schema,
            connector=connector,
        )
        assert tool.name == "mat_sg_build_bulk"
        assert tool.description == "Build bulk structure"
        assert tool.json_schema == schema


class TestLazyMCPToolExecution:
    def test_first_execute_connects(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="desc",
            input_schema={},
            connector=connector,
        )
        result = tool.execute({"param": "value"})
        assert len(connector.connect_calls) == 1
        assert connector.connect_calls[0] == ("mat_sg", "build_bulk")
        connector._fake_tool.execute.assert_called_once()

    def test_second_execute_reuses_connection(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="desc",
            input_schema={},
            connector=connector,
        )
        tool.execute({"a": "1"})
        tool.execute({"a": "2"})
        # Only connected once
        assert len(connector.connect_calls) == 1
        # But executed twice
        assert connector._fake_tool.execute.call_count == 2

    def test_execute_returns_string(self):
        connector = FakeConnector()
        connector._fake_tool.execute.return_value = ("hello world", {})
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
        )
        result = tool.execute({})
        assert result == "hello world"

    def test_execute_serializes_dict_observation(self):
        connector = FakeConnector()
        connector._fake_tool.execute.return_value = ({"key": "val"}, {})
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
        )
        result = tool.execute({})
        assert json.loads(result) == {"key": "val"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement LazyMCPTool**

```python
# matmaster/tools/lazy_mcp.py
"""Lazy MCP tool loading -- placeholder tools + on-demand connector.

LazyMCPTool satisfies the matmaster Tool Protocol using cached schemas.
On first execute(), it connects to the MCP server via LazyMCPConnector.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from evomaster.agent.tools.mcp.mcp import MCPTool
    from evomaster.agent.tools.mcp.mcp_manager import MCPToolManager


class LazyMCPTool:
    """Placeholder MCP tool -- holds cached schema, connects on first execute.

    Implements matmaster Tool Protocol (name, description, json_schema, execute).
    Can be registered directly into ToolRegistry without EvoToolAdapter.
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        remote_tool_name: str,
        description: str,
        input_schema: dict,
        connector: Any,
    ) -> None:
        self._name = tool_name
        self._description = description
        self._input_schema = input_schema
        self._server_name = server_name
        self._remote_tool_name = remote_tool_name
        self._connector = connector
        self._real_tool: MCPTool | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def json_schema(self) -> dict[str, Any]:
        return self._input_schema

    def execute(self, arguments: dict[str, Any]) -> str:
        if self._real_tool is None:
            self._real_tool = self._connector.connect_and_get_tool(
                self._server_name, self._remote_tool_name
            )
        args_json = json.dumps(arguments)
        observation, _info = self._real_tool.execute(
            self._connector.session, args_json
        )
        if isinstance(observation, str):
            return observation
        return json.dumps(observation, default=str)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/lazy_mcp.py tests/matmaster/tools/test_lazy_mcp.py
git commit -m "feat: add LazyMCPTool implementing Tool Protocol with lazy connection"
```

---

## Chunk 3: LazyMCPConnector + configure_mcp_manager

### Task 5: configure_mcp_manager extraction

**Files:**
- Modify: `matmaster/tools/lazy_mcp.py` (add configure_mcp_manager)

- [ ] **Step 1: Write test for configure_mcp_manager**

```python
# Append to tests/matmaster/tools/test_lazy_mcp.py
from matmaster.tools.lazy_mcp import configure_mcp_manager


class FakeMCPManager:
    """Minimal MCPToolManager mock for configure_mcp_manager tests."""

    def __init__(self):
        self.path_adaptor_servers: set = set()
        self.path_adaptor_factory = None
        self.sync_tools_by_server: dict = {}
        self.tool_include_only: dict = {}


class TestConfigureMCPManager:
    def test_sets_path_adaptor_servers(self):
        manager = FakeMCPManager()
        config = {
            "path_adaptor": "calculation",
            "calculation_servers": ["mat_sg", "mat_dpa"],
        }
        configure_mcp_manager(manager, config)
        assert manager.path_adaptor_servers == {"mat_sg", "mat_dpa"}

    def test_sets_sync_tools(self):
        manager = FakeMCPManager()
        config = {
            "calculation_executors": {
                "mat_sg": {"sync_tools": ["build_bulk_structure_by_wyckoff"]},
            }
        }
        configure_mcp_manager(manager, config)
        assert "build_bulk_structure_by_wyckoff" in manager.sync_tools_by_server["mat_sg"]

    def test_sets_tool_include_only(self):
        manager = FakeMCPManager()
        config = {
            "tool_include_only": {
                "mat_sn": ["web-search", "search-papers-enhanced"],
                "bad_entry": "not_a_list",
            }
        }
        configure_mcp_manager(manager, config)
        assert manager.tool_include_only["mat_sn"] == ["web-search", "search-papers-enhanced"]
        # Non-list values get empty list (block all tools), matching old playground behavior
        assert manager.tool_include_only["bad_entry"] == []

    def test_empty_config_noop(self):
        manager = FakeMCPManager()
        configure_mcp_manager(manager, {})
        assert manager.path_adaptor_servers == set()
        assert manager.sync_tools_by_server == {}
        assert manager.tool_include_only == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_lazy_mcp.py::TestConfigureMCPManager -v`
Expected: FAIL — configure_mcp_manager not found

- [ ] **Step 3: Implement configure_mcp_manager**

Add to `matmaster/tools/lazy_mcp.py`:

```python
def configure_mcp_manager(manager: Any, mcp_config: dict) -> None:
    """Inject MatMaster domain-specific config into MCPToolManager.

    Extracted from playground._setup_mcp_tools() for shared use by
    LazyMCPConnector and the old playground path.

    Injects: path_adaptor, sync_tools_by_server, tool_include_only.
    """
    if mcp_config.get("path_adaptor") == "calculation":
        calc_servers = mcp_config.get("calculation_servers")
        if calc_servers:
            manager.path_adaptor_servers = set(calc_servers)
        try:
            from evomaster.adaptors.calculation import get_calculation_path_adaptor

            manager.path_adaptor_factory = lambda: get_calculation_path_adaptor(
                mcp_config
            )
        except ImportError:
            logger.warning("evomaster.adaptors.calculation not available, skipping path_adaptor")

    executors = mcp_config.get("calculation_executors") or {}
    manager.sync_tools_by_server = {
        name: set(cfg.get("sync_tools") or [])
        for name, cfg in executors.items()
        if cfg.get("sync_tools")
    }

    include_only = mcp_config.get("tool_include_only")
    if include_only and isinstance(include_only, dict):
        # Match old playground behavior: non-list values become [] (block all tools)
        manager.tool_include_only = {
            k: list(v) if isinstance(v, (list, tuple)) else []
            for k, v in include_only.items()
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_lazy_mcp.py::TestConfigureMCPManager -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/lazy_mcp.py tests/matmaster/tools/test_lazy_mcp.py
git commit -m "feat: extract configure_mcp_manager for shared MCP config injection"
```

---

### Task 5.5: Refactor playground._setup_mcp_tools to use configure_mcp_manager

**Files:**
- Modify: `playground/mat_master/core/playground.py:505-553`

- [ ] **Step 1: Replace inline config injection with configure_mcp_manager call**

In `playground/mat_master/core/playground.py`, the `_setup_mcp_tools` method (around line 505-553) has inline path_adaptor, sync_tools, and tool_include_only setup. Replace with:

```python
        from matmaster.tools.lazy_mcp import configure_mcp_manager

        # ... (after manager = MCPToolManager(), progress_cb setup)

        configure_mcp_manager(manager, mcp_config)

        # Logging (keep existing info log about path_adaptor_servers)
        if manager.path_adaptor_servers:
            self.logger.info(
                'Path adaptor enabled for servers: %s', manager.path_adaptor_servers
            )
```

Remove the old inline code that sets `manager.path_adaptor_servers`, `manager.path_adaptor_factory`, `manager.sync_tools_by_server`, and `manager.tool_include_only` directly (lines ~510-553).

- [ ] **Step 2: Run existing playground tests to verify no regression**

Run: `uv run pytest tests/ -k "playground" -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add playground/mat_master/core/playground.py
git commit -m "refactor: use shared configure_mcp_manager in playground._setup_mcp_tools"
```

---

### Task 6: LazyMCPConnector

**Files:**
- Modify: `matmaster/tools/lazy_mcp.py` (add LazyMCPConnector)
- Modify: `tests/matmaster/tools/test_lazy_mcp.py`

Note: LazyMCPConnector involves async event loop + real MCP connections. Unit tests use mocks for the MCPToolManager; integration testing with real MCP servers is out of scope for this plan.

- [ ] **Step 1: Write tests for LazyMCPConnector**

```python
# Append to tests/matmaster/tools/test_lazy_mcp.py
from unittest.mock import AsyncMock, patch, MagicMock
from matmaster.tools.lazy_mcp import LazyMCPConnector


class TestLazyMCPConnector:
    def test_init_state(self):
        connector = LazyMCPConnector(
            mcp_server_config={"mat_sg": {"transport": "http", "url": "http://localhost"}},
            mcp_config={},
        )
        assert connector._manager is None
        assert connector._loop is None

    def test_cleanup_noop_when_not_connected(self):
        """Cleanup on a fresh connector should not raise."""
        connector = LazyMCPConnector(
            mcp_server_config={},
            mcp_config={},
        )
        connector.cleanup()  # Should not raise

    def test_missing_server_raises(self):
        connector = LazyMCPConnector(
            mcp_server_config={},
            mcp_config={},
        )
        # Force _ensure_manager to not actually create event loops
        connector._manager = FakeMCPManager()
        connector._manager.connections = {}
        with pytest.raises(ValueError, match="not in config"):
            connector.connect_and_get_tool("nonexistent", "some_tool")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_lazy_mcp.py::TestLazyMCPConnector -v`
Expected: FAIL — LazyMCPConnector not found

- [ ] **Step 3: Implement LazyMCPConnector**

Add to `matmaster/tools/lazy_mcp.py`:

```python
class LazyMCPConnector:
    """On-demand MCP server connector with background event loop.

    Creates a background asyncio event loop thread on first connect.
    Applies domain-specific config via configure_mcp_manager().
    """

    def __init__(
        self,
        mcp_server_config: dict,
        mcp_config: dict,
        session: Any = None,
    ) -> None:
        self._server_config = mcp_server_config
        self._mcp_config = mcp_config
        self._manager: MCPToolManager | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self.session = session

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and not self._loop.is_closed():
            return self._loop
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="lazy-mcp-loop"
        )
        self._loop_thread.start()
        return self._loop

    def _ensure_manager(self) -> Any:
        if self._manager is not None:
            return self._manager
        from evomaster.agent.tools.mcp.mcp_manager import MCPToolManager

        loop = self._ensure_loop()
        self._manager = MCPToolManager()
        self._manager.loop = loop
        configure_mcp_manager(self._manager, self._mcp_config)
        return self._manager

    def connect_and_get_tool(self, server_name: str, remote_tool_name: str) -> Any:
        manager = self._ensure_manager()

        if server_name not in manager.connections:
            server_cfg = self._server_config.get(server_name)
            if not server_cfg:
                raise ValueError(f"MCP server '{server_name}' not in config")
            fut = asyncio.run_coroutine_threadsafe(
                manager.add_server(name=server_name, **server_cfg),
                manager.loop,
            )
            fut.result(timeout=60)

        return manager.tools_by_server[server_name][
            f"{server_name}_{remote_tool_name}"
        ]

    def cleanup(self) -> None:
        if self._manager and self._loop and not self._loop.is_closed():
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._manager.cleanup(), self._loop
                )
                fut.result(timeout=30)
            except Exception as e:
                logger.warning("LazyMCPConnector cleanup error: %s", e)
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread:
            self._loop_thread.join(timeout=5)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/lazy_mcp.py tests/matmaster/tools/test_lazy_mcp.py
git commit -m "feat: add LazyMCPConnector with background event loop and domain config"
```

---

## Chunk 4: SkillTool callback + Exp integration

### Task 7: SkillTool on_skill_hit callback

**Files:**
- Modify: `evomaster/agent/tools/skill.py:49-68,110-113`

Note: Task 1's "Files" section references `test_schema_cache.py` but actual test file is `test_skill_meta_extras.py` — use the latter.

- [ ] **Step 1: Write test for on_skill_hit callback**

```python
# tests/matmaster/tools/test_skill_tool_callback.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from evomaster.skills.base import SkillRegistry
from evomaster.agent.tools.skill import SkillTool


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

    def test_callback_invoked_with_mcp_server(self, tmp_path):
        self._make_skill_dir(tmp_path, "test-skill", mcp_server="mat_sg")
        registry = SkillRegistry(tmp_path)
        hit_servers = []
        tool = SkillTool(registry, on_skill_hit=lambda s: hit_servers.append(s))

        session = MagicMock()
        import json
        args = json.dumps({"skill_name": "test-skill", "action": "get_info"})
        tool.execute(session, args)
        assert hit_servers == ["mat_sg"]

    def test_callback_not_invoked_without_mcp_server(self, tmp_path):
        self._make_skill_dir(tmp_path, "plain-skill")
        registry = SkillRegistry(tmp_path)
        hit_servers = []
        tool = SkillTool(registry, on_skill_hit=lambda s: hit_servers.append(s))

        session = MagicMock()
        import json
        args = json.dumps({"skill_name": "plain-skill", "action": "get_info"})
        tool.execute(session, args)
        assert hit_servers == []

    def test_no_callback_is_fine(self, tmp_path):
        self._make_skill_dir(tmp_path, "test-skill", mcp_server="mat_sg")
        registry = SkillRegistry(tmp_path)
        tool = SkillTool(registry)  # No callback

        session = MagicMock()
        import json
        args = json.dumps({"skill_name": "test-skill", "action": "get_info"})
        obs, info = tool.execute(session, args)
        assert "Skill body" in obs  # Still returns full_info
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_skill_tool_callback.py -v`
Expected: FAIL — SkillTool() takes 1 positional argument (no on_skill_hit)

- [ ] **Step 3: Implement on_skill_hit in SkillTool**

In `evomaster/agent/tools/skill.py`:

1. Update `__init__` (line 61-68):

```python
    def __init__(self, skill_registry: SkillRegistry,
                 on_skill_hit: Callable[[str], None] | None = None):
        super().__init__()
        self.skill_registry = skill_registry
        self._on_skill_hit = on_skill_hit
```

Add `from collections.abc import Callable` to imports.

2. Update `_get_info` (line 151-164), add callback trigger after `full_info = skill.get_full_info()`:

```python
        # Trigger callback for lazy MCP schema injection
        mcp_server = skill.meta_info.extras.get("mcp_server")
        if mcp_server and self._on_skill_hit:
            self._on_skill_hit(mcp_server)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_skill_tool_callback.py -v`
Expected: PASS

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `uv run pytest tests/ -k "skill" -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add evomaster/agent/tools/skill.py tests/matmaster/tools/test_skill_tool_callback.py
git commit -m "feat: add on_skill_hit callback to SkillTool for lazy MCP injection"
```

---

### Task 8: Exp._init_skill_tools + build_runtime integration

**Files:**
- Modify: `matmaster/core/exp.py:117-121,248-254`
- Modify: `tests/matmaster/core/test_exp.py`

- [ ] **Step 1: Write test for _init_skill_tools filling**

```python
# Append to tests/matmaster/core/test_exp.py
# or create tests/matmaster/core/test_exp_skills.py

from pathlib import Path
from unittest.mock import MagicMock
import json

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.tools.tool_registry import ToolRegistry


class TestExpInitSkillTools:
    def _make_skill_dir(self, tmp_path):
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nBody\n"
        )
        return tmp_path / "skills"

    def _make_cache(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        schemas = [{"name": "build_bulk", "description": "Build", "input_schema": {}}]
        (cache_dir / "mat_sg.json").write_text(json.dumps(schemas))
        return cache_dir

    def test_skill_tools_registered_when_enabled(self, tmp_path):
        skills_root = self._make_skill_dir(tmp_path)
        cache_dir = self._make_cache(tmp_path)

        cfg = ExpConfig.model_validate({
            "name": "test",
            "skills": {
                "enabled": True,
                "skills_root": str(skills_root),
                "cache_dir": str(cache_dir),
                "config_dir": str(tmp_path),
                "mcp_config_file": "mcp_config.json",
            },
        })
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock()
        ctx.session = MagicMock()

        exp._init_skill_tools(ctx, registry)

        # use_skill tool should be registered
        assert "use_skill" in registry

    def test_skill_tools_skipped_when_disabled(self, tmp_path):
        cfg = ExpConfig.model_validate({
            "name": "test",
            "skills": {"enabled": False},
        })
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock()

        exp._init_skill_tools(ctx, registry)

        assert "use_skill" not in registry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/core/test_exp.py::TestExpInitSkillTools -v`
(or the new file if created separately)
Expected: FAIL — _init_skill_tools is still a stub

- [ ] **Step 3: Implement _init_skill_tools**

In `matmaster/core/exp.py`, replace the stub `_init_skill_tools` (line 248-254):

```python
    def _init_skill_tools(
        self,
        ctx: PlaygroundContext,
        registry: ToolRegistry,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize skill tools with lazy MCP schema injection."""
        skills_cfg = self._config.skills
        if not skills_cfg.enabled:
            return

        from pathlib import Path

        from evomaster.agent.tools.skill import SkillTool
        from evomaster.skills.base import SkillRegistry

        from matmaster.tools.lazy_mcp import LazyMCPConnector
        from matmaster.tools.schema_cache import ToolSchemaCache

        skill_registry = SkillRegistry(Path(skills_cfg.skills_root))
        schema_cache = ToolSchemaCache(Path(skills_cfg.cache_dir))

        # MCP config: merge runtime config with static config
        mcp_config = config or {}
        mcp_config_file = mcp_config.get("config_file", skills_cfg.mcp_config_file)
        config_path = Path(mcp_config_file)
        if not config_path.is_absolute():
            config_path = Path(skills_cfg.config_dir) / config_path

        if mcp_config.get("path_adaptor") == "calculation":
            try:
                from evomaster.adaptors.calculation import resolve_mcp_config_path

                config_path = resolve_mcp_config_path(config_path)
            except ImportError:
                pass

        # Load server connection config from JSON
        # (spec's load_mcp_server_config is inlined here — no such function exists in codebase)
        import json

        server_config = {}
        if config_path.exists():
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                server_config = raw.get("mcpServers", {})
            except Exception as e:
                self.logger.warning("Failed to load MCP server config: %s", e)

        # __EVOMASTER_WORKSPACES__ placeholder replacement (matches playground.py:470-498)
        _PLACEHOLDER = "__EVOMASTER_WORKSPACES__"
        workspace_root = getattr(ctx, "workspace_path", None)
        if workspace_root and _PLACEHOLDER in json.dumps(server_config):

            def _deep_replace(obj, old: str, new: str):
                if isinstance(obj, str):
                    return obj.replace(old, new)
                if isinstance(obj, list):
                    return [_deep_replace(x, old, new) for x in obj]
                if isinstance(obj, dict):
                    return {k: _deep_replace(v, old, new) for k, v in obj.items()}
                return obj

            server_config = _deep_replace(server_config, _PLACEHOLDER, str(workspace_root))
            self.logger.info("Replaced %s -> %s in MCP config", _PLACEHOLDER, workspace_root)

        connector = LazyMCPConnector(
            mcp_server_config=server_config,
            mcp_config=mcp_config,
            session=ctx.session,
        )
        self._register_cleanup(connector.cleanup)

        def on_skill_hit(mcp_server: str) -> None:
            schemas = schema_cache.load(mcp_server)
            if not schemas:
                self.logger.warning(
                    "No cached schema for MCP server '%s', tools not injected",
                    mcp_server,
                )
                return
            from matmaster.tools.lazy_mcp import LazyMCPTool

            for tool_schema in schemas:
                original_name = tool_schema["name"]
                prefixed_name = f"{mcp_server}_{original_name}"
                if prefixed_name in registry:
                    continue
                lazy_tool = LazyMCPTool(
                    server_name=mcp_server,
                    tool_name=prefixed_name,
                    remote_tool_name=original_name,
                    description=tool_schema.get("description", ""),
                    input_schema=tool_schema.get("input_schema", {}),
                    connector=connector,
                )
                registry.register(lazy_tool, source="mcp")

        skill_tool = SkillTool(skill_registry, on_skill_hit=on_skill_hit)
        adapted = EvoToolAdapter(skill_tool, ctx.session)
        registry.register(adapted, source="skill")

        self._skill_registry = skill_registry
```

- [ ] **Step 4: Update build_runtime to pass skill_registry to ContextBuilder**

In `matmaster/core/exp.py`, update line 126:

```python
        system_prompt = builder.build(
            ctx, registry,
            mode=spec.mode,
            identity=identity,
            skill_registry=getattr(self, "_skill_registry", None),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_exp.py::TestExpInitSkillTools -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/matmaster/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add matmaster/core/exp.py tests/matmaster/core/test_exp.py
git commit -m "feat: implement _init_skill_tools with lazy MCP schema injection"
```

---

## Chunk 5: Cache generation script

### Task 9: cache_mcp_schemas CLI tool

**Files:**
- Create: `matmaster/tools/cache_mcp_schemas.py`

Note: This script requires real MCP server connections to function. It is designed to be run manually or in CI. No automated tests (would require live servers). Manual verification described below.

- [ ] **Step 1: Implement cache_mcp_schemas**

```python
# matmaster/tools/cache_mcp_schemas.py
"""CLI tool to generate cached MCP tool schemas.

Connects to all MCP servers defined in config, applies full filtering
(tool_include_only, sync_tools, dedup via MCPToolManager._build_tools),
and dumps the resulting tool schemas to matmaster/cache/<server>.json.

Usage:
    uv run python -m matmaster.tools.cache_mcp_schemas
    uv run python -m matmaster.tools.cache_mcp_schemas --config-dir configs/mat_master
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate MCP tool schema cache")
    parser.add_argument(
        "--config-dir",
        default="configs/mat_master",
        help="Directory containing config.yaml and mcp_config*.json",
    )
    parser.add_argument(
        "--output-dir",
        default="matmaster/cache",
        help="Output directory for cached schema JSON files",
    )
    return parser.parse_args()


async def generate_cache(config_dir: Path, output_dir: Path) -> None:
    import yaml

    from evomaster.agent.tools.mcp.mcp_manager import MCPToolManager
    from matmaster.tools.lazy_mcp import configure_mcp_manager

    config_yaml = config_dir / "config.yaml"
    if not config_yaml.exists():
        logger.error("config.yaml not found at %s", config_yaml)
        sys.exit(1)

    with open(config_yaml, encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    mcp_config = raw_config.get("mcp", {})
    if not mcp_config:
        logger.error("No 'mcp' section in config.yaml")
        sys.exit(1)

    mcp_config_file = mcp_config.get("config_file", "mcp_config.json")
    mcp_config_path = config_dir / mcp_config_file

    if mcp_config.get("path_adaptor") == "calculation":
        try:
            from evomaster.adaptors.calculation import resolve_mcp_config_path
            mcp_config_path = resolve_mcp_config_path(mcp_config_path)
        except ImportError:
            pass

    if not mcp_config_path.exists():
        logger.error("MCP config not found: %s", mcp_config_path)
        sys.exit(1)

    with open(mcp_config_path, encoding="utf-8") as f:
        server_defs = json.load(f).get("mcpServers", {})

    output_dir.mkdir(parents=True, exist_ok=True)

    manager = MCPToolManager()
    manager.loop = asyncio.get_running_loop()
    configure_mcp_manager(manager, mcp_config)

    for name, server_cfg in server_defs.items():
        try:
            logger.info("Connecting to %s (%s)...", name, server_cfg.get("transport"))
            await manager.add_server(name=name, **server_cfg)
        except Exception as e:
            logger.error("Failed to connect %s: %s", name, e)
            continue

    for server_name, tools in manager.tools_by_server.items():
        schemas = []
        for tool in tools.values():
            spec = tool.get_tool_spec()
            schemas.append({
                "name": tool._remote_tool_name or spec.function.name,
                "description": spec.function.description,
                "input_schema": spec.function.parameters,
            })
        out_path = output_dir / f"{server_name}.json"
        out_path.write_text(json.dumps(schemas, indent=2, ensure_ascii=False))
        logger.info("Wrote %d tools to %s", len(schemas), out_path)

    await manager.cleanup()
    logger.info("Done. Cache written to %s", output_dir)


def main() -> None:
    args = parse_args()
    asyncio.run(generate_cache(Path(args.config_dir), Path(args.output_dir)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify script is importable**

Run: `uv run python -c "from matmaster.tools.cache_mcp_schemas import main; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add matmaster/tools/cache_mcp_schemas.py
git commit -m "feat: add CLI tool for generating MCP tool schema cache"
```

---

## Chunk 6: Skill split + SKILL.md frontmatter updates

### Task 10: Audit and update SKILL.md files

**Files:**
- Modify: `playground/mat_master/skills/*/SKILL.md`
- Possibly create new skill directories for splits

This task requires manual review of each SKILL.md to determine:
1. Which skills need `mcp_server` frontmatter added
2. Which skills need splitting (currently span multiple MCP servers)
3. Which skills are MCP-independent (no changes needed)

- [ ] **Step 1: Audit all existing SKILL.md files**

Run through each skill directory and check which MCP tools its full_info references.
Document the mapping in a table.

- [ ] **Step 2: Add mcp_server frontmatter to single-server skills**

For skills that map to exactly one MCP server, add `mcp_server: <name>` to frontmatter.

- [ ] **Step 3: Split multi-server skills**

For skills like structure-manager that span multiple servers:
1. Create new skill directories (e.g., `structure-generator/`, `structure-database/`)
2. Write new SKILL.md with focused full_info for each server
3. Keep or archive the original skill directory

- [ ] **Step 4: Verify all skill directories load**

Run: `uv run python -c "
from pathlib import Path
from evomaster.skills.base import SkillRegistry
reg = SkillRegistry(Path('playground/mat_master/skills'))
for s in reg.get_all_skills():
    mcp = s.meta_info.extras.get('mcp_server', '-')
    print(f'{s.meta_info.name}: mcp_server={mcp}')
"`
Expected: Each skill prints with its mcp_server (or `-` for non-MCP skills)

- [ ] **Step 5: Commit**

```bash
git add playground/mat_master/skills/
git commit -m "feat: add mcp_server frontmatter and split multi-server skills"
```

---

## Chunk 7: Final integration test

### Task 11: End-to-end integration test

**Files:**
- Create: `tests/matmaster/integration/test_lazy_mcp_integration.py`

- [ ] **Step 1: Write integration test**

Tests the full flow: Exp.build_runtime with skills enabled → use_skill triggers schema injection → LazyMCPTool appears in registry. Does NOT require real MCP connections.

```python
# tests/matmaster/integration/test_lazy_mcp_integration.py
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.tools.tool_registry import ToolRegistry


class TestLazyMCPIntegration:
    def _setup_env(self, tmp_path):
        """Create skill dir + cache dir + mcp_config.json."""
        # Skill
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nUse mat_sg tools.\n"
        )

        # Cache
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        schemas = [
            {"name": "build_bulk", "description": "Build bulk", "input_schema": {"type": "object"}},
        ]
        (cache_dir / "mat_sg.json").write_text(json.dumps(schemas))

        # MCP config (not used for real connections in this test)
        (tmp_path / "mcp_config.json").write_text(json.dumps({"mcpServers": {}}))

        return tmp_path

    def test_full_flow_skill_triggers_schema_injection(self, tmp_path):
        env = self._setup_env(tmp_path)

        cfg = ExpConfig.model_validate({
            "name": "test",
            "skills": {
                "enabled": True,
                "skills_root": str(env / "skills"),
                "cache_dir": str(env / "cache"),
                "config_dir": str(env),
                "mcp_config_file": "mcp_config.json",
            },
        })
        exp = Exp(cfg)
        registry = ToolRegistry()

        ctx = MagicMock()
        ctx.session = MagicMock()

        # Initialize skill tools
        exp._init_skill_tools(ctx, registry)

        # use_skill should be registered
        assert "use_skill" in registry

        # Before skill trigger: no MCP tools
        assert "mat_sg_build_bulk" not in registry

        # Simulate skill trigger via use_skill tool
        args = json.dumps({"skill_name": "test-skill", "action": "get_info"})
        # Find the adapted skill tool and call its inner execute
        skill_tool_adapter = registry._tools["use_skill"]
        # EvoToolAdapter wraps execute differently, call via registry
        result = registry.execute("use_skill", {"skill_name": "test-skill", "action": "get_info"})

        # Verify use_skill returned successfully (no Error: prefix)
        assert not result.startswith("Error:"), f"use_skill failed: {result}"

        # After skill trigger: mat_sg tools should be injected
        assert "mat_sg_build_bulk" in registry

        # Verify it's a LazyMCPTool
        from matmaster.tools.lazy_mcp import LazyMCPTool
        lazy = registry._tools["mat_sg_build_bulk"]
        assert isinstance(lazy, LazyMCPTool)
        assert lazy.name == "mat_sg_build_bulk"
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/matmaster/integration/test_lazy_mcp_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/matmaster/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/matmaster/integration/test_lazy_mcp_integration.py
git commit -m "test: add end-to-end integration test for lazy MCP loading"
```
