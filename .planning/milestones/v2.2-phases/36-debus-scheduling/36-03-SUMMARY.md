---
phase: 36-debus-scheduling
plan: 03
subsystem: core/exp, core/context_compactor
tags: [debus, api-cleanup, event-sink, generator]
dependency_graph:
  requires: [36-02]
  provides: [bus-free-exp-signatures, event-sink-only-compactor]
  affects: [matmaster/core/exp.py, matmaster/core/context_compactor.py, matmaster/devshell/runner.py]
tech_stack:
  added: []
  patterns: [event-sink-callback, generator-event-collection]
key_files:
  created: []
  modified:
    - matmaster/core/exp.py
    - matmaster/core/context_compactor.py
    - matmaster/devshell/runner.py
    - tests/matmaster/core/test_exp.py
    - tests/matmaster/core/test_context_compactor.py
    - tests/matmaster/core/test_exp_runtime_v2.py
    - tests/matmaster/integration/test_subagent_spawn.py
    - tests/matmaster/integration/test_e2e_mat_master.py
    - tests/matmaster/integration/test_bohrium_execution_contract.py
    - tests/matmaster/integration/test_compaction_real_api.py
    - tests/matmaster/integration/test_pipeline_alignment.py
    - tests/matmaster/integration/test_e2e_minimal.py
    - tests/matmaster/integration/test_upstream_scenarios.py
    - tests/matmaster/devshell/test_compaction_via_devshell.py
decisions:
  - "BohriumSetupResult test adapted to 5-field NamedTuple signature (post-Plan 02)"
  - "E2E pipeline tests switched from kernel.run()+bus-drain to kernel.run_stream() generator collection"
  - "DevShell runner.run() keeps bus parameter in signature for Plan 04 but ignores it"
metrics:
  duration_seconds: 1066
  completed: "2026-04-03T09:23:00Z"
---

# Phase 36 Plan 03: Remove bus= from Exp and ContextCompactor Summary

Bus-free Exp signatures (_make_spawn_fn, build_runtime, run, run_stream) and event_sink-only ContextCompactor, with generator-based regression tests replacing bus-draining patterns.

## What Changed

### Task 1: Remove bus from Exp/runtime/spawn signatures (1b211554)

- Stripped `bus: MessageBus | None` parameter from `Exp._make_spawn_fn()`, `Exp.build_runtime()`, `Exp.run()`, and `Exp.run_stream()` in `matmaster/core/exp.py`
- Removed `from matmaster.core.bus import MessageBus` import from `exp.py`
- Updated `test_exp.py`: replaced bus-specific tests with signature inspection assertions (`test_build_runtime_has_no_bus_parameter`, `test_run_no_bus_parameter`)
- Updated `test_subagent_spawn.py`: all `_make_spawn_fn(ctx, bus=None, ...)` calls changed to `_make_spawn_fn(ctx, ...)`
- Updated `test_e2e_mat_master.py`: E2E pipeline tests switched to `kernel.run_stream()` generator-based event collection; Bohrium event tests adapted from `bus.emit_nowait()` to `event_sink()` callback; `test_bohrium_abort` fixed for 5-field BohriumSetupResult signature
- Updated `test_bohrium_execution_contract.py`: `_make_bohrium_service()` uses `event_sink=` instead of `bus=`; `test_execution_binding_before_build_runtime` adapted from mocked `build_runtime` to mocked `run_stream` async generator

### Task 2: Remove ContextCompactor(bus=...) and rewrite integration tests (a6383543)

- Deleted deprecated `bus: Any | None = None` parameter and `bus.emit` wrapper branch from `ContextCompactor.__init__()`
- Compactor is now event_sink-only: `__init__(config, summary_provider, event_sink=None)`
- Rewrote `test_context_compactor.py` event emission tests to use async sink spies (list append)
- Rewrote `test_compaction_real_api.py` to collect events via `event_sink` list instead of bus draining
- Rewrote `test_pipeline_alignment.py` to use `kernel.run_stream()` generator for event sequence verification
- Rewrote `test_e2e_minimal.py` to use `kernel.run_stream()` generator; asserts `RunResultEvent` from generator

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed BohriumSetupResult 5-field signature in abort test**
- Found during: Task 1
- Issue: `test_bohrium_abort_emits_top_level_error_and_stream_closed` used `BohriumSetupResult(False, ...)` with 2 positional args, but Plan 02 changed BohriumSetupResult to require 5 fields
- Fix: Adapted to `BohriumSetupResult(False, ..., None, None, None)` and simplified abort test to verify abort_result return directly
- Files modified: `tests/matmaster/integration/test_e2e_mat_master.py`
- Commit: 1b211554

**2. [Rule 3 - Blocking] Fixed DevShell runner and callers broken by build_runtime bus removal**
- Found during: Post-task verification
- Issue: `matmaster/devshell/runner.py` called `exp.build_runtime(ctx, bus=bus)`, `test_upstream_scenarios.py` called `exp.build_runtime(pg_ctx, bus=bus)`, `test_compaction_via_devshell.py` used `ContextCompactor(bus=bus)` -- all fail after parameter removal
- Fix: Removed `bus=bus` from build_runtime calls; replaced MessageBus with _EventCollector in devshell compaction tests
- Files modified: `matmaster/devshell/runner.py`, `tests/matmaster/integration/test_upstream_scenarios.py`, `tests/matmaster/devshell/test_compaction_via_devshell.py`
- Commit: a073bbb1

## Deferred Items

- `tests/matmaster/devshell/test_integration.py`: 2 tests (`test_full_run_with_tool_call`, `test_bus_events_emitted`) still depend on bus-based event routing in DevShell. Deferred to Plan 04 which handles DevShell's bus dependency holistically.

## Verification

- 141 tests passed, 7 skipped across all plan-specified and auto-fixed test files
- `rg "bus: MessageBus|bus=" src/services/agent_run_service.py matmaster/core/exp.py matmaster/core/context_compactor.py tests/matmaster/core/test_exp.py tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/integration/test_subagent_spawn.py tests/matmaster/integration/test_compaction_real_api.py tests/matmaster/integration/test_e2e_mat_master.py tests/matmaster/integration/test_bohrium_execution_contract.py tests/matmaster/integration/test_pipeline_alignment.py tests/matmaster/integration/test_e2e_minimal.py` returns zero matches
- `context_compactor.py` contains `event_sink` and no bus fallback branch

## Known Stubs

None -- all stubs in the plan scope are fully implemented.

## Self-Check: PASSED

All key files exist, all 3 commits found (1b211554, a6383543, a073bbb1).
