# Phase 24: emit_nowait Tech Debt Cleanup - Research

**Researched:** 2026-03-30
**Domain:** asyncio event emission pattern migration + docstring/annotation cleanup
**Confidence:** HIGH

## Summary

Phase 24 is a mechanical tech debt cleanup phase. The matmaster/ package's Kernel execution loop is now fully async (completed in Phase 17), but 12 call sites in hooks and ContextCompactor still use the synchronous `bus.emit_nowait()` instead of `await bus.emit()`. This was an intentional deviation documented in Phase 16, with the plan to upgrade once the Kernel became async.

The migration is straightforward: every affected call site is already in an `async def` method, so the change is purely `self._bus.emit_nowait(Event(...))` to `await self._bus.emit(Event(...))`. Additionally, 4 stale docstrings referencing "sync kernel context" need cleanup, the MessageBus class docstring needs updating to reflect the new primary path, and one type annotation in `agent_run_service.py` needs fixing.

**Primary recommendation:** Execute as a single-wave plan with two tasks: (1) migrate all 12 emit_nowait calls + clean stale comments + update bus docstring, (2) update test assertions from `emit_nowait` to `emit` in 3 mock-based test files.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: Migrate all 12 emit_nowait() calls in matmaster/ to await bus.emit(), not just the 7 in EventEmitterHook. Files: hooks.py (6), assistant_state.py (1), output_processor.py (2), skill_hit.py (1), context_compactor.py (2)
- D-02: Preserve MessageBus.emit_nowait() method (src/ service layer has 10 sync callers). Update bus.py docstring to reflect emit() is now the primary path for matmaster/ code
- D-03: Clean all 4 stale "sync kernel context" docstrings in hooks.py:185, assistant_state.py:29, output_processor.py:27, skill_hit.py:28
- D-04: Fix stop_event type annotation from Any to threading.Event at agent_run_service.py:257

### Claude's Discretion
- bus.py docstring wording
- Test modification scope (if mock uses emit_nowait then adapt)

### Deferred Ideas (OUT OF SCOPE)
None
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HOOK-03 | EventEmitterHook adapts to async MessageBus | All 12 emit_nowait calls in matmaster/ are in async methods and can be directly awaited. Test infrastructure (pytest-asyncio auto mode) supports async test execution. Mock-based tests need assertion updates. |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- Use `uv run` or `.venv`, not system Python
- Import grouping: stdlib, third-party, local (all at top)
- New tools must implement Tool Protocol and return ToolResult (not applicable here)
- Tests: `uv run pytest` for full suite, `uv run pytest tests/path -x` for targeted

## Standard Stack

No new libraries needed. This phase only modifies existing code.

| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| pytest-asyncio | >=0.24.0 | async test execution | Already installed, asyncio_mode="auto" |
| asyncio (stdlib) | 3.10+ | Queue, event loop | Already in use |
| threading (stdlib) | 3.10+ | threading.Event type annotation | Already imported in agent_run_service.py |

## Architecture Patterns

### Migration Pattern: emit_nowait to await emit

The transformation is uniform across all 12 sites:

```python
# Before (sync emit, fire-and-forget):
self._bus.emit_nowait(
    SomeEvent(
        source=self._source,
        ...
    )
)

# After (async emit, awaited):
await self._bus.emit(
    SomeEvent(
        source=self._source,
        ...
    )
)
```

**Key verification:** Both `emit()` and `emit_nowait()` ultimately call `self._queue.put_nowait(event)` internally. The async `emit()` method is non-blocking for an unbounded queue (the default). This means the migration has zero behavioral change -- the only difference is that the calling code now properly participates in the async protocol instead of bypassing it.

### ContextCompactor Special Case

ContextCompactor has an optional `self._bus` that can be `None`. The existing pattern already includes a None guard:

```python
if self._bus is not None:
    self._bus.emit_nowait(ContextCompactionEvent(...))
```

Migrated:

```python
if self._bus is not None:
    await self._bus.emit(ContextCompactionEvent(...))
```

No additional logic changes needed -- `compact_if_needed` is already `async def`.

### Test Pattern: Two Categories

Tests split into two distinct patterns that need different handling:

**Category A -- Real MessageBus (NO changes needed):**
- `tests/matmaster/core/test_hooks.py` -- TestEventEmitterHook and TestEventEmitterHookSpawnId use a real `MessageBus()` instance
- `tests/matmaster/core/test_context_compactor.py` -- TestCompactorEventEmission uses a real `MessageBus()` instance
- `tests/matmaster/integration/test_event_router.py` -- uses real `MessageBus()` with `emit_nowait` (but these are testing the EventRouter which legitimately uses emit_nowait from outside the event loop context)

These tests verify behavior via `bus.get_nowait()` after the hook runs. Since the hook code changes from `emit_nowait` to `await emit`, the events still arrive in the queue and `get_nowait()` still works. **No test changes needed.**

**Category B -- MagicMock bus (assertions need updating):**
- `tests/matmaster/hooks/test_output_processor.py` -- 4 positive assertions + 2 negative assertions on `bus.emit_nowait`
- `tests/matmaster/hooks/test_assistant_state.py` -- 4 positive assertions + 2 negative assertions on `bus.emit_nowait`
- `tests/matmaster/hooks/test_skill_hit.py` -- 1 positive assertion + 3 negative assertions on `bus.emit_nowait`

These use `bus = MagicMock()`. After migration, hook code calls `await self._bus.emit(...)`. MagicMock returns a MagicMock from attribute access, and `await MagicMock()` works because MagicMock supports `__await__` in Python 3.8+. However, assertions like `bus.emit_nowait.assert_called()` must change to `bus.emit.assert_called()`.

Specifically, every `bus.emit_nowait.assert_called*` and `bus.emit_nowait.call_args` must become `bus.emit.assert_called*` and `bus.emit.call_args`.

### Stale Comment Cleanup Pattern

All 4 stale docstrings follow the same template. Remove the line:

```
Uses bus.emit_nowait() for thread-safe emit from sync kernel context.
```

And for EventEmitterHook (hooks.py:185-186), also remove:

```
Will switch to await bus.emit() when kernel becomes async.
```

### bus.py Docstring Update

The class docstring currently says:

```
For cross-thread callers (service layer), emit_nowait() uses
loop.call_soon_threadsafe to schedule put_nowait on the correct
event loop, avoiding the asyncio.Queue thread-safety issue.
```

Should be updated to clarify that `emit()` is the primary path for code running inside the event loop (matmaster/ package), while `emit_nowait()` is solely for cross-thread callers (src/ service layer).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async MagicMock | Custom AsyncMock wrapper | `MagicMock()` directly | Python 3.8+ MagicMock already supports `await` via `__await__` returning `MagicMock()` |

## Common Pitfalls

### Pitfall 1: MagicMock await behavior
**What goes wrong:** Forgetting that `await bus.emit(event)` on a MagicMock will work but the assertion target changes from `bus.emit_nowait` to `bus.emit`.
**Why it happens:** The method name is different, and tests assert on the specific method name.
**How to avoid:** Systematic find-and-replace in the 3 mock-based test files.
**Warning signs:** Tests pass but assertions check the wrong method (emit_nowait was never called, so `assert_not_called` vacuously passes).

### Pitfall 2: Forgetting ContextCompactor None guard
**What goes wrong:** Adding `await` to the emit call without preserving the `if self._bus is not None` guard.
**Why it happens:** The two call sites in context_compactor.py are inside conditional blocks.
**How to avoid:** The replacement is purely `emit_nowait(` to `await self._bus.emit(` -- the guard is in the enclosing `if` statement and is not touched.
**Warning signs:** AttributeError on NoneType when bus is None.

### Pitfall 3: Integration test emit_nowait calls (out of scope)
**What goes wrong:** Accidentally migrating `tests/matmaster/integration/test_event_router.py` emit_nowait calls.
**Why it happens:** grep shows emit_nowait in those files, tempting to change them.
**How to avoid:** Those tests deliberately test the EventRouter's ability to consume events from `emit_nowait`. They simulate the service layer pattern. Leave them unchanged.
**Warning signs:** The test context is simulating cross-thread usage.

### Pitfall 4: Missing assertion update creates false passing test
**What goes wrong:** A `bus.emit_nowait.assert_not_called()` assertion passes vacuously after migration because the code now calls `bus.emit()` instead.
**Why it happens:** emit_nowait is never called (because we migrated to emit), so the assertion that it was not called passes -- but it's testing nothing.
**How to avoid:** Update ALL emit_nowait assertions in mock-based tests, including negative ones.
**Warning signs:** Test suite passes but negative tests are no longer exercising the intended logic.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio >=0.24.0 |
| Config file | pyproject.toml (asyncio_mode = "auto") |
| Quick run command | `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_bus.py -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HOOK-03-a | EventEmitterHook uses await bus.emit() | unit | `uv run pytest tests/matmaster/core/test_hooks.py::TestEventEmitterHook -x` | Yes |
| HOOK-03-b | EventEmitterHook spawn_id propagation still works | unit | `uv run pytest tests/matmaster/core/test_hooks.py::TestEventEmitterHookSpawnId -x` | Yes |
| HOOK-03-c | AssistantStateHook uses await bus.emit() | unit | `uv run pytest tests/matmaster/hooks/test_assistant_state.py -x` | Yes |
| HOOK-03-d | OutputProcessorHook uses await bus.emit() | unit | `uv run pytest tests/matmaster/hooks/test_output_processor.py -x` | Yes |
| HOOK-03-e | SkillHitHook uses await bus.emit() | unit | `uv run pytest tests/matmaster/hooks/test_skill_hit.py -x` | Yes |
| HOOK-03-f | ContextCompactor uses await bus.emit() | unit | `uv run pytest tests/matmaster/core/test_context_compactor.py::TestCompactorEventEmission -x` | Yes |
| HOOK-03-g | bus.emit_nowait still works for service layer | unit | `uv run pytest tests/matmaster/core/test_bus.py::TestMessageBusEmitNowait -x` | Yes |
| D-04 | stop_event typed as threading.Event | static | Manual verification (mypy not configured as CI gate) | N/A |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_bus.py -x`
- **Per wave merge:** `uv run pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
None -- existing test infrastructure covers all phase requirements. No new test files needed.

## Code Examples

### Complete migration for EventEmitterHook.pre_tool_call

```python
# Before:
async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
    self._bus.emit_nowait(
        ToolCallEvent(
            source=self._source,
            spawn_id=self._spawn_id,
            call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
        )
    )
    return HookAction.CONTINUE

# After:
async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
    await self._bus.emit(
        ToolCallEvent(
            source=self._source,
            spawn_id=self._spawn_id,
            call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
        )
    )
    return HookAction.CONTINUE
```

### Complete migration for test assertion (MagicMock pattern)

```python
# Before:
bus.emit_nowait.assert_called()
emitted = bus.emit_nowait.call_args[0][0]

# After:
bus.emit.assert_called()
emitted = bus.emit.call_args[0][0]

# Negative case -- Before:
bus.emit_nowait.assert_not_called()

# Negative case -- After:
bus.emit.assert_not_called()
```

### Updated bus.py docstring

```python
class MessageBus:
    """Async event bus.

    Agent kernel and hooks call ``await emit()`` to publish BusEvent
    from within the event loop.  EventRouter consumes via ``await get()``
    in an async task.

    For cross-thread callers (src/ service layer running in separate threads),
    ``emit_nowait()`` uses ``loop.call_soon_threadsafe`` to schedule
    ``put_nowait`` on the correct event loop.

    Based on asyncio.Queue, safe within a single event loop.
    """
```

## Detailed Change Inventory

### Source Files (matmaster/)

| File | Line(s) | Change | Type |
|------|---------|--------|------|
| `matmaster/core/hooks.py` | 202 | `self._bus.emit_nowait(` -> `await self._bus.emit(` | code |
| `matmaster/core/hooks.py` | 215 | same | code |
| `matmaster/core/hooks.py` | 230 | same | code |
| `matmaster/core/hooks.py` | 241 | same | code |
| `matmaster/core/hooks.py` | 256 | same | code |
| `matmaster/core/hooks.py` | 269 | same | code |
| `matmaster/core/hooks.py` | 185-186 | Remove stale docstring lines | comment |
| `matmaster/hooks/assistant_state.py` | 45 | `self._bus.emit_nowait(` -> `await self._bus.emit(` | code |
| `matmaster/hooks/assistant_state.py` | 29 | Remove stale docstring line | comment |
| `matmaster/hooks/output_processor.py` | 49 | `self._bus.emit_nowait(` -> `await self._bus.emit(` | code |
| `matmaster/hooks/output_processor.py` | 62 | same | code |
| `matmaster/hooks/output_processor.py` | 27 | Remove stale docstring line | comment |
| `matmaster/hooks/skill_hit.py` | 44 | `self._bus.emit_nowait(` -> `await self._bus.emit(` | code |
| `matmaster/hooks/skill_hit.py` | 28 | Remove stale docstring line | comment |
| `matmaster/core/context_compactor.py` | 205 | `self._bus.emit_nowait(` -> `await self._bus.emit(` | code |
| `matmaster/core/context_compactor.py` | 251 | same | code |
| `matmaster/core/bus.py` | 15-25 | Update class docstring | comment |

### Service Layer (src/)

| File | Line | Change | Type |
|------|------|--------|------|
| `src/services/agent_run_service.py` | 257 | `stop_event: Any` -> `stop_event: threading.Event` | annotation |

### Test Files

| File | Lines | Change | Type |
|------|-------|--------|------|
| `tests/matmaster/hooks/test_output_processor.py` | 34,35,59,60,81,92 | `emit_nowait` -> `emit` in assertions | test |
| `tests/matmaster/hooks/test_assistant_state.py` | 34,35,62,63,79,94 | same | test |
| `tests/matmaster/hooks/test_skill_hit.py` | 28,29,43,54,65 | same | test |

### Files NOT Changed (verified out of scope)

| File | Reason |
|------|--------|
| `tests/matmaster/core/test_hooks.py` | Uses real MessageBus, not MagicMock -- works with both emit and emit_nowait |
| `tests/matmaster/core/test_context_compactor.py` | Uses real MessageBus -- no assertion changes needed |
| `tests/matmaster/core/test_bus.py` | Tests emit_nowait as a legitimate API for service layer |
| `tests/matmaster/integration/test_event_router.py` | Tests emit_nowait from simulated service layer context |
| `src/services/agent_run_service.py` (emit_nowait calls) | Service layer runs in separate thread, legitimately needs emit_nowait |

## Open Questions

None. The scope, affected files, and migration pattern are fully determined by CONTEXT.md decisions and source code audit.

## Sources

### Primary (HIGH confidence)
- Direct source code audit of all affected files in the working tree
- `matmaster/core/bus.py` -- MessageBus.emit() and emit_nowait() implementations verified
- `matmaster/core/hooks.py` -- All 6 EventEmitterHook emit_nowait calls at lines 202, 215, 230, 241, 256, 269
- `matmaster/hooks/assistant_state.py`, `output_processor.py`, `skill_hit.py` -- 4 additional emit_nowait calls
- `matmaster/core/context_compactor.py` -- 2 emit_nowait calls at lines 205, 251
- Test files: `test_hooks.py`, `test_output_processor.py`, `test_assistant_state.py`, `test_skill_hit.py`, `test_context_compactor.py`, `test_bus.py`

### Secondary (HIGH confidence)
- `.planning/phases/24-emit-nowait-tech-debt/24-CONTEXT.md` -- User decisions D-01 through D-04
- `.planning/v2.0-MILESTONE-AUDIT.md` -- Tech debt identification

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new libraries, pure code modification
- Architecture: HIGH - migration pattern is mechanical, fully verified against source code
- Pitfalls: HIGH - all edge cases identified through code audit (MagicMock tests, ContextCompactor None guard, integration test scope boundary)

**Research date:** 2026-03-30
**Valid until:** indefinite (code-specific findings, not library-version dependent)
