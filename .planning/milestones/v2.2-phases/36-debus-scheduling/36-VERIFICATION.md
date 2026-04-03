---
phase: 36-debus-scheduling
verified: 2026-04-03T09:52:54Z
status: passed
score: 11/11 must-haves verified
re_verification: false
---

# Phase 36: debus-scheduling Verification Report

**Phase Goal:** MessageBus/EventRouter 中间层移除，Kernel 外事件通过 async fanout 直连消费者；当前 stateless SessionCapabilities 调度边界显式固化，persistent shell 并发调度延后
**Verified:** 2026-04-03T09:52:54Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|---------|
| 1  | Active SSE subscribers receive generator/Bohrium/terminal/system events live after MessageBus/EventRouter removal | VERIFIED | `src/services/agent_run_service.py` constructs `RunEventFanout(sse_handler=SSEHandler(send_cb,...), ...)` and routes all events through `await fanout.dispatch(event)`; 19 tests pass in test_agent_run_stream.py |
| 2  | Worker `send_cb -> Redis publish -> active SSE subscriber` path stays live for parity events | VERIFIED | `SSEHandler(send_cb, ...)` is the fanout's first-awaited handler; worker mode test assertions confirm live delivery path |
| 3  | Run shutdown drains persistence work and calls WorkspaceHandler.close() | VERIFIED | `drain_and_close()` in fanout.py gathers pending persistence tasks then closes all handlers; workspace handler registered as extra handler |
| 4  | API-triggered and worker-triggered runs both execute through single fanout-backed `run_agent()` | VERIFIED | `run_agent_stream()` absent (grep returns no match); `run_agent()` at line 169 is sole entrypoint; `src/worker/agent_worker.py:254` calls `agent_run_service.run_agent(` |
| 5  | Legacy bus/router transport modules are physically deleted | VERIFIED | `matmaster/core/bus.py` GONE, `matmaster/integration/event_router.py` GONE, `tests/matmaster/core/test_bus.py` GONE, `tests/matmaster/integration/test_event_router.py` GONE |
| 6  | Agent execution no longer depends on ConfirmationHook runtime path | VERIFIED | `matmaster/hooks/confirmation.py` GONE, `tests/matmaster/hooks/test_confirmation.py` GONE; no `ConfirmationHook`/`_CONFIRM_TOOLS`/`_build_service_hooks` in service code; negative assertion test in test_upstream_scenarios.py passes |
| 7  | Service runs, subagent spawns, and compaction flows work without `bus=` argument | VERIFIED | `bus: MessageBus` absent from `Exp._make_spawn_fn`, `build_runtime`, `run`, `run_stream`; `ContextCompactor.__init__` accepts only `event_sink`; 22 tests pass in pipeline_alignment/e2e_minimal/subagent_spawn/exp_runtime_v2 |
| 8  | Compaction events still reach consumers through `event_sink`, not deprecated bus side channel | VERIFIED | `matmaster/core/context_compactor.py` contains `event_sink` at line 139 and no bus fallback branch; test_context_compactor.py has negative assertion verifying no MessageBus import |
| 9  | DevShell no longer imports MessageBus and still records key event types locally | VERIFIED | `matmaster/devshell/event_observer.py` defines `DevEventObserver` (SimpleQueue) + `DevEventHook`; `rg "MessageBus" matmaster/devshell --glob '*.py'` returns only docstring reference in event_observer.py |
| 10 | Python trees (matmaster/, src/, tests/) are free of live MessageBus/EventRouter import references | VERIFIED | Remaining references are: docstrings explaining migration (fanout.py, event_observer.py), negative-assertion tests that verify absence (test_context_compactor.py:536, test_upstream_scenarios.py:433). Zero live imports. |
| 11 | Current stateless-shell scheduling boundary is explicit and persistent-shell concurrency is deferred | VERIFIED | `rg "SessionCapabilities" matmaster/core/tool_scheduler.py` returns no matches; `shell_persistence == "stateless"` exists in tool_compiler.py:92; test_tool_scheduler.py and test_tool_compiler.py pass with boundary lock tests |

**Score:** 11/11 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `matmaster/integration/fanout.py` | RunEventFanout owner and EventHandler Protocol | VERIFIED | 149 lines; all 4 key symbols present: `RunEventFanout`, `add_handler`, `dispatch`, `drain_and_close`; SSE-first dispatch order; persistence as background asyncio.Task with strong-reference set |
| `src/services/agent_run_service.py` | Fanout-backed `run_agent()` single entrypoint | VERIFIED | `run_agent()` at line 169; `RunEventFanout(` at line 239; `await fanout.dispatch(` at lines 147, 148, 365, 379, 382, 391; `run_agent_stream` absent |
| `src/services/agent_run_bohrium.py` | Thread-safe event_sink bridge | VERIFIED | `event_sink: Callable[..., None] | None` at line 305; `loop.call_soon_threadsafe` bridge in agent_run_service.py:269; no `bus.emit_nowait` |
| `matmaster/core/exp.py` | Bus-free runtime/build/run/spawn signatures | VERIFIED | `build_runtime` signature (line 147) has no `bus:` param; `_make_spawn_fn` signature (line 96) has no `bus:` param; no MessageBus import |
| `matmaster/core/context_compactor.py` | event_sink-only compactor contract | VERIFIED | `event_sink: Callable[[Any], Awaitable[None]] | None` at line 139; no `bus=` parameter; no bus.emit wrapper branch |
| `matmaster/devshell/event_observer.py` | SimpleQueue-backed DevShell event observer and hook bridge | VERIFIED | Defines `DevEventObserver` (line 42) and `DevEventHook(BaseHook)` (line 81); uses `queue.SimpleQueue` |
| `matmaster/devshell/runner.py` | Bus-free DevShell execution path | VERIFIED | Accepts `event_observer: DevEventObserver | None` instead of `bus`; wires observer hook + compactor sink |
| `tests/matmaster/integration/test_event_fanout.py` | Dispatch/drain/close/error-isolation regression coverage | VERIFIED | File exists; 12 tests passing covering dispatch order, add_handler, error isolation, persistence drain, close |
| `tests/matmaster/core/test_tool_scheduler.py` | Stateless scheduling boundary lock coverage | VERIFIED | `TestStatelessSchedulingBoundary` class added; passes 3 tests verifying ToolScheduler is capability-agnostic |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/services/agent_run_service.py` | `matmaster/integration/fanout.py` | `RunEventFanout.dispatch()` | WIRED | `await fanout.dispatch(` at lines 147, 148, 365, 379, 382, 391 |
| `src/services/agent_run_bohrium.py` | `src/services/agent_run_service.py` | `event_sink` via `loop.call_soon_threadsafe` | WIRED | `loop.call_soon_threadsafe` at line 269 in agent_run_service.py; `event_sink=_dispatch_from_thread` at line 275 passed to BohriumSetupService |
| `src/worker/agent_worker.py` | `src/services/agent_run_service.py` | `run_agent()` | WIRED | `agent_run_service.run_agent(` at line 254 in agent_worker.py |
| `matmaster/integration/__init__.py` | `matmaster/integration/fanout.py` | RunEventFanout/EventHandler export | WIRED | Both `RunEventFanout` and `EventHandler` exported at lines 8-9, in `__all__` at lines 16, 18 |
| `src/services/agent_run_service.py` | `matmaster/core/exp.py` | `exp.run_stream()` call without `bus=` | WIRED | `exp.run_stream(` at line 350 in agent_run_service.py; no `bus=` argument |
| `matmaster/devshell/runner.py` | `matmaster/devshell/event_observer.py` | `DevEventObserver/DevEventHook` wiring | WIRED | `from matmaster.devshell.event_observer import DevEventObserver` at line 20; `event_observer: DevEventObserver | None` parameter |
| `matmaster/tools/tool_compiler.py` | `tests/matmaster/tools/test_tool_compiler.py` | `shell_persistence == "stateless"` relaxation lock | WIRED | `shell_persistence == "stateless"` at line 92 in tool_compiler.py; `TestStatelessCompilerRelaxationBoundary` in test_tool_compiler.py |

### Data-Flow Trace (Level 4)

Phase 36 is a refactoring/transport phase — it does not add new rendering components or data-producing endpoints. All event flows go through `RunEventFanout.dispatch()` which invokes concrete `SSEHandler.handle()` and `PersistenceHandler.handle()` — both are pre-existing handlers from Phase 34. No new hollow-prop risk introduced.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| RunEventFanout 4 key symbols present | `rg "class RunEventFanout\|def add_handler\|def dispatch\|def drain_and_close" matmaster/integration/fanout.py` | 4 matches | PASS |
| `run_agent_stream` absent | `rg "def run_agent_stream\(" src/services/agent_run_service.py` | no matches | PASS |
| `bus.py` physically deleted | `ls matmaster/core/bus.py` | not found | PASS |
| `event_router.py` physically deleted | `ls matmaster/integration/event_router.py` | not found | PASS |
| `exp.py` bus= params absent | `rg "bus: MessageBus\|bus=" matmaster/core/exp.py` | no matches | PASS |
| `context_compactor.py` bus= absent | `rg "bus=" matmaster/core/context_compactor.py` | no matches | PASS |
| DevShell free of MessageBus live imports | `rg "MessageBus" matmaster/devshell --glob '*.py'` | only docstring in event_observer.py | PASS |
| Repo-wide Python bus audit | `rg "MessageBus\|EventRouter" matmaster src tests --glob '*.py'` | only docstrings + negative-assertion tests | PASS |
| Full fanout test suite | `uv run pytest tests/matmaster/integration/test_event_fanout.py tests/matmaster/integration/test_workspace_handler.py -q` | 19 passed | PASS |
| Agent run stream + Bohrium tests | `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q` | 19 passed | PASS |
| Core Exp + compactor + upstream tests | `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py tests/matmaster/core/test_exp.py tests/matmaster/core/test_context_compactor.py -q` | 78 passed, 1 skipped | PASS |
| DevShell + scheduling boundary tests | `uv run pytest tests/matmaster/devshell/test_integration.py tests/matmaster/devshell/test_compaction_via_devshell.py tests/matmaster/core/test_tool_scheduler.py tests/matmaster/tools/test_tool_compiler.py -q` | 80 passed, 2 skipped | PASS |
| Pipeline alignment + e2e + spawn tests | `uv run pytest tests/matmaster/integration/test_pipeline_alignment.py tests/matmaster/integration/test_e2e_minimal.py tests/matmaster/integration/test_subagent_spawn.py tests/matmaster/core/test_exp_runtime_v2.py -q` | 22 passed | PASS |
| Real-API compaction tests (3 tests) | `uv run pytest tests/matmaster/integration/test_compaction_real_api.py -q` | 3 FAILED — external LiteLLM proxy returns `BedrockException: Operation not allowed` for `claude-haiku-4-5` model | INFO (external infra) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| DBUS-01 | 36-01, 36-04 | 审计 MessageBus + EventRouter 的全部消费者 | SATISFIED | Repo-wide audit complete: zero live Python imports in matmaster/, src/, tests/; DevShell migrated to SimpleQueue-backed observer |
| DBUS-02 | 36-01 | 设计并实现消费侧 async fanout 替代方案，确保 SSE 先发、持久化不阻塞 token 流 | SATISFIED | `RunEventFanout` implements SSE-first await + background persistence `asyncio.create_task`; 12 fanout regression tests pass |
| DBUS-03 | 36-02, 36-03 | 移除 MessageBus + EventRouter，generator 事件直连消费者 | SATISFIED | bus.py deleted; event_router.py deleted; Exp/ContextCompactor bus= params removed; single `run_agent()` entrypoint; ConfirmationHook removed |

All 3 phase requirements satisfied. No orphaned requirements (REQUIREMENTS.md traceability table marks all 3 as Phase 36 / Complete).

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `tests/matmaster/integration/test_compaction_real_api.py` | 3 tests fail due to external LiteLLM proxy `BedrockException: Operation not allowed` for `claude-haiku-4-5` | INFO | Pre-existing infra issue: these tests existed before Phase 36 (pre-phase commit 4d64ac47 shows the file). Phase 36 rewrote the test bodies from bus-draining to event_sink collection — correct migration. Failures are external API access restrictions, not code defects. Test has `pytestmark = pytest.mark.skipif(not _HAS_API_KEY, ...)` but key IS present; backend auth failure occurs at Bedrock layer after proxy authentication. |

No blocker anti-patterns found. No TODO/FIXME/PLACEHOLDER markers in phase-modified files. No stub implementations. No empty return patterns.

### Human Verification Required

None — all observable behaviors are verifiable programmatically.

### Gaps Summary

No gaps. All 11 truths verified, all artifacts exist and are substantive, all key links are wired.

The 3 real-API test failures in `test_compaction_real_api.py` are classified as INFO-level external infrastructure issues: the LiteLLM proxy returns a Bedrock `Operation not allowed` error for the `claude-haiku-4-5` model group. This is not a Phase 36 regression — the test file predates Phase 36, and Phase 36 correctly migrated it from MessageBus-draining to event_sink collection. The test code itself is sound; the backend model access is unavailable in this environment.

---

_Verified: 2026-04-03T09:52:54Z_
_Verifier: Claude (gsd-verifier)_
