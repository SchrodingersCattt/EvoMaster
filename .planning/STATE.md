---
gsd_state_version: 1.0
milestone: v2.1
milestone_name: matmaster/ 完全独立化
status: executing
stopped_at: Completed 30-01-PLAN.md
last_updated: "2026-04-02T01:35:00Z"
last_activity: 2026-04-02 -- Phase 30 Plan 01 complete
progress:
  total_phases: 19
  completed_phases: 18
  total_plans: 40
  completed_plans: 38
  percent: 97
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-01)

**Core value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界
**Current focus:** Phase 30 — decoupling-audit

## Current Position

Phase: 30 (decoupling-audit) — EXECUTING
Plan: 2 of 3
Status: Executing Phase 30
Last activity: 2026-04-02 -- Phase 30 Plan 01 complete (isolation test infrastructure)

Progress: [==================..] 97%

## Performance Metrics

**Velocity:**
- Total plans completed: 14 in v2.1 (Phases 25-30)
- Average duration: ~15min
- Total execution time: ~3.5h

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 29-01 | workspace_resolver + imports | 5min | 2 | 6 |
| 29-02 | legacy-deletion-config-migration | 4min | 2 | 384 |
| 30-01 | isolation-test-infrastructure | 14min | 2 | 16 |

## Accumulated Context

### Decisions

- [v2.1 redefine] 范围从仅 evomaster 解耦扩展为三方向完全独立化（evomaster + playground + src）
- [v2.1 redefine] Phase 编号延续既有历史，从 Phase 25 开始，不重置
- [v2.1 roadmap] INVR + CONS 合并为 Phase 28，因 bohrium_setup 同时涉及 src 反向依赖和 consumer 迁移
- [v2.1 roadmap] TOOL-09/TOOL-10 合入 Phase 26，与 TOOL-07/TOOL-08 一起完成全部 tool 内化
- [Phase 30-01] Import audit uses allowlist pattern (24 known violations) for tracking decoupling progress
- [Phase 30-01] LocalSession stop_event implemented via Popen polling (was no-op)
- [Phase 30-01] SSHSession not in matmaster -- importorskip used for evomaster SSH in bohrium test

### Pending Todos

None.

### Blockers/Concerns

- API/worker 与本地 Web 主路径在迁移期间必须始终保持可运行
- tests/test_streaming_thought_protocol.py 的收集失败需要并入质量门禁

## Session Continuity

Last session: 2026-04-02T01:35:00Z
Stopped at: Completed 30-01-PLAN.md
Resume file: .planning/phases/30-decoupling-audit/30-01-SUMMARY.md
