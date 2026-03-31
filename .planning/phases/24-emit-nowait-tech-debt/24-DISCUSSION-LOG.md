# Phase 24: emit_nowait Tech Debt Cleanup - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-30
**Phase:** 24-emit-nowait-tech-debt
**Areas discussed:** Migration scope, MessageBus retention, Stale comment scope, Type annotation fix
**Mode:** --auto (all decisions auto-selected)

---

## Migration Scope

| Option | Description | Selected |
|--------|-------------|----------|
| EventEmitterHook only (6 calls) | Only migrate the 6 emit_nowait in core/hooks.py per ROADMAP literal reading | |
| All matmaster/ hooks (10 calls) | Migrate all 4 hook files (hooks.py + 3 in hooks/) | |
| All matmaster/ emit_nowait (12 calls) | Migrate all hooks + ContextCompactor | ✓ |

**User's choice:** [auto] All matmaster/ emit_nowait (12 calls) -- recommended default
**Notes:** ROADMAP's "7 处" was an early estimate. All 12 calls run in async kernel context since Phase 17. Consistent migration avoids leaving confusing mixed patterns.

---

## MessageBus.emit_nowait Retention

| Option | Description | Selected |
|--------|-------------|----------|
| Keep emit_nowait | Retain for service layer sync callers, update docstring | ✓ |
| Remove emit_nowait | Delete method entirely, service layer refactored | |

**User's choice:** [auto] Keep emit_nowait -- recommended default
**Notes:** src/services/agent_run_service.py has 10 sync emit_nowait calls. Service layer is out of scope for v2.0.

---

## Stale Comment Scope

| Option | Description | Selected |
|--------|-------------|----------|
| hooks.py only | Only fix the 1 stale comment in core/hooks.py | |
| All 4 files | Fix stale "sync kernel context" in all hook files | ✓ |

**User's choice:** [auto] All 4 files -- recommended default
**Notes:** The same stale pattern appears in assistant_state.py, output_processor.py, skill_hit.py. Cleaning all is consistent.

---

## Claude's Discretion

- bus.py class docstring wording after emit_nowait scope clarification
- Test mock adaptations (if emit vs emit_nowait assertions exist)

## Deferred Ideas

None -- focused tech debt phase, no scope creep.
