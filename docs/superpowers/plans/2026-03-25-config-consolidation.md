# Config Consolidation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up matmaster_config/ by eliminating duplicate LLM config, removing dead sections, extracting MCP into mcp.yaml, and fixing the runtime MCP config injection bug.

**Architecture:** Four workstreams: (1) config file restructuring, (2) path routing fix in PlaygroundManager, (3) Exp MCP self-load bug fix, (4) cache_mcp_schemas CLI update. **Execution order:** Chunk 1 must complete first (creates `mcp.yaml`), then Chunks 2-4 can proceed in any order.

**Tech Stack:** Python 3.10+, Pydantic v2, YAML, TOML, pytest

**Spec:** `docs/superpowers/specs/2026-03-25-config-consolidation-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Rewrite | `matmaster_config/config.yaml` | Slim down to ~45 lines (agents, env stub, session, playground, workspace) |
| Create | `matmaster_config/mcp.yaml` | Extracted MCP section (~240 lines) |
| Modify | `matmaster/config/exp.py:24-31` | Add `mcp_runtime_file` field to `ExpSkillsConfig` |
| Modify | `matmaster/exps/direct.toml:14-19` | Fix `config_dir` path |
| Modify | `matmaster/core/exp.py:117-124,297-335` | MCP self-load + rename param |
| Modify | `matmaster/core/playground.py:365-415` | Path routing via `_config_dir_for()` |
| Modify | `matmaster/tools/cache_mcp_schemas.py:24-53` | Read from mcp.yaml, update default |
| Modify | `src/services/agent_run_service.py:125-146` | Path routing in `_validate_llm_configs()` |
| Modify | `tests/matmaster/config/test_exp.py:75-82` | Cover `mcp_runtime_file` default |
| Create | `tests/matmaster/config/test_config_consolidation.py` | Validate cleaned config.yaml loads via EvoMasterConfig |
| Modify | `tests/matmaster/integration/test_lazy_mcp_integration.py` | Cover Exp self-load mcp.yaml path |

---

## Chunk 1: Config File Restructuring

### Task 1: Create mcp.yaml from config.yaml mcp section

**Files:**
- Create: `matmaster_config/mcp.yaml`

- [ ] **Step 1: Extract mcp section from config.yaml into mcp.yaml**

Copy lines 286-533 from `matmaster_config/config.yaml` (the `mcp:` block) into a new file `matmaster_config/mcp.yaml`. Remove the `mcp:` wrapper (content becomes top-level). Delete `config_file: "mcp_config.json"` and `enabled: true` lines.

The file should start with:
```yaml
# MCP service config
# Endpoint addresses defined in mcp_config.{env}.json

path_adaptor: "calculation"
calculation_servers:
```

And contain `calculation_servers`, `tool_include_only`, and `calculation_executors` sections unchanged.

- [ ] **Step 2: Verify mcp.yaml parses correctly**

Run: `uv run python -c "import yaml; d = yaml.safe_load(open('matmaster_config/mcp.yaml')); print('keys:', list(d.keys())); assert 'path_adaptor' in d; assert 'calculation_executors' in d; print('OK')"`

Expected: `keys: ['path_adaptor', 'calculation_servers', 'tool_include_only', 'calculation_executors']` then `OK`

- [ ] **Step 3: Commit**

```bash
git add matmaster_config/mcp.yaml
git commit -m "config: extract mcp section into matmaster_config/mcp.yaml"
```

### Task 2: Slim down config.yaml

**Files:**
- Modify: `matmaster_config/config.yaml`

- [ ] **Step 1: Write the validation test**

Create `tests/matmaster/config/test_config_consolidation.py`:

```python
"""Validate cleaned config.yaml loads through EvoMasterConfig without errors."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def cleaned_config():
    config_path = Path("matmaster_config/config.yaml")
    if not config_path.exists():
        pytest.skip("matmaster_config/config.yaml not found")
    with open(config_path) as f:
        return yaml.safe_load(f)


class TestCleanedConfigYaml:
    def test_loads_via_evomaster_config(self, cleaned_config):
        """EvoMasterConfig(**config_dict) must not raise."""
        from evomaster.config import EvoMasterConfig

        cfg = EvoMasterConfig(**cleaned_config)
        assert cfg.env is not None  # env stub loaded

    def test_has_agents_general_llm(self, cleaned_config):
        assert cleaned_config["agents"]["general"]["llm"] == "litellm"

    def test_no_dead_sections(self, cleaned_config):
        dead = {"llm", "mat_master", "llm_output", "logging", "skills",
                "project_root", "results_dir", "debug", "mcp"}
        present_dead = dead & set(cleaned_config.keys())
        assert present_dead == set(), f"Dead sections still present: {present_dead}"

    def test_env_stub_present(self, cleaned_config):
        assert "env" in cleaned_config
        assert "cluster" in cleaned_config["env"]
        assert "docker" in cleaned_config["env"]
        assert "scheduler" in cleaned_config["env"]

    def test_session_present(self, cleaned_config):
        assert "session" in cleaned_config
        assert cleaned_config["session"]["type"] == "local"

    def test_playground_present(self, cleaned_config):
        assert "playground" in cleaned_config
        assert "archival" in cleaned_config["playground"]

    def test_workspace_present(self, cleaned_config):
        assert "workspace" in cleaned_config
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/config/test_config_consolidation.py -v`

Expected: FAIL on `test_no_dead_sections` (current config.yaml still has dead sections)

- [ ] **Step 3: Replace config.yaml with cleaned version**

Rewrite `matmaster_config/config.yaml` to contain only:

```yaml
# MatMaster main config
# LLM: llm_config.yaml | MCP: mcp.yaml | MCP endpoints: mcp_config.{env}.json

agents:
  general:
    llm: "litellm"

# env: retained as stub -- EvoMasterConfig.env requires cluster/docker/scheduler
# sub-fields with no defaults. Removing causes Pydantic validation error.
env:
  cluster:
    debug_pool:
      type: "cpu"
      max_concurrent: 1
    train_pool:
      type: "cpu"
      max_concurrent: 1
  docker:
    base_image: "python:3.11-slim"
    registry: "docker.io"
    pull_policy: "if_not_present"
  scheduler:
    type: "local"
    queue_timeout: 300
    retry_failed: false
    max_retries: 1

session:
  type: "local"
  local:
    working_dir: "./playground/mat_master/workspace"
    timeout: 60
    gpu_devices: null
    cpu_devices: null
    symlinks: {}
  docker:
    image: "evomaster/base:latest"
    container_name: null
    use_existing_container: null
    working_dir: "/workspace"
    memory_limit: "64g"
    cpu_limit: 16.0
    gpu_devices: "0"
    network_mode: "host"
    volumes: {"./playground/mat_master/workspace": "/workspace"}
    env_vars: {}
    auto_remove: false
    timeout: 300

playground:
  cache_dir: ".cache/matmaster"
  archival:
    enabled: true
    oss_bucket: "${OSS_BUCKET_NAME}"
    oss_prefix: "matmaster_evo/chat_workspace"
    credential_ref: "env:aliyun-oss"

workspace: "./workspace"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/config/test_config_consolidation.py -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster_config/config.yaml tests/matmaster/config/test_config_consolidation.py
git commit -m "config: remove dead sections from config.yaml, add validation test"
```

---

## Chunk 2: Path Routing and TOML Fix

### Task 3: Add `_config_dir_for()` helper to PlaygroundManager

**Files:**
- Modify: `matmaster/core/playground.py:340-415`

- [ ] **Step 1: Write the test**

Add to `tests/matmaster/config/test_config_consolidation.py`:

```python
class TestConfigDirRouting:
    def test_mat_master_routes_to_matmaster_config(self, tmp_path):
        from matmaster.core.playground import PlaygroundManager
        mgr = PlaygroundManager(tmp_path)
        assert mgr._config_dir_for("mat_master") == tmp_path / "matmaster_config"

    def test_minimal_routes_to_configs(self, tmp_path):
        from matmaster.core.playground import PlaygroundManager
        mgr = PlaygroundManager(tmp_path)
        assert mgr._config_dir_for("minimal") == tmp_path / "configs" / "minimal"

    def test_unknown_routes_to_configs(self, tmp_path):
        from matmaster.core.playground import PlaygroundManager
        mgr = PlaygroundManager(tmp_path)
        assert mgr._config_dir_for("other") == tmp_path / "configs" / "other"

    def test_get_or_create_uses_matmaster_config_dir(self, tmp_path):
        """Verify get_or_create() actually uses _config_dir_for(), not hardcoded path."""
        from matmaster.core.playground import PlaygroundManager
        from unittest.mock import patch

        mgr = PlaygroundManager(tmp_path)
        # Create matmaster_config/config.yaml so Playground.__init__ can load it
        cfg_dir = tmp_path / "matmaster_config"
        cfg_dir.mkdir()
        # Patch Playground to avoid full init, just verify the path passed
        with patch("matmaster.core.playground.Playground") as mock_pg:
            mock_pg.return_value = mock_pg
            mgr.get_or_create("test-session", "mat_master")
            call_args = mock_pg.call_args
            config_path = call_args.kwargs.get("config_path") or call_args[0][0]
            assert "matmaster_config" in str(config_path)
            assert "configs/mat_master" not in str(config_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/config/test_config_consolidation.py::TestConfigDirRouting -v`

Expected: FAIL (method does not exist)

- [ ] **Step 3: Implement `_config_dir_for()` and update callers**

In `matmaster/core/playground.py`, add the helper method to `PlaygroundManager` class (before `validate_startup`):

```python
def _config_dir_for(self, playground_type: str) -> Path:
    """Return config directory for a playground type.

    mat_master uses the flat matmaster_config/ layout;
    other types (minimal, etc.) retain configs/{type}/.
    """
    if playground_type == "mat_master":
        return self._project_root / "matmaster_config"
    return self._project_root / "configs" / playground_type
```

Update `validate_startup()` (line ~369):
```python
# Before:
config_path = self._project_root / "configs" / pg_type / "config.yaml"
# After:
config_path = self._config_dir_for(pg_type) / "config.yaml"
```

Update `get_or_create()` (line ~410-411):
```python
# Before:
config_path = (
    self._project_root / "configs" / playground_type / "config.yaml"
)
# After:
config_path = self._config_dir_for(playground_type) / "config.yaml"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/matmaster/config/test_config_consolidation.py::TestConfigDirRouting -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/playground.py tests/matmaster/config/test_config_consolidation.py
git commit -m "feat: add _config_dir_for() routing in PlaygroundManager"
```

### Task 4: Fix `_validate_llm_configs()` path in agent_run_service.py

**Files:**
- Modify: `src/services/agent_run_service.py:116-146`

- [ ] **Step 1: Update paths to use matmaster_config for mat_master**

In `_validate_llm_configs()`, replace the loop body:

```python
# Before:
for pg_type in ("mat_master", "minimal"):
    llm_config_path = _project_root / "configs" / pg_type / "llm_config.yaml"
    # ...
    config_path = _project_root / "configs" / pg_type / "config.yaml"

# After:
for pg_type in ("mat_master", "minimal"):
    if pg_type == "mat_master":
        cfg_dir = _project_root / "matmaster_config"
    else:
        cfg_dir = _project_root / "configs" / pg_type
    llm_config_path = cfg_dir / "llm_config.yaml"
    # ...
    config_path = cfg_dir / "config.yaml"
```

- [ ] **Step 2: Verify import still works**

Run: `uv run python -c "from src.services.agent_run_service import AgentRunService; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/services/agent_run_service.py
git commit -m "fix: route mat_master config path to matmaster_config/ in agent_run_service"
```

### Task 5: Fix direct.toml config_dir

**Files:**
- Modify: `matmaster/exps/direct.toml:18`
- Modify: `tests/matmaster/config/test_exp.py`

- [ ] **Step 1: Update direct.toml**

Change line 18:
```toml
# Before:
config_dir = "configs/mat_master"
# After:
config_dir = "matmaster_config"
```

- [ ] **Step 2: Verify ExpConfig loads correctly**

Run: `uv run python -c "from matmaster.config.loader import load_exp_config; c = load_exp_config('direct'); print(c.skills.config_dir); assert c.skills.config_dir == 'matmaster_config'"`

Expected: `matmaster_config`

- [ ] **Step 3: Commit**

```bash
git add matmaster/exps/direct.toml
git commit -m "fix: update direct.toml config_dir to matmaster_config"
```

---

## Chunk 3: Exp MCP Self-Load Bug Fix

### Task 6: Add `mcp_runtime_file` to ExpSkillsConfig

**Files:**
- Modify: `matmaster/config/exp.py:24-31`
- Modify: `tests/matmaster/config/test_exp.py:75-82`

- [ ] **Step 1: Update the test**

In `tests/matmaster/config/test_exp.py`, add assertion to `TestExpSkillsConfig.test_defaults()`:

```python
class TestExpSkillsConfig:
    def test_defaults(self):
        cfg = ExpSkillsConfig()
        assert cfg.enabled is False
        assert cfg.skills_root == ""
        assert cfg.cache_dir == ""
        assert cfg.config_dir == ""
        assert cfg.mcp_config_file == ""
        assert cfg.mcp_runtime_file == "mcp.yaml"  # New
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/config/test_exp.py::TestExpSkillsConfig::test_defaults -v`

Expected: FAIL (`ExpSkillsConfig` has no `mcp_runtime_file` attribute)

- [ ] **Step 3: Add field to ExpSkillsConfig**

In `matmaster/config/exp.py`, add to `ExpSkillsConfig`:

```python
class ExpSkillsConfig(BaseModel):
    """Skill registration and lazy MCP loading settings."""

    enabled: bool = False
    skills_root: str = ""
    cache_dir: str = ""
    config_dir: str = ""
    mcp_config_file: str = ""
    mcp_runtime_file: str = "mcp.yaml"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/config/test_exp.py::TestExpSkillsConfig -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/config/exp.py tests/matmaster/config/test_exp.py
git commit -m "feat: add mcp_runtime_file field to ExpSkillsConfig"
```

### Task 7: Fix Exp.build_runtime() and _init_skill_tools() for MCP self-load

**Files:**
- Modify: `matmaster/core/exp.py:117-124,297-335`
- Modify: `tests/matmaster/integration/test_lazy_mcp_integration.py`
- Modify: `tests/matmaster/core/test_exp_skills.py`

- [ ] **Step 1: Write new integration test for MCP self-load**

Add to `tests/matmaster/integration/test_lazy_mcp_integration.py`:

```python
class TestExpMCPSelfLoad:
    """Verify Exp._init_skill_tools() self-loads mcp.yaml when no runtime config injected."""

    def test_self_loads_mcp_yaml(self, tmp_path):
        """mcp.yaml is loaded from config_dir and passed to LazyMCPConnector."""
        import yaml

        # Create minimal mcp.yaml
        (tmp_path / "mcp.yaml").write_text(yaml.dump({
            "path_adaptor": "calculation",
            "calculation_servers": ["mat_sg"],
        }))
        (tmp_path / "mcp_config.json").write_text('{"mcpServers": {}}')

        # Create skill dir (required by SkillRegistry)
        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nBody\n"
        )

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        schemas = [{"name": "build_bulk", "description": "Build", "input_schema": {}}]
        import json
        (cache_dir / "mat_sg.json").write_text(json.dumps(schemas))

        from matmaster.config.exp import ExpConfig, ExpSkillsConfig
        cfg = ExpConfig(
            skills=ExpSkillsConfig(
                enabled=True,
                skills_root=str(tmp_path / "skills"),
                cache_dir=str(cache_dir),
                config_dir=str(tmp_path),
                mcp_config_file="mcp_config.json",
                mcp_runtime_file="mcp.yaml",
            )
        )

        from matmaster.core.exp import Exp
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()

        # Run _init_skill_tools -- should self-load mcp.yaml
        exp._init_skill_tools(ctx, registry)

        # use_skill registered means the full path worked
        assert "use_skill" in registry

        # Trigger skill to verify lazy tools get injected
        result = registry.execute("use_skill", {"skill_name": "test-skill", "action": "get_info"})
        assert not result.startswith("Error:"), f"use_skill failed: {result}"
        assert "mat_sg_build_bulk" in registry

    def test_raises_when_mcp_yaml_missing(self, tmp_path):
        """When mcp.yaml does not exist, FileNotFoundError is raised."""
        from matmaster.config.exp import ExpConfig, ExpSkillsConfig
        cfg = ExpConfig(
            skills=ExpSkillsConfig(
                enabled=True,
                skills_root=str(tmp_path / "skills"),
                cache_dir=str(tmp_path / "cache"),
                config_dir=str(tmp_path),
                mcp_config_file="mcp_config.json",
                mcp_runtime_file="mcp.yaml",
            )
        )

        from matmaster.core.exp import Exp
        exp = Exp(cfg)

        import pytest as _pytest
        with _pytest.raises(FileNotFoundError, match="MCP runtime config not found"):
            exp._init_skill_tools(MagicMock(), MagicMock())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/integration/test_lazy_mcp_integration.py::TestExpMCPSelfLoad -v`

Expected: FAIL (current code doesn't self-load)

- [ ] **Step 3: Modify build_runtime() caller**

In `matmaster/core/exp.py`, change lines 117-124:

```python
# Before:
        # 2. Skills/MCP: runtime-injected (must be before system prompt)
        if skills:
            self._init_skill_tools(ctx, registry, skills)
        elif self._config.skills.enabled:
            # Lazy MCP: config-driven skill loading (no runtime param needed)
            self._init_skill_tools(ctx, registry)
        if mcp:
            self._init_mcp_tools(ctx, registry, mcp)

# After:
        # 2. Skills/MCP: runtime-injected (must be before system prompt)
        if skills or self._config.skills.enabled:
            self._init_skill_tools(ctx, registry, skills_config=skills)
        if mcp:
            self._init_mcp_tools(ctx, registry, mcp)
```

- [ ] **Step 4: Modify _init_skill_tools() for MCP self-load**

In `matmaster/core/exp.py`, change `_init_skill_tools` method signature and add self-load logic. Rename parameter `config` to `skills_config`:

```python
    def _init_skill_tools(
        self,
        ctx: PlaygroundContext,
        registry: ToolRegistry,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize skill tools with lazy MCP schema injection."""
        skills_cfg = self._config.skills
        if not skills_cfg.enabled:
            return

        import json as _json
        from pathlib import Path

        from evomaster.agent.tools.skill import SkillTool
        from evomaster.skills.base import SkillRegistry

        from matmaster.tools.lazy_mcp import LazyMCPConnector, LazyMCPTool
        from matmaster.tools.schema_cache import ToolSchemaCache

        skill_registry = SkillRegistry(Path(skills_cfg.skills_root))
        schema_cache = ToolSchemaCache(Path(skills_cfg.cache_dir))

        # MCP runtime config: ALWAYS self-load from config_dir.
        # Independent of skills_config -- MCP runtime config (path_adaptor,
        # calculation_executors) is a separate concern from skill routing.
        from matmaster.config.loader import _load_raw
        mcp_runtime_path = Path(skills_cfg.config_dir) / skills_cfg.mcp_runtime_file
        if mcp_runtime_path.exists():
            mcp_config = _load_raw(mcp_runtime_path)
        else:
            raise FileNotFoundError(
                f"MCP runtime config not found: {mcp_runtime_path}. "
                f"Required when skills.enabled=true."
            )

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
        server_config: dict = {}
        if config_path.exists():
            try:
                raw = _json.loads(config_path.read_text(encoding="utf-8"))
                server_config = raw.get("mcpServers", {})
            except Exception as e:
                self.logger.warning("Failed to load MCP server config: %s", e)

        connector = LazyMCPConnector(
            mcp_server_config=server_config,
            mcp_config=mcp_config,
            session=ctx.session,
        )
        self._register_cleanup(connector.cleanup)

        # ... rest of method unchanged (on_skill_hit callback, SkillTool registration)
```

The rest of the method (on_skill_hit callback, SkillTool creation, registry.register) stays exactly as-is.

- [ ] **Step 5: Fix ALL existing tests that call `_init_skill_tools()` to provide mcp.yaml**

After the `FileNotFoundError` change, every test that calls `_init_skill_tools()` with `enabled=True` needs a `mcp.yaml` in its `config_dir`. Three files are affected:

**File 1: `tests/matmaster/integration/test_lazy_mcp_integration.py`**

Update `_setup_env()` to create `mcp.yaml`. Add after line 38 (`mcp_config.json` creation):

```python
        # MCP runtime config (required by _init_skill_tools self-load)
        import yaml as _yaml
        (tmp_path / "mcp.yaml").write_text(_yaml.dump({
            "path_adaptor": "calculation",
            "calculation_servers": ["mat_sg"],
        }))
```

Update all `ExpConfig.model_validate()` calls in this file to include `"mcp_runtime_file": "mcp.yaml"` in the skills dict. This affects:
- `test_full_flow_skill_triggers_schema_injection` (line ~47)
- `test_multiple_skills_same_server_no_duplicate` (line ~89)
- `test_no_cache_warns_but_doesnt_crash` (line ~134) -- also add `mcp.yaml` creation before `ExpConfig`:

```python
        import yaml as _yaml
        (tmp_path / "mcp.yaml").write_text(_yaml.dump({
            "path_adaptor": "calculation",
            "calculation_servers": [],
        }))
```

**File 2: `tests/matmaster/core/test_exp_skills.py`**

Add a helper function after `_make_cache()`:

```python
def _make_mcp_yaml(tmp_path: Path) -> None:
    import yaml
    (tmp_path / "mcp.yaml").write_text(yaml.dump({
        "path_adaptor": "calculation",
        "calculation_servers": ["mat_sg"],
    }))
```

Call `_make_mcp_yaml(tmp_path)` at the start of:
- `test_skill_tools_registered_when_enabled` (line ~33)
- `test_skill_trigger_injects_lazy_tools` (line ~70)

Also add `"mcp_runtime_file": "mcp.yaml"` to their `ExpConfig.model_validate()` skills dicts.

`test_skill_tools_skipped_when_disabled` does NOT need changes (`enabled=False` returns early before self-load).

**File 3: No other test files call `_init_skill_tools()` directly.**

- [ ] **Step 6: Run integration tests**

Run: `uv run pytest tests/matmaster/integration/test_lazy_mcp_integration.py -v`

Expected: All PASS (existing + new tests)

- [ ] **Step 7: Run full test suite to check for regressions**

Run: `uv run pytest tests/matmaster/ -v`

Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add matmaster/core/exp.py tests/matmaster/integration/test_lazy_mcp_integration.py
git commit -m "fix: Exp self-loads mcp.yaml for runtime MCP config injection"
```

---

## Chunk 4: cache_mcp_schemas.py CLI Update

### Task 8: Update cache_mcp_schemas.py to read mcp.yaml

**Files:**
- Modify: `matmaster/tools/cache_mcp_schemas.py:24-58`

- [ ] **Step 1: Update `parse_args()` default and `generate_cache()` to read mcp.yaml**

In `matmaster/tools/cache_mcp_schemas.py`:

Change `parse_args()`:
```python
parser.add_argument(
    "--config-dir",
    default="matmaster_config",  # Was: "configs/mat_master"
    help="Directory containing mcp.yaml and mcp_config*.json",
)
```

Change `generate_cache()` to read `mcp.yaml` instead of `config.yaml`'s `mcp:` key:

```python
async def generate_cache(config_dir: Path, output_dir: Path) -> None:
    import yaml

    from evomaster.agent.tools.mcp.mcp_manager import MCPToolManager
    from matmaster.tools.lazy_mcp import configure_mcp_manager

    mcp_yaml = config_dir / "mcp.yaml"
    if not mcp_yaml.exists():
        logger.error("mcp.yaml not found at %s", mcp_yaml)
        sys.exit(1)

    with open(mcp_yaml, encoding="utf-8") as f:
        mcp_config = yaml.safe_load(f)

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

    # ... rest unchanged
```

- [ ] **Step 2: Verify module imports**

Run: `uv run python -c "from matmaster.tools.cache_mcp_schemas import parse_args; a = parse_args.__code__; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add matmaster/tools/cache_mcp_schemas.py
git commit -m "fix: update cache_mcp_schemas to read mcp.yaml instead of config.yaml"
```

---

## Chunk 5: Final Verification

### Task 9: Full regression test + import smoke test

- [ ] **Step 1: Run all matmaster tests**

Run: `uv run pytest tests/matmaster/ -v`

Expected: All PASS

- [ ] **Step 2: Verify Exp loads correctly from direct.toml**

Run: `uv run python -c "from matmaster.config.loader import load_exp_config; c = load_exp_config('direct'); print('config_dir:', c.skills.config_dir); print('mcp_runtime_file:', c.skills.mcp_runtime_file); assert c.skills.config_dir == 'matmaster_config'; assert c.skills.mcp_runtime_file == 'mcp.yaml'; print('OK')"`

Expected: prints config_dir/mcp_runtime_file then `OK`

- [ ] **Step 3: Verify config.yaml loads via ConfigManager**

Run: `uv run python -c "from evomaster.config import ConfigManager; cm = ConfigManager(config_dir='matmaster_config'); cfg = cm.load(); print('agents.llm:', cfg.agents.get('general', {}).get('llm')); print('env.scheduler.type:', cfg.env.scheduler.type); print('OK')"`

Expected: `agents.llm: litellm`, `env.scheduler.type: local`, `OK`

- [ ] **Step 4: Verify file counts**

Run: `wc -l matmaster_config/config.yaml matmaster_config/mcp.yaml matmaster_config/llm_config.yaml`

Expected: config.yaml ~45 lines, mcp.yaml ~240 lines, llm_config.yaml ~122 lines
