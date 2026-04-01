---
gsd_state_version: 1.0
milestone: v2.1
milestone_name: matmaster/ 完全独立化
status: executing
stopped_at: "Completed 27-01-PLAN.md"
last_updated: "2026-04-01T10:44:29Z"
last_activity: 2026-04-01 -- Phase 27 Plan 01 MCP native subsystem complete
progress:
  total_phases: 19
  completed_phases: 15
  total_plans: 32
  completed_plans: 30
  percent: 94
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-01)

**Core value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界
**Current focus:** Phase 27 -- mcp-calculation

## Current Position

Phase: 27 (mcp-calculation) -- EXECUTING
Plan: 2 of 3
Status: Plan 01 complete, executing Plan 02
Last activity: 2026-04-01 -- Phase 27 Plan 01 MCP native subsystem complete

Progress: [█████████░] 94%

## Performance Metrics

**Velocity:**

- Total plans completed: 1 in Phase 27
- Average duration: 3min
- Total execution time: 3min

## Accumulated Context

### Decisions

- [v2.1 redefine] 范围从仅 evomaster 解耦扩展为三方向完全独立化（evomaster + playground + src）
- [v2.1 redefine] Phase 编号延续既有历史，从 Phase 25 开始，不重置
- [v2.1 roadmap] INVR + CONS 合并为 Phase 28，因 bohrium_setup 同时涉及 src 反向依赖和 consumer 迁移
- [v2.1 roadmap] TOOL-09/TOOL-10 合入 Phase 26，与 TOOL-07/TOOL-08 一起完成全部 tool 内化
- [Phase 27-01]: MCPToolManager._build_tools stores lightweight dicts instead of MCPTool instances
- [Phase 27-01]: add_server uses simple sequential retry instead of runner task pattern
- [Phase 27-01]: MCP_CONNECT_TIMEOUT moved from manager.py to connection.py as shared constant

### Pending Todos

None.

### Blockers/Concerns

- API/worker 与本地 Web 主路径在迁移期间必须始终保持可运行
- tests/test_streaming_thought_protocol.py 的收集失败需要并入质量门禁

## Session Continuity

Last session: 2026-04-01T10:44:29Z
Stopped at: Completed 27-01-PLAN.md
Resume file: .planning/phases/27-mcp-calculation/27-01-SUMMARY.md
