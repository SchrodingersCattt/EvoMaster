# Architecture Patterns: matmaster Async Transformation (v2.0)

**Domain:** Agent framework sync-to-async migration
**Researched:** 2026-03-26
**Confidence:** HIGH (based on codebase source analysis + official Python/OpenAI docs)

## Current Architecture Snapshot

The matmaster framework uses a three-layer sync architecture:

```
Layer 1: Playground     -- prepare() -> PlaygroundContext (frozen Pydantic model)
Layer 2: Exp            -- assemble() / build_runtime() / run() with threading.Event for cancel
Layer 3: AgentKernel    -- run() sync loop: LLM stream -> guard -> hook -> tool -> accumulate -> repeat
```

Cross-cutting:
- MessageBus: queue.Queue (thread-safe, sync-only)
- EventRouter: background threading.Thread consuming from MessageBus, dispatching to EventHandler list
- Integration handlers (SSE/Persistence/Workspace): all sync EventHandler implementations
- SubAgent spawn: synchronous in parent thread via spawn_fn closure

Boundary with src/ service layer:
- AgentRunService.run_agent_sync() runs in ThreadPoolExecutor(max_workers=2) from FastAPI
- Uses asyncio.run_coroutine_threadsafe() to bridge back to event loop for quota deduction and SSE send_cb

## Recommended Architecture: Async Transformation

### Design Principle: Bottom-Up Async with Clean Boundary

Async transformation propagates bottom-up through the call chain. The I/O-bound leaf nodes (LLM calls, tool execution, hook callbacks) become async first, then their callers (kernel loop, exp lifecycle) follow. The sync boundary sits at the service layer (src/), which already uses ThreadPoolExecutor -- this boundary changes minimally (adds asyncio.run() wrapper).

```
                    src/ Service Layer (MINIMAL CHANGE)
                    ====================================
                    AgentRunService.run_agent_sync()
                    runs in ThreadPoolExecutor
                    |
                    | asyncio.run() wrapper (new)
                    v
              ==========================================
              ||  matmaster/ Framework (ALL ASYNC)    ||
              ==========================================
              |                                        |
   Exp.run() [async]                     MessageBus [asyncio.Queue]
        |                                        |
   build_runtime() [async]               EventRouter [asyncio.Task]
        |                                        |
   AgentKernel.run() [async]             handlers [async]
        |
   +----+----+----+----+
   |    |    |    |    |
  LLM  Tool Guard Hook Compactor
  [async][async][SYNC][async][async]
```

### Component Boundaries (Before/After)

| Component | Current Signature | After Async | Boundary Type |
|-----------|-------------------|-------------|---------------|
| AgentKernel.run() | `def run(...) -> KernelRunResult` | `async def run(...) -> KernelRunResult` | Internal: fully async |
| AgentKernel._call_llm() | `def _call_llm(...)` with time.sleep retry | `async def _call_llm(...)` with asyncio.sleep retry | Internal: fully async |
| AgentKernel._do_stream_llm() | `for chunk in provider.chat_stream(...)` | `async for chunk in provider.chat_stream(...)` | Internal: follows provider |
| LLMProvider.chat() | `def chat(...) -> LLMResponse` | `async def chat(...) -> LLMResponse` | Protocol boundary changes |
| LLMProvider.chat_stream() | `def chat_stream(...) -> Iterator[StreamChunk]` | `async def chat_stream(...) -> AsyncIterator[StreamChunk]` | Protocol boundary changes |
| LLMProvider.chat_with_retry() | `def chat_with_retry(...)` | **REMOVED from Protocol** | Retry is kernel concern |
| OpenAIProvider | openai.OpenAI + httpx.Client | openai.AsyncOpenAI + httpx.AsyncClient | Implementation change |
| Tool.execute() | `def execute(...) -> str/ToolResult` | `async def execute(...) -> str/ToolResult` | Protocol boundary changes |
| ToolRegistry.execute() | `def execute(...)` | `async def execute(...)` | Follows Tool Protocol |
| BuiltinTool._execute() | `@abstractmethod def _execute(...)` | `@abstractmethod async def _execute(...)` | ABC boundary changes |
| BashTool._execute() | session.exec_bash (sync) | asyncio.to_thread(session.exec_bash) | Leaf: bridges sync session |
| Hook Protocol (7 methods) | all sync | all async | Protocol boundary changes |
| BaseHook (7 methods) | sync no-ops | async no-ops | Default impl changes |
| EventEmitterHook | sync bus.emit() | async bus.emit() | Follows MessageBus |
| Guard.evaluate() | `def evaluate(...)` | **NO CHANGE -- stays sync** | Pure computation, no I/O |
| GuardPipeline.evaluate() | sync | **NO CHANGE -- stays sync** | Pure computation |
| MessageBus | queue.Queue | asyncio.Queue | Full replacement |
| ContextCompactor.compact_if_needed() | sync (calls LLM chat) | async (awaits LLM chat) | Follows LLMProvider |
| Exp.assemble() | sync pure transform | **NO CHANGE -- stays sync** | No I/O |
| Exp.build_runtime() | sync resource creation | async (tool/provider init) | Minor: mostly data transform |
| Exp.run() | sync orchestration | async orchestration | Follows kernel |
| Exp._make_spawn_fn() | returns sync Callable | returns async Callable | Follows kernel |
| EventRouter | threading.Thread consumer | asyncio.Task consumer | Full replacement |
| EventHandler.handle() | `def handle(...)` | `async def handle(...)` | Protocol boundary changes |
| AgentRunService.run_agent_sync() | sync in ThreadPoolExecutor | sync wrapper + asyncio.run() | Sync/async bridge |
| Playground.prepare() | sync file I/O | **NO CHANGE (out of scope)** | Stays sync |
| DevShell DevRunner.run() | sync | **NO CHANGE (out of scope)** | Wraps with asyncio.run() later |

### Data Flow (After Async)

```
AgentRunService.run_agent_sync() [sync, in ThreadPoolExecutor]
  |
  asyncio.run(self._run_agent_async(...))     # NEW: creates event loop for this run
  |
  v
_run_agent_async() [new async orchestrator method]
  |
  +-- Playground.prepare()               [stays sync -- file I/O, out of scope]
  +-- EventRouter.start()                [asyncio.create_task, not threading.Thread]
  +-- await Exp.build_runtime(ctx, bus)  [async: provider/tool init]
  +-- await kernel.run(spec, task, ...)  [async: main agent loop]
  |     |
  |     +-- loop:
  |     |     await run_pre_llm_call()    [async hooks]
  |     |     await run_should_continue() [async hooks]
  |     |     await compactor.compact_if_needed() [async: LLM call]
  |     |     await _call_llm()           [async: stream iteration]
  |     |     |
  |     |     for each tool_call:
  |     |       guard_pipeline.evaluate() [stays SYNC -- pure computation]
  |     |       await run_pre_tool_call() [async hooks]
  |     |       await registry.execute()  [async tool dispatch]
  |     |       await run_post_tool_call()[async hooks]
  |     |
  |     +-- return KernelRunResult
  |
  +-- await bus.emit(run_result_event)   [async: asyncio.Queue]
  +-- await router.stop()                [async: drain + close]
```

## Patterns to Follow

### Pattern 1: Protocol Hard Cut (Not Gradual Dual Protocols)

Replace sync Protocols with async Protocols in one shot. Do not maintain parallel sync/async Protocol definitions.

**Why:** The codebase is internal (no external consumers). All 863 tests need async infrastructure regardless. Dual Protocols double API surface for zero benefit.

**What this means:** Phase 1 of implementation changes Protocol files only. This breaks all tests. Each subsequent phase fixes implementations and their tests module by module.

```python
# matmaster/types/llm_provider.py -- AFTER

from typing import Any, AsyncIterator, Protocol, runtime_checkable
from matmaster.types.messages import LLMResponse, StreamChunk


@runtime_checkable
class LLMProvider(Protocol):
    """Async LLM provider -- primary Protocol for v2.0+.

    chat_with_retry is REMOVED from Protocol. Retry logic lives in
    AgentKernel._call_llm() (timeout-doubling strategy). The Protocol
    only defines primitive operations.
    """

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]: ...
```

**chat_with_retry removal rationale:** The current codebase already has retry logic in two places: (1) LLMProvider.chat_with_retry() in the Protocol, and (2) AgentKernel._call_llm() with timeout-doubling. The kernel is the sole consumer of the provider and already implements its own retry. Protocol-level retry is redundant and forces every provider to duplicate retry logic. Remove from Protocol; keep only the kernel-level retry.

### Pattern 2: Guard Stays Sync (Explicitly)

Guards (Guard Protocol, GuardPipeline, LoopDetectionGuard) perform pure computation: JSON fingerprinting over a deque sliding window. No I/O, no await needed. Making them async adds overhead for a sub-millisecond CPU operation.

```python
# Guard Protocol -- NO CHANGE from v1.1:
@runtime_checkable
class Guard(Protocol):
    def evaluate(self, ctx: GuardContext) -> GuardResult: ...

# Calling sync from async is free for CPU-bound work:
# In async kernel:
guard_result = guard_pipeline.evaluate(tc, turn, max_turns)  # no await
```

### Pattern 3: MessageBus to asyncio.Queue

Direct replacement. API names stay the same (emit/get/get_nowait/pending/empty). Methods become async where they were blocking.

```python
import asyncio
from matmaster.types.events import BusEvent


class MessageBus:
    """Async event bus backed by asyncio.Queue."""

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=maxsize)

    async def emit(self, event: BusEvent) -> None:
        """Emit event (coroutine-safe, not thread-safe)."""
        await self._queue.put(event)

    async def get(self, timeout: float | None = None) -> BusEvent:
        """Consume next event. Raises asyncio.TimeoutError on timeout."""
        if timeout is not None:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        return await self._queue.get()

    def get_nowait(self) -> BusEvent:
        """Non-blocking consume. Raises asyncio.QueueEmpty when empty."""
        return self._queue.get_nowait()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def empty(self) -> bool:
        return self._queue.empty()
```

### Pattern 4: EventRouter as asyncio.Task

Replace the background threading.Thread with an asyncio.Task. The router starts as a task in the same event loop as the kernel, consuming from the async MessageBus.

```python
class EventRouter:
    """Async event consumer dispatching to handlers."""

    def __init__(self, bus: MessageBus, handlers: list[EventHandler]) -> None:
        self._bus = bus
        self._handlers = handlers
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Spawn consume loop as asyncio.Task."""
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self, drain_timeout: float = 2.0) -> None:
        """Cancel task, drain remaining events, close handlers."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Drain remaining events
        while not self._bus.empty:
            try:
                event = self._bus.get_nowait()
                await self._dispatch(event)
            except asyncio.QueueEmpty:
                break
        await self._close_handlers()

    async def _consume_loop(self) -> None:
        """Main consume loop."""
        while True:
            try:
                event = await asyncio.wait_for(self._bus.get(), timeout=0.1)
                await self._dispatch(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
```

### Pattern 5: asyncio.Event for Cancellation (Replace threading.Event)

threading.Event is not compatible with asyncio. Replace with asyncio.Event in the kernel. The src/ service layer thread bridges via loop.call_soon_threadsafe().

```python
# In AgentKernel.run():
async def run(
    self,
    spec: AgentRuntimeSpec,
    task: str,
    history: list[Message] | None = None,
    stop_event: asyncio.Event | None = None,  # was threading.Event
) -> KernelRunResult:
    ...
    while turn < spec.max_turns:
        if stop_event and stop_event.is_set():
            return self._finish(...)
        ...
```

**Thread-safe stop signal from service layer:**
```python
# In src/services/agent_run_service.py:
# The run creates its own event loop via asyncio.run().
# To set stop_event from the FastAPI thread:

async def _run_agent_async(self, ..., external_stop_requested: Callable[[], bool]):
    stop_event = asyncio.Event()
    # Periodically check external cancellation signal
    async def _poll_cancel():
        while not stop_event.is_set():
            if external_stop_requested():
                stop_event.set()
                return
            await asyncio.sleep(0.5)
    cancel_poller = asyncio.create_task(_poll_cancel())
    try:
        result = await kernel.run(spec, task, stop_event=stop_event)
    finally:
        cancel_poller.cancel()
```

Alternative (simpler): since asyncio.run() creates a NEW event loop per thread, the service layer can store a reference to the loop and use loop.call_soon_threadsafe(stop_event.set). This works because the loop is known and accessible:

```python
def run_agent_sync(self, ...):
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()  # created before loop.run_until_complete
    # Store loop reference for external cancellation
    self._active_loops[session_id] = (loop, stop_event)
    try:
        loop.run_until_complete(self._run_agent_async(..., stop_event=stop_event))
    finally:
        del self._active_loops[session_id]
        loop.close()

def cancel_run(self, session_id: str) -> None:
    """Called from FastAPI thread to cancel a running agent."""
    entry = self._active_loops.get(session_id)
    if entry:
        loop, stop_event = entry
        loop.call_soon_threadsafe(stop_event.set)
```

### Pattern 6: asyncio.to_thread for evomaster Session Calls

evomaster BaseSession methods (exec_bash, file operations via paramiko/docker) are inherently sync. Bridge with asyncio.to_thread() to avoid blocking the event loop.

```python
# In BashTool._execute (async):
async def _execute(self, arguments: dict[str, Any]) -> str:
    session = self._require_session()
    command = arguments.get("command", "").strip()
    ...
    # Bridge sync session call to thread pool
    result = await asyncio.to_thread(
        session.exec_bash,
        command=command,
        timeout=timeout,
        is_input=is_input,
    )
    ...
```

**Scope of asyncio.to_thread usage:**
- BashTool: session.exec_bash()
- ReadTool, WriteTool, EditTool, GlobTool, GrepTool, ListDirTool: all use session.exec_bash() for remote execution
- EvoToolAdapter: wraps evomaster BaseTool.execute(session, args_json)
- MonitorJobTool (via EvoToolAdapter): same pattern

### Pattern 7: SubAgent Spawn as Sequential Async

SubAgent spawn stays sequential in v2.0 (parent awaits child completion). The spawn_fn return type changes from sync Callable to async Callable.

```python
# In Exp._make_spawn_fn():
@staticmethod
def _make_spawn_fn(
    ctx: PlaygroundContext,
    bus: MessageBus | None,
    source_prefix: str,
) -> Callable[[str, str, asyncio.Event | None], Awaitable[str]]:

    async def spawn_fn(
        exp_name: str,
        task: str,
        stop_event: asyncio.Event | None = None,
    ) -> str:
        child_config = load_exp_config(exp_name)
        child_exp = Exp(child_config)
        child_runtime = await child_exp.build_runtime(ctx, bus=bus, ...)
        try:
            run_result = await child_runtime.kernel.run(
                child_runtime.spec, task, stop_event=stop_event
            )
            ...
        finally:
            await child_runtime.cleanup()  # cleanup may become async

    return spawn_fn
```

**SpawnTool._execute becomes async:**
```python
async def _execute(self, arguments: dict[str, Any]) -> str:
    ...
    return await self._spawn_fn(exp_name, task, self._stop_event)
```

**v2.0 scope:** Parent awaits child. True concurrent multi-agent (parent continues while child runs) is v2.1+ feature.

### Pattern 8: Service Layer Bridge (asyncio.run Wrapper)

The ONLY change to src/ code. run_agent_sync() wraps the new async orchestrator.

```python
# In src/services/agent_run_service.py:
class AgentRunService:
    def run_agent_sync(self, session_id, user_prompt, send_cb, loop, ...):
        """Called from ThreadPoolExecutor -- bridges to async matmaster."""
        # Create event loop for this run
        run_loop = asyncio.new_event_loop()
        try:
            run_loop.run_until_complete(
                self._run_agent_async(
                    session_id, user_prompt, send_cb, loop, ...
                )
            )
        finally:
            run_loop.close()

    async def _run_agent_async(self, session_id, user_prompt, send_cb, fastapi_loop, ...):
        """Async orchestration using matmaster components."""
        bus = MessageBus()  # now asyncio.Queue
        ...
        # Playground.prepare() stays sync (no change)
        pg_ctx = playground.prepare(run_meta)

        # All matmaster calls are now async
        runtime = await exp.build_runtime(pg_ctx, bus=bus, ...)
        result = await runtime.kernel.run(spec, task, ...)

        # SSE callbacks that need FastAPI loop
        def _send_sse(payload):
            asyncio.run_coroutine_threadsafe(send_cb(payload), fastapi_loop)
        ...
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Dual Sync+Async Protocol Definitions

**What:** Maintaining both SyncLLMProvider and AsyncLLMProvider Protocols for gradual migration.
**Why bad:** Python typing cannot express "method is either sync or async" in a runtime_checkable Protocol. Dual Protocols double the interface surface for an internal codebase with no external consumers. Every component needs to check which variant it received.
**Instead:** One async Protocol. Sync callers (DevShell) use asyncio.run() wrapper.

### Anti-Pattern 2: Making Guards Async

**What:** Converting Guard.evaluate() to async because "everything should be async."
**Why bad:** Guards are pure computation (JSON fingerprint + deque sliding window, <1ms). Async overhead (coroutine frame, task scheduling) costs more than the computation itself. Forces GuardPipeline into await chains for no I/O gain.
**Instead:** Keep Guard sync. Sync calls from async context are free for CPU-bound work.

### Anti-Pattern 3: Using janus Queue for MessageBus

**What:** Using the janus library for a dual sync/async queue to allow both sync and async producers.
**Why bad:** After migration, ALL producers (kernel hooks, EventEmitterHook) and ALL consumers (EventRouter) are async. Janus adds a dependency and measurable performance overhead (janus docs: "for sync-only and async-only cases, use native queues -- otherwise the slowdown can be significant"). The only sync boundary (src/ service layer) uses asyncio.run() which creates the event loop context.
**Instead:** asyncio.Queue directly. No sync producers after migration.

### Anti-Pattern 4: Partial Protocol Async (Some Methods Sync, Some Async)

**What:** Making only chat_stream() async while keeping chat() sync in LLMProvider.
**Why bad:** ContextCompactor._summarize() calls LLMProvider.chat(). If chat() stays sync, the compactor blocks the event loop during summarization. Partial async creates confusing contracts where some methods can be awaited and others cannot.
**Instead:** All LLMProvider methods become async. OpenAIProvider uses AsyncOpenAI for everything.

### Anti-Pattern 5: asyncio.to_thread() for Everything

**What:** Wrapping every existing sync function with asyncio.to_thread() instead of native async conversion.
**Why bad:** Thread pool bridges add latency and consume thread pool slots. For I/O-bound operations like LLM API calls and subprocess execution, native async is strictly better. to_thread() should be reserved for genuinely unavoidable sync libraries.
**Instead:** Native async for all matmaster code. Only use to_thread() for evomaster BaseSession method calls (paramiko/docker -- genuinely sync C-extension libraries).

### Anti-Pattern 6: Nested asyncio.run()

**What:** Calling asyncio.run() inside already-async code, or from code that already has a running event loop.
**Why bad:** RuntimeError: "cannot be called when another event loop is running." This is the single most common migration bug.
**Instead:** asyncio.run() appears ONLY at the one sync-to-async boundary (AgentRunService.run_agent_sync). All internal matmaster code uses plain await. DevShell uses its own asyncio.run() at entry point.

### Anti-Pattern 7: Keeping threading.Event Alongside asyncio.Event

**What:** Using threading.Event for stop_event because "it works from any thread."
**Why bad:** threading.Event.wait() in async context blocks the event loop. asyncio.Event is not thread-safe but that is the correct choice for async code. Cross-thread signaling uses loop.call_soon_threadsafe().
**Instead:** asyncio.Event inside matmaster. Service layer bridges via loop.call_soon_threadsafe(stop_event.set) or a cancel-poller task.

## Migration Order (Recommended Build Sequence)

The order is driven by dependency analysis. Each phase builds on the previous.

### Phase 1: Protocol Definitions + Test Infrastructure (Foundation)

Change Protocol definitions only. No implementations yet. Set up pytest-asyncio infrastructure.

**Modified files:**
- `matmaster/types/llm_provider.py` -- LLMProvider: chat -> async, chat_stream -> AsyncIterator, remove chat_with_retry
- `matmaster/tools/tool_registry.py` -- Tool Protocol: execute -> async; ToolRegistry.execute -> async
- `matmaster/core/hooks.py` -- Hook Protocol: all 7 methods -> async; BaseHook: async no-ops; all run_* helpers -> async; EventEmitterHook -> async
- `matmaster/types/guards.py` -- Guard Protocol: **NO CHANGE**

**New infrastructure:**
- `pyproject.toml` -- add pytest-asyncio dev dependency
- `tests/conftest.py` -- asyncio mode configuration
- `tests/fixtures/` -- async mock factories (AsyncMockProvider, AsyncMockTool)

**Test impact:** All 863 tests break at Protocol level. This is expected and intentional.

**Dependency:** None -- this is the foundation.

### Phase 2: LLM Provider (Leaf I/O -- Highest Value)

Convert OpenAIProvider to use AsyncOpenAI. This is the highest-value change because LLM calls dominate wall-clock time.

**Modified files:**
- `matmaster/providers/openai_provider.py` -- openai.OpenAI -> openai.AsyncOpenAI, httpx.Client -> httpx.AsyncClient, Iterator -> AsyncIterator, chat/chat_stream -> async, remove chat_with_retry method
- `matmaster/providers/llm_factory.py` -- build_provider returns async provider

**Test files to fix:**
- `tests/matmaster/providers/` -- all provider tests -> async

**Dependency:** Phase 1 (async LLMProvider Protocol)

### Phase 3: Tool System (Leaf I/O)

Convert all 12 builtin tools + adapter + registry to async execute.

**Modified files:**
- `matmaster/tools/builtin/base.py` -- BuiltinTool: execute -> async, _execute -> async abstract
- `matmaster/tools/builtin/bash_tool.py` -- asyncio.to_thread(session.exec_bash)
- `matmaster/tools/builtin/read_tool.py`, write_tool.py, edit_tool.py -- asyncio.to_thread for session calls
- `matmaster/tools/builtin/glob_tool.py`, grep_tool.py, listdir_tool.py -- asyncio.to_thread
- `matmaster/tools/builtin/spawn_tool.py` -- _execute -> async, spawn_fn -> async callable
- `matmaster/tools/builtin/task/*.py` -- async (JSON file I/O, minor)
- `matmaster/tools/tool_registry.py` -- execute() -> async
- `matmaster/tools/evomaster_tool_adapter.py` -- execute() -> async (wraps sync evo tool with asyncio.to_thread)
- `matmaster/tools/skill_tool.py` -- execute -> async
- `matmaster/tools/lazy_mcp.py` -- LazyMCPTool.execute -> async

**Key boundary:** All tools that call session methods use asyncio.to_thread() to bridge the sync evomaster session API. This is the correct approach because evomaster sessions wrap paramiko (SSH), docker SDK, and subprocess -- all inherently sync.

**Test files to fix:**
- `tests/matmaster/tools/` -- all tool tests -> async

**Dependency:** Phase 1 (async Tool Protocol)

### Phase 4: Hook System (Middleware)

Convert all hooks to async. Hooks are lightweight and this phase is straightforward.

**Modified files:**
- `matmaster/core/hooks.py` -- Hook Protocol, BaseHook, all run_* helpers, EventEmitterHook -> async
- `matmaster/hooks/confirmation.py` -- ConfirmationHook: pre_tool_call -> async, queue.Queue.get -> asyncio.Queue.get
- `matmaster/hooks/output_processor.py` -- post_tool_call -> async
- `matmaster/hooks/skill_hit.py` -- post_tool_call -> async
- `matmaster/hooks/assistant_state.py` -- on_stream_chunk/on_segment_complete -> async

**Test files to fix:**
- `tests/matmaster/hooks/` -- all hook tests -> async
- `tests/matmaster/core/test_hooks.py` -- hook Protocol tests -> async

**Dependency:** Phase 1 (async Hook Protocol)

### Phase 5: MessageBus + EventRouter (Infrastructure)

Replace sync queue/thread infrastructure with async queue/task.

**Modified files:**
- `matmaster/core/bus.py` -- queue.Queue -> asyncio.Queue, emit/get -> async
- `matmaster/integration/event_router.py` -- threading.Thread -> asyncio.Task, EventHandler Protocol: handle -> async
- `matmaster/integration/sse_handler.py` -- handle -> async
- `matmaster/integration/persistence_handler.py` -- handle -> async
- `matmaster/integration/workspace_handler.py` -- handle -> async

**Test files to fix:**
- `tests/matmaster/core/test_bus.py` -- async queue tests
- `tests/matmaster/integration/` -- all handler tests -> async

**Dependency:** Phase 4 (EventEmitterHook needs async bus.emit)

### Phase 6: AgentKernel (Core Loop -- Convergence Point)

All leaf dependencies are now async. The kernel loop becomes async. This is the largest single change.

**Modified files:**
- `matmaster/core/agent.py` -- run() -> async, _call_llm -> async (asyncio.sleep replaces time.sleep), _do_stream_llm -> async (async for replaces for), stop_event: asyncio.Event replaces threading.Event
- `matmaster/core/context_compactor.py` -- compact_if_needed -> async, _summarize -> async (uses async LLMProvider.chat)
- `matmaster/core/guard_pipeline.py` -- **NO CHANGE** (stays sync, called from async kernel without await)

**Test files to fix:**
- `tests/matmaster/core/test_agent.py` -- kernel execution tests -> async
- `tests/matmaster/core/test_context_compactor.py` -- async

**Dependency:** Phases 2, 3, 4, 5 (all consumed components async)

### Phase 7: Exp Lifecycle (Orchestration)

Exp methods become async to orchestrate async kernel.

**Modified files:**
- `matmaster/core/exp.py` -- build_runtime() -> async, run() -> async, _make_spawn_fn return type -> async callable, _run_cleanup_callbacks -> handle async cleanups
- `matmaster/types/runtime.py` -- AgentRuntime.cleanup type -> Callable (may need to support async cleanup)

**Test files to fix:**
- `tests/matmaster/core/test_exp.py` -- all exp lifecycle tests -> async
- `tests/matmaster/core/test_exp_skills.py` -- async

**Dependency:** Phase 6 (async kernel)

### Phase 8: Service Layer Bridge (Boundary -- Minimal src/ Change)

Add asyncio.run() bridge in the service layer. This is the ONLY change to src/ code.

**Modified files:**
- `src/services/agent_run_service.py` -- run_agent_sync wraps new _run_agent_async with asyncio.new_event_loop().run_until_complete(), stop_event bridging

**Test files to fix:**
- `tests/test_chat_stream_direct.py` and other integration tests -> verify async bridge works

**Dependency:** Phase 7 (all matmaster async)

### Phase 9: DevShell Wrapper (Out of Scope for v2.0)

DevShell is explicitly out of scope per PROJECT.md. When needed:

**Modified files:**
- `matmaster/devshell/runner.py` -- DevRunner.run() wraps async kernel with asyncio.run()

**Dependency:** Phase 7

## Integration Points: Sync/Async Boundaries

There are exactly 3 sync/async boundaries in the final architecture:

### Boundary 1: AgentRunService -> matmaster (Primary)

```
src/services/agent_run_service.py (sync, in ThreadPoolExecutor)
  |
  asyncio.new_event_loop() + loop.run_until_complete()
  |
  v
matmaster/ (fully async, dedicated event loop per run)
```

**Mechanism:** Each agent run gets its own event loop via asyncio.new_event_loop(). This avoids conflicts with the FastAPI event loop.
**Stop event bridge:** Service layer stores (loop, stop_event) reference. External cancellation calls loop.call_soon_threadsafe(stop_event.set).
**SSE callback bridge:** Already uses asyncio.run_coroutine_threadsafe(send_cb, fastapi_loop) -- no change needed.

### Boundary 2: evomaster BaseSession Calls (Tool Layer)

```
matmaster/tools/builtin/ (async)
  |
  await asyncio.to_thread(session.exec_bash, command=..., timeout=...)
  |
  v
evomaster/agent/session/ (sync: LocalSession, SSHSession, DockerSession)
```

**Mechanism:** asyncio.to_thread() for all session method calls.
**Affected tools:** BashTool, ReadTool, WriteTool, EditTool, GlobTool, GrepTool, ListDirTool (all use session.exec_bash), EvoToolAdapter (wraps MonitorJobTool).
**Why not convert evomaster sessions:** Out of scope. evomaster uses paramiko (inherently sync C-extension for SSH), docker SDK (sync HTTP), subprocess (has async alternative but session API is the contract).

### Boundary 3: DevShell (Deferred)

```
matmaster/devshell/runner.py (sync REPL input loop)
  |
  asyncio.run(...)
  |
  v
matmaster/ (fully async)
```

**Deferred to:** Post-v2.0. DevShell continues to work during development by wrapping async calls with asyncio.run() at the REPL entry point.

## Protocol Changes Summary

| Protocol | File | Changes |
|----------|------|---------|
| LLMProvider | types/llm_provider.py | chat: sync -> async, chat_stream: Iterator -> AsyncIterator, chat_with_retry: **REMOVED** |
| Tool | tools/tool_registry.py | execute: sync -> async |
| Hook (7 methods) | core/hooks.py | pre_tool_call, post_tool_call, pre_llm_call, should_continue, on_stream_chunk, on_segment_complete, on_guard_blocked: all sync -> async |
| Guard | types/guards.py | **NO CHANGE** (stays sync) |
| EventHandler | integration/event_router.py | handle: sync -> async |
| WorkerRegistry | types/worker_registry.py | **NO CHANGE** (src/ layer, out of scope) |
| ReplyQueueLike | hooks/confirmation.py | get: blocking -> async (asyncio.Queue pattern) |

## New Dependencies

| Package | Version | Purpose | Scope |
|---------|---------|---------|-------|
| pytest-asyncio | >=0.24 | async test support (@pytest.mark.asyncio) | dev only |

No new production dependencies. AsyncOpenAI is built into the openai SDK. httpx.AsyncClient is built into httpx. asyncio.Queue is stdlib. All existing deps already support async.

## Scalability Considerations

| Concern | Current (sync) | After Async (v2.0) | Future (v2.1+ multi-agent) |
|---------|----------------|---------------------|---------------------------|
| Concurrent agents | ThreadPoolExecutor(2), each blocks a thread | Same thread pool, but internal I/O non-blocking | Multiple agents share one event loop |
| SubAgent spawn | Sequential, blocks parent thread | Sequential-async (parent awaits child) | asyncio.create_task: parent + child concurrent |
| LLM streaming | Blocks thread during entire stream | Non-blocking: event loop serves other tasks | Multiple LLM streams interleaved |
| Tool execution | Blocks thread | Non-blocking (session calls via to_thread) | Parallel tool execution possible |
| Event processing | Background thread + queue.Queue | asyncio.Task + asyncio.Queue | Same, scales to multi-agent event bus |
| Memory per agent | Thread stack ~8MB default | Coroutine frame ~few KB | 10x+ reduction enables dense multi-agent |

## Sources

- Python asyncio documentation: https://docs.python.org/3/library/asyncio.html
- Python asyncio.Queue: https://docs.python.org/3/library/asyncio-queue.html
- Python asyncio synchronization primitives: https://docs.python.org/3/library/asyncio-sync.html
- OpenAI Python SDK (AsyncOpenAI): https://github.com/openai/openai-python
- Gradual asyncio migration patterns: https://www.erichgrunewald.com/posts/gradually-migrating-python-code-to-asyncio/
- janus queue (evaluated, rejected for this use case): https://github.com/aio-libs/janus
- Python typing Protocol discussion: https://github.com/python/typing/discussions/1520
- asyncio cancellation patterns: https://docs.python.org/3/library/asyncio-task.html
- asyncio.Event thread safety: https://docs.python.org/3/library/asyncio-sync.html
- Combining sync and async Python code: https://spwoodcock.dev/blog/2025-02-python-dry-async/
