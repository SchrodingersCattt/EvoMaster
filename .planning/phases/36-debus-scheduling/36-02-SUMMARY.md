---
phase: 36-debus-scheduling
plan: 02
subsystem: service-layer
tags: [entrypoint-collapse, debus, confirmation-removal, fanout, transport-deletion]

# Dependency graph
requires:
  - phase: 36-01
    provides: RunEventFanout, BohriumSetupService event_sink, fanout dispatch in run_agent_stream()
provides:
  - single run_agent() entrypoint (fanout-backed)
  - deleted EventRouter/bus transport surface
  - deleted ConfirmationHook runtime path
affects:
  - matmaster/core/exp.py (still imports bus.py stub -- Plan 03 removes)
  - matmaster/devshell/ (still imports bus.py stub -- Plan 04 removes)

# Tech stack
added: []
patterns:
  - "importlib.import_module for lazy submodule access in __getattr__"

# Key files
created: []
modified:
  - src/services/agent_run_service.py
  - matmaster/core/__init__.py
  - matmaster/integration/__init__.py
  - matmaster/hooks/__init__.py
  - matmaster/core/bus.py
  - tests/matmaster/services/test_agent_run_stream.py
  - tests/matmaster/integration/test_upstream_scenarios.py
deleted:
  - matmaster/integration/event_router.py
  - matmaster/hooks/confirmation.py
  - tests/matmaster/core/test_bus.py
  - tests/matmaster/integration/test_event_router.py
  - tests/matmaster/hooks/test_confirmation.py

# Decisions
key-decisions:
  - "bus.py retained as deprecated stub: exp.py and devshell still import MessageBus; physical deletion deferred to Plan 03/04"
  - "ReplyQueueLike and _poll_reply_queue kept as dormant plumbing for future v2.3 bidirectional stream"
  - "confirmation_request event type preserved in events.py per D-03 scope"
  - "importlib.import_module used in core/__init__.__getattr__ to avoid recursive import when resolving matmaster.core.exp"

# Metrics
duration: 895s
completed: "2026-04-03T09:01:19Z"
tasks_completed: 2
tasks_total: 2
files_changed: 11
lines_removed: ~2055
lines_added: ~66
---

# Phase 36 Plan 02: Service Entrypoint Collapse + Transport Deletion Summary

Single fanout-backed run_agent() entrypoint with EventRouter/bus transport and ConfirmationHook runtime path physically deleted.

## What Changed

### Task 1: Collapse to one service entrypoint (8ba0c778)

- **Deleted** old bus-backed `run_agent()` implementation (L216-487): MessageBus + EventRouter + kernel.run() path
- **Renamed** `run_agent_stream()` to `run_agent()` as the sole entrypoint
- **Deleted** `matmaster/integration/event_router.py` (133 lines, EventRouter class + EventHandler Protocol)
- **Deleted** `tests/matmaster/core/test_bus.py` and `tests/matmaster/integration/test_event_router.py` (1073 lines combined)
- **Updated** `matmaster/core/__init__.py`: removed `MessageBus` from exports
- **Updated** `matmaster/integration/__init__.py`: replaced EventRouter/EventHandler imports with RunEventFanout/EventHandler from fanout.py
- **Rewrote** `tests/matmaster/services/test_agent_run_stream.py`: all tests exercise `run_agent()` only; explicit assertion that `run_agent_stream()` no longer exists

### Task 2: Delete ConfirmationHook runtime plumbing (4f7bd25c)

- **Deleted** `matmaster/hooks/confirmation.py` (68 lines, ConfirmationHook class)
- **Deleted** `tests/matmaster/hooks/test_confirmation.py` (126 lines)
- **Emptied** `matmaster/hooks/__init__.py` exports (all business hooks now retired)
- **Rewrote** `tests/matmaster/integration/test_upstream_scenarios.py`: removed TestAgentRunServiceConfirmationRecovery class and all confirmation helpers; retained reply-queue poll tests as dormant plumbing; fixed BohriumSetupService test to use event_sink instead of bus
- **Retained** `confirmation_request` event type definition in `matmaster/types/events.py`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] bus.py retained as deprecated stub instead of physical deletion**
- **Found during:** Task 1
- **Issue:** Plan says to delete `matmaster/core/bus.py`, but `matmaster/core/exp.py` (not in Task 1 scope) imports `MessageBus` from it. Deleting bus.py causes ModuleNotFoundError when any test touches exp.py.
- **Fix:** Kept bus.py as a minimal stub with deprecation docstring. Plan 03 removes the `bus=` parameter from Exp, at which point bus.py is physically deleted.
- **Files modified:** matmaster/core/bus.py
- **Commit:** 8ba0c778

**2. [Rule 3 - Blocking] core/__init__.__getattr__ needed importlib for submodule access**
- **Found during:** Task 1
- **Issue:** After removing MessageBus from core/__init__.py exports, `patch('matmaster.core.exp.Exp')` in tests triggered `__getattr__('exp')` which needed to resolve the submodule. Direct `from . import exp` caused recursion.
- **Fix:** Used `importlib.import_module("matmaster.core.exp")` in `__getattr__` to break the recursive import chain.
- **Files modified:** matmaster/core/__init__.py
- **Commit:** 8ba0c778

**3. [Rule 1 - Bug] BohriumSetupService test used obsolete bus= keyword**
- **Found during:** Task 2
- **Issue:** `test_bohrium_setup_lifecycle` in test_upstream_scenarios.py passed `bus=MessageBus()` to BohriumSetupService, but Plan 01 changed the parameter to `event_sink=`.
- **Fix:** Updated test to use `event_sink=MagicMock()`.
- **Files modified:** tests/matmaster/integration/test_upstream_scenarios.py
- **Commit:** 4f7bd25c

## Known Stubs

None -- no stubs were introduced. The bus.py deprecation stub is documented above as a deviation with a clear removal plan (Plan 03).

## Verification

```
$ uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/integration/test_event_fanout.py tests/matmaster/test_bohrium_setup_injection.py tests/matmaster/integration/test_upstream_scenarios.py -q
44 passed
```

All acceptance criteria verified:
- `run_agent_stream()` method absent from AgentRunService
- `MessageBus`/`EventRouter` absent from core/__init__.py and integration/__init__.py exports
- `event_router.py`, `test_bus.py`, `test_event_router.py` physically deleted
- `confirmation.py`, `test_confirmation.py` physically deleted
- `ConfirmationHook`/`_CONFIRM_TOOLS`/`_build_service_hooks` absent from service code
- `confirmation_request` event type still defined in events.py

## Self-Check: PASSED

All created files exist, all deleted files absent, all commit hashes found.
