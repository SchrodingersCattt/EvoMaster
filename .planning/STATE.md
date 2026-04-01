---
gsd_state_version: 1.0
milestone: v2.1
milestone_name: matmaster/ 完全独立化
status: defining_requirements
stopped_at: Redefining v2.1 scope and roadmap
last_updated: "2026-04-01T16:00:00+0800"
last_activity: 2026-04-01
progress:
  total_phases: 0
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-01)

**Core value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界
**Current focus:** 重新定义 v2.1 scope — matmaster 完全独立化

## Current Position

Phase: Not started (defining requirements)
Plan: —
Status: Defining requirements
Last activity: 2026-04-01 — v2.1 scope expanded to full independence (evomaster + playground + src)

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0 in v2.1
- Average duration: -
- Total execution time: 0h

## Accumulated Context

### Decisions

- [v2.1 redefine] 范围从仅 evomaster 解耦扩展为三方向完全独立化（evomaster + playground + src）
- [v2.1 redefine] Phase 编号延续既有历史，从 Phase 25 开始，不重置
- [v2.1 redefine] `matmaster/` 运行时禁止 import evomaster、playground 或 src
- [v2.1 redefine] 不做外部 research，依据代码耦合清单直接定义 requirements

### Pending Todos

None.

### Blockers/Concerns

- `.planning/MILESTONES.md` 的 v1.1 / v2.0 历史仍不完整，当前连续性以 PROJECT/ROADMAP 为准
- `tests/test_streaming_thought_protocol.py` 的收集失败需要并入质量门禁
- API/worker 与本地 Web 主路径在迁移期间必须始终保持可运行

## Session Continuity

Last session: 2026-04-01T16:00:00+0800
Stopped at: Redefining v2.1 scope, proceeding to requirements and roadmap
Resume file: None
