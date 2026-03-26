# Exp Config Extraction Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract hardcoded Exp configuration from agent_run_service.py into standalone toml files under matmaster/exps/, with typed ExpConfig model and toml-based loader.

**Architecture:** Each Exp type is a self-contained toml file in `matmaster/exps/`. The loader resolves by name (== frontend `mode` param), parses toml into typed `ExpConfig`, and hands it to `Exp`. Runtime-only context (skills, mcp) is injected via `build_runtime()` parameters instead of config.

**Tech Stack:** Python 3.11+ (tomllib), Pydantic v2, pytest

**Spec:** `docs/superpowers/specs/2026-03-24-exp-config-extraction-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `matmaster/exps/direct.toml` | Create | First exp definition (identity, tools, max_turns) |
| `matmaster/config/exp.py` | Modify | Remove runtime fields, add `developer_instructions` |
| `matmaster/config/loader.py` | Modify | Rewrite `load_exp_config()` to load toml by name |
| `matmaster/core/exp.py` | Modify | Accept `ExpConfig`, add skills/mcp to `build_runtime()` |
| `src/services/agent_run_service.py` | Modify | Replace hardcoded dict with `load_exp_config(mode)` |
| `matmaster/config/__init__.py` | Modify | Update exports |
| `tests/matmaster/config/test_exp.py` | Rewrite | Test new ExpConfig fields + toml loading |
| `tests/matmaster/core/test_exp.py` | Rewrite | All `Exp({dict})` → `Exp(ExpConfig(...))` |
| `tests/matmaster/integration/test_pipeline_alignment.py` | Modify | Update Exp construction |
| `tests/matmaster/integration/test_e2e_minimal.py` | Modify | Update Exp construction |
| `tests/matmaster/integration/test_e2e_mat_master.py` | Modify | Update Exp construction |
| `tests/matmaster/integration/test_upstream_scenarios.py` | Modify | Update Exp construction |

---

## Chunk 1: ExpConfig Model + Toml Loader + direct.toml

### Task 1: Create `matmaster/exps/direct.toml`

**Files:**
- Create: `matmaster/exps/direct.toml`

- [ ] **Step 1: Create the exps directory, `__init__.py`, and toml file**

```bash
mkdir -p matmaster/exps
touch matmaster/exps/__init__.py
```

Write `matmaster/exps/direct.toml`:

```toml
name = "direct"
mode = "direct"
max_turns = 200
guards = []

developer_instructions = '''
You are Mat Master, an autonomous agent for materials science and computational materials.
'''

[tools]
builtin = ["*"]
mcp = "*"
```

Note: `developer_instructions` content is a minimal identity stub. The full identity text will be refined when the old prompt template is reviewed. The key structural requirement is that it sits before all `[table]` sections in the toml file.

- [ ] **Step 2: Verify toml parses correctly**

Run: `uv run python -c "import tomllib; print(tomllib.load(open('matmaster/exps/direct.toml','rb')))"`

Expected: dict with `developer_instructions` as top-level key (NOT nested under `tools`).

- [ ] **Step 3: Commit**

```bash
git add matmaster/exps/direct.toml matmaster/exps/__init__.py
git commit -m "feat: add matmaster/exps/direct.toml exp definition"
```

---

### Task 2: Update ExpConfig model

**Files:**
- Modify: `matmaster/config/exp.py`
- Test: `tests/matmaster/config/test_exp.py`

- [ ] **Step 1: Write the failing tests for new ExpConfig**

Replace `tests/matmaster/config/test_exp.py` with:

```python
"""Tests for matmaster.config.exp -- ExpConfig model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from matmaster.config.exp import ExpConfig, ExpToolsConfig


class TestExpToolsConfig:
    def test_defaults(self):
        cfg = ExpToolsConfig()
        assert cfg.builtin == ["*"]
        assert cfg.mcp == "*"


class TestExpConfig:
    def test_defaults(self):
        cfg = ExpConfig()
        assert cfg.name == "direct"
        assert cfg.mode == "direct"
        assert cfg.max_turns == 100
        assert cfg.guards == []
        assert cfg.developer_instructions == ""
        assert cfg.tools.builtin == ["*"]

    def test_from_toml_dict(self):
        """Simulate what tomllib.load() would produce from direct.toml."""
        data = {
            "name": "direct",
            "mode": "direct",
            "max_turns": 200,
            "guards": [],
            "developer_instructions": "You are Mat Master.",
            "tools": {"builtin": ["*"], "mcp": "*"},
        }
        cfg = ExpConfig.model_validate(data)
        assert cfg.name == "direct"
        assert cfg.max_turns == 200
        assert cfg.developer_instructions == "You are Mat Master."

    def test_extra_fields_ignored(self):
        """Unknown fields from toml are silently ignored."""
        data = {"name": "test", "unknown_field": "value", "another": 123}
        cfg = ExpConfig.model_validate(data)
        assert cfg.name == "test"
        assert not hasattr(cfg, "unknown_field")

    def test_skills_mcp_compaction_not_accepted(self):
        """These fields were removed -- they should be silently ignored via extra=ignore."""
        data = {
            "name": "test",
            "skills": {"enabled": True},
            "mcp": {"servers": []},
            "compaction": {"enabled": True},
        }
        cfg = ExpConfig.model_validate(data)
        assert cfg.name == "test"
        assert not hasattr(cfg, "skills")
        assert not hasattr(cfg, "compaction")

    def test_developer_instructions_multiline(self):
        """Multiline strings from toml are preserved."""
        data = {
            "developer_instructions": "Line 1\nLine 2\nLine 3",
        }
        cfg = ExpConfig.model_validate(data)
        assert "Line 2" in cfg.developer_instructions
```

Run: `uv run pytest tests/matmaster/config/test_exp.py -v`

Expected: Several FAIL because `ExpConfig` still has `skills`, `mcp`, `compaction` fields and lacks `developer_instructions`.

- [ ] **Step 2: Update ExpConfig to match new schema**

Edit `matmaster/config/exp.py`:

```python
"""Exp assembly configuration models.

Typed config for ``matmaster.core.exp.Exp``. Loaded from toml files
in ``matmaster/exps/``.

Usage::

    from matmaster.config.loader import load_exp_config
    cfg = load_exp_config("direct")
    exp = Exp(cfg)
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExpToolsConfig(BaseModel):
    """Tool registration settings for Exp."""

    builtin: list[str] = Field(default_factory=lambda: ["*"])
    mcp: str = "*"


class ExpConfig(BaseModel):
    """Exp assembly configuration.

    Loaded from ``matmaster/exps/{name}.toml``. Default values are fallbacks
    when fields are absent from the toml file.

    ``extra="ignore"`` allows forward-compatible loading when toml files
    contain fields not yet modeled.
    """

    name: str = "direct"
    mode: str = "direct"
    max_turns: int = 100
    guards: list[str] = Field(default_factory=list)
    tools: ExpToolsConfig = Field(default_factory=ExpToolsConfig)
    developer_instructions: str = ""

    model_config = ConfigDict(extra="ignore")
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/config/test_exp.py -v`

Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add matmaster/config/exp.py tests/matmaster/config/test_exp.py
git commit -m "refactor: update ExpConfig -- remove runtime fields, add developer_instructions"
```

---

### Task 3: Rewrite `load_exp_config()` to load toml by name

**Files:**
- Modify: `matmaster/config/loader.py`
- Test: `tests/matmaster/config/test_loader.py` (the `TestLoadExpConfig` class)

- [ ] **Step 1: Write failing tests for new loader**

The existing `TestLoadExpConfig` in `tests/matmaster/config/test_loader.py` tests the old signature. Replace the `TestLoadExpConfig` class (lines 101-133) with:

```python
class TestLoadExpConfig:
    """Tests for load_exp_config() -- toml-based loading."""

    def test_load_direct(self, tmp_path):
        """Load a valid toml file by name."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "direct.toml").write_text(
            'name = "direct"\nmode = "direct"\nmax_turns = 200\n'
            'developer_instructions = "You are Mat Master."\n'
            "\n[tools]\nbuiltin = ['*']\nmcp = '*'\n",
            encoding="utf-8",
        )
        cfg = load_exp_config("direct", exps_dir=exps_dir)
        assert isinstance(cfg, ExpConfig)
        assert cfg.name == "direct"
        assert cfg.max_turns == 200
        assert cfg.developer_instructions == "You are Mat Master."

    def test_unknown_name_raises(self, tmp_path):
        """Unknown exp name raises FileNotFoundError with available list."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "direct.toml").write_text('name = "direct"\n')
        with pytest.raises(FileNotFoundError, match="unknown_exp"):
            load_exp_config("unknown_exp", exps_dir=exps_dir)

    def test_error_message_lists_available(self, tmp_path):
        """FileNotFoundError message includes available exp names."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "direct.toml").write_text('name = "direct"\n')
        (exps_dir / "planner.toml").write_text('name = "planner"\n')
        with pytest.raises(FileNotFoundError, match="direct") as exc_info:
            load_exp_config("nope", exps_dir=exps_dir)
        assert "planner" in str(exc_info.value)

    def test_env_var_expansion(self, tmp_path, monkeypatch):
        """${ENV} patterns are expanded in non-developer_instructions fields."""
        monkeypatch.setenv("TEST_MCP", "custom")
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "test.toml").write_text(
            'name = "test"\n\n[tools]\nbuiltin = ["*"]\nmcp = "${TEST_MCP}"\n'
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.tools.mcp == "custom"

    def test_developer_instructions_not_expanded(self, tmp_path, monkeypatch):
        """${...} in developer_instructions is preserved, not expanded."""
        monkeypatch.setenv("FOO", "bar")
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "test.toml").write_text(
            'name = "test"\n'
            "developer_instructions = 'Use ${FOO} as template var'\n"
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert "${FOO}" in cfg.developer_instructions

    def test_default_exps_dir(self):
        """Default exps_dir resolves to matmaster/exps/ and can load direct.toml."""
        cfg = load_exp_config("direct")
        assert cfg.name == "direct"
```

Run: `uv run pytest tests/matmaster/config/test_loader.py::TestLoadExpConfig -v`

Expected: FAIL because `load_exp_config` still has old signature.

- [ ] **Step 2: Rewrite `load_exp_config()` in loader.py**

Replace the `load_exp_config` function (lines 72-98) in `matmaster/config/loader.py` with:

```python
def load_exp_config(
    name: str,
    *,
    exps_dir: Path | None = None,
) -> ExpConfig:
    """Load ``matmaster/exps/{name}.toml`` into ``ExpConfig``.

    Args:
        name: Exp definition name (matches toml filename without extension).
        exps_dir: Override directory to search for toml files.
            Defaults to ``matmaster/exps/`` relative to this package.

    Returns:
        Validated ``ExpConfig``.

    Raises:
        FileNotFoundError: If no toml file matches *name*.
    """
    import tomllib

    if exps_dir is None:
        exps_dir = Path(__file__).resolve().parent.parent / "exps"

    toml_path = exps_dir / f"{name}.toml"
    if not toml_path.exists():
        available = sorted(p.stem for p in exps_dir.glob("*.toml"))
        raise FileNotFoundError(
            f"Exp definition not found: {toml_path}, "
            f"available: {available}"
        )

    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    # Preserve developer_instructions verbatim (avoid ${...} misexpansion)
    dev_instr = raw.pop("developer_instructions", "")
    raw = _expand_env_vars(raw)
    raw["developer_instructions"] = dev_instr
    return ExpConfig.model_validate(raw)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/config/test_loader.py -v`

Expected: All PASS (both LLM and Exp loader tests).

- [ ] **Step 4: Commit**

```bash
git add matmaster/config/loader.py tests/matmaster/config/test_loader.py
git commit -m "refactor: rewrite load_exp_config to load toml by name"
```

---

## Chunk 2: Exp Class Refactor + Service Layer + Test Adaptation

### Task 4: Refactor `Exp` to accept `ExpConfig`

**Files:**
- Modify: `matmaster/core/exp.py`
- Test: `tests/matmaster/core/test_exp.py`

This is the largest task. The key changes:

1. `__init__` accepts `ExpConfig` instead of `dict`
2. `assemble()` uses typed attribute access
3. `build_runtime()` gains `skills`/`mcp` keyword parameters
4. `_init_skill_tools`/`_init_mcp_tools` gain a config dict parameter
5. `run()` forwards `skills`/`mcp` to `build_runtime()`
6. All internal `.get()` calls become attribute access

- [ ] **Step 1: Write a focused failing test for the new Exp interface**

Add to the TOP of `tests/matmaster/core/test_exp.py` (after imports), a new test that exercises the core new behavior:

```python
from matmaster.config.exp import ExpConfig


class TestExpWithExpConfig:
    """Tests for Exp accepting ExpConfig (new interface)."""

    def test_init_accepts_exp_config(self):
        cfg = ExpConfig(name="test")
        exp = Exp(cfg)
        assert exp.exp_name == "test"

    def test_build_runtime_accepts_skills_mcp(self):
        cfg = ExpConfig(name="test")
        exp = Exp(cfg)
        ctx = _make_ctx(with_llm=True)
        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(
                ctx,
                skills={"enabled": True},
                mcp={"servers": []},
            )
        assert runtime.spec is not None

    def test_developer_instructions_flows_to_identity(self):
        cfg = ExpConfig(
            name="test",
            developer_instructions="Custom identity text",
        )
        exp = Exp(cfg)
        ctx = _make_ctx(with_llm=True)
        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)
        assert "Custom identity text" in runtime.spec.system_prompt
```

Note: Uses the existing `_make_ctx()` helper defined at the top of the test file (not a pytest fixture). `with_llm=True` provides a mock LLM provider which `build_runtime` requires.

Run: `uv run pytest tests/matmaster/core/test_exp.py::TestExpWithExpConfig -v`

Expected: FAIL because `Exp.__init__` doesn't accept `ExpConfig`.

- [ ] **Step 2: Refactor `Exp` class**

Edit `matmaster/core/exp.py`. The changes below are listed by section:

**2a. Imports:** Add `ExpConfig` import:

```python
from matmaster.config.exp import ExpConfig
```

**2b. `__init__`:** Change signature:

```python
def __init__(self, config: ExpConfig) -> None:
    self._config = config
    self._cleanup_callbacks: list[Callable[[], None]] = []
    self.logger = logging.getLogger(self.__class__.__name__)
```

**2c. `exp_name` property:**

```python
@property
def exp_name(self) -> str:
    """From config.name."""
    return self._config.name
```

**2d. `assemble()`:** Replace dict `.get()` with typed attribute access. Keep compaction handling unchanged (it reads from a separate source, not ExpConfig):

```python
def assemble(self, ctx: PlaygroundContext) -> AgentRuntimeSpec:
    """Data transform: config + ctx -> AgentRuntimeSpec."""
    # NOTE: compaction logic left unchanged -- managed by separate process.
    # It currently reads from self._config dict which no longer exists;
    # with ExpConfig, compaction defaults to disabled (CompactionConfig()).
    return AgentRuntimeSpec(
        llm_provider=ctx.llm_provider,
        max_turns=self._config.max_turns,
        guards=self._config.guards,
        mode=self._config.mode,
        compaction=CompactionConfig(),
        meta={},
    )
```

**2e. `build_runtime()`:** Add `skills`/`mcp` params, fix ordering (tools before prompt):

```python
def build_runtime(
    self,
    ctx: PlaygroundContext,
    *,
    bus: MessageBus | None = None,
    skills: dict[str, Any] | None = None,
    mcp: dict[str, Any] | None = None,
) -> AgentRuntime:
    """Resource creation: assemble -> tools -> prompt -> kernel."""
    spec = self.assemble(ctx)

    # Tools: register ALL tools before building system prompt
    registry = ToolRegistry()
    if "*" in self._config.tools.builtin and ctx.session is not None:
        self._init_builtin_tools(ctx, registry)

    # Skills/MCP: runtime-injected (must be before system prompt)
    if skills:
        self._init_skill_tools(ctx, registry, skills)
    if mcp:
        self._init_mcp_tools(ctx, registry, mcp)

    # System prompt via ContextBuilder
    builder = ContextBuilder()
    identity = self._config.developer_instructions or None
    system_prompt = builder.build(ctx, registry, mode=spec.mode, identity=identity)

    # Hooks
    hooks = list(spec.hooks)
    if bus is not None:
        emitter_hook = EventEmitterHook(bus, source=self.exp_name)
        hooks.append(emitter_hook)

    # Compaction: unchanged, managed by separate process
    compactor = None
    if spec.compaction.enabled and spec.llm_provider is not None:
        from matmaster.core.context_compactor import ContextCompactor

        summary_provider = spec.llm_provider
        if spec.compaction.compaction_llm:
            resolved = self._resolve_compaction_llm(
                spec.compaction.compaction_llm, ctx
            )
            if resolved:
                from matmaster.providers.openai_provider import OpenAIProvider

                summary_provider = OpenAIProvider(**resolved)
            else:
                self.logger.warning(
                    "compaction_llm key=%r not found, falling back to main provider",
                    spec.compaction.compaction_llm,
                )

        compactor = ContextCompactor(
            config=spec.compaction,
            summary_provider=summary_provider,
            bus=bus,
        )

    spec = spec.model_copy(
        update={
            "tool_registry": registry,
            "system_prompt": system_prompt,
            "hooks": hooks,
            "compactor": compactor,
        }
    )

    from matmaster.core.agent import AgentKernel

    kernel = AgentKernel()

    return AgentRuntime(
        kernel=kernel,
        spec=spec,
        cleanup=self._run_cleanup_callbacks,
    )
```

**2f. `_init_skill_tools` / `_init_mcp_tools`:** Add config dict param:

```python
def _init_skill_tools(
    self,
    ctx: PlaygroundContext,
    registry: ToolRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Initialize skill tools (stub -- factory mechanism refined later)."""

def _init_mcp_tools(
    self,
    ctx: PlaygroundContext,
    registry: ToolRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Initialize MCP tools (stub -- factory mechanism refined later)."""
```

**2g. `run()`:** Forward `skills`/`mcp`:

```python
def run(
    self,
    ctx: PlaygroundContext,
    task: str,
    *,
    bus: MessageBus | None = None,
    history: list[Message] | None = None,
    stop_event: threading.Event | None = None,
    skills: dict[str, Any] | None = None,
    mcp: dict[str, Any] | None = None,
) -> RunResultEvent:
    """build_runtime -> kernel.run -> cleanup."""
    runtime = self.build_runtime(ctx, bus=bus, skills=skills, mcp=mcp)
    try:
        result = runtime.kernel.run(
            runtime.spec, task, history=history, stop_event=stop_event
        )
        return result.event
    finally:
        runtime.cleanup()
```

**2h. Fix `_resolve_compaction_llm`:** The legacy fallback path uses `self._config.get("_llm_profiles", {})` which won't work on `ExpConfig`. Remove the legacy dict fallback branch, keeping only the `ctx.llm_config` path:

```python
def _resolve_compaction_llm(
    self, key: str, ctx: PlaygroundContext
) -> dict[str, Any] | None:
    """Resolve compaction LLM profile from PlaygroundContext.llm_config."""
    llm_config = getattr(ctx, "llm_config", None)
    if llm_config is None:
        return None

    try:
        profile = llm_config.get_profile(key)
    except KeyError:
        return None
    return {
        "model": profile.model,
        "api_key": profile.api_key,
        "base_url": profile.base_url,
        "temperature": profile.effective_temperature(),
        "max_tokens": profile.max_tokens,
        "timeout": profile.timeout,
    }
```

**2i. Remove `_load_file_content` static method** (no longer needed -- prompt is inline in ExpConfig).

- [ ] **Step 3: Run the new test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_exp.py::TestExpWithExpConfig -v`

Expected: PASS.

- [ ] **Step 4: Update all existing tests in `test_exp.py`**

Every `Exp({dict})` construction needs to become `Exp(ExpConfig(...))`. The pattern is mechanical:

- `Exp({"name": "test"})` → `Exp(ExpConfig(name="test"))`
- `Exp({"name": "test", "max_turns": 50})` → `Exp(ExpConfig(name="test", max_turns=50))`
- `Exp({})` → `Exp(ExpConfig())`
- For configs with `identity` key: use `developer_instructions` instead
- For configs with `compaction` dict: use `ExpConfig()` with default (compaction is no longer in ExpConfig)
- For configs with `skills`/`mcp` in the dict: move to `build_runtime()` params

Key test changes needed:

- Tests that check `exp._config.get("name", "unnamed")` → check `exp.exp_name`
- Tests that check `exp._config` is a dict → check it's an `ExpConfig`
- Tests that put `skills`/`mcp` in the config dict → pass to `build_runtime(skills=..., mcp=...)`
- Tests for `identity` in config → use `developer_instructions` in `ExpConfig`
- Tests for `compaction` in config → these test the *old* compaction-from-config behavior which is now removed; replace with tests that verify `CompactionConfig()` default is used

- [ ] **Step 5: Run the full test_exp.py suite**

Run: `uv run pytest tests/matmaster/core/test_exp.py -v`

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/exp.py tests/matmaster/core/test_exp.py
git commit -m "refactor: Exp accepts ExpConfig, build_runtime takes skills/mcp params"
```

---

### Task 5: Update `agent_run_service.py` Stage 4

**Files:**
- Modify: `src/services/agent_run_service.py:322-362`

- [ ] **Step 1: Replace Stage 4 exp assembly**

Replace lines 350-362 in `src/services/agent_run_service.py`:

```python
# Old:
exp_config = {
    "name": "direct",
    "tools": {"builtin": ["*"]},
    "guards": [],
    "termination": {"max_turns": 100},
    "prompt": {},
    "context": {},
    "skills": pg_ctx.run_meta.get("skill_config", {}),
    "mcp": pg_ctx.run_meta.get("mcp_config", {}),
}

exp = Exp(exp_config)
runtime = exp.build_runtime(pg_ctx, bus=bus)
```

With:

```python
from matmaster.config.loader import load_exp_config

exp_name = mode or "direct"
exp_config = load_exp_config(exp_name)
exp = Exp(exp_config)
runtime = exp.build_runtime(
    pg_ctx,
    bus=bus,
    skills=pg_ctx.run_meta.get("skill_config"),
    mcp=pg_ctx.run_meta.get("mcp_config"),
)
```

Note: `load_exp_config` import may already be present from the LLM extraction (line 323 imports `load_llm_config` from same module). Adjust the import to include both.

Also: `mode` variable must be available in scope. Check that the `execute_agent_run` method receives `mode` as a parameter. If not, it comes from `run_meta` -- trace the call to verify.

- [ ] **Step 2: Verify `mode` is accessible in Stage 4 scope**

Search for how `mode` is passed to the execute method. The current hardcoded `"direct"` suggests `mode` may not yet be a parameter. If so, extract it from `pg_ctx.run_meta.get("mode")` or default to `"direct"`:

```python
exp_name = pg_ctx.run_meta.get("mode") or "direct"
```

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from matmaster.config.loader import load_exp_config; print(load_exp_config('direct').name)"`

Expected: `direct`

- [ ] **Step 4: Commit**

```bash
git add src/services/agent_run_service.py
git commit -m "refactor: agent_run_service Stage 4 uses load_exp_config(mode)"
```

---

### Task 6: Update integration tests

**Files:**
- Modify: `tests/matmaster/integration/test_pipeline_alignment.py`
- Modify: `tests/matmaster/integration/test_e2e_minimal.py`
- Modify: `tests/matmaster/integration/test_e2e_mat_master.py`
- Modify: `tests/matmaster/integration/test_upstream_scenarios.py`

- [ ] **Step 1: Update all `_EXP_CONFIG` dicts and `Exp()` calls**

Pattern for each file:

1. Add import: `from matmaster.config.exp import ExpConfig`
2. Change `_EXP_CONFIG` dict to `_EXP_CONFIG = ExpConfig(name="...", ...)`
3. `Exp(self._EXP_CONFIG)` stays the same (now passes ExpConfig instead of dict)
4. If any test passes `skills`/`mcp` in the config dict, move to `build_runtime()` call

For each file, the `_EXP_CONFIG` dict typically looks like:

```python
# Old
_EXP_CONFIG = {
    "name": "direct",
    "tools": {"builtin": ["*"]},
    ...
}

# New
_EXP_CONFIG = ExpConfig(name="direct")
```

Default `ExpConfig()` already has `tools.builtin=["*"]` and `mode="direct"`, so most fields can be omitted.

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/matmaster/integration/ -v --timeout=60`

Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/integration/
git commit -m "test: update integration tests for Exp(ExpConfig) interface"
```

---

### Task 7: Update `__init__.py` exports and final cleanup

**Files:**
- Modify: `matmaster/config/__init__.py`

- [ ] **Step 1: Verify exports are correct**

Current `__init__.py` already exports `ExpConfig`, `ExpToolsConfig`, `load_exp_config`. No changes needed unless the import paths changed. Verify:

Run: `uv run python -c "from matmaster.config import ExpConfig, load_exp_config; print('OK')"`

Expected: `OK`

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest tests/matmaster/ -v`

Expected: All PASS.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "refactor: complete Exp config extraction to toml-based definitions"
```
