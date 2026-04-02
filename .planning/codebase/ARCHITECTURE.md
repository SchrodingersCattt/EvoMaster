# Architecture

**Analysis Date:** 2026-04-02

## Pattern Overview

**Overall:** Three-layer pipeline architecture with event-driven communication

**Key Characteristics:**
- Strict unidirectional dependency: Playground -> Exp -> AgentKernel (no reverse imports)
- Protocol-based structural typing (`@runtime_checkable`) for all extension points (Tool, Hook, Guard, LLMProvider, Session)
- Frozen Pydantic models as inter-layer contracts (PlaygroundContext, AgentRuntimeSpec)
- AsyncIO event bus (MessageBus) for decoupled event delivery to SSE/persistence handlers
- Generator-first kernel design: single `_run_items()` AsyncGenerator powers both `run()` and `run_stream()`

## Layers

**Layer 1: Playground (Environment Preparation)**
- Purpose: Create workspace directories, sessions (local/SSH), logging, and produce an immutable environment snapshot
- Location: `matmaster/core/playground.py`
- Contains: `Playground` (single-run environment setup), `PlaygroundManager` (thread-safe instance cache + startup validation)
- Depends on: `matmaster/types/context.py` (PlaygroundContext), `matmaster/sessions/` (LocalSession, SSHSession)
- Used by: `src/services/agent_run_service.py` (Stage 1), `matmaster/devshell/runner.py`
- Output: `PlaygroundContext` (frozen Pydantic model)

**Layer 2: Exp (Config-Driven Assembly)**
- Purpose: Transform ExpConfig + PlaygroundContext into a fully-assembled AgentRuntime with tools, system prompt, hooks, and kernel
- Location: `matmaster/core/exp.py`
- Contains: `Exp` class with three-phase lifecycle: `assemble()` -> `build_runtime()` -> `run()`
- Depends on: `matmaster/config/exp.py` (ExpConfig), `matmaster/config/loader.py`, `matmaster/tools/tool_registry.py`, `matmaster/core/context_builder.py`, `matmaster/core/hooks.py`, `matmaster/providers/`
- Used by: `src/services/agent_run_service.py` (Stage 4), `matmaster/devshell/runner.py`
- Output: `AgentRuntime` (frozen dataclass bundling kernel + spec + cleanup)

**Layer 3: AgentKernel (Pure Async Execution Loop)**
- Purpose: Execute the LLM -> guard -> hook -> tool -> message accumulate cycle until termination
- Location: `matmaster/core/agent.py`
- Contains: `AgentKernel` class (stateless), `_KernelState` (per-invocation mutable state), `_KernelItem` / `_TerminalItem` (generator output types)
- Depends on: `matmaster/types/runtime.py` (AgentRuntimeSpec), `matmaster/core/hooks.py`, `matmaster/core/guard_pipeline.py`, `matmaster/core/tool_runner.py`
- Used by: Only via `Exp.run()` or `Exp.build_runtime()` -> `runtime.kernel.run()`
- Output: `KernelRunResult` (frozen dataclass with `KernelResult` + message transcript)

**Service Layer (Orchestration)**
- Purpose: Thin pipeline orchestrator that wires Playground -> EventRouter -> Bohrium -> Exp -> Kernel
- Location: `src/services/agent_run_service.py`
- Contains: `AgentRunService.run_agent()` -- the 6-stage pipeline entry point
- Depends on: All three matmaster layers, plus `src/dao/`, `src/services/`, `matmaster/integration/`
- Used by: `src/worker/agent_worker.py`, `src/apis/chat_api.py`

## Data Flow

**Agent Run Pipeline (6 stages in `AgentRunService.run_agent()`):**

1. **Stage 1 - Playground:** `PlaygroundManager.get_or_create(session_id)` -> `playground.prepare(run_meta)` -> `PlaygroundContext`
2. **Stage 2 - EventRouter:** Create `MessageBus` + `EventRouter` with `SSEHandler` + `PersistenceHandler`. `router.start()` spawns async consumer task
3. **Stage 3 - Bohrium:** `BohriumSetupService.run_setup()` -> SSH session attach -> `pg_ctx.with_execution()` updates session/workdir
4. **Stage 4 - Exp Assembly:** `load_exp_config(mode)` -> `Exp(config)` -> `exp.build_runtime(pg_ctx, bus=bus)` -> `AgentRuntime`
5. **Stage 5 - History:** `ChatHistoryConverter.events_to_messages()` converts persisted events to `Message[]` for multi-turn
6. **Stage 6 - Kernel:** `runtime.kernel.run(spec, task, history, stop_event)` -> `KernelRunResult` -> post-processing events

**Kernel Execution Loop (`_run_items()` generator):**

1. Build initial messages: `[SystemMessage(spec.system_prompt), *history, UserMessage(task)]`
2. Per turn: `run_pre_llm_call(hooks)` -> `run_should_continue(hooks)` -> `compactor.compact_if_needed()` -> `_resolve_tool_definitions()` -> `_call_llm()`
3. If no tool_calls: validate `finish_reason == 'stop'` -> yield ThoughtEvent/ResponseEvent -> yield terminal (natural/invalid_finish)
4. If tool_calls: yield AssistantMessage -> `tool_runner.execute_batch(tool_calls, ctx)` -> yield ToolMessages -> loop
5. Termination: natural (no tool_calls) / max_turns / cancelled (stop_event) / hook_stopped

**Event Flow (MessageBus -> SSE/Persistence):**

```
AgentKernel._run_items()
  -> Hook callbacks (EventEmitterHook)
    -> MessageBus.emit(BusEvent)
      -> EventRouter._consume_loop() (asyncio.Task)
        -> SSEHandler.handle(event)          # Low-latency SSE to frontend
        -> PersistenceHandler.handle(event)  # DB persistence
```

**State Management:**
- `PlaygroundContext`: frozen Pydantic model, passed by value between layers. Mutations create new instances via `model_copy(update={...})`
- `AgentRuntimeSpec`: frozen Pydantic model, assembled by Exp, consumed by Kernel. `model_copy()` for service-layer hook injection
- `_KernelState`: mutable dataclass, local to each `_run_items()` invocation. Not stored on Kernel instance (stateless kernel)
- `MessageBus`: asyncio.Queue-backed, single producer (kernel via hooks) / single consumer (EventRouter)

## Key Abstractions

**PlaygroundContext (`matmaster/types/context.py`):**
- Purpose: Immutable environment snapshot from Layer 1 to Layer 2
- Fields: `workdir`, `session_type`, `cache_area`, `execution_workdir`, `session`, `llm_provider`, `llm_config`, `run_meta`, `archival`
- Pattern: Frozen Pydantic model with `with_execution()` and `with_bohrium()` copy-update methods
- Created by: `Playground.prepare()`
- Consumed by: `Exp.assemble()`, `Exp.build_runtime()`, `Exp._init_builtin_tools()`

**AgentRuntimeSpec (`matmaster/types/runtime.py`):**
- Purpose: Complete runtime specification for kernel execution
- Fields: `llm_provider`, `tool_registry`, `guards`, `max_turns`, `hooks`, `compaction`, `system_prompt`, `compactor`, `meta`, plus v2 fields (`tool_runner`, `tool_catalog`, `runtime_topology`, `capability_policy`, `structural_validation`)
- Pattern: Frozen Pydantic model with `model_validator` for v2 field type checking (lazy imports to avoid circular deps)
- Created by: `Exp.assemble()` (initial), `Exp.build_runtime()` (enriched via `model_copy`)
- Consumed by: `AgentKernel.run()`, `AgentKernel._run_items()`

**KernelResult / KernelRunResult (`matmaster/types/runtime.py`):**
- Purpose: Terminal execution result + full message transcript
- `KernelResult`: frozen dataclass with `status`, `reason`, `final_content`, `num_turns`, `stop_reason`, `usage`. Has `to_run_result_event()` for bus emission
- `KernelRunResult`: frozen dataclass bundling `KernelResult` + `messages: list[Message]`
- Created by: `AgentKernel.run()` (from `_TerminalItem`)

**AgentRuntime (`matmaster/types/runtime.py`):**
- Purpose: Runtime bundle returned by `Exp.build_runtime()`
- Fields: `kernel: AgentKernel`, `spec: AgentRuntimeSpec`, `cleanup: Callable`
- Pattern: Frozen dataclass

**Tool Protocol (`matmaster/tools/tool_registry.py`):**
- Purpose: Unified interface for all tool types (builtin, MCP, skill)
- Methods: `name`, `description`, `json_schema` (properties), `execute(arguments) -> str | ToolResult | None`
- Pattern: `@runtime_checkable Protocol`

**Hook Protocol (`matmaster/core/hooks.py`):**
- Purpose: Observation and interception of kernel execution
- 7 hook points: `pre_tool_call` (intercepting), `post_tool_call`, `pre_llm_call`, `should_continue` (intercepting), `on_stream_chunk`, `on_segment_complete`, `on_guard_blocked`
- Pattern: `@runtime_checkable Protocol` + `BaseHook` default implementation
- Key implementation: `EventEmitterHook` bridges hook calls to `MessageBus` events

**Guard Protocol (`matmaster/types/guards.py`):**
- Purpose: Tool call gating (allow/deny with guidance)
- Method: `evaluate(ctx: GuardContext) -> GuardResult`
- Pattern: `@runtime_checkable Protocol`
- Key implementation: `LoopDetectionGuard` in `matmaster/core/guard_pipeline.py` (built-in, always first)

**LLMProvider Protocol (`matmaster/types/llm_provider.py`):**
- Purpose: LLM backend abstraction (chat, chat_stream, async context manager)
- Pattern: `@runtime_checkable Protocol`
- Implementation: `matmaster/providers/openai_provider.py` (OpenAI SDK via LiteLLM Proxy)

**Session Protocol (`matmaster/types/session.py`):**
- Purpose: Execution environment abstraction (bash, file I/O)
- Methods: `is_open`, `open`, `close`, `exec_bash`, `read_file`, `write_file`, `path_exists`, `is_file`
- Implementations: `matmaster/sessions/local.py` (LocalSession), `matmaster/sessions/ssh.py` (SSHSession)

## Entry Points

**FastAPI Application (`app.py`):**
- Location: `app.py`
- Triggers: HTTP requests, lifespan events
- Responsibilities: CORS, routing, middleware, worker heartbeat, playground init

**Agent Worker (`src/worker/agent_worker.py`):**
- Location: `src/worker/agent_worker.py`
- Triggers: Background task queue
- Responsibilities: Delegates to `AgentRunService.run_agent()`

**Chat API (`src/apis/chat_api.py`):**
- Location: `src/apis/chat_api.py`
- Triggers: HTTP POST from frontend
- Responsibilities: Parse request, invoke `AgentRunService.run_agent()` via SSE

**DevShell (`matmaster/devshell/`):**
- Location: `matmaster/devshell/__main__.py`, `matmaster/devshell/cli.py`
- Triggers: CLI invocation (`python -m matmaster.devshell`)
- Responsibilities: Interactive REPL for local development, bypasses web service layer

## Error Handling

**Strategy:** Layer-appropriate error handling with no swallowed exceptions in DAO layer

**Patterns:**
- **Kernel level:** LLM errors are retried with timeout-doubling backoff (`_call_llm()` in `matmaster/core/agent.py`). Non-retryable `LLMError` propagates up. Stream cancellation via `_KernelStopRequested` internal exception
- **Exp level:** `try/finally` in `Exp.run()` guarantees cleanup callbacks execute even on kernel failure. Cleanup callbacks are fault-tolerant (each runs independently, exceptions logged)
- **Service level:** `AgentRunService.run_agent()` wraps the entire pipeline in `try/except` with `_emit_error_and_close(bus, msg)` to deliver ErrorEvent + StreamClosedEvent to frontend. Cleanup order: Bohrium -> Exp -> Router
- **Tool execution:** `InlineToolRunner.execute_batch()` wraps each tool in try/except, returns `ToolResult.from_error()` on failure. Guard blocks return `ToolResult(status="blocked")`
- **Guard pipeline:** Short-circuits on first deny. `GuardResult.guidance` injected back to LLM to steer behavior

## Cross-Cutting Concerns

**Logging:**
- Python stdlib `logging` throughout
- Per-run file logging set up by `Playground._setup_logging()` under `<run_dir>/logs/{task_id}.log`
- Request logging middleware in `app.py` with sensitive field redaction
- `LogContext` for session-aware log correlation (`src/utils/logger.py`)

**Validation:**
- Pydantic `model_validator` for runtime type checking on v2 fields (`AgentRuntimeSpec._check_v2_field_types`)
- `frozen=True` on all inter-layer contract models prevents post-construction mutation
- Guard pipeline for tool call validation (loop detection + external guards)

**Authentication:**
- Bohrium platform credentials managed by `BohriumSetupService` (`src/services/agent_run_bohrium.py`)
- User identity via `src/services/user_service.py`
- LLM API keys in `config/llm_config.yaml` (loaded via `matmaster/config/loader.py`)

**Cancellation:**
- `threading.Event` (or `RedisBackedStopEvent`) propagated through all layers
- Kernel checks at: turn boundary, every N stream chunks, retry backoff slices, between serial tool calls
- Tools receive `_stop_event` injection for cooperative cancellation

**Context Compaction:**
- `ContextCompactor` (`matmaster/core/context_compactor.py`) triggered when token count exceeds `trigger_ratio * context_window_tokens`
- Strategies: summary (default), sliding_window, latest_half
- Optional separate LLM for compaction summaries

## Tool Runtime v2 (Phase 32)

**ToolRunner Protocol (`matmaster/core/tool_runner.py`):**
- Defines `execute_batch()` interface for tool execution strategies
- `InlineToolRunner`: Phase 1 transition implementation wrapping guard -> pre_hook -> parallel execute -> post_hook
- Three-phase execution: serial gating (guard + pre_hook) -> parallel execution (asyncio.gather) -> serial post-hooks

**ToolCatalog (`matmaster/tools/tool_catalog.py`):**
- Phase 1 facade over ToolRegistry with base+overlay structure
- Version tracking enables kernel to cache tool definitions and refresh only on changes
- `register_overlay()` increments version for lazy MCP tool injection

**RuntimeTopology (`matmaster/types/topology.py`):**
- `ToolPlane` enum: SESSION_SHELL, SESSION_FS, CONTROL_PLANE, EXTERNAL_SERVICE
- `SessionCapabilities`: describes session abilities (shell persistence, file ops mode, upload)
- `RuntimeTopology`: captures session kind, workspace roots, active planes

**Current State:** Phase 1 transition complete. ToolRunner/ToolCatalog are wired but delegate to existing ToolRegistry. Phase 2 (Plan 33+) will implement full ToolRunner with ToolCatalog lookup, StructuralValidation, RunStateGuard, CapabilityPolicy, and ToolScheduler.

---

*Architecture analysis: 2026-04-02*
