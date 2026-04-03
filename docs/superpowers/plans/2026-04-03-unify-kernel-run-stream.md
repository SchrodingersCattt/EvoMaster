# Unify Kernel to Single run_stream Path — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate AgentKernel's dual `run()`/`run_stream()` paths, keeping only `run_stream()` as sole public API.

**Architecture:** Extend `RunResultEvent` with `num_turns`/`usage`/`messages` to carry terminal data currently only in `KernelRunResult`. Provide a shared `drain_run_stream()` helper so non-streaming callers (DevShell, evaluation, spawn) get structured results without duplicating drain logic. Delete `run()`, `_run_loop()`, `_call_llm()`, `_do_stream_llm()`, `_finish()`, `KernelRunResult`, and `Exp.run()`.

**Tech Stack:** Python 3.10+, Pydantic, asyncio

**Spec:** `docs/superpowers/specs/2026-04-03-unify-kernel-run-stream-design.md`

---

## Chunk 1: Extend Terminal Contract + Drain Helper

### Task 1: Extend `_TerminalItem` with state data

**Files:**
- Modify: `matmaster/core/agent.py:68-72`

- [ ] **Step 1: Add fields to `_TerminalItem`**

```python
@dataclass
class _TerminalItem:
    """Signals that the kernel loop reached a terminal state."""

    reason: str
    final_content: str | None = None
    num_turns: int = 0
    usage: dict[str, int] = dc_field(default_factory=dict)
    messages: list[Any] = dc_field(default_factory=list)
```

- [ ] **Step 2: Update all `_TerminalItem` construction sites in `_run_items()`**

There are 7 terminal yield points in `_run_items()`. Each needs `num_turns`, `usage`, `messages` from `state`:

```python
# Line ~369-371 (cancelled)
yield _KernelItem(
    terminal=_TerminalItem(
        reason='cancelled',
        num_turns=state.turn,
        usage=dict(state.total_usage),
        messages=list(state.messages),
    )
)

# Line ~381-383 (hook_stopped)
yield _KernelItem(
    terminal=_TerminalItem(
        reason='hook_stopped',
        num_turns=state.turn - 1,
        usage=dict(state.total_usage),
        messages=list(state.messages),
    )
)

# Line ~448-450 (invalid_finish)
yield _KernelItem(
    terminal=_TerminalItem(
        reason='invalid_finish',
        num_turns=state.turn,
        usage=dict(state.total_usage),
        messages=list(state.messages),
    )
)

# Line ~458-463 (natural)
yield _KernelItem(
    terminal=_TerminalItem(
        reason='natural',
        final_content=response.content,
        num_turns=state.turn,
        usage=dict(state.total_usage),
        messages=list(state.messages),
    )
)

# Line ~538-539 (max_turns)
yield _KernelItem(
    terminal=_TerminalItem(
        reason='max_turns',
        num_turns=state.turn,
        usage=dict(state.total_usage),
        messages=list(state.messages),
    )
)
```

Two more terminal sites that are easy to miss:

```python
# Line ~426-428 (cancelled from _KernelStopRequested during LLM stream)
except _KernelStopRequested:
    yield _KernelItem(
        terminal=_TerminalItem(
            reason='cancelled',
            num_turns=state.turn,
            usage=dict(state.total_usage),
            messages=list(state.messages),
        )
    )
    return

# Line ~433-435 (invalid_finish when llm_response is None)
if llm_response is None:
    yield _KernelItem(
        terminal=_TerminalItem(
            reason='invalid_finish',
            num_turns=state.turn,
            usage=dict(state.total_usage),
            messages=list(state.messages),
        )
    )
    return
```

- [ ] **Step 3: Commit**

```bash
git add matmaster/core/agent.py
git commit -m "feat: extend _TerminalItem with num_turns, usage, messages"
```

### Task 2: Extend `RunResultEvent` and wire through `run_stream()`

**Files:**
- Modify: `matmaster/types/events.py:75-85`
- Modify: `matmaster/core/agent.py:305-318` (`run_stream` -> `_consume_and_yield`)

- [ ] **Step 1: Add fields to `RunResultEvent`**

In `matmaster/types/events.py`:

```python
class RunResultEvent(EventBase):
    """Business terminal event for a run outcome."""

    type: Literal["run_result", "finish"] = "run_result"
    status: str = "completed"
    reason: str = ""
    final_content: str | None = None
    num_turns: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    messages: list[Any] = Field(default_factory=list)
```

- [ ] **Step 2: Pass new fields through in `run_stream()._consume_and_yield()`**

In `matmaster/core/agent.py`, update the `RunResultEvent` construction inside `_consume_and_yield()`:

```python
async def _consume_and_yield():
    async for item in self._run_items(spec, task, history, stop_event):
        if item.terminal is not None:
            reason = item.terminal.reason
            status = 'cancelled' if reason == 'cancelled' else (
                'failed' if reason == 'invalid_finish' else 'completed'
            )
            yield RunResultEvent(
                source="agent",
                status=status,
                reason=reason,
                final_content=item.terminal.final_content,
                num_turns=item.terminal.num_turns,
                usage=item.terminal.usage,
                messages=item.terminal.messages,
            )
            return
        if item.event is not None:
            yield item.event
```

- [ ] **Step 3: Run existing stream tests to verify no regression**

Run: `pytest tests/matmaster/core/test_agent_kernel_stream.py -v`
Expected: All existing tests PASS (new fields are additive, defaults are backward-compatible)

- [ ] **Step 4: Commit**

```bash
git add matmaster/types/events.py matmaster/core/agent.py
git commit -m "feat: extend RunResultEvent with num_turns, usage, messages"
```

### Task 3: Create shared `drain_run_stream()` helper

**Files:**
- Create: `matmaster/core/stream_drain.py`

- [ ] **Step 1: Write the drain module**

```python
"""Shared drain helper for consuming run_stream() to completion."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DrainResult:
    """Structured result from draining a run_stream() to completion."""

    status: str
    reason: str
    final_content: str | None
    num_turns: int
    usage: dict[str, int]
    messages: list[Any]
    events: list[Any] = field(default_factory=list)


async def drain_run_stream(
    stream: AsyncIterator[Any],
) -> DrainResult:
    """Consume run_stream() to completion, return structured result.

    Collects all intermediate events and extracts terminal RunResultEvent.
    Raises RuntimeError if stream ends without a terminal event.
    """
    from matmaster.types.events import RunResultEvent

    events: list[Any] = []
    async for event in stream:
        if isinstance(event, RunResultEvent):
            return DrainResult(
                status=event.status,
                reason=event.reason,
                final_content=event.final_content,
                num_turns=event.num_turns,
                usage=event.usage,
                messages=event.messages,
                events=events,
            )
        events.append(event)
    raise RuntimeError("run_stream ended without RunResultEvent")
```

- [ ] **Step 2: Commit**

```bash
git add matmaster/core/stream_drain.py
git commit -m "feat: add drain_run_stream() shared helper"
```

---

## Chunk 2: Migrate Callers

### Task 4: Migrate `Exp._make_spawn_fn()` to use `drain_run_stream`

**Files:**
- Modify: `matmaster/core/exp.py:107-131`

- [ ] **Step 1: Update `spawn_fn` to drain `run_stream`**

Replace the `spawn_fn` body in `_make_spawn_fn()`:

```python
async def spawn_fn(
    exp_name: str,
    task: str,
    stop_event: threading.Event | None = None,
) -> str:
    from matmaster.config.loader import load_exp_config
    from matmaster.core.stream_drain import drain_run_stream

    child_config = load_exp_config(exp_name)
    child_exp = Exp(child_config)
    child_source = f'{source_prefix}:{exp_name}'
    child_spawn_id = uuid.uuid4().hex[:16]
    drain = await drain_run_stream(
        child_exp.run_stream(
            ctx,
            task,
            stop_event=stop_event,
            source_override=child_source,
            spawn_id=child_spawn_id,
        )
    )
    if drain.status == "completed" and drain.final_content:
        return drain.final_content
    return (
        f"SubAgent finished with status={drain.status}, reason={drain.reason}"
    )
```

- [ ] **Step 2: Commit**

```bash
git add matmaster/core/exp.py
git commit -m "refactor: spawn_fn uses drain_run_stream instead of Exp.run"
```

### Task 5: Migrate `DevRunner.run()` to use `drain_run_stream`

**Files:**
- Modify: `matmaster/devshell/runner.py:112-166`

- [ ] **Step 1: Rewrite `DevRunner.run()` to consume `run_stream`**

```python
def run(
    self,
    task: str,
    *,
    stop_event: threading.Event | None = None,
    event_observer: DevEventObserver | None = None,
) -> DrainResult:
    """Execute a single agent run.

    Returns DrainResult with terminal data and message transcript.
    Appends run messages to history for multi-turn accumulation.
    """
    from matmaster.core.stream_drain import DrainResult, drain_run_stream

    exp = Exp(self._exp_config)

    async def _run_once() -> DrainResult:
        try:
            runtime = await exp.build_runtime(self._pg_ctx)
            hooks = [*runtime.spec.hooks, self._stream_hook]

            if event_observer is not None:
                hooks.append(event_observer.hook)
                if runtime.spec.compactor is not None:
                    runtime.spec.compactor._event_sink = (
                        event_observer.make_event_sink()
                    )

            spec = runtime.spec.model_copy(update={"hooks": hooks})
            return await drain_run_stream(
                runtime.kernel.run_stream(
                    spec, task, history=self.history, stop_event=stop_event
                )
            )
        finally:
            await exp._run_cleanup_callbacks()

    result = asyncio.run(_run_once())

    # Accumulate history for non-cancelled runs.
    if result.status != "cancelled":
        skip_count = 1 + len(self.history) + 1  # System + history + User
        new_messages = result.messages[skip_count:]
        self.history.append(UserMessage(content=task))
        self.history.extend(new_messages)

    # Emit RunResultEvent for observer
    if event_observer is not None:
        from matmaster.types.events import RunResultEvent
        event_observer.emit(RunResultEvent(
            source="agent",
            status=result.status,
            reason=result.reason,
            final_content=result.final_content,
        ))

    return result
```

- [ ] **Step 2: Remove `KernelRunResult` import from runner.py**

Delete the import line:
```python
from matmaster.types.runtime import KernelRunResult
```

- [ ] **Step 3: Commit**

```bash
git add matmaster/devshell/runner.py
git commit -m "refactor: DevRunner.run uses drain_run_stream"
```

### Task 6: Migrate `debug_run.py` to use `DrainResult`

**Files:**
- Modify: `matmaster/devshell/debug_run.py:88-96`

- [ ] **Step 1: Update result field access**

```python
    # -- Breakpoint-friendly: step into runner.run() --
    result = runner.run(task, stop_event=stop_event, event_observer=observer)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Status: {result.status} | Reason: {result.reason} | Turns: {result.num_turns}")
    print(f"Usage: {result.usage}")
    if result.final_content:
        print(f"\n--- Final Content ---\n{result.final_content}")
```

- [ ] **Step 2: Commit**

```bash
git add matmaster/devshell/debug_run.py
git commit -m "refactor: debug_run uses DrainResult fields"
```

### Task 7: Migrate `evaluation/core/mat_runner.py`

**Files:**
- Modify: `evaluation/core/mat_runner.py:75-119`

Note: The spec mentions switching tool call extraction to event-based (`ToolCallEvent`/`ToolResultEvent`). We retain `_extract_tool_calls_from_messages(drain_result.messages)` because it already works and `DrainResult.messages` provides the same data. Event-based extraction can be a follow-up if needed.

- [ ] **Step 1: Rewrite `_run()` to use `drain_run_stream`**

```python
    async def _run() -> Any:
        from matmaster.core.stream_drain import drain_run_stream
        try:
            runtime = await exp.build_runtime(pg_ctx)
            return await drain_run_stream(
                runtime.kernel.run_stream(runtime.spec, prompt)
            )
        finally:
            await exp._run_cleanup_callbacks()

    try:
        drain_result = asyncio.run(_run())
    finally:
        try:
            session.close()
        except Exception:
            pass
    duration_ms = int((time.monotonic() - t0) * 1000)

    # 6. Extract answer and tool calls from drain result
    answer = drain_result.final_content or ""
    tool_calls = _extract_tool_calls_from_messages(drain_result.messages)

    # 7. Fallback: try trajectory file if answer is empty
    trajectory_path = _guess_trajectory_file(run_dir=run_dir, task_id=task_id)
    if not answer and trajectory_path is not None and trajectory_path.exists():
        answer = extract_answer_from_trajectory_file(trajectory_path, task_id=task_id)
    if not tool_calls and trajectory_path is not None and trajectory_path.exists():
        tool_calls = extract_tool_calls_from_trajectory_file(
            trajectory_path, task_id=task_id
        )

    return {
        "task_id": task_id,
        "mode": mode,
        "answer": answer,
        "tool_calls": tool_calls,
        "result": {
            "status": drain_result.status,
            "reason": drain_result.reason,
            "num_turns": drain_result.num_turns,
            "usage": drain_result.usage,
        },
        "trajectory_path": str(trajectory_path) if trajectory_path else "",
        "status": drain_result.status,
        "duration_ms": duration_ms,
    }
```

- [ ] **Step 2: Commit**

```bash
git add evaluation/core/mat_runner.py
git commit -m "refactor: mat_runner uses drain_run_stream"
```

---

## Chunk 3: Delete Dead Code

### Task 8: Delete `Exp.run()`

**Files:**
- Modify: `matmaster/core/exp.py:353-388`

- [ ] **Step 1: Delete the `Exp.run()` method**

Remove the entire `run()` method (the `# ── Phase 3a: run ──` section through to `return result.result`). Keep `run_stream()` intact.

- [ ] **Step 2: Clean up dead imports and docstrings in `exp.py`**

Remove `KernelResult` from the import line (no longer used after `Exp.run()` is gone):

```python
# Before:
from matmaster.types.runtime import AgentRuntime, AgentRuntimeSpec, KernelResult
# After:
from matmaster.types.runtime import AgentRuntime, AgentRuntimeSpec
```

Update module and class docstrings that reference `run()`:
- Module docstring lines mentioning `run()` — replace with `run_stream()` references
- Class docstring mentioning `run()` — update to reflect `run_stream` as sole entry
- `_make_spawn_fn` docstring: `runs it via child_exp.run()` → `runs it via child_exp.run_stream()`

- [ ] **Step 3: Run stream tests**

Run: `pytest tests/matmaster/core/test_exp.py -v -k "not run_result and not KernelRunResult"` (skip tests that will be deleted in Task 11)
Expected: Remaining tests PASS

- [ ] **Step 4: Commit**

```bash
git add matmaster/core/exp.py
git commit -m "refactor: delete Exp.run(), run_stream is sole API"
```

### Task 9: Delete `AgentKernel.run()`, `_run_loop()`, `_finish()`, `_call_llm()`, `_do_stream_llm()`

**Files:**
- Modify: `matmaster/core/agent.py`

- [ ] **Step 1: Delete the following methods from AgentKernel**

Delete these methods (line ranges are approximate — verify before deleting):

| Method | Lines |
|--------|-------|
| `run()` | 107-143 |
| `_run_loop()` | 145-278 |
| `_call_llm()` | 628-766 |
| `_do_stream_llm()` | 788-949 |
| `_finish()` | 1186-1215 |

Keep: `run_stream()`, `_run_items()`, `_call_llm_streaming()`, `_stream_llm_items()`, `_sleep_backoff_with_stop_async()`, `_is_valid_natural_finish()`, `_is_incomplete_response()`, `_accumulate_usage()`.

- [ ] **Step 2: Update module-level docstring**

Replace the existing docstring (lines 1-13) with:

```python
"""AgentKernel -- pure async execution loop for the agent kernel.

Consumes an AgentRuntimeSpec and executes the LLM -> guard -> hook -> tool
-> message accumulate -> loop cycle via run_stream(), the sole public API.
run_stream() yields BusEvent objects through the _run_items() generator.

Termination conditions:
- natural: LLM returns no tool_calls
- max_turns: turn counter reaches spec.max_turns
- cancelled: stop_event is set (checked each turn, during stream chunks, retry
  backoff, and between serial tool_calls)
- hook_stopped: should_continue hook returns False
"""
```

- [ ] **Step 3: Remove stale imports that were only used by deleted methods**

Check for imports of `KernelRunResult`, `KernelResult` — remove if no longer referenced in this file.

- [ ] **Step 4: Run stream tests**

Run: `pytest tests/matmaster/core/test_agent_kernel_stream.py -v -k "not TestRunBackwardCompat"`
Expected: All remaining stream tests PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/agent.py
git commit -m "refactor: delete run(), _run_loop, _call_llm, _do_stream_llm, _finish from AgentKernel"
```

### Task 10: Delete `KernelRunResult` and clean up `runtime.py`

**Files:**
- Modify: `matmaster/types/runtime.py:153-163`

- [ ] **Step 1: Delete `KernelRunResult` class**

Remove the `KernelRunResult` dataclass (lines 153-163).

- [ ] **Step 2: Delete `KernelResult.to_run_result_event()` method**

This method (lines 130-137) has no remaining callers after DevRunner migrated to `DrainResult`. Delete the entire method.

- [ ] **Step 3: Update `KernelResult` docstring**

Replace `AgentKernel.run() 的终止结果摘要` with `AgentKernel 的终止结果摘要，由 run_stream 内部产生`.

- [ ] **Step 4: Update `AgentRuntimeSpec` docstring**

Replace `传递给 AgentKernel.run(spec, task)` with `传递给 AgentKernel.run_stream(spec, task)`.

- [ ] **Step 5: Update module-level docstring**

Replace `AgentKernel.run(spec, task)` with `AgentKernel.run_stream(spec, task)` in the module docstring.

- [ ] **Step 6: Remove `RunResultEvent` import if no longer needed**

After deleting `to_run_result_event()`, check if `RunResultEvent` import at line 20 is still used. If not, remove it.

- [ ] **Step 7: Commit**

```bash
git add matmaster/types/runtime.py
git commit -m "refactor: delete KernelRunResult and to_run_result_event, update docstrings"
```

---

## Chunk 4: Delete Tests + Clean Up References

### Task 11: Delete test files and cases that depend on `run()` / `KernelRunResult`

**Files:**
- Delete: `tests/matmaster/core/test_agent_kernel.py`
- Delete: `tests/matmaster/core/test_agent_kernel_extended.py`
- Modify: `tests/matmaster/core/test_agent_kernel_stream.py` — delete `TestRunBackwardCompat` class
- Modify: `tests/matmaster/core/test_exp.py` — delete cases referencing `Exp.run()` or `KernelRunResult`
- Modify: `tests/matmaster/types/test_runtime.py` — delete cases testing `KernelRunResult`
- Modify: `tests/matmaster/devshell/test_compaction_via_devshell.py` — delete cases at lines 739, 790
- Modify: `tests/matmaster/integration/test_e2e_mat_master.py` — delete case at line 238
- Modify: `tests/matmaster/integration/test_upstream_scenarios.py` — delete cases at lines 104, 123
- Modify: `tests/matmaster/integration/test_stream_timeout_retry.py` — delete case at line 57
- Modify: `tests/matmaster/core/test_context_compactor.py` — delete case at line 652
- Delete: `tests/matmaster/integration/test_subagent_spawn.py`
- Modify: `tests/matmaster/devshell/test_integration.py` — delete cases depending on `kernel.run()`
- Modify: `tests/matmaster/integration/test_compaction_real_api.py` — delete cases at lines 284, 350, 376

- [ ] **Step 1: Delete entire test files**

```bash
rm tests/matmaster/core/test_agent_kernel.py
rm tests/matmaster/core/test_agent_kernel_extended.py
rm tests/matmaster/integration/test_subagent_spawn.py
```

- [ ] **Step 2: Delete `TestRunBackwardCompat` from `test_agent_kernel_stream.py`**

Remove the `TestRunBackwardCompat` class and its test methods (around lines 360-385).

- [ ] **Step 3: Delete affected cases from remaining test files**

For each file listed above, find and delete the specific test functions/classes that call `kernel.run()`, `Exp.run()`, or reference `KernelRunResult`. When a test function is the only test in a class, delete the entire class. If removing cases leaves unused fixtures or imports, clean those up too.

For files where all tests depend on `run()` (like `test_integration.py`), check whether any tests remain — if none survive, delete the entire file.

- [ ] **Step 4: Run full test suite to verify**

Run: `pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: No import errors, no `AttributeError` for deleted methods. Some tests may fail for unrelated reasons — verify no failures reference `run()`, `_run_loop`, `KernelRunResult`, or `Exp.run`.

- [ ] **Step 5: Commit**

```bash
git add -A tests/
git commit -m "refactor: delete tests depending on kernel.run() / KernelRunResult"
```

### Task 12: Clean up stale docstring references

**Files:**
- Modify: `matmaster/providers/openai_provider.py` — update `_call_llm()` reference
- Modify: `matmaster/types/llm_provider.py` — update `_call_llm()` reference
- Modify: `matmaster/core/agent.py` — update `_run_items()` docstring mentioning `_run_loop()`

- [ ] **Step 1: Fix `openai_provider.py` docstring**

Find the comment referencing `Kernel._call_llm()` and replace with `Kernel._call_llm_streaming()`.

- [ ] **Step 2: Fix `llm_provider.py` docstring**

Find the comment referencing `Kernel._call_llm()` and replace with `Kernel._call_llm_streaming()`.

- [ ] **Step 3: Fix `_run_items()` docstring in `agent.py`**

Current text: `Mirrors _run_loop() logic but yields events instead of calling hooks`
Replace with: `Core generator loop: yields _KernelItem for each event.`

Note: `exp.py` docstrings were already cleaned up in Task 8 Step 2.

- [ ] **Step 4: Fix `_call_llm_streaming()` docstring in `agent.py`**

Current text (line 550): `Retry wrapper around _stream_llm_items, same retry semantics as _call_llm.`
Replace with: `Retry wrapper around _stream_llm_items with timeout-doubling retry on transient errors.`

- [ ] **Step 5: Run a quick smoke test**

Run: `pytest tests/matmaster/core/test_agent_kernel_stream.py -v -k "not TestRunBackwardCompat"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/providers/openai_provider.py matmaster/types/llm_provider.py matmaster/core/agent.py
git commit -m "chore: update stale docstring references to deleted methods"
```

### Task 13: Final verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -x -v --tb=short 2>&1 | tail -50`

- [ ] **Step 2: Grep for any remaining references to deleted symbols**

```bash
grep -rn "KernelRunResult\|kernel\.run(\|Exp\.run(\|_run_loop\|_call_llm[^_]\|_do_stream_llm\|_finish(" matmaster/ evaluation/ tests/ --include="*.py" | grep -v "__pycache__"
```

Expected: No hits in production code (hits in comments/docs are acceptable).

- [ ] **Step 3: Fix any remaining references found in Step 2**

- [ ] **Step 4: Final commit if any cleanup was needed**

```bash
git add -A
git commit -m "chore: final cleanup of stale references"
```
