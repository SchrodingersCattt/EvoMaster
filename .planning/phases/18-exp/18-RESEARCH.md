# Phase 18: Exp 生命周期异步化 - Research

**Researched:** 2026-03-29
**Domain:** Python asyncio -- async lifecycle migration for Exp layer (assemble/build_runtime/run)
**Confidence:** HIGH

## Summary

Phase 18 is a mechanical async migration of the Exp layer's three-phase lifecycle. The core pattern has been established in Phases 12-17: sync methods become async def, bridge loops are removed, and callers are updated. Phase 18 applies this exact pattern to `matmaster/core/exp.py` (assemble, build_runtime, run) and `matmaster/tools/builtin/spawn_tool.py` (SpawnTool execute override).

The migration scope is small and well-bounded. `exp.py` has 497 lines total; the changes affect approximately 5 method signatures, 2 bridge loop removals, and 1 cleanup callback mechanism upgrade. `spawn_tool.py` at 141 lines needs its execute() overridden to directly await the async spawn_fn, bypassing the BuiltinTool base class `to_thread` pattern.

**Primary recommendation:** Follow the established Phase 14/17 migration pattern exactly. Change signatures to async def, remove bridge loops (asyncio.new_event_loop + run_until_complete), add await where needed. The service layer gets a temporary bridge extension (not a restructure -- that's Phase 19).

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** assemble() and build_runtime() both become async def. No async I/O currently, but unified interface for future MCP network initialization. Consistent with Protocol hard cut (no sync/async dual interfaces).
- **D-02:** _run_cleanup_callbacks() becomes async def. Uses inspect.iscoroutinefunction(cb) to detect async callbacks; sync callbacks called directly, async callbacks awaited. Type annotation: `list[Callable[[], None] | Callable[[], Coroutine]]`.
- **D-03:** agent_run_service.py gets temporary bridge extension. Existing bridge loop expanded to also cover build_runtime() call. Phase 19 restructures the service layer.
- **D-04:** spawn_fn closure becomes async def. _make_spawn_fn returns async callable. Internal await chain: child_exp.build_runtime() -> child_runtime.kernel.run(). Bridge loop removed.
- **D-05:** SpawnTool overrides async execute() directly, bypassing BuiltinTool's to_thread(_execute) pattern. SpawnTool is the only BuiltinTool needing native async execution. _execute() either retained with unused marker or deleted.
- **D-06:** Exp.run() becomes async def. Bridge loop removed. Direct await kernel.run(). Cleanup in async finally with await _run_cleanup_callbacks().

### Claude's Discretion
- _init_builtin_tools / _init_skill_tools / _init_mcp_tools internal helpers: whether to also make async (currently all sync operations; build_runtime() being async allows optional conversion)
- _resolve_compaction_llm: whether to make async (currently pure sync table lookup)
- Test migration scope: test_exp.py tests for run/build_runtime/assemble need async def conversion
- SpawnTool: _execute() handling after execute() override (keep empty impl vs delete)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EXPL-01 | Exp.assemble() becomes async def | Signature change only; no internal I/O. Tests: 6 assemble tests in test_exp.py need async |
| EXPL-02 | Exp.build_runtime() becomes async def | Signature change + `await self.assemble(ctx)` internally. 15+ test call sites need async. Service layer needs bridge extension |
| EXPL-03 | Exp.run() becomes async def, await kernel.run() | Remove 5-line bridge loop, direct await. 3 test_exp.py TestExpRun tests + 2 TestExpCleanup tests need async |
| EXPL-04 | SubAgent spawn complete async chain | spawn_fn becomes async, SpawnTool overrides execute(), spawn integration tests migrate to async |

</phase_requirements>

## Architecture Patterns

### Transformation Map

The following table shows the exact before/after for each method:

| Method | Before | After | Key Change |
|--------|--------|-------|------------|
| `Exp.assemble()` | `def assemble(ctx) -> AgentRuntimeSpec` | `async def assemble(ctx) -> AgentRuntimeSpec` | Signature only |
| `Exp.build_runtime()` | `def build_runtime(ctx, ...) -> AgentRuntime` | `async def build_runtime(ctx, ...) -> AgentRuntime` | `await self.assemble(ctx)` + cleanup type update |
| `Exp.run()` | `def run(ctx, task, ...) -> KernelResult` with bridge loop | `async def run(ctx, task, ...) -> KernelResult` with `await` | Remove 5-line bridge, await kernel.run() |
| `Exp._run_cleanup_callbacks()` | `def _run_cleanup_callbacks()` sync only | `async def _run_cleanup_callbacks()` with coroutine detection | `inspect.iscoroutinefunction` dispatch |
| `Exp._make_spawn_fn()` | Returns sync `spawn_fn` with bridge loop | Returns async `spawn_fn` with direct await | Remove bridge, await build_runtime + kernel.run |
| `SpawnTool.execute()` | Inherits `BuiltinTool.execute()` (to_thread) | Override `async def execute()` directly | Bypass to_thread, await spawn_fn |
| `SpawnTool._execute()` | Sync implementation, called by base class | Dead code after execute() override | Delete or mark unused |

### Pattern: Exp.run() bridge removal

**Before (Phase 17 state):**
```python
def run(self, ctx, task, *, bus=None, history=None, stop_event=None, skills=None, mcp=None) -> KernelResult:
    runtime = self.build_runtime(ctx, bus=bus, skills=skills, mcp=mcp)
    # ... stop_event injection ...
    _loop = asyncio.new_event_loop()
    try:
        result = _loop.run_until_complete(
            runtime.kernel.run(runtime.spec, task, history=history, stop_event=stop_event)
        )
        return result.result
    finally:
        _loop.close()
        runtime.cleanup()
```

**After (Phase 18):**
```python
async def run(self, ctx, task, *, bus=None, history=None, stop_event=None, skills=None, mcp=None) -> KernelResult:
    runtime = await self.build_runtime(ctx, bus=bus, skills=skills, mcp=mcp)
    # ... stop_event injection ...
    try:
        result = await runtime.kernel.run(runtime.spec, task, history=history, stop_event=stop_event)
        return result.result
    finally:
        await self._run_cleanup_callbacks()
```

### Pattern: spawn_fn async closure

**Before:**
```python
def spawn_fn(exp_name, task, stop_event=None) -> str:
    child_config = load_exp_config(exp_name)
    child_exp = Exp(child_config)
    child_runtime = child_exp.build_runtime(ctx, bus=bus, ...)
    _loop = asyncio.new_event_loop()
    try:
        run_result = _loop.run_until_complete(child_runtime.kernel.run(...))
        ...
    finally:
        _loop.close()
        child_runtime.cleanup()
```

**After:**
```python
async def spawn_fn(exp_name, task, stop_event=None) -> str:
    child_config = load_exp_config(exp_name)
    child_exp = Exp(child_config)
    child_runtime = await child_exp.build_runtime(ctx, bus=bus, ...)
    try:
        run_result = await child_runtime.kernel.run(...)
        ...
    finally:
        await child_exp._run_cleanup_callbacks()
```

### Pattern: SpawnTool execute() override

**Before:** SpawnTool inherits `BuiltinTool.execute()` which does `await asyncio.to_thread(self._execute, arguments)`. SpawnTool._execute() calls sync spawn_fn.

**After:** SpawnTool overrides `execute()` directly:
```python
async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
    if self._spawn_fn is None:
        return "Error: Spawn is not available in this context (recursion depth limit reached)"
    exp_name = arguments.get("exp_name", "").strip()
    task = arguments.get("task", "").strip()
    if not exp_name or not task:
        return "Error: Both exp_name and task are required"
    try:
        return await self._spawn_fn(exp_name, task, self._stop_event)
    except Exception as e:
        self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
        return f"Error: {e}"
```

This preserves the error handling from both _execute() (validation, None guard) and BuiltinTool.execute() (exception catching).

### Pattern: async cleanup callback dispatch

```python
async def _run_cleanup_callbacks(self) -> None:
    for cb in self._cleanup_callbacks:
        try:
            if inspect.iscoroutinefunction(cb):
                await cb()
            else:
                cb()
        except Exception:
            self.logger.warning("Cleanup callback %s raised, continuing", cb, exc_info=True)
    self._cleanup_callbacks.clear()
```

### Pattern: agent_run_service bridge extension

The service layer has an existing bridge pattern at line 470:
```python
_loop = asyncio.new_event_loop()
try:
    kernel_result = _loop.run_until_complete(runtime.kernel.run(...))
finally:
    _loop.close()
```

Phase 18 extends this to also cover build_runtime():
```python
_loop = asyncio.new_event_loop()
try:
    runtime = _loop.run_until_complete(
        exp.build_runtime(pg_ctx, bus=bus, skills=..., mcp=...)
    )
    # ... hook injection, stop_event injection, history ...
    kernel_result = _loop.run_until_complete(
        runtime.kernel.run(spec=spec, task=user_prompt, history=history, stop_event=stop_event)
    )
finally:
    _loop.close()
```

The _loop creation moves up to before `build_runtime()` (currently at line 470, needs to move to ~line 426).

### AgentRuntime.cleanup Type Annotation

Currently `cleanup: Callable[[], None]`. After Phase 18, `_run_cleanup_callbacks` is async, so the type must accommodate both:

**Option A (recommended):** Change to `cleanup: Callable[[], Any]` -- callers that need await can inspect. Simpler, and service layer bridge calls it sync anyway.

**Option B:** Change to `cleanup: Callable[[], Coroutine[Any, Any, None]] | Callable[[], None]`. More precise but complicates callers.

**Key consideration:** DevShell calls `runtime.cleanup()` synchronously (line 129 of runner.py, line 199 of repl.py). DevShell is out of scope for v2.0 async migration. The AgentRuntime dataclass must allow sync callers to still work, OR DevShell must be updated minimally.

**Resolution approach:** Since `_run_cleanup_callbacks` becomes async, `build_runtime()` should pass it as the cleanup callable. Callers that are async (Exp.run, spawn_fn) will `await` it. Sync callers (DevShell, agent_run_service via bridge) can wrap in `loop.run_until_complete()` or the service bridge loop.

Actually, looking more carefully: `runtime.cleanup()` is currently called at line 305 `runtime.cleanup()` in Exp.run(), at line 131 `child_runtime.cleanup()` in spawn_fn, at line 129 in DevShell runner, and at line 199 in DevShell repl. After Phase 18, Exp.run() calls `await self._run_cleanup_callbacks()` directly (not through runtime.cleanup). spawn_fn similarly. So only DevShell and agent_run_service call `runtime.cleanup()`.

The cleanest approach: keep `runtime.cleanup` as the (now-async) `_run_cleanup_callbacks`, but make sure the bridge loop in agent_run_service can handle it. DevShell already has its own bridge loop that can be extended to cover cleanup.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async/sync callback detection | Custom type dispatch | `inspect.iscoroutinefunction(cb)` | Standard library, handles bound methods and functools.partial correctly |
| Async bridge for sync callers | Complex threading bridge | `asyncio.new_event_loop()` + `run_until_complete()` | Established project pattern from Phase 13-17 |
| Concurrent cleanup | `asyncio.gather` for parallel cleanup | Sequential for-loop with error isolation | Cleanup order may matter; simplicity wins |

## Common Pitfalls

### Pitfall 1: AgentRuntime.cleanup type mismatch
**What goes wrong:** `AgentRuntime.cleanup` is typed as `Callable[[], None]`. After Phase 18, `_run_cleanup_callbacks` is async (returns coroutine). Sync callers get a coroutine object instead of None.
**Why it happens:** Type annotation not updated, or callers not adapted.
**How to avoid:** Update `AgentRuntime.cleanup` type annotation. Ensure Exp.run() and spawn_fn call `await self._run_cleanup_callbacks()` directly (not through runtime.cleanup). DevShell/service layer callers must use bridge loop.
**Warning signs:** "coroutine was never awaited" runtime warning. Tests passing but cleanup not actually running.

### Pitfall 2: DevShell breakage
**What goes wrong:** DevShell calls `exp.build_runtime()` and `runtime.cleanup()` synchronously. If these become async, DevShell breaks.
**Why it happens:** DevShell is out of v2.0 scope but shares the same Exp class.
**How to avoid:** DevShell already has a bridge loop pattern (runner.py line 111). Extend it to cover build_runtime(). For cleanup, the bridge loop can be reused. Minimal change, not a full DevShell async migration.
**Warning signs:** DevShell tests failing after Phase 18.

### Pitfall 3: SpawnTool._execute deletion breaking tests
**What goes wrong:** Existing tests for SpawnTool call `await tool.execute(...)` which goes through BuiltinTool.execute() -> to_thread(_execute). After override, this path is dead. But test mocks may still set up expectations on _execute.
**Why it happens:** Test structure assumes base class dispatch.
**How to avoid:** Check all SpawnTool test files. Current tests (test_spawn_tool.py) already use `await tool.execute(...)` which works with either path. The spawn_fn mock (`Mock(return_value=...)`) needs to become `AsyncMock(return_value=...)` since spawn_fn is now async.
**Warning signs:** `TypeError: object MagicMock can't be used in 'await' expression`.

### Pitfall 4: spawn_fn closure test mocking
**What goes wrong:** Integration tests (test_subagent_spawn.py) mock `Exp.build_runtime` as a sync return. After Phase 18, build_runtime is async, so patch.object must use AsyncMock.
**Why it happens:** Mocked build_runtime returns value directly, not a coroutine.
**How to avoid:** Change `patch.object(Exp, "build_runtime", return_value=mock_runtime)` to `patch.object(Exp, "build_runtime", new_callable=AsyncMock, return_value=mock_runtime)`. Similarly for `patch.object(exp, "build_runtime", return_value=mock_runtime)` in test_exp.py.
**Warning signs:** `TypeError: object MagicMock can't be used in 'await' expression` or tests hanging.

### Pitfall 5: _make_spawn_fn signature type annotation
**What goes wrong:** The return type annotation of `_make_spawn_fn` is `Callable[[str, str, threading.Event | None], str]`. After async, the inner function returns a coroutine, so the type should be `Callable[[str, str, threading.Event | None], Coroutine[Any, Any, str]]`.
**Why it happens:** Type annotation not updated with async change.
**How to avoid:** Update return type of `_make_spawn_fn`. SpawnTool's `spawn_fn` parameter type also needs updating from `Callable[..., str]` to `Callable[..., str] | Callable[..., Coroutine[Any, Any, str]]` (or a simpler alias).

### Pitfall 6: Cleanup via runtime.cleanup() vs direct _run_cleanup_callbacks()
**What goes wrong:** After Phase 18, Exp.run() and spawn_fn should call `await self._run_cleanup_callbacks()` directly rather than `runtime.cleanup()`. But `runtime.cleanup` is still set to `self._run_cleanup_callbacks` in build_runtime(). Callers that use `runtime.cleanup()` without await get "coroutine never awaited".
**Why it happens:** Two code paths to the same function, one awaited, one not.
**How to avoid:** Clear strategy -- Exp.run() and spawn_fn use `await self._run_cleanup_callbacks()` in their finally blocks. External callers (service, devshell) use runtime.cleanup() through their bridge loops. In build_runtime(), the cleanup callable passed to AgentRuntime should be the async version, and callers handle it appropriately.

## Code Examples

### Verified: inspect.iscoroutinefunction for cleanup detection
```python
# Tested on project Python (3.10+)
import inspect

def sync_cb(): pass
async def async_cb(): pass

class C:
    def clear(self): pass

c = C()
assert inspect.iscoroutinefunction(sync_cb) is False
assert inspect.iscoroutinefunction(async_cb) is True
assert inspect.iscoroutinefunction(c.clear) is False  # ReadTracker.clear
```

### Verified: Current cleanup callbacks
Two callbacks are registered:
1. `tracker.clear` -- ReadTracker.clear, sync method (line 346 of exp.py)
2. `connector.cleanup` -- LazyMCPConnector.cleanup, sync method (line 458 of exp.py)

Both are currently sync. The async detection in _run_cleanup_callbacks handles them correctly via the `iscoroutinefunction` check.

### Test migration pattern
```python
# Before (sync test)
def test_run_calls_build_runtime_then_kernel(self) -> None:
    exp = Exp(ExpConfig(name="test"))
    ctx = _make_ctx(with_llm=True)
    mock_kernel = MagicMock()
    mock_kernel.run = AsyncMock(return_value=mock_kernel_result)
    mock_runtime = AgentRuntime(kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup)
    with patch.object(exp, "build_runtime", return_value=mock_runtime) as mock_br:
        result = exp.run(ctx, "do something")

# After (async test)
async def test_run_calls_build_runtime_then_kernel(self) -> None:
    exp = Exp(ExpConfig(name="test"))
    ctx = _make_ctx(with_llm=True)
    mock_kernel = MagicMock()
    mock_kernel.run = AsyncMock(return_value=mock_kernel_result)
    mock_cleanup = AsyncMock()  # cleanup is now async
    mock_runtime = AgentRuntime(kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup)
    with patch.object(exp, "build_runtime", new_callable=AsyncMock, return_value=mock_runtime) as mock_br:
        result = await exp.run(ctx, "do something")
```

## Caller Impact Analysis

### Direct callers of `exp.build_runtime()` (sync -> async)

| Caller | File | Current | Phase 18 Action |
|--------|------|---------|-----------------|
| `Exp.run()` | `matmaster/core/exp.py:286` | `self.build_runtime(ctx, ...)` | `await self.build_runtime(ctx, ...)` |
| `Exp._make_spawn_fn.spawn_fn` | `matmaster/core/exp.py:112` | `child_exp.build_runtime(ctx, ...)` | `await child_exp.build_runtime(ctx, ...)` |
| `agent_run_service.py` | `src/services/agent_run_service.py:427` | `exp.build_runtime(pg_ctx, ...)` | `_loop.run_until_complete(exp.build_runtime(...))` |
| `DevRunner.run()` | `matmaster/devshell/runner.py:104` | `exp.build_runtime(self._pg_ctx, ...)` | Bridge loop or leave sync (out of scope, but will break) |
| `_show_tools()` | `matmaster/devshell/repl.py:188` | `exp.build_runtime(runner._pg_ctx)` | Bridge loop or leave sync (out of scope, but will break) |
| Integration tests (5 files) | `tests/matmaster/integration/` | `exp.build_runtime(pg_ctx, ...)` | Already in async test functions, add `await` |
| Unit tests (test_exp.py) | `tests/matmaster/core/test_exp.py` | mixed sync/async | Tests calling build_runtime directly add `await`, test functions become `async def` |

### Direct callers of `exp.run()` (sync -> async)

| Caller | File | Current | Phase 18 Action |
|--------|------|---------|-----------------|
| test_exp.py TestExpRun | 3 tests | `exp.run(ctx, "task")` | `await exp.run(ctx, "task")`, tests become async def |
| test_exp.py TestExpCleanup | 2 tests calling run() | `exp.run(ctx, "task")` | `await exp.run(ctx, "task")`, tests become async def |
| evomaster (legacy) | `evomaster/core/playground.py` | `exp.run(...)` | Out of scope -- evomaster is legacy being removed |

### DevShell impact (out of v2.0 scope but requires minimal bridge)

DevShell calls `exp.build_runtime()` at two locations. Both already have bridge loops for `kernel.run()`. The simplest fix: extend the existing bridge loop to also cover `build_runtime()`:

```python
# runner.py -- before Phase 18
runtime = exp.build_runtime(self._pg_ctx, bus=bus)
_loop = asyncio.new_event_loop()
try:
    result = _loop.run_until_complete(runtime.kernel.run(...))
finally:
    _loop.close()
    runtime.cleanup()

# runner.py -- after Phase 18 (minimal bridge extension)
_loop = asyncio.new_event_loop()
try:
    runtime = _loop.run_until_complete(exp.build_runtime(self._pg_ctx, bus=bus))
    result = _loop.run_until_complete(runtime.kernel.run(...))
finally:
    _loop.close()
    _loop_cleanup = asyncio.new_event_loop()
    try:
        _loop_cleanup.run_until_complete(exp._run_cleanup_callbacks())
    finally:
        _loop_cleanup.close()
```

Actually, per D-03 and CONTEXT.md, only agent_run_service.py gets the bridge. DevShell is out of scope. However, DevShell will break without at minimum a bridge. The planner should decide: either add minimal bridges to DevShell (3-4 line changes, same pattern) or accept DevShell tests may fail. The CONTEXT.md canonical refs say "DevShell 使用 Exp，不在 v2.0 范围，暂不改". This means we should NOT add bridges to DevShell, but must verify DevShell tests are not run in the Phase 18 test suite, or are expected to fail.

Let me verify DevShell test status:

**DevShell tests check (verified):** DevShell tests in `tests/matmaster/devshell/` call `exp.build_runtime()` indirectly through DevRunner. These will break. However, per project direction, DevShell is out of v2.0 scope. The planner must either:
1. Accept DevShell test failures as known/expected
2. Add minimal bridge to DevRunner (same pattern as agent_run_service)

**Recommendation:** Add minimal bridge to DevRunner and _show_tools in repl.py. It's 4-5 lines each, follows the exact same pattern as agent_run_service, and prevents unnecessary test failures. This is NOT a DevShell async migration -- just extending the existing bridge pattern.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | `pyproject.toml` (asyncio_mode="auto") |
| Quick run command | `uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/integration/test_subagent_spawn.py tests/matmaster/tools/test_spawn_tool.py -x` |
| Full suite command | `uv run pytest tests/matmaster/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXPL-01 | assemble() is async def | unit | `uv run pytest tests/matmaster/core/test_exp.py::TestExpAssemble -x` | Exists (6 tests, need async migration) |
| EXPL-02 | build_runtime() is async def | unit + integration | `uv run pytest tests/matmaster/core/test_exp.py::TestExpBuildRuntime -x` | Exists (7 tests, need async + await) |
| EXPL-03 | run() is async def, await kernel.run() | unit | `uv run pytest tests/matmaster/core/test_exp.py::TestExpRun tests/matmaster/core/test_exp.py::TestExpCleanup -x` | Exists (7 tests, need async migration) |
| EXPL-04 | SubAgent spawn async chain | integration | `uv run pytest tests/matmaster/integration/test_subagent_spawn.py tests/matmaster/tools/test_spawn_tool.py -x` | Exists (17 tests, need async + AsyncMock migration) |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/integration/test_subagent_spawn.py tests/matmaster/tools/test_spawn_tool.py -x`
- **Per wave merge:** `uv run pytest tests/matmaster/ -x`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
None -- existing test infrastructure (pytest-asyncio auto mode, async mock factories) covers all requirements. No new test files needed; existing tests are migrated in-place.

## Test Migration Inventory

**Total affected test files: 3 primary + 4 integration**

| File | Test Count | sync->async Tests | Mock Changes |
|------|-----------|-------------------|-------------|
| `tests/matmaster/core/test_exp.py` | 44 | ~25 tests (assemble: 6, build_runtime: 7, run: 3, cleanup: 4, identity: 2, sysprompt: 2, builtin-tools: depends) | `patch.object(exp, "build_runtime", ...)` -> AsyncMock |
| `tests/matmaster/integration/test_subagent_spawn.py` | 10 | 7 sync tests -> async | `patch.object(Exp, "build_runtime", ...)` -> AsyncMock; spawn_fn calls need await |
| `tests/matmaster/tools/test_spawn_tool.py` | 13 | 0 (already async) | `Mock(return_value=...)` for spawn_fn -> `AsyncMock(return_value=...)` |
| `tests/matmaster/integration/test_pipeline_alignment.py` | ~3 | 0 (already async, calls build_runtime sync) | Add `await` to build_runtime call |
| `tests/matmaster/integration/test_e2e_minimal.py` | ~2 | 0 (already async, calls build_runtime sync) | Add `await` to build_runtime call |
| `tests/matmaster/integration/test_e2e_mat_master.py` | ~5 | 0 (already async, calls build_runtime sync) | Add `await` to build_runtime call |
| `tests/matmaster/integration/test_upstream_scenarios.py` | ~4 | 0 (already async, calls build_runtime sync) | Add `await` to build_runtime call |

## Open Questions

1. **DevShell bridge -- include or exclude?**
   - What we know: DevShell calls build_runtime() and runtime.cleanup() synchronously. It has its own bridge loop for kernel.run(). CONTEXT.md says "暂不改".
   - What's unclear: Does "暂不改" mean "accept test failures" or "add minimal bridge to prevent breakage"?
   - Recommendation: Add minimal bridge (5 lines per file, exact same pattern as agent_run_service). This prevents unnecessary red in CI without constituting a DevShell async migration. Mark as Claude's discretion.

2. **AgentRuntime.cleanup type annotation**
   - What we know: Currently `Callable[[], None]`. _run_cleanup_callbacks becomes async. External callers need to handle both sync and async.
   - What's unclear: Best type annotation that satisfies both async callers (Exp.run, spawn_fn) and sync callers (DevShell via bridge).
   - Recommendation: Use `Callable[[], Any]` as the simplest approach. Callers that know it's async will await; bridge callers will run_until_complete.

3. **SpawnTool._execute() disposal**
   - What we know: After execute() override, _execute() is dead code. BuiltinTool.execute() references it but SpawnTool's execute() no longer calls it.
   - What's unclear: Whether to delete _execute() or keep an empty stub.
   - Recommendation: Delete _execute() entirely. BuiltinTool ABC marks it @abstractmethod, but since SpawnTool overrides execute() (the Protocol method), the ABC contract is satisfied. Actually -- BuiltinTool uses @abstractmethod on _execute(), so deleting it may cause ABC instantiation error. Instead, provide a stub: `def _execute(self, arguments): raise NotImplementedError("SpawnTool uses async execute() directly")`.

## Project Constraints (from CLAUDE.md)

- Always use `uv run` or `.venv` for Python execution
- Import grouping: stdlib -> third-party -> local, all at file top
- New tools must implement Tool Protocol and return ToolResult
- Testing: `uv run pytest` for all tests
- Protocol hard cut: no sync/async dual interfaces (from PROJECT.md)
- Guard Protocol stays sync (from PROJECT.md)
- stop_event stays threading.Event (from PROJECT.md)

## Sources

### Primary (HIGH confidence)
- `matmaster/core/exp.py` -- current Exp implementation (497 lines), direct code reading
- `matmaster/tools/builtin/spawn_tool.py` -- current SpawnTool implementation (141 lines)
- `matmaster/tools/builtin/base.py` -- BuiltinTool ABC (70 lines)
- `src/services/agent_run_service.py` -- service layer bridge pattern (lines 426-481)
- `matmaster/types/runtime.py` -- AgentRuntime, AgentRuntimeSpec definitions
- `.planning/phases/18-exp/18-CONTEXT.md` -- user decisions D-01 through D-06
- `.planning/phases/17-agentkernel/17-CONTEXT.md` -- Phase 17 bridge patterns

### Secondary (MEDIUM confidence)
- `matmaster/devshell/runner.py` -- DevShell bridge pattern (line 111)
- `matmaster/devshell/repl.py` -- DevShell _show_tools (line 188)
- `matmaster/tools/lazy_mcp.py` -- LazyMCPConnector.cleanup (sync, line 193)
- `matmaster/tools/builtin/read_tracker.py` -- ReadTracker.clear (sync, line 36)

### Test verification (HIGH confidence)
- All 67 tests passing: `tests/matmaster/core/test_exp.py` (44) + `tests/matmaster/integration/test_subagent_spawn.py` (10) + `tests/matmaster/tools/test_spawn_tool.py` (13)
- pytest-asyncio 1.3.0 with auto mode confirmed
- `inspect.iscoroutinefunction` behavior verified for sync/async/bound method cases

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries, pure asyncio patterns from Python stdlib
- Architecture: HIGH -- exact patterns established in Phases 13-17, mechanical application
- Pitfalls: HIGH -- identified from direct code reading and Phase 17 migration experience

**Research date:** 2026-03-29
**Valid until:** Indefinite (Python asyncio patterns are stable; project-internal patterns locked by prior phases)
