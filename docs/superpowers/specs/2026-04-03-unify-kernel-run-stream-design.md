# Unify AgentKernel to Single run_stream Path

Date: 2026-04-03
Status: Approved

## Problem

AgentKernel maintains two parallel execution paths:

- `run()` -> `_run_loop()`: synchronous return of `KernelRunResult`, no intermediate events
- `run_stream()` -> `_run_items()`: generator-first, yields `BusEvent` stream with full observability

Only `run_stream` is used by the production main flow (`agent_run_service` -> `Exp.run_stream` -> `kernel.run_stream`). The `run()` path is only called by DevShell and sub-agent spawn. The two internal loops (`_run_loop` and `_run_items`) are independently maintained duplicates with diverging feature sets — `_run_items` has catalog version caching, `AssistantStateEvent`, `ToolCallEvent`, `ToolResultEvent`, and streaming LLM sub-generator that `_run_loop` lacks.

## Decision

Eliminate the `run()` path entirely. `run_stream()` becomes the sole public API. All callers migrate to consume the event stream via a shared drain helper.

## Changes

### 1. Extend terminal contract (matmaster/types/events.py)

Extend `RunResultEvent` with fields currently only available through `KernelRunResult`:

```python
class RunResultEvent(EventBase):
    type: Literal["run_result", "finish"] = "run_result"
    status: str = "completed"
    reason: str = ""
    final_content: str | None = None
    # New fields
    num_turns: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    messages: list[Any] = Field(default_factory=list)
```

- `num_turns` and `usage`: from `_KernelState.turn` and `_KernelState.total_usage`
- `messages`: from `_KernelState.messages`, the complete conversation transcript

Data source: `_run_items()` terminal `_KernelItem` already has access to `state`. Extend `_TerminalItem` to carry `num_turns`, `usage`, `messages`, then `run_stream()` passes them through to `RunResultEvent`.

### 2. Shared drain helper (matmaster/core/stream_drain.py — new file)

A single `drain_run_stream()` function that all non-streaming callers use:

```python
@dataclass
class DrainResult:
    status: str
    reason: str
    final_content: str | None
    num_turns: int
    usage: dict[str, int]
    messages: list[Any]
    events: list[Any]  # all intermediate events collected during drain

async def drain_run_stream(
    stream: AsyncIterator[Any],
) -> DrainResult:
    """Consume run_stream() to completion, return structured result."""
    events = []
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
    # Stream ended without terminal event — treat as error
    raise RuntimeError("run_stream ended without RunResultEvent")
```

This eliminates drain duplication across callers and standardizes error handling.

### 3. AgentKernel (matmaster/core/agent.py)

Delete:
- `run()` method
- `_run_loop()` method
- `_finish()` helper (only called by `_run_loop`)
- `_call_llm()` method (non-streaming LLM call, only used by `_run_loop`)
- `_do_stream_llm()` method (only called by `_call_llm`)
- Module-level docstring reference to `_finish()` — update to reflect `_run_items` as sole path

Modify:
- `_TerminalItem`: add `num_turns`, `usage`, `messages` fields
- `run_stream()`: pass new `_TerminalItem` fields through to `RunResultEvent`

Retain (unchanged):
- `run_stream()` signature and public behavior
- `_run_items()` — core generator loop
- `_call_llm_streaming()` and retry logic
- `_is_valid_natural_finish()` — used by `_run_items`

### 4. Types (matmaster/types/runtime.py)

Delete:
- `KernelRunResult` dataclass — replaced by `RunResultEvent` + `DrainResult`

### 5. Exp (matmaster/core/exp.py)

Delete:
- `Exp.run()` method (Phase 3a, ~360-388)

Modify `_make_spawn_fn()`:
- `spawn_fn` calls `drain_run_stream(child_exp.run_stream(...))` instead of `child_exp.run()`
- Extracts `final_content` from `DrainResult`
- External signature unchanged: `async (exp_name, task, stop_event) -> str`
- Child agent events are consumed and discarded within `spawn_fn` (signature returns `str` only)

### 6. Evaluation (evaluation/core/mat_runner.py)

Modify `MatRunner`:
- Use `drain_run_stream()` to consume `run_stream()`
- `DrainResult` provides `final_content`, `num_turns`, `usage`
- Tool call extraction: use `DrainResult.events` to filter `ToolCallEvent`/`ToolResultEvent` instead of walking `KernelRunResult.messages`

### 7. DevShell (matmaster/devshell/runner.py)

Modify `DevRunner.run()`:
- Use `drain_run_stream()` to consume `kernel.run_stream(...)`
- History accumulation: use `DrainResult.messages` to extract new messages — same logic as current `KernelRunResult.messages` approach
- `DevStreamHook`/`DevEventHook` callbacks (`on_stream_chunk`, `on_segment_complete`, `pre_tool_call`, `post_tool_call`) will no longer be triggered since `_run_items` yields events instead of calling hooks — DevShell must switch to consuming yielded events for real-time output

Modify `debug_run.py`:
- Switch from `KernelRunResult` field access to `DrainResult` fields (same names, direct mapping)

### 8. Tests

Delete entire files:
- `tests/matmaster/core/test_agent_kernel.py`
- `tests/matmaster/core/test_agent_kernel_extended.py`

Delete specific cases/classes:
- `tests/matmaster/core/test_agent_kernel_stream.py`: `TestRunBackwardCompat` class
- `tests/matmaster/core/test_exp.py`: cases referencing `Exp.run()` or `KernelRunResult`
- `tests/matmaster/types/test_runtime.py`: cases testing `KernelRunResult`
- `tests/matmaster/devshell/test_compaction_via_devshell.py`: calls at lines 739, 790
- `tests/matmaster/integration/test_e2e_mat_master.py`: call at line 238
- `tests/matmaster/integration/test_upstream_scenarios.py`: calls at lines 104, 123
- `tests/matmaster/integration/test_stream_timeout_retry.py`: call at line 57
- `tests/matmaster/core/test_context_compactor.py`: call at line 652
- `tests/matmaster/integration/test_subagent_spawn.py`: all 8 tests mock `Exp.run`
- `tests/matmaster/devshell/test_integration.py`: depends on `kernel.run()` path
- `tests/matmaster/integration/test_compaction_real_api.py`: calls at lines 284, 350, 376

No test migration — these cases are not rewritten for `run_stream`.

## What Does NOT Change

- `run_stream()` public signature, `_run_items()`, `_call_llm_streaming()` and retry logic
- `Exp.run_stream()`
- DevShell CLI/REPL upper-layer call structure (adapts to `DevRunner.run()` return value change)
- Production main flow (`agent_run_service` -> `Exp.run_stream` -> `kernel.run_stream`)

## Stale References to Clean Up

- `matmaster/providers/openai_provider.py`: docstring references `Kernel._call_llm()` — update to `_call_llm_streaming`
- `matmaster/types/llm_provider.py`: docstring references `Kernel._call_llm()` — update
- `matmaster/types/runtime.py`: `KernelResult` docstring references `AgentKernel.run()` — update to `run_stream`
- `matmaster/core/agent.py`: module-level docstring references `_finish()` — update
