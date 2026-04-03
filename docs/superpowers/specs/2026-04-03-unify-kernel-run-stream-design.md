# Unify AgentKernel to Single run_stream Path

Date: 2026-04-03
Status: Approved

## Problem

AgentKernel maintains two parallel execution paths:

- `run()` -> `_run_loop()`: synchronous return of `KernelRunResult`, no intermediate events
- `run_stream()` -> `_run_items()`: generator-first, yields `BusEvent` stream with full observability

Only `run_stream` is used by the production main flow (`agent_run_service` -> `Exp.run_stream` -> `kernel.run_stream`). The `run()` path is only called by DevShell and sub-agent spawn. The two internal loops (`_run_loop` and `_run_items`) are independently maintained duplicates with diverging feature sets — `_run_items` has catalog version caching, `AssistantStateEvent`, `ToolCallEvent`, `ToolResultEvent`, and streaming LLM sub-generator that `_run_loop` lacks.

## Decision

Eliminate the `run()` path entirely. `run_stream()` becomes the sole public API. All callers migrate to consume the event stream.

## Changes

### AgentKernel (matmaster/core/agent.py)

Delete:
- `run()` method
- `_run_loop()` method
- `_finish()` helper (only called by `_run_loop`)
- `_call_llm()` method (non-streaming LLM call, only used by `_run_loop`)
- `_do_stream_llm()` method (only called by `_call_llm`)
- Module-level docstring reference to `_finish()` — update to reflect `_run_items` as sole path

Retain:
- `run_stream()` — unchanged, sole public entry point
- `_run_items()` — core generator loop, unchanged
- `_call_llm_streaming()` and retry logic — unchanged
- `_is_valid_natural_finish()` — used by `_run_items`

### Types (matmaster/types/runtime.py)

Delete:
- `KernelRunResult` dataclass — no remaining consumers after migration

### Exp (matmaster/core/exp.py)

Delete:
- `Exp.run()` method (Phase 3a, ~360-388)

Modify `_make_spawn_fn()`:
- `spawn_fn` internally drains `child_exp.run_stream()` instead of calling `child_exp.run()`
- Extracts `final_content` from the terminal `RunResultEvent`
- External signature unchanged: `async (exp_name, task, stop_event) -> str`
- Child agent events are consumed and discarded within `spawn_fn` (signature returns `str` only)

### Evaluation (evaluation/core/mat_runner.py)

Modify `MatRunner`:
- Switch from `kernel.run()` to consuming `kernel.run_stream()` or `Exp.run_stream()`
- Drain event stream, extract `final_content` from terminal `RunResultEvent`
- Tool call extraction currently uses `KernelRunResult.messages` — rebuild from `ToolCallEvent`/`ToolResultEvent` in the stream instead

### DevShell (matmaster/devshell/runner.py)

Modify `DevRunner.run()`:
- Switch from `await runtime.kernel.run(...)` to consuming `runtime.kernel.run_stream(...)`
- DevShell drains the event stream and extracts the final result itself
- No wrapper method provided at the Kernel level
- History accumulation: currently relies on `KernelRunResult.messages` to extract new messages — rebuild from `AssistantStateEvent` and `ToolResultEvent` in the stream, or maintain a shadow messages list within DevRunner
- `DevStreamHook`/`DevEventHook` callbacks (`on_stream_chunk`, `on_segment_complete`, `pre_tool_call`, `post_tool_call`) will no longer be triggered since `_run_items` yields events instead of calling hooks — DevShell must switch to consuming yielded events for real-time output

Modify `debug_run.py`:
- Hard-codes `KernelRunResult` field access (`result.result.status`, `.reason`, `.num_turns`, `.usage`, `.final_content`) — adapt to new return type from `DevRunner.run()`

### Tests

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

- `run_stream()`, `_run_items()`, `_call_llm_streaming()` and retry logic
- `Exp.run_stream()`
- DevShell CLI/REPL upper-layer call structure (adapts to `DevRunner.run()` return value change)

## Stale References to Clean Up

- `matmaster/providers/openai_provider.py`: docstring references `Kernel._call_llm()` — update to `_call_llm_streaming`
- `matmaster/types/llm_provider.py`: docstring references `Kernel._call_llm()` — update
- `matmaster/types/runtime.py`: `KernelResult` docstring references `AgentKernel.run()` — update to `run_stream`
- `matmaster/core/agent.py`: module-level docstring references `_finish()` — update
- Production main flow (`agent_run_service` -> `Exp.run_stream` -> `kernel.run_stream`)
