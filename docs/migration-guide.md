# MatMaster v2 Migration Guide

This document records the architectural changes, replaced components, configuration
changes, and migration steps for the matmaster framework refactoring (v2). It is
intended for team members and future maintainers who need to understand what
changed, why, and how to adapt any downstream code that depended on the old
architecture.

**Scope:** mat_master and minimal playground types only. x_master migration is
out of scope (see [Out of Scope](#out-of-scope)).

**Date:** 2026-03-22

---

## Overview

MatMaster v2 restructures the framework from an **inheritance-driven** design
(where playground subclasses, agent subclasses, and factory registries managed
behaviour) to a **contract-driven** design (where typed data contracts flow
between three layers with clear boundaries).

The three-layer architecture is:

1. **Playground** (Layer 1) -- Physical environment preparation (workspace, session, logging).
   Output: `PlaygroundContext` (frozen Pydantic model).

2. **Exp** (Layer 2) -- Capability assembly (tools, LLM, hooks, guards, prompt).
   Output: `AgentRuntimeSpec` (frozen Pydantic model).

3. **Kernel** (Layer 3) -- Pure execution loop (LLM call -> guard -> hook -> tool -> message accumulate -> loop).
   Consumes `AgentRuntimeSpec`, produces `FinishEvent`.

The refactoring was motivated by several problems in the old codebase:

- **Layer penetration:** Playground directly registered skills, agent's `run()` mixed
  loop logic with config assembly, Exp handled both execution and assembly.
- **Scattered capabilities:** Prompt, tool, MCP, skill, LLM config scattered across
  `evomaster/core`, `evomaster/agent`, and `playground/mat_master` packages.
- **Heavy kernel:** `MatMasterAgent.run()` carried config cascading, compaction
  setup, tool guard init -- polluting the execution loop.
- **Legacy patterns:** BasePlayground AgentSlots, BaseExp trajectory collection,
  and other v0.0.1 patterns no longer fit modern agent framework needs.
- **Type safety degradation:** `Dict[str, Any]` used extensively for config and
  inter-component communication, deferring errors to runtime.

---

## Architecture Changes

The following table maps old components to their new equivalents:

| Old Component | New Component | Change Type |
|---|---|---|
| `StreamingMatMasterAgent` | `AgentKernel` + Hook system | Replaced |
| `get_playground_class()` factory | `Playground(config_path)` direct construction | Replaced |
| `evomaster.utils.TaskInstance` | `str` (task) + `list[Message]` (history) | Replaced |
| `evomaster.utils.create_llm` / `LLMConfig` | `OpenAIProvider` direct construction (LLMProvider Protocol) | Replaced |
| `event_callback` closure (130 lines) | `EventRouter` + `PersistenceHandler` + `SSEHandler` + `WorkspaceHandler` | Decomposed |
| `playground.mat_master.ConfirmationManager` | `ConfirmationHook` (Hook Protocol) | Replaced |
| `pg._create_exp()` internal creation | `DirectExp` explicit construction | Externalized |
| `BasePlayground` subclasses (`MatMasterPlayground`, `MinimalPlayground`) | Unified `Playground` class (config-driven) | Consolidated |
| `BaseExp` + `DirectSolver` | `Exp` (base) + `DirectExp` (concrete) | Replaced |
| `agent._emit()` callback chain | `EventEmitterHook` -> `MessageBus` | Replaced |
| `ToolCallbackPipeline` (before/after) | `Guard` (before) + `Hook` (before/after) | Decomposed |
| `ContextCompactor` (inline in agent) | `CompactionConfig` in `AgentRuntimeSpec` | Externalized |

### Key Design Shifts

- **Inheritance to composition:** Old system used class inheritance (`BasePlayground` ->
  `MatMasterPlayground`). New system uses a single `Playground` class with
  config-driven behaviour.
- **Callbacks to event bus:** Old system passed callback closures. New system
  uses `MessageBus` (synchronous `queue.Queue`) with typed `BusEvent` discriminated union.
- **Monolithic agent to kernel + hooks:** Old `StreamingMatMasterAgent` (500+ lines)
  handled execution, events, confirmation, output processing, and state tracking.
  New `AgentKernel` (250 lines) only runs the LLM -> tool loop; all side effects
  are injected as Hook implementations.

---

## New Components

### matmaster/engine/agent.py -- AgentKernel

Pure execution loop consuming `AgentRuntimeSpec`. Runs the
LLM -> guard -> hook -> tool -> message accumulate -> loop cycle. All
termination paths produce a `FinishEvent` with a reason
(`natural`, `max_turns`, `cancelled`, `hook_stopped`).

```python
kernel = AgentKernel()
finish_event = kernel.run(spec, task="user prompt", history=messages, stop_event=stop)
```

### matmaster/types/ -- Typed Contracts

- **`PlaygroundContext`** (context.py) -- Frozen Pydantic model for environment
  context: `workdir`, `session_type`, `cache_area`, `env_vars`,
  `archival` (WorkspaceArchivalConfig), `run_meta`. Contains `with_bohrium()`
  for immutable update with Bohrium results.
- **`AgentRuntimeSpec`** (runtime.py) -- Frozen Pydantic model for agent runtime
  specification: `llm_provider`, `tool_registry`, `guards`, `max_turns`,
  `hooks`, `compaction`, `system_prompt`, `mode`.
- **`BusEvent`** (events.py) -- Discriminated union of 16 event types (7 AgentEvent +
  9 SystemEvent), using Pydantic `Literal` discriminator on the `type` field.

### matmaster/assembly/ -- Exp Layer

- **`DirectExp`** (direct_exp.py) -- Direct execution mode assembly. Owns
  MCP and Skill initialization. Accepts `llm_provider`, `builtin_tools`,
  `guards`, capability config (mcp_config, skill_config), and factory
  callables. `assemble(ctx)` builds a complete `AgentRuntimeSpec`.
- **`ToolRegistry`** (tool_registry.py) -- Flat tool registration table.
  Tools implement `Tool` Protocol (name, description, parameters JSON Schema,
  execute). Source-tagged registration (`builtin`, `skill`, `mcp`).
- **`ContextBuilder`** (context_builder.py) -- Multi-source system prompt
  assembly from identity, skills, memory, task, and mode sections.
- **`EvoToolAdapter`** (evomaster_tool_adapter.py) -- Adapter wrapping legacy
  EvoMaster `BaseTool` instances into the matmaster `Tool` Protocol without
  inheritance.

### matmaster/hooks/ -- Business Hooks

All hooks implement the `Hook` Protocol from `matmaster.engine.hooks`:

- **`ConfirmationHook`** (confirmation.py) -- Intercepts tool calls requiring
  human confirmation. Wraps `ReplyQueueLike` for pause/resume flow.
- **`OutputProcessorHook`** (output_processor.py) -- Post-tool-call processing:
  auto-save pattern matching and output summarization.
- **`SkillHitHook`** (skill_hit.py) -- Tracks skill tool invocations, emits
  `SkillHitEvent` to the MessageBus.
- **`AssistantStateHook`** (assistant_state.py) -- Emits `AssistantStateEvent`
  after assistant messages with tool_calls for persistence.

The generic `EventEmitterHook` (the kernel->bus bridge) remains in
`matmaster/engine/hooks.py`.

### matmaster/integration/ -- Event and Workspace Integration

- **`EventRouter`** (event_router.py) -- Background thread consumer dispatching
  `BusEvent` from `MessageBus` to registered handlers. Single-consumer,
  multi-handler pattern. Lifecycle bound to a single run.
- **`PersistenceHandler`** (event_router.py) -- Persists events to DB via
  `events_table.add_event()`. Filter rules: skip `log_line`/`llm_token`,
  skip streaming `ThoughtEvent` deltas.
- **`SSEHandler`** (event_router.py) -- Pushes events to SSE `send_cb`.
  Filter rules: skip `assistant_state`, skip planner streaming thoughts,
  skip direct mode non-streaming complete thoughts. Supports async/sync
  dual-mode send via `asyncio.run_coroutine_threadsafe`.
- **`WorkspaceHandler`** (workspace_handler.py) -- Debounced workspace
  snapshot and upload. Triggers only on `ToolResultEvent`, skips when
  SSH-attached, debounces by time, compares directory snapshots.

### src/services/agent_run_bohrium.py -- Bohrium Runtime Orchestration

- **`BohriumSetupService`** -- Owns Bohrium credential loading, node/SSH
  setup, cleanup, and event bridge wiring for a single run.
- **`SkillSyncSpec` / `derive_skill_sync_spec()`** -- Resolve skill roots from
  `ExpConfig` before Bohrium setup so remote skill sync uses the finalized run
  configuration.

### matmaster/bus/ -- Event Bus

- **`MessageBus`** (queue.py) -- Synchronous `queue.Queue`-based event bus.
  Agent kernel emits events via `EventEmitterHook`, consumers read from bus.
- **`QueueBridge`** (bridge.py) -- Maps 16 `BusEvent` types to SSE payload
  format for frontend consumption.

### matmaster/playground/ -- Unified Playground

- **`Playground`** (playground.py) -- Single class for all playground types
  (mat_master, minimal). Config-driven via YAML path. Two-phase lifecycle:
  `prepare()` returns `PlaygroundContext`, `cleanup()` releases resources.
  No subclasses needed.

---

## Pipeline Flow

### Old Pipeline

```
init_playground_sync()
  -> get_playground_class('mat_master', config_path=...)
     -> dynamic import + factory registry lookup
  -> pg.set_run_dir(run_dir, task_id=session_id)
  -> pg._mcp_progress_callback = lambda ...    (closure injection)
  -> pg.setup()                                 (MCP connect, skill load)

run_agent_sync()
  -> pg._create_exp()                          (DirectSolver or ResearchPlanner)
  -> exp.run(task=TaskInstance(...))            (agent.run inside exp.run)
     -> agent._emit()                          (130-line event_callback closure)
        -> _should_persist_event()             (inline filter)
        -> _should_skip_push()                 (inline filter)
        -> send_cb()                           (SSE push)
        -> events_table.add_event()            (DB persist)
        -> workspace snapshot + upload          (debounced)
```

### New Pipeline

```
run_agent_sync()                               (thin 6-stage orchestration)
  Stage 1: Playground.prepare(run_meta)        -> PlaygroundContext
  Stage 2: EventRouter(bus, handlers).start()  (background consumer)
  Stage 3: BohriumSetupService.run_setup(...)  -> ssh_attached, pg_ctx update
  Stage 4: DirectExp(...).assemble(ctx, hooks) -> AgentRuntimeSpec
  Stage 5: ChatHistoryConverter.events_to_messages() -> list[Message]
  Stage 6: AgentKernel().run(spec, task, history, stop_event) -> FinishEvent
  Post:    quota, workspace upload, cleanup
```

**Key differences:**

1. No dynamic module imports or factory registries. Direct construction of
   `Playground` and `DirectExp`.
2. Event handling decomposed into independently testable handlers instead of
   a monolithic closure.
3. `TaskInstance` eliminated -- task is a `str`, history is `list[Message]`.
4. MCP/Skill initialization moved from Playground to Exp layer.
5. `run_agent_sync()` reduced from ~490 effective lines to ~200 lines.

---

## Configuration Changes

### Config YAML Location

Configuration files are now at `config/` (single directory).

### Startup Validation

- **Old:** Dynamic module import at startup via `get_playground_class()` factory.
  Loaded and instantiated playground subclasses.
- **New:** Lightweight YAML validation only (D-04). Checks file existence and
  that the `agents` key is present. No dynamic module import.

### Config Distribution

- **Old:** Playground read the full config and passed it through to agent/exp.
- **New:** Service layer reads config once and distributes:
  - Physical environment config (session, workspace, archival) -> `Playground`
  - Capability config (LLM, tools, MCP, skills) -> `DirectExp`

### PlaygroundContext Scope

- **Old:** `PlaygroundContext` (or its equivalent in the old system) carried
  `mcp_manager` and `skill_registry`.
- **New:** `PlaygroundContext` is strictly environment-only: `workdir`,
  `session_type`, `cache_area`, `env_vars`, `archival`, `run_meta`. No
  capability objects. MCP and Skill initialization are handled by
  `DirectExp.assemble()`.

### AgentRuntimeSpec

New contract (no old equivalent). Contains all execution configuration:
- `llm_provider`: LLMProvider Protocol implementation
- `tool_registry`: ToolRegistry with registered tools
- `guards`: list of Guard Protocol implementations
- `max_turns`: termination limit (default 100)
- `hooks`: list of Hook Protocol implementations
- `compaction`: CompactionConfig
- `system_prompt`: assembled by ContextBuilder
- `mode`: `"direct"` or `"planner"`

---

## Breaking Changes

The following public APIs are removed or replaced. Downstream code depending on
these must be updated.

### Removed APIs

| Removed API | Replacement | Notes |
|---|---|---|
| `evomaster.core.get_playground_class()` | `Playground(config_path)` | No factory registry; direct construction |
| `playground.mat_master.service.stream_agent()` | `AgentKernel.run(spec, task, ...)` | Agent execution is now via kernel |
| `playground.mat_master.service.confirm()` | `ConfirmationHook` in hooks list | Confirmation is a Hook, not a standalone API |
| `evomaster.utils.TaskInstance` | `task: str` + `history: list[Message]` | TaskInstance wrapper removed |
| `event_callback` signature | Events flow through `MessageBus` -> `EventRouter` | No callback closures |
| `pg._create_exp()` | `DirectExp(...)` explicit construction | Exp creation externalized to service layer |
| `pg.setup()` | `Playground.prepare()` -> `PlaygroundContext` | Returns typed contract |
| `pg.set_run_dir()` | `Playground.prepare(run_meta={"run_dir": ...})` | Part of prepare() |
| `evomaster.utils.create_llm(LLMConfig(...))` | `OpenAIProvider(model=..., api_key=...)` | LLMProvider Protocol |
| `StreamingMatMasterAgent(...)` | `AgentKernel()` (stateless) | Kernel is stateless; state flows through spec |

### Changed Signatures

| API | Old Signature | New Signature |
|---|---|---|
| `ChatHistoryConverter.events_to_messages()` | N/A (new method) | `classmethod(events) -> list[Message]` |
| `ChatHistoryConverter.events_to_dialog_messages()` | Returns `list[dict]` | Preserved for backward compatibility |

### Dependency Removals

Code importing these modules should switch to the new equivalents:

| Old Import | New Import |
|---|---|
| `from evomaster.core import get_playground_class` | `from matmaster.playground.playground import Playground` |
| `from evomaster.utils import LLMConfig, create_llm` | `from matmaster.types.llm_provider import LLMProvider` (Protocol) |
| `from evomaster.utils import TaskInstance` | Not needed; use `str` + `list[Message]` |
| `from playground.mat_master import ConfirmationManager` | `from matmaster.hooks import ConfirmationHook` |
| `from playground.mat_master import StreamingMatMasterAgent` | `from matmaster.engine.agent import AgentKernel` |

### New Dependencies

| New Import | Purpose | Introduced In |
|---|---|---|
| `from matmaster.engine.agent import AgentKernel` | Execution engine | Phase 2 |
| `from matmaster.types.runtime import AgentRuntimeSpec` | Runtime contract | Phase 1 |
| `from matmaster.types.context import PlaygroundContext` | Environment contract | Phase 1 |
| `from matmaster.bus.queue import MessageBus` | Event bus | Phase 1 |
| `from matmaster.bus.bridge import QueueBridge` | SSE bridge | Phase 1 |
| `from matmaster.engine.hooks import EventEmitterHook` | Kernel->bus bridge | Phase 2 |
| `from matmaster.playground.playground import Playground` | Environment preparation | Phase 4 |
| `from matmaster.assembly.direct_exp import DirectExp` | Capability assembly | Phase 3 |
| `from matmaster.hooks import ConfirmationHook, ...` | Business hooks | Phase 5 |
| `from matmaster.integration import EventRouter, ...` | Event routing | Phase 5 |

---

## Deprecation Notices

### evomaster/ Package

A `DeprecationWarning` is emitted at import time for old evomaster modules
(per D-02):

```python
try:
    import evomaster.core
    warnings.warn(
        "evomaster.core is deprecated; use matmaster.playground.playground.Playground",
        DeprecationWarning,
        stacklevel=2,
    )
except ImportError:
    pass
```

The `evomaster/` package is retained on disk for backward compatibility but is
no longer called from the service layer.

### playground/mat_master/ Modules

The following modules are retained on disk but are no longer called from the
service layer:

- `playground/mat_master/service.py` -- `stream_agent()`, `confirm()`
- `playground/mat_master/agent.py` -- `StreamingMatMasterAgent`
- `playground/mat_master/confirmation.py` -- `ConfirmationManager`

These may be removed in a future cleanup phase once migration stabilizes.

### Preserved Interfaces

The following service-layer interfaces are **unchanged** and require no caller
modifications:

- `agent_run_service.run_agent_sync()` -- method signature (12 parameters)
  preserved exactly
- `stream_service.py` -- SSE streaming, ReplyQueue
- `events_service.py` -- event persistence
- `quota_service.py` -- quota deduction
- `worker_registry_service.py` -- Redis worker registry
- `sessions_service.py` -- session lifecycle

---

## Out of Scope

The following items are explicitly excluded from this migration and are deferred
to future work:

### x_master Migration

The `x_master` playground type is not supported in the new pipeline. Requests
for `x_master` raise `ValueError` in the new `run_agent_sync()` (per D-03).
A separate migration effort is needed for x_master.

### PlannerExp Rewrite

The `ResearchPlanner` / `PlannerExp` (multi-stage solver with 5-phase state
machine) has design patterns that conflict with the new architecture. Phase 5
only validates the `direct` mode flow. PlannerExp will be rewritten separately
(per D-05, D-06).

### Old Code Deletion

Old code in `evomaster/` and `playground/mat_master/` is retained on disk with
deprecation warnings. Deletion is deferred until migration stabilizes and all
downstream consumers have been updated.

### Context Compaction Integration

`CompactionConfig` exists in `AgentRuntimeSpec` but the actual compaction
strategy implementation is deferred. The kernel respects the config field but
does not yet execute compaction.

### Tool Parallel Execution

Tools execute serially in the kernel loop. Parallel tool execution is a
deferred enhancement from Phase 2.

### Session Protocol Abstraction

A formal `Session` Protocol for the session layer (Local/Docker/SSH) is
deferred from Phase 4. The current implementation uses the existing
`BaseSession` class directly.

---

## Migration Checklist

For downstream code that depends on the old architecture, use the following
checklist to verify migration completeness:

- [ ] Replace `get_playground_class()` calls with `Playground(config_path)`
- [ ] Replace `pg.setup()` with `pg.prepare(run_meta)` and use the returned `PlaygroundContext`
- [ ] Replace `pg._create_exp()` with explicit `DirectExp(...)` construction
- [ ] Replace `StreamingMatMasterAgent` instantiation with `AgentKernel()` usage
- [ ] Replace `TaskInstance` usage with `task: str` + `history: list[Message]`
- [ ] Replace `create_llm(LLMConfig(...))` with `LLMProvider` Protocol implementation
- [ ] Replace `event_callback` closures with `EventRouter` + handler setup
- [ ] Replace `ConfirmationManager` with `ConfirmationHook`
- [ ] Update imports from `evomaster.*` to `matmaster.*` equivalents
- [ ] Verify config YAML paths are still correct (unchanged)
- [ ] Test with `direct` mode (planner mode not yet supported in new pipeline)
