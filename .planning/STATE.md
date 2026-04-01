---
gsd_state_version: 1.0
milestone: v2.1
milestone_name: matmaster/ 完全独立化
status: executing
stopped_at: Completed 27-03-PLAN.md
last_updated: "2026-04-01T11:21:42.234Z"
last_activity: 2026-04-01
progress:
  total_phases: 19
  completed_phases: 16
  total_plans: 32
  completed_plans: 32
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-01)

**Core value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界
**Current focus:** Phase 27 — mcp-calculation

## Current Position

Phase: 28
Plan: Not started
Status: Ready to execute
Last activity: 2026-04-01

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
- [v2.1 roadmap] INVR + CONS 合并为 Phase 28，因 bohrium_setup 同时涉及 src 反向依赖和 consumer 迁移
- [v2.1 roadmap] TOOL-09/TOOL-10 合入 Phase 26，与 TOOL-07/TOOL-08 一起完成全部 tool 内化
- [Phase 26-tool]: Lazy import strategy for evomaster.adaptors.calculation: function-level import defers module load
- [Phase 26-tool]: Duck-typing via hasattr replaces isinstance(SSHSession) for cross-package session detection
- [Phase 25-session-playground]: Merged SSHSession + SSHEnv into single class directly holding paramiko.SSHClient (no Env intermediate)
- [Phase 25-session-playground]: Playground parameterized constructor (5 keyword-only params) replaces config_path-based construction
- [Phase 25-session-playground]: PlaygroundManager._load_raw_config returns raw dict (not Pydantic model) to avoid EvoMasterConfig coupling
- [Phase 25-session-playground]: Docker session branch removed; only local and ssh supported
- [Phase 25-session-playground]: agent_run_service reads matmaster_config/ directly instead of via playground.config
- [Phase 27-mcp-calculation]: evomaster.env imports changed to evomaster.env.bohrium to avoid triggering full load chain
- [Phase 27-mcp-calculation]: BaseSession TYPE_CHECKING import removed; session param uses Any + duck-typing for cross-package compatibility
- [Phase 27]: LazyMCPTool.execute calls MCPConnection.call_tool directly, eliminating MCPTool intermediate and asyncio.to_thread
- [Phase 27]: cache_mcp_schemas adapted to dict-based tools_by_server from MCPToolManager

### Pending Todos

None.

### Blockers/Concerns

- API/worker 与本地 Web 主路径在迁移期间必须始终保持可运行
- tests/test_streaming_thought_protocol.py 的收集失败需要并入质量门禁

## Session Continuity

Last session: 2026-04-01T11:14:07.485Z
Stopped at: Completed 27-03-PLAN.md
Resume file: None
