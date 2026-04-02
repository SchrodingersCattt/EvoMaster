---
gsd_state_version: 1.0
milestone: v2.1
milestone_name: matmaster/ 完全独立化
status: executing
stopped_at: Completed 31-02-PLAN.md
last_updated: "2026-04-02T04:16:04.496Z"
last_activity: 2026-04-02
progress:
  total_phases: 20
  completed_phases: 20
  total_plans: 42
  completed_plans: 42
  percent: 98
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-01)

**Core value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界
**Current focus:** Phase 31 — tech-debt-cleanup

## Current Position

Phase: 31
Plan: Not started
Status: Ready to execute
Last activity: 2026-04-02

Progress: [===================.] 98%

## Performance Metrics

**Velocity:**

- Total plans completed: 14 in v2.1 (Phases 25-31)
- Average duration: ~14min
- Total execution time: ~3.1h

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 29-01 | workspace_resolver + imports | 5min | 2 | 6 |
| Phase 29-02 Plegacy-deletion-config-migration | 4min | 2 tasks | 384 files |
| Phase 30-02 Pevomaster-deletion-config | 9min | 2 tasks | 120 files |
| Phase 30 P03 | 3min | 1 tasks | 1 files |
| Phase 31-01 | 7min | 2 tasks | 6 files |
| Phase 31 P02 | 5min | 2 tasks | 20 files |

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
- [Phase 28-src-consumer]: Simplified BOHRIUM_OPENAPI_HOST to single os.getenv with static default (no URL_PART logic)
- [Phase 28-src-consumer]: BohriumSetupResult duplicated as NamedTuple in matmaster to break src reverse dependency
- [Phase 28-src-consumer]: Import audit xfail strategy for pre-migration tests (strict=False)
- [Phase 28]: BohriumSetupService: callback injection with 4 callables replaces sessions_service injection; consumer uses functools.partial for binding
- [Phase 28]: Reversed _adapt_tool_calls_format direction: legacy evomaster nested -> matmaster flat (backward-compatible with historical DB data)
- [Phase 28]: Simplified events_to_messages: removed second format conversion layer, both code paths now use matmaster types natively
- [Phase 28]: Hoisted agent_run_bohrium function imports to file-level in agent_run_service.py (src->src is valid forward dependency)
- [Phase 29-01]: Used openai.OpenAI sync client in monitor_job/_llm.py instead of wrapping async OpenAIProvider
- [Phase 29-01]: Placed workspace_resolver in matmaster/integration/ alongside bohrium_setup/bohrium_env
- [Phase 29-02]: Removed stale playground frontend lib .gitignore exception during cleanup
- [Phase 29-02]: Session working_dir standardized to ./workspace at project root for both config files
- [Phase 30-02]: Import audit KNOWN_VIOLATIONS cleaned from 24 to 1 -- 23 stale entries resolved in prior phases
- [Phase 30-02]: config.yaml user skill paths (~/.evomaster-skills) kept as-is -- filesystem names not package imports, deferred to v2.2
- [Phase 30]: v2.1 migration doc uses actual test data (1276 passed, 5 skipped) per D-11, no hardcoded baselines
- [Phase 30]: v2.2 cleanup ordered P1-P5: config unification > history cleanup > path normalization > packaging > archive disposal
- [Phase 31-01]: create_autospec(Session, instance=True) for Session Protocol mocks; MockLLMProvider from conftest for LLMProvider Protocol mocks (autospec returns coroutine, not async iterator)
- [Phase 31]: Normalized requirements-completed (hyphenated) to requirements_completed (underscored) across 8 SUMMARY files for tooling consistency

### Pending Todos

None.

### Blockers/Concerns

- API/worker 与本地 Web 主路径在迁移期间必须始终保持可运行
- tests/test_streaming_thought_protocol.py 的收集失败需要并入质量门禁

## Session Continuity

Last session: 2026-04-02T04:08:03.757Z
Stopped at: Completed 31-02-PLAN.md
Resume file: None
