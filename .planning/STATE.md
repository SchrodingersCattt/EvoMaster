---
gsd_state_version: 1.0
milestone: v2.1
milestone_name: matmaster/ 与 evomaster/ 彻底解耦
status: ready_to_plan
stopped_at: Roadmap created; Phase 25 is next
last_updated: "2026-04-01T14:28:24+0800"
last_activity: 2026-04-01
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-01)

**Core value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界
**Current focus:** Phase 25 规划就绪 - Session 与 Playground 原生化

## Current Position

Phase: 25 of 30 (Session 与 Playground 原生化)
Plan: 0 of TBD
Status: Ready to plan
Last activity: 2026-04-01 - v2.1 roadmap created and all 15 requirements mapped

Progress: [░░░░░░░░░░] 0% (0/6 phases, plans TBD)

## Performance Metrics

**Velocity:**
- Total plans completed: 0 in v2.1
- Average duration: -
- Total execution time: 0h

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 25-30 | TBD | - | - |

**Recent Trend:**
- Last 5 plans: -
- Trend: Baseline not established

## Accumulated Context

### Decisions

- [v2.1 roadmap] Phase 编号延续既有历史，从 Phase 25 开始，不重置
- [v2.1 roadmap] 解耦顺序采用 foundation -> builtin tools -> MCP/calculation -> consumers -> main paths -> audit/docs
- [v2.1 roadmap] `matmaster/` 运行时内禁止继续直接依赖 `evomaster`，兼容层只允许留在 `src/` 或历史入口边界
- [v2.1 roadmap] 不做外部 research，直接以 PROJECT.md 与 REQUIREMENTS.md 作为里程碑依据

### Pending Todos

None.

### Blockers/Concerns

- `.planning/MILESTONES.md` 的 v1.1 / v2.0 历史仍不完整，当前连续性以 PROJECT/ROADMAP 为准
- `tests/test_streaming_thought_protocol.py` 的收集失败需要并入 Phase 30 质量门禁视角统一处理
- API/worker 与本地 Web 主路径在迁移期间必须始终保持可运行，不能为了解耦牺牲主验证链路

## Session Continuity

Last session: 2026-04-01T14:28:24+0800
Stopped at: ROADMAP.md, REQUIREMENTS.md, STATE.md updated for v2.1
Resume file: None
