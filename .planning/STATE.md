---
gsd_state_version: 1.0
milestone: v2.2
milestone_name: AgentKernel Generator-First + Tool Runtime v2
status: executing
stopped_at: Completed 36-02-PLAN.md
last_updated: "2026-04-03T09:03:27.937Z"
last_activity: 2026-04-03
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 19
  completed_plans: 17
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-02)

**Core value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界
**Current focus:** Phase 36 — debus-scheduling

## Current Position

Phase: 36 (debus-scheduling) — EXECUTING
Plan: 3 of 4
Status: Ready to execute
Last activity: 2026-04-03

Progress: [====================] 100%

## Accumulated Context

### Decisions

(v2.1 决策已归档到 milestones/v2.1-ROADMAP.md，清空以备下一里程碑)

- [Phase 35]: write_file excluded from ReadBeforeModifyGuard._MODIFY_TOOLS; uses validate_input (needs session.path_exists)
- [Phase 35]: AgentRuntimeSpec gains read_tracker field; agent.py passes it to GuardPipeline constructor
- [Phase 35]: ToolCatalog is sole upper-layer facade; ContextBuilder generic tools section; _SimpleTestToolRunner for kernel test compatibility
- [Phase 36]: EventHandler Protocol moved to fanout.py for post-deletion survival
- [Phase 36]: BohriumSetupService takes event_sink: Callable instead of bus: MessageBus
- [Phase 36]: set[asyncio.Task] for pending persistence (Python 3.10 compat, no TaskGroup)
- [Phase 36]: bus.py retained as deprecated stub: exp.py/devshell still import MessageBus; physical deletion deferred to Plan 03/04
- [Phase 36]: ConfirmationHook fully deleted (D-03/D-04); confirmation_request event type preserved; reply-queue dormant for v2.3

### Pending Todos

None.

### Blockers/Concerns

- tests/test_streaming_thought_protocol.py 的收集失败需要并入质量门禁

## Session Continuity

Last session: 2026-04-03T09:03:27.934Z
Stopped at: Completed 36-02-PLAN.md
Resume file: None
