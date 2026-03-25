# Context Structure Refactor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add universal system_prompt section from `_base.toml` and remove redundant mode_contract from the context assembly pipeline.

**Architecture:** `_base.toml` provides shared system_prompt, loaded by `load_exp_config()` and merged into `ExpConfig`. `ContextBuilder` places it as the first section for prompt caching. `mode_contract` removed entirely, content merged into `developer_instructions`.

**Tech Stack:** Python 3.10+, Pydantic, tomllib, pytest

**Scope:** This plan covers the `matmaster/` new architecture path and `devshell` path only. The old `playground/mat_master/core/agent.py` local Web path (which uses `build_mat_master_system_prompt()`) is intentionally out of scope — it will be unified when the old architecture is replaced. See spec "Not Changed" section for details on the known behavioral divergence.

---

## Chunk 1: Config Layer (`ExpConfig` + `loader.py`)

### Task 1: Add `system_prompt` and remove `mode_contract` from ExpConfig

**Files:**
- Modify: `matmaster/config/exp.py:36-55`
- Test: `tests/matmaster/config/test_exp.py`

- [ ] **Step 1: Write failing tests for ExpConfig changes**

In `tests/matmaster/config/test_exp.py`, replace the two `mode_contract` tests and add `system_prompt` tests:

```python
# Replace test_mode_contract_default (line 66-68) and test_mode_contract_from_dict (line 70-76) with:

def test_system_prompt_default(self):
    cfg = ExpConfig()
    assert cfg.system_prompt == ""

def test_system_prompt_from_dict(self):
    data = {
        "name": "direct",
        "system_prompt": "You are Mat Master.",
    }
    cfg = ExpConfig.model_validate(data)
    assert cfg.system_prompt == "You are Mat Master."

def test_mode_contract_rejected(self):
    """mode_contract field is ignored (extra='ignore')."""
    data = {"name": "direct", "mode_contract": "Execute directly."}
    cfg = ExpConfig.model_validate(data)
    assert not hasattr(cfg, "mode_contract")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/config/test_exp.py::TestExpConfig::test_system_prompt_default tests/matmaster/config/test_exp.py::TestExpConfig::test_system_prompt_from_dict tests/matmaster/config/test_exp.py::TestExpConfig::test_mode_contract_rejected -v`
Expected: FAIL — `system_prompt` not in ExpConfig, `mode_contract` still exists

- [ ] **Step 3: Update ExpConfig**

In `matmaster/config/exp.py`:
- Replace line 53 (`mode_contract: str = ""`) with `system_prompt: str = ""`
- Line 52 (`developer_instructions: str = ""`) stays unchanged

Result (lines 51-53):
```python
    skills: ExpSkillsConfig = Field(default_factory=ExpSkillsConfig)
    system_prompt: str = ""
    developer_instructions: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/config/test_exp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/config/exp.py tests/matmaster/config/test_exp.py
git commit -m "feat: add system_prompt to ExpConfig, remove mode_contract"
```

### Task 2: Add `_base.toml` loading and `load_base_system_prompt()` to loader

**Files:**
- Modify: `matmaster/config/loader.py:72-112`
- Create: `matmaster/exps/_base.toml`
- Test: `tests/matmaster/config/test_loader.py`

- [ ] **Step 1: Create `_base.toml` placeholder**

Create `matmaster/exps/_base.toml`:

```toml
system_prompt = '''
You are Mat Master, an autonomous AI agent for materials science and computational materials.
'''
```

- [ ] **Step 2: Write failing tests for loader merge semantics**

In `tests/matmaster/config/test_loader.py`, remove the three `mode_contract` tests (lines 161-185: `test_mode_contract_not_expanded`, `test_mode_contract_loaded`, `test_mode_contract_reaches_system_prompt`) and add a new test class:

```python
from matmaster.config.loader import load_base_system_prompt, load_exp_config


class TestBaseTomlMerge:
    """Tests for _base.toml system_prompt merge semantics."""

    def test_base_present_exp_no_override(self, tmp_path):
        """_base.toml system_prompt used when exp toml has none."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            "system_prompt = 'Base system prompt'\n"
        )
        (exps_dir / "test.toml").write_text(
            'name = "test"\ndeveloper_instructions = "DI"\n'
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.system_prompt == "Base system prompt"

    def test_exp_overrides_base(self, tmp_path):
        """Exp toml system_prompt overrides _base.toml."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            "system_prompt = 'Base prompt'\n"
        )
        (exps_dir / "test.toml").write_text(
            'name = "test"\nsystem_prompt = "Exp override"\n'
        )
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.system_prompt == "Exp override"

    def test_base_missing(self, tmp_path, caplog):
        """Missing _base.toml yields empty system_prompt with warning."""
        import logging
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "test.toml").write_text('name = "test"\n')
        with caplog.at_level(logging.WARNING):
            cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.system_prompt == ""
        assert "_base.toml" in caplog.text

    def test_base_extra_fields_ignored(self, tmp_path):
        """Non-system_prompt fields in _base.toml do not pollute ExpConfig."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            'system_prompt = "Base"\nname = "SHOULD_NOT_LEAK"\nmode = "BAD"\n'
        )
        (exps_dir / "test.toml").write_text('name = "test"\nmode = "direct"\n')
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert cfg.name == "test"
        assert cfg.mode == "direct"
        assert cfg.system_prompt == "Base"

    def test_base_system_prompt_not_env_expanded(self, tmp_path, monkeypatch):
        """${...} in _base.toml system_prompt preserved verbatim."""
        monkeypatch.setenv("FOO", "bar")
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            "system_prompt = 'Use ${FOO} literally'\n"
        )
        (exps_dir / "test.toml").write_text('name = "test"\n')
        cfg = load_exp_config("test", exps_dir=exps_dir)
        assert "${FOO}" in cfg.system_prompt

    def test_exp_discovery_excludes_underscore_prefix(self, tmp_path):
        """Error message for unknown exp does not list _base."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text("system_prompt = 'x'\n")
        (exps_dir / "direct.toml").write_text('name = "direct"\n')
        with pytest.raises(FileNotFoundError) as exc_info:
            load_exp_config("nope", exps_dir=exps_dir)
        assert "_base" not in str(exc_info.value)
        assert "direct" in str(exc_info.value)

    def test_underscore_prefix_name_rejected(self, tmp_path):
        """load_exp_config('_base') raises ValueError, not silently loads."""
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text("system_prompt = 'x'\n")
        with pytest.raises(ValueError, match="reserved"):
            load_exp_config("_base", exps_dir=exps_dir)


class TestLoadBaseSystemPrompt:
    """Tests for the standalone load_base_system_prompt() helper."""

    def test_returns_system_prompt(self, tmp_path):
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        (exps_dir / "_base.toml").write_text(
            "system_prompt = 'Hello from base'\n"
        )
        result = load_base_system_prompt(exps_dir=exps_dir)
        assert result == "Hello from base"

    def test_missing_base_returns_empty(self, tmp_path, caplog):
        import logging
        exps_dir = tmp_path / "exps"
        exps_dir.mkdir()
        with caplog.at_level(logging.WARNING):
            result = load_base_system_prompt(exps_dir=exps_dir)
        assert result == ""
        assert "_base.toml" in caplog.text
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/config/test_loader.py::TestBaseTomlMerge tests/matmaster/config/test_loader.py::TestLoadBaseSystemPrompt -v`
Expected: FAIL — `load_base_system_prompt` does not exist, loader has no `_base.toml` logic

- [ ] **Step 4: Implement loader changes**

First, add `import logging` to `matmaster/config/loader.py` at line 18 (after `from typing import Any`):

```python
import logging
```

Then, insert the following between `load_llm_config()` (ends at line 69) and `load_exp_config()` (starts at line 72). Finally, replace `load_exp_config()` (lines 72-113) with the updated version below.

Complete new code to insert after line 69 and replace lines 72-113:

```python
logger = logging.getLogger(__name__)


def _load_base_system_prompt(exps_dir: Path) -> str:
    """Load system_prompt from _base.toml. Returns empty string if missing."""
    import tomllib

    base_path = exps_dir / "_base.toml"
    if not base_path.exists():
        logger.warning("_base.toml not found at %s, system_prompt will be empty", base_path)
        return ""
    with open(base_path, "rb") as f:
        base_raw = tomllib.load(f)
    return base_raw.get("system_prompt", "")


def load_base_system_prompt(*, exps_dir: Path | None = None) -> str:
    """Public helper: load system_prompt from _base.toml.

    Used by devshell and other entry points that hand-build ExpConfig
    without going through load_exp_config().
    """
    if exps_dir is None:
        exps_dir = Path(__file__).resolve().parent.parent / "exps"
    return _load_base_system_prompt(exps_dir)


def load_exp_config(
    name: str,
    *,
    exps_dir: Path | None = None,
) -> ExpConfig:
    """Load ``matmaster/exps/{name}.toml`` into ``ExpConfig``.

    Automatically merges system_prompt from ``_base.toml`` (if present).
    Exp-level system_prompt overrides the base value.
    """
    import tomllib

    if exps_dir is None:
        exps_dir = Path(__file__).resolve().parent.parent / "exps"

    if name.startswith("_"):
        raise ValueError(
            f"Exp name '{name}' is reserved (underscore prefix). "
            f"Use load_base_system_prompt() to access _base.toml."
        )

    toml_path = exps_dir / f"{name}.toml"
    if not toml_path.exists():
        available = sorted(
            p.stem for p in exps_dir.glob("*.toml")
            if not p.stem.startswith("_")
        )
        raise FileNotFoundError(
            f"Exp definition not found: {toml_path}, "
            f"available: {available}"
        )

    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    # Load base system_prompt (exp can override)
    base_system_prompt = _load_base_system_prompt(exps_dir)

    # Preserve prompt fields verbatim (avoid ${...} misexpansion)
    system_prompt = raw.pop("system_prompt", base_system_prompt)
    dev_instr = raw.pop("developer_instructions", "")
    raw = _expand_env_vars(raw)
    raw["system_prompt"] = system_prompt
    raw["developer_instructions"] = dev_instr
    return ExpConfig.model_validate(raw)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/config/test_loader.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/config/loader.py matmaster/exps/_base.toml tests/matmaster/config/test_loader.py
git commit -m "feat: add _base.toml merge and load_base_system_prompt helper"
```

---

## Chunk 2: ContextBuilder + Exp Integration

### Task 3: Update ContextBuilder — add system_prompt section, remove mode_contract

**Files:**
- Modify: `matmaster/core/context_builder.py`
- Test: `tests/matmaster/core/test_context_builder.py`

- [ ] **Step 1: Write failing tests**

In `tests/matmaster/core/test_context_builder.py`:

Remove these tests entirely:
- `test_build_with_mode_contract_only` (lines 99-108)
- `test_mode_contract_text_passthrough` (lines 162-169)
- `test_different_mode_contracts_produce_different_prompts` (lines 172-184)

Update `test_build_no_args_produces_empty` (line 83): change docstring to `"""Build with all defaults (empty system_prompt, empty identity, no tools) produces empty string."""`

Update `test_build_with_identity_only` (lines 89-96): remove `assert "# Mode Contract" not in result` (line 96), add `assert "# System" not in result`.

Replace `test_section_order_fixed` (lines 111-136) with:

```python
def test_section_order_fixed(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """All sections enabled -- fixed order system_prompt < identity < skills
    < tools < memory < task."""
    reg = ToolRegistry()
    reg.register(MockTool("t1"))

    result = builder.build(
        ctx,
        reg,
        system_prompt="Test system prompt",
        identity="Test identity",
        skill_registry=MockSkillRegistry(),
        memory_context="some memory",
        task_context="some task",
    )

    idx_system = result.index("# System")
    idx_identity = result.index("# Identity")
    idx_skills = result.index("# Skills")
    idx_tools = result.index("# Available Tools")
    idx_memory = result.index("# Memory")
    idx_task = result.index("# Task Context")

    assert idx_system < idx_identity < idx_skills < idx_tools < idx_memory < idx_task
```

Replace `test_strip_trailing_newlines` (lines 195-206) with:

```python
def test_strip_trailing_newlines(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """TOML multi-line strings may have trailing newlines -- stripped."""
    result = builder.build(
        ctx, tool_registry,
        system_prompt="\nBase prompt\n",
        identity="\nMat Master\n",
    )
    assert "# System\n\nBase prompt\n\n---" in result
    assert "# Identity\n\nMat Master" in result
```

Add new test:

```python
def test_build_with_system_prompt_only(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """Passing system_prompt produces only the system section."""
    result = builder.build(ctx, tool_registry, system_prompt="Base persona.")
    assert "# System" in result
    assert "Base persona." in result
    assert "# Identity" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/core/test_context_builder.py -v`
Expected: FAIL — `system_prompt` parameter not accepted, mode_contract tests removed but code still has it

- [ ] **Step 3: Implement ContextBuilder changes**

Rewrite `matmaster/core/context_builder.py` to:

```python
"""ContextBuilder -- sectioned system prompt assembler.

Constructs the system prompt from multiple sources in a fixed order.
LLM prompt caching benefits from stable prefix, so high-frequency change
sections (task, memory) are placed last.

Section order: system_prompt -> identity -> skills -> tools -> memory -> task

All static text (system_prompt, identity) comes from the caller (toml config).
ContextBuilder has no default text of its own -- empty string means the
section is skipped entirely.
"""

from __future__ import annotations

from typing import Any

from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.context import PlaygroundContext


class ContextBuilder:
    """Sectioned system prompt assembler.

    Section order (fixed): system_prompt -> identity -> skills -> tools -> memory -> task
    LLM prompt caching benefits from stable prefix, so high-frequency change sections
    (task, memory) are placed last.

    All static text is caller-supplied. Empty string = section skipped.
    """

    SEPARATOR = "\n\n---\n\n"

    SECTION_ORDER = ("system_prompt", "identity", "skills", "tools", "memory", "task")

    def build(
        self,
        ctx: PlaygroundContext,
        tool_registry: ToolRegistry,
        *,
        system_prompt: str = "",
        identity: str = "",
        skill_registry: Any = None,
        memory_context: str | None = None,
        task_context: str | None = None,
        disabled_sections: set[str] | None = None,
    ) -> str:
        """Assemble system prompt from sections in fixed order.

        Args:
            ctx: PlaygroundContext from Playground.prepare().
            tool_registry: ToolRegistry with registered tools.
            system_prompt: Universal base text from _base.toml.
            identity: Identity text from toml developer_instructions.
            skill_registry: Optional skill registry with get_meta_info_context().
            memory_context: Optional memory/conversation summary text.
            task_context: Optional task description text.
            disabled_sections: Set of section names to skip.

        Returns:
            Assembled system prompt string with sections joined by SEPARATOR.
        """
        disabled = disabled_sections or set()

        section_builders: dict[str, str] = {}

        for section_name in self.SECTION_ORDER:
            if section_name in disabled:
                continue

            content = self._build_section(
                section_name,
                system_prompt=system_prompt,
                identity=identity,
                skill_registry=skill_registry,
                tool_registry=tool_registry,
                memory_context=memory_context,
                task_context=task_context,
            )

            if content:
                section_builders[section_name] = content

        return self.SEPARATOR.join(section_builders.values())

    def _build_section(
        self,
        name: str,
        *,
        system_prompt: str,
        identity: str,
        skill_registry: Any,
        tool_registry: ToolRegistry,
        memory_context: str | None,
        task_context: str | None,
    ) -> str:
        """Dispatch to the appropriate section builder."""
        if name == "system_prompt":
            return self._build_system_prompt(system_prompt)
        if name == "identity":
            return self._build_identity(identity)
        if name == "skills":
            return self._build_skills(skill_registry)
        if name == "tools":
            return self._build_tools(tool_registry)
        if name == "memory":
            return self._build_memory(memory_context)
        if name == "task":
            return self._build_task(task_context)
        return ""

    @staticmethod
    def _build_system_prompt(system_prompt: str) -> str:
        """Build the system prompt section. Empty string = skip."""
        text = system_prompt.strip()
        if not text:
            return ""
        return f"# System\n\n{text}"

    @staticmethod
    def _build_identity(identity: str) -> str:
        """Build the identity section. Empty string = skip."""
        text = identity.strip()
        if not text:
            return ""
        return f"# Identity\n\n{text}"

    @staticmethod
    def _build_skills(skill_registry: Any) -> str:
        """Build the skills section from skill registry."""
        if skill_registry is None:
            return ""
        method = getattr(skill_registry, "get_meta_info_context", None)
        if method is None:
            return ""
        context = method()
        if not context:
            return ""
        return f"# Skills\n\n{context}"

    @staticmethod
    def _build_tools(tool_registry: ToolRegistry) -> str:
        """Build the available tools section."""
        tools = tool_registry.all_tools
        if not tools:
            return ""
        lines = [f"- {tool.name}: {tool.description}" for tool in tools]
        return "# Available Tools\n\n" + "\n".join(lines)

    @staticmethod
    def _build_memory(memory_context: str | None) -> str:
        """Build the memory section. Returns empty string if no context."""
        if not memory_context:
            return ""
        return f"# Memory\n\n{memory_context}"

    @staticmethod
    def _build_task(task_context: str | None) -> str:
        """Build the task context section. Returns empty string if no context."""
        if not task_context:
            return ""
        return f"# Task Context\n\n{task_context}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/core/test_context_builder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/context_builder.py tests/matmaster/core/test_context_builder.py
git commit -m "feat: add system_prompt section to ContextBuilder, remove mode_contract"
```

### Task 4: Update Exp.build_runtime() call site

**Files:**
- Modify: `matmaster/core/exp.py:124-129`
- Test: `tests/matmaster/core/test_exp.py`

- [ ] **Step 1: Update test_exp.py**

In `tests/matmaster/core/test_exp.py`:

Replace `TestModeContractOverride` class (lines 349-372) with:

```python
class TestSystemPromptOverride:
    """system_prompt from config is forwarded to ContextBuilder.build()."""

    def test_system_prompt_from_config(self) -> None:
        exp = Exp(ExpConfig(
            name="test",
            system_prompt="Base persona text.",
            tools=ExpToolsConfig(builtin=[]),
        ))
        ctx = _make_ctx(with_llm=True)
        runtime = exp.build_runtime(ctx)

        assert "Base persona text." in runtime.spec.system_prompt

    def test_empty_system_prompt_skips_section(self) -> None:
        exp = Exp(ExpConfig(
            name="test",
            system_prompt="",
            tools=ExpToolsConfig(builtin=[]),
        ))
        ctx = _make_ctx(with_llm=True)
        runtime = exp.build_runtime(ctx)

        assert "# System" not in runtime.spec.system_prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/core/test_exp.py::TestSystemPromptOverride -v`
Expected: FAIL — `exp.py` still passes `mode_contract` to builder

- [ ] **Step 3: Update exp.py build_runtime call**

In `matmaster/core/exp.py`, change lines 124-129:

```python
        # 3. System prompt via ContextBuilder
        builder = ContextBuilder()
        system_prompt = builder.build(
            ctx, registry,
            system_prompt=self._config.system_prompt,
            identity=self._config.developer_instructions,
            skill_registry=getattr(self, "_skill_registry", None),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/core/test_exp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/exp.py tests/matmaster/core/test_exp.py
git commit -m "feat: wire system_prompt through Exp.build_runtime, remove mode_contract"
```

---

## Chunk 3: TOML + Devshell + Integration Tests

### Task 5: Update direct.toml — merge mode_contract into developer_instructions

**Files:**
- Modify: `matmaster/exps/direct.toml`
- Test: `tests/matmaster/integration/test_direct_toml_prompt.py`

- [ ] **Step 1: Write failing integration tests first**

In `tests/matmaster/integration/test_direct_toml_prompt.py`, replace `test_mode_contract_nonempty_and_direct` (lines 66-72) with:

```python
def test_mode_contract_removed():
    """mode_contract field no longer exists on ExpConfig."""
    cfg = load_exp_config("direct")
    assert not hasattr(cfg, "mode_contract")


def test_execution_mode_in_developer_instructions():
    """Former mode_contract content now lives in developer_instructions."""
    cfg = load_exp_config("direct")
    di = cfg.developer_instructions.lower()
    assert "direct execution mode" in di


def test_system_prompt_from_base():
    """system_prompt is loaded from _base.toml."""
    cfg = load_exp_config("direct")
    assert len(cfg.system_prompt.strip()) > 0
```

- [ ] **Step 2: Run tests to verify `test_execution_mode_in_developer_instructions` fails**

Run: `uv run pytest tests/matmaster/integration/test_direct_toml_prompt.py::test_execution_mode_in_developer_instructions -v`
Expected: FAIL — direct.toml does not yet contain "direct execution mode" in developer_instructions

- [ ] **Step 3: Update direct.toml**

Replace `matmaster/exps/direct.toml` content. Remove `mode_contract` block, append its content as `# Execution Mode` section to `developer_instructions`:

```toml
name = "direct"
mode = "direct"
max_turns = 200
guards = []

developer_instructions = '''
You are Mat Master, an autonomous agent for materials science and computational materials.
You operate on a remote compute node via session. All file operations happen in the remote workspace.

# Tool Usage
- Use read_file to read files, NOT cat/head/tail via execute_bash
- Use write_file to create or overwrite files, NOT echo/heredoc via execute_bash
- Use edit_file to modify files, NOT sed/awk via execute_bash
- Use glob to search file paths, NOT find/ls via execute_bash
- Use grep to search file content, NOT grep/rg via execute_bash
- Reserve execute_bash for shell commands, package management, running scripts, and system operations
- Use task tools (task_create, task_list, task_update, task_complete) to track multi-step work

# Behavior
- Read and understand existing files before modifying them
- Avoid over-engineering. Only make changes directly needed for the task
- Do not propose modifications to code you have not read
- If blocked on an approach, consider alternatives rather than retrying the same action
- Do not introduce security vulnerabilities. Never expose secrets, keys, or tokens in files

# Output Style
- Be concise and direct. Lead with the action or result, not the reasoning
- Focus on: decisions needing user input, status updates, errors and blockers
- Avoid lengthy explanations unless the user asks for detail

# Remote Environment
- All file paths are on the remote compute node, not your local machine
- The workspace directory is your primary working area
- Long-running computations should be tracked via task tools
- Network access may be restricted; do not assume internet availability

# Execution Mode
- You are in direct execution mode. Complete the user's task directly using available tools
- Execute actions immediately without asking for confirmation unless the task is ambiguous
'''

[tools]
builtin = [
    "execute_bash",
    "list_dir",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "task_create",
    "task_get",
    "task_list",
    "task_update",
    "task_complete",
]
mcp = "*"

[skills]
enabled = true
skills_root = "playground/mat_master/skills"
cache_dir = "matmaster/cache"
config_dir = "matmaster_config"
mcp_config_file = "mcp_config.json"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/integration/test_direct_toml_prompt.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/exps/direct.toml tests/matmaster/integration/test_direct_toml_prompt.py
git commit -m "refactor: merge mode_contract into developer_instructions in direct.toml"
```

### Task 6: Update devshell config and runner

**Files:**
- Modify: `matmaster/devshell/config.py:22-29`
- Modify: `matmaster/devshell/runner.py:70-80`
- Test: `tests/matmaster/devshell/test_runner.py`

- [ ] **Step 1: Update test_runner.py**

In `tests/matmaster/devshell/test_runner.py`, replace `TestBuildExpConfig` class (lines 24-48) with:

```python
class TestBuildExpConfig:
    def test_system_prompt_default(self):
        """AgentConfig defaults system_prompt to empty string."""
        from matmaster.devshell.config import AgentConfig
        cfg = AgentConfig()
        assert cfg.system_prompt == ""

    def test_system_prompt_forwarded(self):
        """_build_exp_config uses explicit system_prompt when provided."""
        from matmaster.devshell.config import AgentConfig, DevConfig
        from matmaster.devshell.runner import DevRunner
        config = DevConfig(
            agent=AgentConfig(system_prompt="Custom prompt.")
        )
        exp_cfg = DevRunner._build_exp_config(config)
        assert exp_cfg.system_prompt == "Custom prompt."

    def test_system_prompt_fallback_to_base(self):
        """_build_exp_config calls load_base_system_prompt when system_prompt is empty."""
        from unittest.mock import patch
        from matmaster.devshell.config import DevConfig
        from matmaster.devshell.runner import DevRunner
        config = DevConfig()
        with patch("matmaster.devshell.runner.load_base_system_prompt", return_value="Mocked base") as mock_load:
            exp_cfg = DevRunner._build_exp_config(config)
        mock_load.assert_called_once()
        assert exp_cfg.system_prompt == "Mocked base"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/devshell/test_runner.py::TestBuildExpConfig -v`
Expected: FAIL — `AgentConfig` still has `mode_contract`, no `system_prompt`

- [ ] **Step 3: Update devshell config.py**

In `matmaster/devshell/config.py`, replace line 29:

```python
class AgentConfig(BaseModel):
    """Agent behavior settings."""

    name: str = "general"
    mode: str = "direct"
    max_turns: int = 20
    identity: str | None = None
    system_prompt: str = ""
```

- [ ] **Step 4: Update devshell runner.py**

In `matmaster/devshell/runner.py`, change `_build_exp_config` (lines 70-80):

```python
    @staticmethod
    def _build_exp_config(config: DevConfig) -> ExpConfig:
        """Convert DevConfig to ExpConfig."""
        from matmaster.config.loader import load_base_system_prompt

        system_prompt = config.agent.system_prompt
        if not system_prompt:
            system_prompt = load_base_system_prompt()

        return ExpConfig(
            name=config.agent.name,
            mode=config.agent.mode,
            max_turns=config.agent.max_turns,
            tools=ExpToolsConfig(builtin=config.tools.builtin),
            developer_instructions=config.agent.identity or "",
            system_prompt=system_prompt,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/devshell/test_runner.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/matmaster/ -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add matmaster/devshell/config.py matmaster/devshell/runner.py tests/matmaster/devshell/test_runner.py
git commit -m "feat: update devshell to use system_prompt, remove mode_contract"
```
