# Self-Describing Tool Protocol Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate tool metadata from 4 central lookup tables in ToolCompiler to self-describing properties on each tool, add dynamic description/prompt injection, and introduce ToolRunnerState to replace ReadTracker.

**Architecture:** Expand Tool Protocol with 12 new attributes + `describe()/prompt()` methods. Upgrade `BuiltinTool`, `LazyMCPTool`, `SkillTool`, and shared test doubles alongside the Protocol so `@runtime_checkable` checks do not break mid-migration. `ToolCompiler` and `ToolCatalog` use `getattr` fallbacks for minimal tools. `ToolRunnerState` replaces `ReadTracker`; `validate_input` reads runner_state in Phase 1, while `ReadTool.execute_with_context()` writes explicit `mark_read` signals in Phase 2 without changing `execute()`'s string-facing behavior.

**Tech Stack:** Python 3.10+, Pydantic, asyncio, pytest

**Spec:** `docs/specs/2026-04-03-self-describing-tool-protocol.md`

---

## Chunk 1: Foundation Types + Protocol + ABC

### Task 1: Create foundation type files

**Files:**
- Create: `matmaster/types/tool_desc_ctx.py`
- Create: `matmaster/types/tool_runner_state.py`

- [ ] **Step 1: Write tests for ToolDescriptionContext and ToolRunnerState**

Create `tests/matmaster/types/test_tool_desc_ctx.py`:
```python
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.topology import RuntimeTopology

def test_tool_description_context_is_frozen():
    topo = RuntimeTopology(session_kind="local", control_root="/c", workspace_root="/w")
    ctx = ToolDescriptionContext(session_kind="local", workspace_root="/w", topology=topo)
    assert ctx.session_kind == "local"
    assert ctx.workspace_root == "/w"
```

Create `tests/matmaster/types/test_tool_runner_state.py`:
```python
from matmaster.types.tool_runner_state import ToolRunnerState

def test_get_set():
    s = ToolRunnerState()
    assert s.get("k") is None
    assert s.get("k", 42) == 42
    s.set("k", "v")
    assert s.get("k") == "v"

def test_clear():
    s = ToolRunnerState()
    s.set("a", 1)
    s.clear()
    assert s.get("a") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/types/test_tool_desc_ctx.py tests/matmaster/types/test_tool_runner_state.py -v`
Expected: ImportError

- [ ] **Step 3: Implement ToolDescriptionContext**

Create `matmaster/types/tool_desc_ctx.py`:
```python
"""ToolDescriptionContext -- passed to tool.describe() and tool.prompt() for dynamic content."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matmaster.types.topology import RuntimeTopology


@dataclass(frozen=True)
class ToolDescriptionContext:
    """Context for dynamic tool descriptions and prompt injection.

    Constructed once per build_runtime() call. Passed to describe(ctx)
    and prompt(ctx) in ToolCatalog.build_definitions() and collect_prompts().
    """
    session_kind: str          # "local" | "ssh" | "docker"
    workspace_root: str
    topology: RuntimeTopology
```

- [ ] **Step 4: Implement ToolRunnerState**

Create `matmaster/types/tool_runner_state.py`:
```python
"""ToolRunnerState -- runner-level mutable shared state for cross-tool communication."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolRunnerState:
    """Runner-level mutable shared state. Tools read/write via exec_ctx.

    THREAD SAFETY CONTRACT:
    - runner_state MUST only be accessed in the asyncio event loop thread,
      i.e., AFTER ``await asyncio.to_thread()`` returns.
    - NEVER access runner_state inside sync ``_execute()`` methods or in
      any code running in the thread pool.
    - asyncio is cooperative single-threaded concurrency: between await
      points, no other coroutine runs, so dict reads/writes are atomic.
    """
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def clear(self) -> None:
        self.data.clear()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/types/test_tool_desc_ctx.py tests/matmaster/types/test_tool_runner_state.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add matmaster/types/tool_desc_ctx.py matmaster/types/tool_runner_state.py tests/matmaster/types/test_tool_desc_ctx.py tests/matmaster/types/test_tool_runner_state.py
git commit -m "feat: add ToolDescriptionContext and ToolRunnerState foundation types"
```

---

### Task 2: Expand Tool Protocol, ToolRegistry, and shared test doubles

**Files:**
- Modify: `matmaster/tools/tool_registry.py`
- Modify: `tests/matmaster/tools/conftest.py`
- Modify: `tests/conftest.py`

> IMPORTANT: Expanding a `@runtime_checkable` Protocol without first updating the shared mock tools will immediately break existing `assert isinstance(tool, Tool)` tests. Land the Protocol change together with the shared test-double updates. Do not create a standalone commit for Task 2.

- [ ] **Step 1: Write tests for `get_raw()` and update shared mocks plan**

Add to `tests/matmaster/tools/test_tool_registry.py`:
```python
from matmaster.tools.tool_registry import Tool, ToolRegistry

def test_get_raw_returns_tool():
    reg = ToolRegistry()
    tool = _FakeTool("test")
    reg.register(tool, source="builtin")
    assert reg.get_raw("test") is tool

def test_get_raw_returns_none():
    reg = ToolRegistry()
    assert reg.get_raw("missing") is None
```

In `tests/matmaster/tools/conftest.py` and `tests/conftest.py`, plan to add conservative metadata defaults plus no-op `describe()` / `prompt()` methods to the shared `MockTool` / `MockAsyncTool` classes so protocol-focused tests remain usable during the migration.

- [ ] **Step 2: Run targeted tests to verify `get_raw()` is missing**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_tool_registry.py -k "get_raw" -v`
Expected: AttributeError: 'ToolRegistry' object has no attribute 'get_raw'

- [ ] **Step 3: Expand Tool Protocol and add get_raw to ToolRegistry**

Edit `matmaster/tools/tool_registry.py`:
1. Add imports for new types at top
2. Add `EffectLevel` type alias
3. Expand `Tool` Protocol with 12 new properties + `describe()` + `prompt()` methods
4. Add `get_raw(name)` method to `ToolRegistry`
5. Update shared test doubles in `tests/matmaster/tools/conftest.py` and `tests/conftest.py` with conservative default metadata plus `describe()` / `prompt()` implementations

The Protocol additions (all with `...` body, indicating Protocol members):
- `describe(self, ctx: ToolDescriptionContext) -> str`
- `prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None`
- `resource_claims` property -> `tuple[ResourceClaim, ...]`
- `capabilities` property -> `frozenset[str]`
- `effect_level` property -> `EffectLevel`
- `fast_path_eligible` property -> `bool`
- `max_result_chars` property -> `int`
- `plane` property -> `ToolPlane`
- `state_mode` property -> `Literal["stateless", "persistent"]`
- `stop_mode` property -> `Literal["cancellable", "best_effort", "non_cancellable"]`
- `exposed_to_model` property -> `bool`

Add to ToolRegistry:
```python
def get_raw(self, name: str) -> Tool | None:
    """Return the raw Tool instance by name, or None."""
    return self._tools.get(name)
```

- [ ] **Step 4: Run targeted registry/protocol-helper tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_tool_registry.py tests/matmaster/test_validation.py -k "get_raw or Tool" -v`
Expected: registry and shared mock tool tests pass; builtin/external tool runtime Protocol conformance is completed in Tasks 3 and 10

- [ ] **Step 5: Do not commit yet**

Fold these changes into Task 3's commit so the first protocol-expansion commit also updates real tool implementations.

---

### Task 3: Expand BuiltinTool ABC

**Files:**
- Modify: `matmaster/tools/builtin/base.py`
- Modify: `matmaster/tools/lazy_mcp.py`
- Modify: `matmaster/tools/skill_tool.py`
- Modify: `tests/matmaster/tools/test_builtin_base.py`
- Modify: `tests/matmaster/tools/test_lazy_mcp.py`
- Modify: `tests/test_skill_tool.py`

> IMPORTANT: Because `Tool` is `@runtime_checkable`, this task also adds temporary protocol shims to `LazyMCPTool` and `SkillTool` so existing `isinstance(tool, Tool)` assertions keep working before Task 10 performs the full metadata refactor.

- [ ] **Step 1: Write test for ABC defaults**

Add to `tests/matmaster/tools/test_builtin_base.py`:
```python
from matmaster.types.topology import ToolPlane

def test_builtin_default_metadata():
    """BuiltinTool ABC provides conservative defaults for all new Protocol attrs."""
    tool = ConcreteBuiltinTool()  # existing test helper
    assert tool.resource_claims == ()
    assert tool.capabilities == frozenset()
    assert tool.effect_level == "local_mutation"
    assert tool.fast_path_eligible is False
    assert tool.max_result_chars == 0
    assert tool.plane == ToolPlane.CONTROL_PLANE
    assert tool.state_mode == "stateless"
    assert tool.stop_mode == "cancellable"
    assert tool.exposed_to_model is True

def test_builtin_describe_returns_description():
    tool = ConcreteBuiltinTool()
    assert tool.describe(None) == tool.description

def test_builtin_prompt_returns_none():
    tool = ConcreteBuiltinTool()
    assert tool.prompt() is None

@pytest.mark.asyncio
async def test_builtin_execute_with_context_delegates():
    tool = ConcreteBuiltinTool()
    result = await tool.execute_with_context({"x": 1}, None)
    assert "concrete" in str(result).lower() or result is not None

@pytest.mark.asyncio
async def test_builtin_validate_input_accepts_runner_state():
    """Base validate_input accepts runner_state kwarg and returns None."""
    from matmaster.types.tool_runner_state import ToolRunnerState
    tool = ConcreteBuiltinTool()
    result = await tool.validate_input({"x": 1}, runner_state=ToolRunnerState())
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_builtin_base.py -k "metadata or describe or prompt or execute_with_context" -v`
Expected: AttributeError

- [ ] **Step 3: Implement ABC expansion**

Edit `matmaster/tools/builtin/base.py`:
1. Add imports for `ResourceClaim`, `ToolPlane`, `ToolDescriptionContext`, `ToolRunnerState`
2. Add `EffectLevel` type alias import
3. Add ClassVar defaults for all new metadata
4. Add `describe()`, `prompt()`, `execute_with_context()` default implementations
5. Extend `validate_input` signature to accept optional `runner_state`

Also edit `matmaster/tools/lazy_mcp.py` and `matmaster/tools/skill_tool.py`:
1. Add conservative metadata attributes required by the expanded Protocol
2. Add minimal `describe()` / `prompt()` / `execute_with_context()` shims that preserve current behavior
3. Keep `tool_runtime_meta` in `LazyMCPTool` for now; full removal happens in Task 10

Key changes:
- `description: ClassVar[str]` stays as ClassVar (no rename to `_description`)
- `describe(self, ctx)` returns `self.description` by default
- `execute_with_context(self, arguments, exec_ctx)` delegates to `_execute` via `to_thread`
- `validate_input(self, arguments, runner_state=None)` adds optional runner_state

- [ ] **Step 4: Run protocol-conformance tests to verify they pass**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_builtin_base.py tests/matmaster/tools/test_lazy_mcp.py tests/test_skill_tool.py -k "Tool or protocol or metadata" -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/tool_registry.py tests/matmaster/tools/test_tool_registry.py tests/matmaster/tools/conftest.py tests/conftest.py matmaster/tools/builtin/base.py matmaster/tools/lazy_mcp.py matmaster/tools/skill_tool.py tests/matmaster/tools/test_builtin_base.py tests/matmaster/tools/test_lazy_mcp.py tests/test_skill_tool.py
git commit -m "feat: expand Tool Protocol and add self-describing compatibility shims"
```

---

### Task 4: Update ToolExecutionContext and ToolInstance types

**Files:**
- Modify: `matmaster/types/tool_spec.py`

- [ ] **Step 1: Add runner_state to ToolExecutionContext**

Edit `matmaster/types/tool_spec.py`:
1. Import `ToolRunnerState`
2. Add `runner_state: ToolRunnerState | None = None` field to `ToolExecutionContext`
3. Change `ToolInstance.input_validator` type from `Callable[[dict[str, Any]], ...]` to `Callable[[dict[str, Any], ToolRunnerState | None], ...]`

- [ ] **Step 2: Run existing tool_spec tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/types/test_tool_spec.py -v`
Expected: all passed

- [ ] **Step 3: Commit**

```bash
git add matmaster/types/tool_spec.py
git commit -m "feat: extend ToolExecutionContext with runner_state, update input_validator signature"
```

---

## Chunk 2: Builtin Tool Metadata Migration

### Task 5: Migrate ReadTool (complex: execute_with_context + mark_read)

**Files:**
- Modify: `matmaster/tools/builtin/read_tool.py`

- [ ] **Step 1: Write test for mark_read via runner_state**

Add to `tests/matmaster/tools/test_read_tool.py`:
```python
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext

@pytest.mark.asyncio
async def test_read_tool_marks_runner_state_on_success(mock_session: MagicMock):
    """Successful full read should mark file in runner_state."""
    mock_session.is_file.return_value = True
    mock_session.read_file.return_value = "line1\nline2"
    tool = ReadTool(session=mock_session)
    state = ToolRunnerState()
    exec_ctx = ToolExecutionContext(runner_state=state)
    result = await tool.execute_with_context(
        {"file_path": "/workspace/test.py"}, exec_ctx
    )
    assert "/workspace/test.py" in state.get("read_files", set())

@pytest.mark.asyncio
async def test_read_tool_does_not_mark_on_overlimit(mock_session: MagicMock):
    """Overlimit read (>2000 lines) should NOT mark file in runner_state."""
    mock_session.is_file.return_value = True
    mock_session.read_file.return_value = "\n".join(f"line{i}" for i in range(3000))
    tool = ReadTool(session=mock_session)
    state = ToolRunnerState()
    exec_ctx = ToolExecutionContext(runner_state=state)
    result = await tool.execute_with_context(
        {"file_path": "/workspace/big.py"}, exec_ctx
    )
    assert "/workspace/big.py" not in state.get("read_files", set())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_read_tool.py -k "runner_state" -v`
Expected: fail (no execute_with_context override yet)

- [ ] **Step 3: Implement ReadTool migration**

Edit `matmaster/tools/builtin/read_tool.py`:
1. Add metadata ClassVars from spec Section 5 metadata table
2. Keep `tracker=None` parameter in `__init__` temporarily (accept but ignore) so current call sites keep working until Task 13
3. Refactor the read core into an internal helper that can emit explicit `mark_read` signals
4. Keep `execute()`'s public contract unchanged: direct callers still receive the same string/error output shape as today
5. Add `execute_with_context()` that consumes the internal helper result, checks `tr.meta.get("mark_read")`, and writes to `runner_state`
6. Remove `ReadTracker` side effects from core logic; delete the import only when the compatibility parameter is no longer needed

- [ ] **Step 4: Run all ReadTool tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_read_tool.py -v`
Expected: all passed (existing tests + new runner_state tests)

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/read_tool.py tests/matmaster/tools/test_read_tool.py
git commit -m "feat: migrate ReadTool to self-describing metadata with runner_state"
```

---

### Task 6: Migrate WriteTool (validate_input with runner_state)

**Files:**
- Modify: `matmaster/tools/builtin/write_tool.py`

- [ ] **Step 1: Write test for read-before-modify via runner_state**

Add to `tests/matmaster/tools/test_write_tool.py`:
```python
from matmaster.types.tool_runner_state import ToolRunnerState

@pytest.mark.asyncio
async def test_write_blocks_unread_file_via_runner_state(mock_session: MagicMock):
    """write_file on existing unread file denied via runner_state."""
    mock_session.path_exists.return_value = True
    tool = WriteTool(session=mock_session, workdir="/workspace")
    state = ToolRunnerState()
    decision = await tool.validate_input(
        {"file_path": "/workspace/existing.py", "content": "x"}, runner_state=state
    )
    assert decision is not None and decision.decision == "deny"

@pytest.mark.asyncio
async def test_write_allows_read_file_via_runner_state(mock_session: MagicMock):
    """write_file on read file allowed via runner_state."""
    mock_session.path_exists.return_value = True
    tool = WriteTool(session=mock_session, workdir="/workspace")
    state = ToolRunnerState()
    state.set("read_files", {"/workspace/existing.py"})
    decision = await tool.validate_input(
        {"file_path": "/workspace/existing.py", "content": "x"}, runner_state=state
    )
    assert decision is None
```

- [ ] **Step 2: Implement WriteTool migration**

Edit `matmaster/tools/builtin/write_tool.py`:
1. Add metadata ClassVars
2. Keep `tracker=None` parameter in `__init__` (accept but ignore) to avoid breaking Exp until Task 14. Remove `ReadTracker` usage inside methods.
3. Rewrite `validate_input(self, arguments, runner_state=None)` per spec Section 5: path boundary check retained, read-before-modify uses `runner_state` instead of `self._tracker`

- [ ] **Step 3: Run all WriteTool tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_write_tool.py -v`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add matmaster/tools/builtin/write_tool.py tests/matmaster/tools/test_write_tool.py
git commit -m "feat: migrate WriteTool to self-describing metadata with runner_state"
```

---

### Task 7: Migrate EditTool (validate_input with runner_state)

**Files:**
- Modify: `matmaster/tools/builtin/edit_tool.py`

- [ ] **Step 1: Write test for read-before-modify via runner_state**

Add to `tests/matmaster/tools/test_edit_tool.py`:
```python
from matmaster.types.tool_runner_state import ToolRunnerState

@pytest.mark.asyncio
async def test_edit_blocks_unread_file_via_runner_state(mock_session: MagicMock):
    tool = EditTool(session=mock_session, workdir=Path("/workspace"))
    state = ToolRunnerState()
    decision = await tool.validate_input(
        {"file_path": "/workspace/f.py", "old_str": "a", "new_str": "b"},
        runner_state=state,
    )
    assert decision is not None and decision.decision == "deny"
    assert "must be read" in decision.reason.lower()
```

- [ ] **Step 2: Implement EditTool migration**

Edit `matmaster/tools/builtin/edit_tool.py`:
1. Add metadata ClassVars
2. Keep `tracker=None` parameter in `__init__` (accept but ignore) to avoid breaking Exp until Task 14. Remove `ReadTracker` usage inside methods.
3. Extend `validate_input(self, arguments, runner_state=None)` to add read-before-modify check using `runner_state` (replacing ReadBeforeModifyGuard)

- [ ] **Step 3: Run all EditTool tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_edit_tool.py -v`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add matmaster/tools/builtin/edit_tool.py tests/matmaster/tools/test_edit_tool.py
git commit -m "feat: migrate EditTool to self-describing metadata with runner_state"
```

---

### Task 8: Migrate BashTool (prompt override)

**Files:**
- Modify: `matmaster/tools/builtin/bash_tool.py`
- Modify: `tests/matmaster/tools/test_tool_descriptions.py`

- [ ] **Step 1: Write test for prompt()**

Add to `tests/matmaster/tools/test_bash_tool.py`:
```python
def test_bash_tool_prompt_returns_usage_guidance():
    tool = BashTool()
    p = tool.prompt()
    assert p is not None
    assert "read_file" in p
    assert "execute_bash" not in p or "cat" in p

def test_bash_tool_metadata():
    from matmaster.types.topology import ToolPlane
    tool = BashTool()
    assert tool.plane == ToolPlane.SESSION_SHELL
    assert tool.effect_level == "local_mutation"
    assert tool.max_result_chars == 12000
```

- [ ] **Step 2: Implement BashTool migration**

Edit `matmaster/tools/builtin/bash_tool.py`:
1. Add metadata ClassVars per spec
2. Add `prompt()` method that returns bash usage guidance (moved from static `description`)
3. Simplify `description` to just "Execute a bash command in the session shell."

Edit `tests/matmaster/tools/test_tool_descriptions.py`:
1. Move bash routing assertions from `BashTool.description` to `BashTool().prompt()`
2. Keep dedicated tool `description` routing assertions on `ReadTool` / `WriteTool` / `EditTool` / `GlobTool` / `GrepTool`

- [ ] **Step 3: Run all BashTool tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_bash_tool.py tests/matmaster/tools/test_tool_descriptions.py -v`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add matmaster/tools/builtin/bash_tool.py tests/matmaster/tools/test_bash_tool.py
git commit -m "feat: migrate BashTool to self-describing metadata with prompt()"
```

---

### Task 9: Migrate remaining 11 simple tools

These tools only need metadata ClassVars added — no behavior changes.

**Files:**
- Modify: `matmaster/tools/builtin/listdir_tool.py`
- Modify: `matmaster/tools/builtin/glob_tool.py`
- Modify: `matmaster/tools/builtin/grep_tool.py`
- Modify: `matmaster/tools/builtin/web_search_tool.py`
- Modify: `matmaster/tools/builtin/web_fetch_tool.py`
- Modify: `matmaster/tools/builtin/spawn_tool.py`
- Modify: `matmaster/tools/builtin/monitor_job/_tool.py`
- Modify: `matmaster/tools/builtin/task/task_create.py`
- Modify: `matmaster/tools/builtin/task/task_get.py`
- Modify: `matmaster/tools/builtin/task/task_list.py`
- Modify: `matmaster/tools/builtin/task/task_update.py`
- Modify: `matmaster/tools/builtin/task/task_complete.py`

- [ ] **Step 1: Write test verifying all builtins have required metadata**

Create `tests/matmaster/tools/test_builtin_metadata.py`:
```python
"""Verify all builtin tools declare self-describing metadata."""
import pytest
from matmaster.tools.builtin import (
    BashTool, ListDirTool, ReadTool, WriteTool, EditTool,
    GlobTool, GrepTool, WebSearchTool, WebFetchTool,
    TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool, TaskCompleteTool,
)
from matmaster.tools.builtin.monitor_job import MonitorJobTool
from matmaster.tools.builtin.spawn_tool import SpawnTool
from matmaster.types.topology import ToolPlane

ALL_BUILTINS = [
    BashTool, ListDirTool, ReadTool, WriteTool, EditTool,
    GlobTool, GrepTool, WebSearchTool, WebFetchTool,
    TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool, TaskCompleteTool,
    SpawnTool, MonitorJobTool,
]

@pytest.mark.parametrize("cls", ALL_BUILTINS, ids=lambda c: c.name)
def test_builtin_has_required_metadata(cls):
    assert isinstance(cls.resource_claims, tuple)
    assert isinstance(cls.capabilities, frozenset)
    assert cls.effect_level in ("none", "local_mutation", "external_effect")
    assert isinstance(cls.fast_path_eligible, bool)
    assert isinstance(cls.max_result_chars, int)
    assert isinstance(cls.plane, ToolPlane)
    assert cls.state_mode in ("stateless", "persistent")
    assert cls.stop_mode in ("cancellable", "best_effort", "non_cancellable")
```

- [ ] **Step 2: Run test to verify it fails for unmigrated tools**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_builtin_metadata.py -v`
Expected: some fail (tools without explicit plane/effect_level will use ABC defaults which may not match expected values)

- [ ] **Step 3: Add metadata ClassVars to each tool**

For each tool, add the ClassVars from the spec Section 5 Full Metadata Table. Pattern for each file:

```python
# Add after json_schema ClassVar:
resource_claims: ClassVar = (ResourceClaim(resource="...", mode="..."),)
capabilities: ClassVar = frozenset({"..."})
effect_level: ClassVar = "..."
fast_path_eligible: ClassVar = True/False
max_result_chars: ClassVar = N
plane: ClassVar = ToolPlane.XXX
# state_mode and stop_mode only if != "stateless"/"cancellable" (ABC defaults)
```

Add necessary imports (`ResourceClaim`, `ToolPlane`) to each file.

Refer to spec Section 5 Full Metadata Table for exact values per tool.

- [ ] **Step 4: Run full test suite for all builtin tools**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_builtin_metadata.py tests/matmaster/tools/ -v --no-header -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/ tests/matmaster/tools/test_builtin_metadata.py
git commit -m "feat: migrate all 16 builtin tools to self-describing metadata"
```

---

## Chunk 3: External Tools + Compiler + Catalog + Runner

### Task 10: Finalize LazyMCPTool and SkillTool adaptation

**Files:**
- Modify: `matmaster/tools/lazy_mcp.py`
- Modify: `matmaster/tools/skill_tool.py`

- [ ] **Step 1: Write test for LazyMCPTool Protocol compliance**

Add to `tests/matmaster/tools/test_lazy_mcp.py`:
```python
def test_lazy_mcp_tool_has_protocol_properties():
    tool = LazyMCPTool(
        server_name="s", tool_name="t", remote_tool_name="t",
        description="d", input_schema={}, connector=None,
        runtime_meta={"plane": "external_service", "effect_level": "external_effect"},
    )
    assert tool.plane == ToolPlane.EXTERNAL_SERVICE
    assert tool.effect_level == "external_effect"
    assert tool.stop_mode == "best_effort"
    assert tool.describe(None) == "d"
    assert tool.prompt() is None
```

- [ ] **Step 2: Implement LazyMCPTool adaptation**

Edit `matmaster/tools/lazy_mcp.py`:
1. In `__init__`, expand `runtime_meta` dict into typed attributes (per spec Section 6)
2. Add `@property` for each new Protocol attr: `resource_claims`, `capabilities`, `effect_level`, `fast_path_eligible`, `max_result_chars`, `plane`, `state_mode`, `stop_mode`, `exposed_to_model`
3. Add `describe(ctx)`, `prompt(ctx)`, `execute_with_context(args, exec_ctx)` methods
4. Remove the temporary compatibility shim added in Task 3 and delete `self.tool_runtime_meta`
5. Add `_parse_claims()` helper function

- [ ] **Step 3: Implement SkillTool adaptation**

Edit `matmaster/tools/skill_tool.py`:
1. Add class-level attributes for all Protocol properties (fixed values per spec Section 6)
2. Add `describe(ctx)`, `prompt(ctx)`, `execute_with_context(args, exec_ctx)` methods

- [ ] **Step 4: Run tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_lazy_mcp.py tests/test_skill_tool.py tests/matmaster/tools/test_skill_tool_callback.py tests/matmaster/tools/test_skill_meta_extras.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/lazy_mcp.py matmaster/tools/skill_tool.py tests/matmaster/tools/test_lazy_mcp.py
git commit -m "feat: adapt LazyMCPTool and SkillTool to expanded Protocol"
```

---

### Task 11: Simplify ToolCompiler + Update ToolCatalog (atomic)

> IMPORTANT: ToolCatalog imports `BUILTIN_CLAIMS`, `BUILTIN_META` from ToolCompiler.
> These two files MUST be updated in the same commit to avoid import breakage.

**Files:**
- Modify: `matmaster/tools/tool_compiler.py`
- Modify: `matmaster/tools/tool_catalog.py`

- [ ] **Step 1: Write tests for both compiler and catalog changes**

Add to `tests/matmaster/tools/test_tool_compiler.py`:
```python
class _SelfDescribingTool(_FakeTool):
    """Tool that declares its own metadata."""
    resource_claims = (ResourceClaim(resource="workspace", mode="shared_read"),)
    capabilities = frozenset({"workspace.read"})
    effect_level = "none"
    fast_path_eligible = True
    max_result_chars = 12000
    plane = ToolPlane.SESSION_FS
    state_mode = "stateless"
    stop_mode = "cancellable"
    exposed_to_model = True

def test_compile_self_describing_tool():
    compiler = ToolCompiler()
    tool = _SelfDescribingTool("my_read")
    instance = compiler.compile(tool, _make_topology(), source="builtin")
    assert instance.tool_binding.plane == ToolPlane.SESSION_FS
    assert instance.tool_spec.effect_level == "none"
    assert instance.tool_spec.fast_path_eligible is True
    assert instance.tool_spec.max_result_chars == 12000

def test_compile_minimal_tool_uses_defaults():
    """_FakeTool has no metadata — compiler uses getattr defaults."""
    compiler = ToolCompiler()
    instance = compiler.compile(_FakeTool("unknown"), _make_topology(), source="mcp")
    assert instance.tool_binding.plane == ToolPlane.CONTROL_PLANE
    assert instance.tool_spec.effect_level == "local_mutation"
    assert instance.tool_spec.fast_path_eligible is False
```

Add to `tests/matmaster/tools/test_tool_catalog.py`:
```python
def test_build_definitions_with_ctx_uses_describe_when_available():
    # Create a tool with describe() that returns dynamic description
    # Verify build_definitions(ctx) uses describe(ctx) result

def test_build_definitions_with_ctx_falls_back_for_minimal_tool():
    # Register a minimal tool with no describe()
    # Verify build_definitions(ctx) falls back to the compiled static description

def test_collect_prompts_gathers_non_none_and_skips_missing_prompt():
    # Create tools where one returns prompt, another returns None,
    # and a minimal tool has no prompt() method at all
    # Verify collect_prompts() joins only available non-None prompt strings
```

- [ ] **Step 2: Rewrite ToolCompiler**

Edit `matmaster/tools/tool_compiler.py`:
1. Delete `BUILTIN_CLAIMS`, `BUILTIN_META`, `BUILTIN_CAPABILITIES`, `BUILTIN_STOP_MODES` dicts
2. Rewrite `compile()` per spec Section 7: use `getattr(tool, attr, default)` for all metadata
3. Keep topology-dependent relaxation for list_dir/glob/grep
4. Keep hasattr fallback for `execute_with_context`

- [ ] **Step 3: Update ToolCatalog**

Edit `matmaster/tools/tool_catalog.py`:
1. Remove imports of `BUILTIN_CLAIMS`, `BUILTIN_META` from `tool_compiler`
2. Update `build_definitions(self, ctx=None)` per spec Section 8
3. Add `collect_prompts(self, ctx=None)` per spec Section 8
4. Use `self._registry.get_raw(name)` instead of `self._registry._tools[name]`
5. In both methods, use `getattr(raw_tool, "describe", None)` / `getattr(raw_tool, "prompt", None)` so minimal tools still work when `ctx` is provided

- [ ] **Step 4: Update existing compiler tests**

Update `tests/matmaster/tools/test_tool_compiler.py`:
- Tests that create `_FakeTool("execute_bash")` and expect bash-specific metadata: change to use `_SelfDescribingTool` or accept getattr defaults
- The key shift: compiler no longer knows what "execute_bash" means — the tool itself declares it

Update `tests/matmaster/tools/test_tool_catalog.py`:
- Update any tests that use `tool_runtime_meta` dict pattern — replace with Protocol properties on test tools

- [ ] **Step 5: Run all compiler and catalog tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/tools/test_tool_compiler.py tests/matmaster/tools/test_tool_catalog.py -v`
Expected: all passed

- [ ] **Step 6: Commit (atomic: compiler + catalog together)**

```bash
git add matmaster/tools/tool_compiler.py matmaster/tools/tool_catalog.py tests/matmaster/tools/test_tool_compiler.py tests/matmaster/tools/test_tool_catalog.py
git commit -m "feat: simplify ToolCompiler + update ToolCatalog (atomic migration)"
```

---

### Task 12: Update FullToolRunner with ToolRunnerState

**Files:**
- Modify: `matmaster/core/tool_runner.py`
- Modify: `tests/matmaster/core/test_full_tool_runner.py`

- [ ] **Step 1: Write tests for runner_state injection and update validator helpers**

Add to `tests/matmaster/core/test_full_tool_runner.py`:
```python
from matmaster.types.tool_runner_state import ToolRunnerState

def test_full_tool_runner_has_state():
    state = ToolRunnerState()
    runner = FullToolRunner(..., state=state)
    assert runner.state is state

def test_full_tool_runner_creates_default_state():
    runner = FullToolRunner(...)  # no state param
    assert runner.state is not None
```

Also update the existing `_deny_validator`, `_exploding_validator`, and `_allow_validator` helpers in this file to accept a second positional argument `runner_state` so they match the `ToolInstance.input_validator(args, runner_state)` signature introduced in Task 4.

- [ ] **Step 2: Implement FullToolRunner changes**

Edit `matmaster/core/tool_runner.py`:
1. Add `state: ToolRunnerState | None = None` to `FullToolRunner.__init__`
2. Add `self._state = state or ToolRunnerState()` and `state` property
3. In `_execute_one()`, construct `exec_ctx` with `runner_state=self._state`
4. In `execute_batch()` Phase 1, pass `self._state` to `instance.input_validator(effective_args, self._state)`

- [ ] **Step 3: Run all runner tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/core/test_full_tool_runner.py tests/matmaster/core/test_tool_runner.py -v`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add matmaster/core/tool_runner.py tests/matmaster/core/test_full_tool_runner.py
git commit -m "feat: integrate ToolRunnerState into FullToolRunner"
```

---

## Chunk 4: Exp Integration + Cleanup

### Task 13: Update Exp (remove ReadTracker, add prompt/state)

**Files:**
- Modify: `matmaster/core/exp.py`
- Modify: `tests/matmaster/core/test_exp.py`

- [ ] **Step 1: Update Exp.build_runtime() and Exp tests**

Edit `matmaster/core/exp.py`:
1. In `_init_builtin_tools()`: remove `tracker = ReadTracker()`, `self._read_tracker = tracker`, remove tracker params from ReadTool/WriteTool/EditTool construction (now safe — tools accept but ignore tracker=None since Tasks 5-7)
2. Remove `'read_tracker': self._read_tracker` from any runtime config dict (around line 320)
3. In `build_runtime()`: create `ToolDescriptionContext`, call `catalog.collect_prompts(desc_ctx)`, append to system_prompt
4. In `build_runtime()`: create `ToolRunnerState()`, pass to `FullToolRunner(state=runner_state)`, register `runner_state.clear` as cleanup
5. Remove `ReadBeforeModifyGuard` injection from guards setup (around line 287-292)

Edit `tests/matmaster/core/test_exp.py`:
1. Replace the current `test_read_tracker_cleanup_registered` expectation with a `ToolRunnerState.clear` cleanup assertion
2. Update any expectations that still reference `spec.read_tracker` or `exp._read_tracker`

- [ ] **Step 2: Run Exp tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/core/test_exp.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_exp_skills.py -v`
Expected: all passed

- [ ] **Step 3: Commit**

```bash
git add matmaster/core/exp.py tests/matmaster/core/test_exp.py
git commit -m "feat: integrate ToolRunnerState and prompt injection into Exp"
```

---

### Task 14: Update AgentKernel

**Files:**
- Modify: `matmaster/core/agent.py`

- [ ] **Step 1: Update build_definitions call sites**

Edit `matmaster/core/agent.py`:
1. Find all `build_definitions()` calls
2. Construct `ToolDescriptionContext` from `spec.runtime_topology` and pass to `build_definitions(desc_ctx)`

- [ ] **Step 2: Run kernel tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -v`
Expected: all passed

- [ ] **Step 3: Commit**

```bash
git add matmaster/core/agent.py
git commit -m "feat: pass ToolDescriptionContext to build_definitions in kernel"
```

---

### Task 15: Delete ReadTracker and ReadBeforeModifyGuard

**Files:**
- Delete: `matmaster/tools/builtin/read_tracker.py`
- Modify: `matmaster/core/guard_pipeline.py`
- Modify: `matmaster/tools/builtin/__init__.py`
- Modify: `tests/matmaster/core/test_guard_pipeline.py`
- Delete: `tests/matmaster/tools/test_read_tracker.py`

- [ ] **Step 1: Remove ReadBeforeModifyGuard from guard_pipeline.py**

Edit `matmaster/core/guard_pipeline.py`:
1. Delete `ReadBeforeModifyGuard` class
2. Remove its import from any `__init__.py` or export

- [ ] **Step 2: Delete read_tracker.py and its test**

```bash
rm matmaster/tools/builtin/read_tracker.py
rm tests/matmaster/tools/test_read_tracker.py
```

- [ ] **Step 3: Remove ReadTracker from builtin __init__.py**

Edit `matmaster/tools/builtin/__init__.py`: remove `ReadTracker` from imports/exports.

- [ ] **Step 4: Update guard_pipeline tests**

Edit `tests/matmaster/core/test_guard_pipeline.py`:
1. Remove all `ReadBeforeModifyGuard` test cases
2. Remove `ReadTracker` import

- [ ] **Step 5: Run targeted removal tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/core/test_guard_pipeline.py tests/matmaster/tools/test_read_tool.py tests/matmaster/tools/test_write_tool.py tests/matmaster/tools/test_edit_tool.py -v`
Expected: all passed; remaining repo-wide cleanup is finished in Task 16

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/guard_pipeline.py matmaster/tools/builtin/__init__.py tests/matmaster/core/test_guard_pipeline.py matmaster/tools/builtin/read_tracker.py tests/matmaster/tools/test_read_tracker.py
git commit -m "feat: delete ReadTracker and ReadBeforeModifyGuard, replaced by ToolRunnerState"
```

---

### Task 16: Update remaining test files and type cleanup

**Files:**
- Modify or Delete: `tests/matmaster/core/test_builtin_claims.py` (imports BUILTIN_CLAIMS/META — DELETE this file, superseded by Task 9's test_builtin_metadata.py)
- Modify: `tests/matmaster/tools/test_builtin_validators.py` (if references tracker)
- Modify: `tests/matmaster/core/test_guard_injection.py` (if references ReadBeforeModifyGuard)
- Modify: `matmaster/types/runtime.py` (if has read_tracker field)
- Modify: `matmaster/tools/builtin/read_tool.py` (remove now-unused tracker=None param from __init__)
- Modify: `matmaster/tools/builtin/write_tool.py` (remove now-unused tracker=None param from __init__)
- Modify: `matmaster/tools/builtin/edit_tool.py` (remove now-unused tracker=None param from __init__)

- [ ] **Step 1: Search for ALL remaining references to deleted constructs**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "ReadTracker|ReadBeforeModifyGuard|BUILTIN_CLAIMS|BUILTIN_META|BUILTIN_CAPABILITIES|BUILTIN_STOP_MODES|tool_runtime_meta|read_tracker|_read_tracker" tests/ matmaster/
```

- [ ] **Step 2: Delete test_builtin_claims.py**

This file's entire premise is "compiler fills metadata from lookup tables by tool name". It uses `_MockTool` without self-describing metadata, and all assertions check lookup-table-derived values. Task 9's `test_builtin_metadata.py` now covers the same verification (all builtins declare correct metadata).

```bash
rm tests/matmaster/core/test_builtin_claims.py
```

- [ ] **Step 3: Remove tracker=None from ReadTool/WriteTool/EditTool __init__**

Now that Exp no longer passes tracker, remove the backward-compat param added in Tasks 5-7.

- [ ] **Step 4: Clean up type files**

Check `matmaster/types/runtime.py` and `matmaster/types/guards.py` for any `read_tracker` field references. Remove them.

- [ ] **Step 5: Fix all remaining references found in Step 1**

Update each file to remove imports and references to deleted constructs.

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m pytest tests/matmaster/ --no-header -q`
Expected: all passed, zero import errors

- [ ] **Step 7: Final commit**

```bash
git add tests/matmaster/core/test_builtin_claims.py tests/matmaster/tools/test_builtin_validators.py tests/matmaster/core/test_guard_injection.py matmaster/types/runtime.py matmaster/types/guards.py matmaster/tools/builtin/read_tool.py matmaster/tools/builtin/write_tool.py matmaster/tools/builtin/edit_tool.py
git commit -m "chore: clean up all references to deleted ReadTracker, lookup tables, and temp compat params"
```

---

## Verification

After all tasks complete:

```bash
# Full test suite
uv run python -m pytest tests/matmaster/ -v --tb=short

# Verify no remaining references to deleted constructs
rg -n "ReadTracker|BUILTIN_CLAIMS|BUILTIN_META|BUILTIN_CAPABILITIES|BUILTIN_STOP_MODES|tool_runtime_meta" matmaster/ tests/

# Verify ToolCompiler has no lookup tables
wc -l matmaster/tools/tool_compiler.py  # should be ~60 lines, down from ~180
```
