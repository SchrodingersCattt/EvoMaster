---
phase: 32-kernel-generator-tool-runtime-v2
plan: 03
subsystem: core
tags: [async-generator, kernel, streaming, tool-runner, event-system]

# Dependency graph
requires:
  - phase: 32-02
    provides: ToolRunner Protocol + InlineToolRunner + ToolCatalog + AgentRuntimeSpec v2 fields
provides:
  - _run_items() AsyncGenerator as unified kernel execution path
  - run_stream() public streaming interface yielding BusEvent
  - run() backward-compatible delegation through _run_items()
  - _resolve_tool_definitions() dual-path (Phase 1 registry / Phase 2 catalog)
  - _KernelItem / _KernelState / _TerminalItem private types
  - spec.tool_runner integration with InlineToolRunner fallback
affects: [34 Exp/Service run_stream integration, 34 Hook retirement, 35 ToolRegistry degradation]

# Tech tracking
tech-stack:
  added: []
  patterns: [AsyncGenerator for unified execution path, dataclass-based kernel state isolation, dual-path tool resolution with version caching]

key-files:
  created:
    - tests/matmaster/core/test_agent_kernel_stream.py
  modified:
    - matmaster/core/agent.py

key-decisions:
  - "_run_items() uses local _KernelState dataclass, not self attributes, preserving Kernel statelessness and concurrency safety"
  - "Phase 1 _run_items() only yields final-snapshot events (ResponseEvent, ThoughtEvent), not per-tool ToolCallEvent/ToolResultEvent (those stay in Hook/EventEmitterHook path until Phase 34 retirement)"
  - "_call_llm() receives tool_defs as parameter from _resolve_tool_definitions() instead of inline resolution"
  - "_make_terminal() helper function extracts status resolution logic from deleted _finish() static method"

patterns-established:
  - "_run_items() generator pattern: yield _KernelItem(event=...) for BusEvent snapshots, yield _KernelItem(messages_delta=...) for transcript collection, yield _KernelItem(terminal=...) for termination"
  - "run_stream()/run() both consume _run_items(), ensuring single execution path with no behavioral divergence"
  - "_resolve_tool_definitions() dual-path: spec.tool_catalog with version caching takes precedence over spec.tool_registry fallback"

requirements-completed: [KGEN-01, KGEN-02, KGEN-03, KGEN-04, KGEN-05, TRUN-05, TDEF-01, REGR-01, REGR-03]

# Metrics
duration: 6min
completed: 2026-04-02
---

# Phase 32 Plan 03: Kernel Generator-First + run_stream() + _resolve_tool_definitions Summary

**_run_items() AsyncGenerator replaces _run_loop(), run_stream() yields BusEvent for streaming consumers, run() delegates unchanged -- 50 kernel tests pass (39 existing zero-modification + 11 new)**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-02T09:53:02Z
- **Completed:** 2026-04-02T09:59:05Z
- **Tasks:** 1 (TDD: RED + GREEN commits)
- **Files modified:** 2

## Accomplishments
- Transformed _run_loop() into _run_items() AsyncGenerator as the single execution path for the kernel
- Added run_stream() public interface yielding BusEvent sequence (ThoughtEvent, ResponseEvent, RunResultEvent)
- Rewrote run() to delegate through _run_items(), maintaining 100% backward compatibility with zero test modifications
- Added _resolve_tool_definitions() with Phase 1 registry fallback and Phase 2 catalog version-caching path
- Integrated spec.tool_runner with InlineToolRunner automatic fallback when tool_runner is None
- Removed _run_loop(), _finish(), and _ToolOutcome (all replaced by generator pattern)
- All 50 kernel tests pass: 39 existing (unchanged) + 11 new stream/generator tests

## Task Commits

Each task was committed atomically:

1. **Task 1: TDD RED - failing tests** - `62fe0035` (test)
2. **Task 1: TDD GREEN - implementation** - `4fcf9108` (feat)

_Task 1 followed TDD flow with separate RED and GREEN commits_

## Files Created/Modified

### Created
- `tests/matmaster/core/test_agent_kernel_stream.py` - 11 tests: run_stream (natural/tools/max_turns/cancelled/thought/ends_with_run_result), run delegation, _resolve_tool_definitions (registry/catalog), _KernelState locality, tool_runner fallback

### Modified
- `matmaster/core/agent.py` - Core transformation: _run_loop() -> _run_items() generator, added run_stream(), rewrote run() delegation, added _TerminalItem/_KernelItem/_KernelState types, added _resolve_tool_definitions(), removed _run_loop()/_finish()/_ToolOutcome

## Decisions Made
- **Local state via _KernelState**: All loop state lives in a per-invocation _KernelState dataclass, not on self. This preserves AgentKernel's statelessness and makes concurrent usage safe.
- **Phase 1 event strategy**: _run_items() yields only final-snapshot events (ResponseEvent for natural finish content, ThoughtEvent for reasoning). ToolCallEvent/ToolResultEvent remain in the Hook/EventEmitterHook path during Phase 1. Phase 34 will migrate tool events to the generator and retire hooks.
- **_call_llm receives tool_defs parameter**: Instead of resolving tool definitions inline in _call_llm, the caller (_run_items) resolves via _resolve_tool_definitions() and passes the result. This supports per-turn catalog version checking.
- **_make_terminal helper**: Extracted status-to-reason mapping from deleted _finish() into a module-level function, keeping the generator code clean.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Worktree did not have Plan 01/02 commits (parallel execution). Resolved by fast-forward merge from the Plan 02 worktree branch.
- 4 pre-existing test failures unchanged from Plan 01/02: web_search rename (2 tests), import audit (playground-skills scripts), real API test (Bedrock auth)

## Known Stubs
None -- all implementations are complete with full test coverage.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- _run_items() generator ready for Phase 34 Exp.run_stream() and AgentRunService.run_agent_stream() integration
- run_stream() can be consumed via `async for event in kernel.run_stream(spec, task)` by any upper layer
- _resolve_tool_definitions() ready for Phase 34 catalog-first path activation
- Hook retirement (Phase 34): EventEmitterHook can be replaced by migrating ToolCallEvent/ToolResultEvent yields into _run_items()
- Phase 32 complete: all 3 plans delivered, kernel generator + Tool Runtime v2 core skeleton ready

---
## Self-Check: PASSED

All 2 key files verified present. All 2 commit hashes verified in git log.

---
*Phase: 32-kernel-generator-tool-runtime-v2*
*Completed: 2026-04-02*
