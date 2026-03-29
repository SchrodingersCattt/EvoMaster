---
phase: 20-confirmation-flow-recovery
plan: 02
subsystem: infra
tags: [redis, confirmation, reply-bridge, worker, event-loop]

# Dependency graph
requires:
  - phase: 20-confirmation-flow-recovery plan 01
    provides: "Future-based ConfirmationHook with buffered early reply delivery"
  - phase: 19-tool-dispatch
    provides: "Unified agent-loop orchestration and cleanup order in agent_run_service.py"
provides:
  - "Redis-compatible confirmation reply bridge in agent_run_service.py"
  - "Confirmation gate prepended before runtime hooks for execute_bash"
  - "Service-layer regression coverage for approval, cancel, and bridge thread exit"
affects: [phase-21-async-leaf-io-cleanup, worker-confirmation, direct-mode]

# Tech tracking
tech-stack:
  added: []
  patterns: ["integer-second Redis polling bridge", "prepend confirmation gate before runtime hooks", "bridge stop/join before loop shutdown"]

key-files:
  created: []
  modified:
    - src/services/agent_run_service.py
    - tests/matmaster/integration/test_upstream_scenarios.py
    - .planning/REQUIREMENTS.md

key-decisions:
  - "Use integer-second polling in the bridge because RedisReplyQueue coerces timeout via int(timeout)"
  - "Enable confirmation only for execute_bash as the minimal Phase 20 recovery surface"
  - "Stop and join the confirmation bridge thread before shutting down the agent loop"

patterns-established:
  - "Service-layer confirmation bridge converts blocking ReplyQueueLike input into hook.resolve/cancel callbacks"
  - "confirmation_hook is prepended ahead of runtime.spec.hooks so pre_tool_call can gate execution before observer hooks run"
  - "Integration tests verify both approval and cancellation against the real run_agent_sync hook merge path"

requirements-completed: [HOOK-02]

# Metrics
duration: 40min
completed: 2026-03-30
---

# Phase 20 Plan 02: Service Confirmation Bridge Summary

**Worker reply_queue is reconnected to the restored ConfirmationHook through a Redis-compatible polling bridge, and Phase 21's `execute_bash` path is gated before existing runtime hooks run**

## Performance

- **Duration:** 40 min
- **Started:** 2026-03-29T16:23:00Z
- **Completed:** 2026-03-30T17:03:49Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Added `_start_confirmation_reply_bridge()` so blocking `reply_queue.get()` can wake the async `ConfirmationHook` via `resolve()` and `cancel()`
- Re-enabled confirmation in `run_agent_sync()` only for `execute_bash`, and prepended the gate before existing runtime hooks
- Added service-level regressions proving approval executes the gated tool, cancel skips it, and the bridge thread exits with Redis-compatible polling
- Closed `HOOK-02` in `.planning/REQUIREMENTS.md`
- Kept the new Phase 21 BashTool async subprocess direction intact by gating `execute_bash` at hook level rather than altering tool internals

## Task Commits

Atomic task commits have not been created yet.

- The user asked to align Phase 20 onto the current Phase 21 base before checkpointing
- This summary records the verified working-tree state and the planning traceability updates
- Commit hashes can be added later if you want a dedicated alignment checkpoint

## Files Created/Modified
- `src/services/agent_run_service.py` - Added `_CONFIRM_TOOLS`, the Redis-compatible bridge thread, confirmation hook prepend logic, and cleanup for bridge shutdown
- `tests/matmaster/integration/test_upstream_scenarios.py` - Added `TestAgentRunServiceConfirmationRecovery` coverage for bridge exit, approval, and cancel behavior
- `.planning/REQUIREMENTS.md` - Marked `HOOK-02` as complete and updated coverage summary to 34/35

## Decisions Made
- The bridge polls in integer seconds only; using `0.5` would become `int(0)` inside `RedisReplyQueue` and map to Redis `BLPOP timeout=0`, which blocks forever
- Confirmation remains scoped to `execute_bash` for now, avoiding accidental gating of every tool before a broader policy exists
- The hook ordering is explicitly `confirmation_hook -> runtime.spec.hooks -> observer_hooks` so gating happens before any downstream `pre_tool_call` observer logic

## Deviations from Plan

One cross-plan hardening fix was required while executing 20-02.

- The original 20-01 restoration still dropped replies that arrived before `pre_tool_call()` started waiting
- Plan 02 verification exposed this race, so the hook was hardened with buffered early-reply delivery before the service bridge could pass reliably
- Scope remained within `HOOK-02`; no unrelated behavior was expanded

## Issues Encountered

None during the alignment run. The main subtle issue was the early-reply race, which is now handled inside `ConfirmationHook`.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 20 behavior is functionally recovered in the working tree and verified by targeted hook and service integration tests
- Remaining async gap count is now one requirement (`TOOL-02`)
- Phase 21 can continue without carrying confirmation flow debt forward

---
*Phase: 20-confirmation-flow-recovery*
*Completed: 2026-03-30*
