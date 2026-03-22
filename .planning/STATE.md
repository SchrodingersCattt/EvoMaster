---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Completed 06-01-PLAN.md
last_updated: "2026-03-22T13:24:30.000Z"
last_activity: 2026-03-22 -- Phase 6 Plan 1 (LLM Factory Wiring) complete
progress:
  total_phases: 7
  completed_phases: 5
  total_plans: 19
  completed_plans: 18
  percent: 94
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-21)

**Core value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界
**Current focus:** All phases complete — milestone v1.0 ready

## Current Position

Phase: 6 of 7 (Service Layer Wiring) — IN PROGRESS
Plan: 1 of 2 in current phase
Status: Plan 1 complete; Plan 2 (builtin tools, DirectExp cleanup, guard removal, WorkerRegistry) pending
Last activity: 2026-03-22 -- Phase 6 Plan 1 complete

Progress: [█████████░] 94%

## Performance Metrics

**Velocity:**
- Total plans completed: 7
- Average duration: 5min
- Total execution time: 0.60 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-foundation-contracts | 2 | 10min | 5min |
| 02-agent-kernel | 3 | 17min | 6min |
| 03-exp-assembly-layer | 1 | 4min | 4min |
| 04-playground-layer | 3 | 14min | 5min |

**Recent Trend:**
- Last 5 plans: 02-03 (5min), 03-02 (4min), 03-04 (2min), 04-01 (5min), 04-03 (4min)
- Trend: Stable

*Updated after each plan completion*
| Phase 03 P01 | 5min | 2 tasks | 6 files |
| Phase 03 P03 | 4min | 2 tasks | 7 files |
| Phase 03 P04 | 2min | 1 tasks | 3 files |
| Phase 04 P01 | 5min | 2 tasks | 6 files |
| Phase 04 P02 | 5min | 2 tasks | 4 files |
| Phase 04 P03 | 4min | 2 tasks | 7 files |
| Phase 05 P01 | 4min | 2 tasks | 15 files |
| Phase 05 P02 | 4min | 2 tasks | 7 files |
| Phase 05 P03 | 5min | 2 tasks | 4 files |
| Phase 05 P04 | 8min | 2 tasks | 8 files |
| Phase 05 P05 | 3min | 1 tasks | 1 files |
| Phase 06 P01 | 5min | 2 tasks | 6 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: 5 phases derived from 25 requirements, standard granularity
- [Roadmap]: ~~Phase 3 (Exp) and Phase 4 (Playground) can execute in parallel~~ Phase 4 now depends on Phase 3 (修正 PlaygroundContext 和 DirectExp)
- [Roadmap]: Quality/cleanup merged into Phase 5 instead of separate phase
- [01-01]: Guard Protocol uses @runtime_checkable for isinstance checks at runtime
- [01-01]: CONT-05 TerminationPolicy simplified to AgentRuntimeSpec.max_turns int field (default 100)
- [01-01]: BusEvent enumerates all 16 types directly for Pydantic discriminator compatibility
- [01-01]: Zero new dependencies -- all from Pydantic v2 (existing) and stdlib
- [Phase 01-02]: QueueBridge.next_payload() returns base payload without session_id/task_id -- injected by agent_run_service
- [Phase 01-02]: Single consumer pattern -- QueueBridge exclusively consumes from MessageBus
- [Phase 01-02]: Synchronous queue.Queue chosen over asyncio.Queue -- agent runs in ThreadPoolExecutor
- [02-01]: ToolCallData.arguments is dict[str, Any] (not raw JSON string) -- parsing at provider boundary
- [02-01]: Hook Protocol separates intercepting hooks (short-circuit) from observation hooks (all-execute)
- [02-01]: GuardPipeline records calls only after all guards pass (denied calls not tracked)
- [02-01]: EventEmitterHook returns CONTINUE after emitting (observation, not interception)
- [Phase 02]: TYPE_CHECKING guard in kernel.py to break circular import with contracts.runtime
- [Phase 02]: ~~OpenAI SDK retry delegated to client-level max_retries~~ Superseded by 02-03
- [02-03]: Retry at Protocol level (chat_with_retry), SDK max_retries=0, every provider implements own retry
- [02-03]: Non-retryable errors (auth, context length) raise immediately without retry
- [Phase 03-02]: ContextBuilder uses static _MODE_CONTRACTS dict for mode text lookup, extensible for future modes
- [Phase 03-02]: WorkerRegistry is Protocol-only in Phase 3; Redis implementation deferred to Phase 5
- [Phase 03-02]: Empty optional sections produce no output rather than empty headers, keeping prompt clean
- [Phase 03-01]: Direct import of ToolRegistry in runtime.py (not TYPE_CHECKING) -- Pydantic needs runtime class resolution, no circular dependency
- [Phase 03-01]: Engine tests use real ToolRegistry + _CatchAllTool instead of duck-typed MockToolRegistry -- validates type constraint end-to-end
- [Phase 03-03]: DirectExp stores guards as list copy (defensive) to prevent external mutation after construction
- [Phase 03-03]: EventEmitterHook source uses exp_name property for consistent bus event attribution
- [Phase 03-03]: Pre-existing TestAgentRuntimeSpec failures (object() as tool_registry) deferred to Phase 5
- [Phase 03]: [Phase 03-04]: TYPE_CHECKING + lazy import in run() body for AgentKernel -- breaks circular import while preserving type annotations
- [Phase 03]: [Phase 03-04]: Module-level __getattr__ (PEP 562) in assembly/__init__.py for lazy Exp/DirectExp export -- avoids triggering engine import chain during package init
- [Phase 04 Context]: Playground only handles physical environment (workspace/session/logging) -- MCP/skill/tool belong to Exp layer
- [Phase 04 Context]: PlaygroundContext removes mcp_manager and skill_registry fields -- adds WorkspaceArchivalConfig nested field
- [Phase 04 Context]: Playground = Workspace equivalence -- 1:1:1 (session:playground:workspace) in current project
- [Phase 04 Context]: prepare()/cleanup() two-phase lifecycle -- no separate setup() needed
- [Phase 04 Context]: Unified Playground class -- mat_master and minimal differ only by config YAML, no subclasses
- [Phase 04 Context]: Service layer reads config and distributes -- physical env config to Playground, capability config to Exp
- [Phase 04 Context]: Exp.assemble() initializes MCP/Skill using mcp_config (constructor) + ctx.workdir (PlaygroundContext)
- [Phase 04 Context]: Mixed cleanup model -- Exp self-manages via try/finally, Playground cleanup by Service layer
- [Phase 04-02]: _build_archival_config() returns config even when enabled=False -- None only when no archival block in YAML
- [Phase 04-02]: Workspace sync before session.open() -- prevents /workspace mkdir on real configs
- [Phase 04-02]: _resolve_cache_area() resolves relative playground.cache_dir under workspace path
- [Phase 04-01]: WorkspaceArchivalConfig._build_archival_config() returns None when archival.enabled is False
- [Phase 04-01]: Playground(config_path) constructor consistent with existing BasePlayground pattern
- [Phase 04-01]: _sync_workspace_to_session_config updates both workspace_path and working_dir to prevent directory inconsistency
- [Phase 04-03]: EvoToolAdapter wraps BaseTool without inheritance -- clean adapter pattern, no EvoMaster coupling in matmaster types
- [Phase 04-03]: Factory callback injection for skill/MCP init -- DirectExp receives callables enabling test isolation
- [Phase 04-03]: Cleanup callbacks execute independently -- one failing callback does not prevent others
- [Phase 04-03]: Structured observation JSON-serialized by adapter -- matmaster Tool Protocol contract stays str-only
- [Phase 05-01]: ReplyQueueLike Protocol duplicated in hooks/confirmation.py to avoid cross-layer import from src/services/
- [Phase 05-01]: AssistantStateHook only emits when last AssistantMessage has tool_calls (not for plain text responses)
- [Phase 05-01]: OutputProcessorHook uses substring matching consistent with existing auto_save_tool_output_patterns logic
- [Phase 05-01]: SkillHitHook extracts skill_name by stripping 'skill:' prefix from tool_call.name
- [Phase 05-02]: PersistenceHandler._should_persist_type() exposed as method for type-level filter testing (log_line/llm_token not in BusEvent union)
- [Phase 05-02]: SSEHandler detects async send_cb via asyncio.iscoroutinefunction at construction time, not per-call
- [Phase 05-02]: WorkspaceHandler uses injected snapshot_fn and upload_fn for full test isolation
- [Phase 05-02]: BohriumSetupService uses lazy imports inside methods to avoid importing src.services at module level
- [Phase 05-03]: BohriumSetupService.setup() called with legacy API params via bridge callback, not new API
- [Phase 05-03]: CancelledEvent used for user cancellation (distinct from ErrorEvent)
- [Phase 05-03]: _bohrium_event_cb bridges legacy bohrium events into BohriumNodeEvent on MessageBus
- [Phase 05-04]: run_agent_sync E2E validates use_quota call rather than add_event (PersistenceHandler correctly filters streaming thoughts)
- [Phase 05-04]: Stop_event pre-set for interrupt tests to avoid timing flakiness
- [Phase 05-04]: Mock tools implement Tool Protocol (json_schema property, arguments dict) for full type compatibility
- [Phase 05-05]: Migration guide structured with 8 sections: Overview, Architecture Changes, New Components, Pipeline Flow, Configuration Changes, Breaking Changes, Deprecation Notices, Out of Scope
- [Phase 05-05]: Architecture Changes table maps 11 old components to new equivalents with change type
- [Phase 06-01]: PlaygroundContext uses arbitrary_types_allowed=True for session field accepting BaseSession instances
- [Phase 06-01]: extra_kwargs merged via dict.update() in OpenAIProvider after tools check, before SDK create() call
- [Phase 06-01]: Model family resolution: explicit config model_family > _infer_model_family substring matching
- [Phase 06-01]: Profile resolution chain: model name match > profile key match > default profile fallback with model override
- [Phase 06-01]: _build_llm_provider signature changed from (pg_ctx, llm_override, model_override) to (playground, model_override) per D-02

### Pending Todos

None yet.

### Blockers/Concerns

- [Research]: Phase 2 needs deeper analysis of MatMasterAgent._step() hook point requirements
- [Research]: Phase 3 ResearchPlanner/Solver absorption needs careful design (multi-agent orchestrator pattern)
- [Research]: Phase 5 event ordering guarantees need baseline recording from current SSE path

## Session Continuity

Last session: 2026-03-22T13:24:30Z
Stopped at: Completed 06-01-PLAN.md
Resume file: None
