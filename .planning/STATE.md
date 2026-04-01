---
gsd_state_version: 1.0
milestone: v2.1
milestone_name: matmaster/ 与 evomaster/ 彻底解耦
status: defining_requirements
stopped_at: Milestone initialization
last_updated: "2026-04-01T14:20:07+0800"
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
**Current focus:** Milestone v2.1 requirements definition for matmaster decoupling

## Current Position

Phase: Not started (defining requirements)
Plan: -
Status: Defining requirements
Last activity: 2026-04-01 — Milestone v2.1 started

Progress: [░░░░░░░░░░] 0% (0/0 phases, 0/0 plans)

## Accumulated Context

### Decisions

Decisions logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [v2.1 init]: 新里程碑聚焦 `matmaster/` 对 `evomaster/` 的彻底运行时解耦
- [v2.1 init]: 默认跳过外部 research，直接以本仓库耦合清单驱动 requirements
- [v2.1 init]: phase 编号从 25 连续编号，不重置
- [v2.0 init]: 全链路 async 改造，自底向上分层迁移
- [v2.0 init]: Guard Protocol 保持同步（纯计算无 I/O）
- [v2.0 init]: stop_event 保留 threading.Event（跨线程安全）
- [v2.0 init]: DevShell 延后改造，用 asyncio.run() 包装调用
- [v2.0 init]: Protocol hard cut（不维护 sync/async 双 Protocol）
- [Phase 12]: validate_async_protocol uses _is_async_callable checking both iscoroutinefunction and isasyncgenfunction for async generator support
- [Phase 12]: chat_with_retry fully eliminated from codebase (Protocol, OpenAIProvider, all 15 test files)
- [Phase 12]: pytest-asyncio installed with auto mode for async test infrastructure
- [Phase 13]: LLMProvider Protocol declares __aenter__/__aexit__ as formal contract for lifecycle management
- [Phase 13]: OpenAIProvider __init__ stores params only, __aenter__ creates AsyncOpenAI + httpx.AsyncClient
- [Phase 13]: AgentKernel.run() creates ONE shared asyncio.new_event_loop() for all async bridging
- [Phase 13]: ContextCompactor._summarize and compact_if_needed are async (KERN-04 pulled into Phase 13)
- [Phase 13]: _sync_iterate_async/_sync_call_async bridge functions marked Phase 13-16, removable in Phase 17
- [Phase 14-01]: BuiltinTool.execute() async via asyncio.to_thread wrapping sync _execute() -- subclasses unchanged
- [Phase 14-01]: ToolRegistry.execute() async with await-then-normalize pattern (avoid coroutine passing to normalize)
- [Phase 14-02]: LazyMCPTool uses granular to_thread (two calls: connect + execute)
- [Phase 14-02]: SkillTool/EvoToolAdapter use _execute_sync() helper pattern
- [Phase 14-02]: Kernel bridge uses dedicated daemon thread event loop + _sync_call_async
- [Phase 15-01]: Hook Protocol + BaseHook all 7 methods async def, run_* helpers async with await
- [Phase 15-01]: _sync_call_async bridge in Kernel for 13 run_* call sites
- [Phase 15-02]: ConfirmationHook uses asyncio.Future + wait_for (non-blocking async wait)
- [Phase 15-02]: resolve/cancel use atomic swap pattern to prevent race conditions
- [Phase 15-02]: Kernel injects bridge loop via duck-typed hasattr(hook, "set_loop")
- [Phase 15-02]: ConfirmationHookAdapter in stream_service.py bridges legacy ReplyQueueLike to hook API
- [Phase 15-03]: All _sync_call_async calls in agent.py now pass per-run _bridge_loop (no module-level fallback)
- [Phase 15-03]: tool_registry.execute call also updated for consistency
- [Phase 17-agentkernel]: Provider lifecycle uses async with (not manual __aenter__/__aexit__)
- [Phase 17-agentkernel]: ExplodingTool test changed sync to async execute() for async Tool Protocol compatibility
- [Phase 17-agentkernel]: Bridge loops inline per D-05, each sync entry creates/destroys own event loop
- [Phase 17-agentkernel]: Test tool fixtures converted to async execute() for ToolRegistry async compatibility
- [Phase 18-exp P01]: run() try/finally starts before build_runtime for partial build failure cleanup coverage
- [Phase 18-exp P01]: Cleanup callbacks use iscoroutinefunction + isawaitable dual detection
- [Phase 18-exp P01]: AgentRuntime.cleanup typed as Callable[[], Any] for sync/async compatibility
- [Phase 18-exp P01]: Service layer Exp cleanup moved into bridge loop's finally (before _loop.close())
- [Phase 18-exp P02]: spawn_fn calls child_exp.run() for full lifecycle reuse (not manual build_runtime + kernel.run)
- [Phase 18-exp P02]: SpawnTool overrides execute() directly for native async (bypasses to_thread pattern)
- [Phase 18-exp P02]: Exp.run() gains source_override/spawn_id optional params forwarded to build_runtime
- [Phase 19-01]: Single daemon thread event loop replaces dual-loop architecture in agent_run_service (D-01)
- [Phase 19-01]: DevShell uses asyncio.run() for single-shot bridge (D-07)
- [Phase 22-audit-metadata-backfill]: SUMMARY body content preserved as historical record -- only frontmatter requirements-completed line edited
- [Phase 23-verification-nyquist-closure]: Milestone audit updated from docs_resolved to all_clear — 35/35 requirements, 11/11 phases, Nyquist COMPLIANT
- [Phase 24-emit-nowait-tech-debt]: MagicMock(emit=AsyncMock()) pattern for testing async bus.emit() callers

### Pending Todos

None.

### Blockers/Concerns

- `.planning/MILESTONES.md` 尚未完整记录 v1.1 / v2.0 历史，当前版本连续性主要依赖 PROJECT/ROADMAP/STATE
- `tests/test_streaming_thought_protocol.py` collection error 仍存在，需要纳入本里程碑统一质量门禁
- `skills/mcp` build_runtime stubs 仍需要 service 层 factory 注入，这一问题与本次 MCP 解耦边界直接相关

## Session Continuity

Last session: 2026-04-01T14:20:07+0800
Stopped at: Milestone initialization
Resume file: None
