# Feature Landscape: Async Agent Framework Patterns

**Domain:** Python async agent execution -- converting sync agent framework to async/await
**Researched:** 2026-03-26
**Scope:** How real-world agent frameworks (Pydantic AI, OpenAI Agents SDK, LangGraph, Microsoft Agent Framework, Google ADK) implement async execution loops, LLM providers, tool dispatch, hooks, event systems, and context compaction

## Table Stakes

Features every async agent framework implements. Missing any of these means the async conversion is incomplete or architecturally broken.

| Feature | Why Expected | Complexity | Sync Equivalent | Notes |
|---------|--------------|------------|-----------------|-------|
| Async kernel execution loop | Core of async conversion -- the `while turn < max_turns` loop must yield control at every I/O point | Medium | `AgentKernel.run()` returns `KernelRunResult` | Every framework: Pydantic AI graph loop, OpenAI SDK Runner.run(), LangGraph ainvoke. The loop itself is `async def run()` with `await` at LLM call and tool dispatch |
| AsyncIterator[StreamChunk] for LLM streaming | LLM streaming is the longest I/O operation. `async for chunk in provider.chat_stream()` is the fundamental async pattern | Low | `Iterator[StreamChunk]` from `chat_stream()` | OpenAI SDK: `AsyncOpenAI.chat.completions.create(stream=True)` returns `AsyncStream`. All frameworks use `async for` over LLM streams |
| Async LLM provider protocol | Provider.chat() and Provider.chat_stream() must be async methods | Low | `LLMProvider` Protocol with sync methods | Pydantic AI: all model calls are awaitable. OpenAI SDK: AsyncOpenAI is drop-in. Simple signature change: `async def chat()` + `async def chat_stream() -> AsyncIterator[StreamChunk]` |
| Async tool execute() | Each tool's execute must be awaitable for non-blocking I/O (subprocess, HTTP, file I/O) | Medium | `Tool.execute()` returns `str \| ToolResult` | Pydantic AI: async functions run on event loop, sync functions offloaded to threads via `asyncio.to_thread()`. OpenAI SDK: sync tools run via `asyncio.to_thread()`. Both approaches viable |
| asyncio.Event for cancellation | Replace `threading.Event` stop_event with `asyncio.Event`. Cancellation checks become `if stop_event.is_set()` (same API, async-native) | Low | `threading.Event` stop_event | asyncio.Event has same set/is_set/wait API. Additionally, asyncio provides native CancelledError at every await point. Using asyncio.Event is simpler and sufficient |
| asyncio.Queue for MessageBus | Replace `queue.Queue` with `asyncio.Queue` for event transport between kernel and handlers | Low | `MessageBus` wrapping `queue.Queue` | Pure drop-in: `asyncio.Queue` has same put/get semantics, just async. If sync consumers remain (SSE handler in thread), use janus for dual-face queue |
| Async hooks (all 7 hook points) | Hooks must be awaitable so hook implementations can do async I/O (e.g., EventEmitterHook emitting to async bus) | Medium | `Hook` Protocol with 7 sync methods | All frameworks: OpenAI SDK RunHooks has 7 async methods (on_agent_start, on_tool_start, etc.). Microsoft Agent Framework middleware is all async. Pattern: `async def pre_tool_call()` |
| Async retry with asyncio.sleep | Replace `time.sleep(backoff)` with `await asyncio.sleep(backoff)` in retry logic. Without this, retries block the entire event loop | Low | `time.sleep()` in `_call_llm` and `chat_with_retry` | Every async framework uses `await asyncio.sleep()`. Critical: `time.sleep()` in async code blocks ALL coroutines on the same event loop |
| Async context compaction | ContextCompactor._summarize() calls LLM synchronously. Must become `await self._summary_provider.chat()` | Low | `ContextCompactor.compact_if_needed()` calling sync `chat()` | Microsoft Agent Framework: CompactionStrategy is `async def __call__`. Google ADK: compaction runs asynchronously in background. matmaster compactor calls LLM -- that call MUST be async |
| Async subprocess for BashTool | Replace `session.exec_bash()` (blocking subprocess) with `asyncio.create_subprocess_exec()` or `asyncio.to_thread()` wrapper | Medium | `session.exec_bash()` blocking call | Python stdlib: `asyncio.create_subprocess_exec()` is the async equivalent. But since session.exec_bash() is evomaster code (out of scope), wrap in `asyncio.to_thread()` instead |
| Async Exp lifecycle | `assemble()`, `build_runtime()`, `run()` must be async. `run()` wraps async kernel.run() with async cleanup | Low-Med | `Exp.run()` calling sync `kernel.run()` | assemble() is pure data transform (could stay sync), but build_runtime() and run() involve I/O (MCP init, kernel execution). Make all three async for consistency |
| Async cleanup callbacks | `_run_cleanup_callbacks()` must be async to support async resource cleanup (MCP connections, subprocess termination) | Low | Sync `Callable[[], None]` cleanup callbacks | Type changes to `Callable[[], Awaitable[None] \| None]`. Run sync callbacks via direct call, async ones via await. Microsoft Agent Framework: cleanup is async |

## Differentiators

Features that set a well-designed async framework apart. Not strictly required for "async works", but significantly improve concurrency, debuggability, or future multi-agent capability.

| Feature | Value Proposition | Complexity | Sync Equivalent | Notes |
|---------|-------------------|------------|-----------------|-------|
| Parallel tool dispatch (asyncio.gather) | When LLM returns N tool_calls, execute them concurrently instead of serially. 2-5x speedup for multi-tool turns | Medium | Serial `for tc in response.tool_calls` loop | **Pydantic AI default behavior**: `asyncio.gather(*[tool.execute(tc) for tc in tool_calls])`. OpenAI Agents SDK: tools run concurrently. LangGraph: parallel tool node. matmaster currently runs tool_calls serially. This is the single highest-value async optimization |
| Sequential tool mode option | Some tools have side effects that require ordering (write-then-read). Need opt-out from parallel dispatch | Low | N/A (always serial today) | Pydantic AI: `sequential=True` per-tool flag, or `agent.parallel_tool_call_execution_mode('sequential')` context manager. Implement as a flag on Tool Protocol or on ToolRegistry |
| Async generator kernel (yield events) | `async def run() -> AsyncGenerator[AgentEvent, None]` that yields events as they happen, instead of returning final result | High | Returns `KernelRunResult` at end | OpenAI SDK: `run_streamed()` returns stream of events. Pydantic AI: `run_stream()` + `iter()` for node-by-node iteration. This enables real-time SSE without MessageBus intermediary. BUT: major API change, consider for later |
| Structured concurrency (TaskGroup) | Use `asyncio.TaskGroup` (Python 3.11+) for managing concurrent tool execution and subagent spawns. Guaranteed cleanup on exception | Medium | N/A | Python 3.11+ feature. Better than bare `asyncio.gather()` because exceptions in one task cancel siblings. matmaster targets Python 3.13, so TaskGroup is available |
| Async subagent spawn as coroutine | `spawn_fn` becomes `async def` -- child agent runs as a coroutine in the same event loop instead of blocking the parent | Medium | Sync `spawn_fn()` blocking parent until child completes | OpenAI SDK: `asyncio.gather()` for parallel agent runs. Pydantic AI subagents: async spawn with sync/async auto-select. Key benefit: parent agent stays responsive while child runs, enables future parallel subagents |
| Graceful cancellation with CancelledError | Beyond `stop_event.is_set()` checks, support `asyncio.Task.cancel()` which raises CancelledError at next await. Cleanup runs in finally blocks | Medium | Only `threading.Event` polling | asyncio native: `task.cancel()` injects CancelledError at the next `await`. Combined with try/finally cleanup, this is more robust than polling `stop_event`. OpenAI SDK streams require consuming before context exit for this reason |
| Sync-to-async bridge for session I/O | Many BuiltinTools call `session.exec_bash()` or `session.file_read()` which are sync (evomaster session). Wrap in `asyncio.to_thread()` for non-blocking execution | Low | Direct sync calls | Pydantic AI pattern: sync tool functions automatically offloaded via `asyncio.to_thread()`. This is critical for matmaster because evomaster session methods are sync and rewriting them is out of scope (v2.0 constraint) |
| Per-tool timeout with asyncio.wait_for | Wrap each `await tool.execute()` in `asyncio.wait_for(coro, timeout=N)`. Prevents single tool from blocking the entire loop forever | Low | No per-tool timeout | Standard asyncio pattern. Pydantic AI: tool-level timeout configuration. Easy to implement: `await asyncio.wait_for(tool.execute(args), timeout=tool.timeout)` |
| Background compaction (non-blocking) | Run context compaction in a background task while the agent continues processing. Apply summary when ready on next turn | High | Blocking `compact_if_needed()` in kernel loop | Microsoft Agent Framework: compaction runs as middleware, potentially async. Google ADK: "Runner handles compaction in background." matmaster v2.0 scope probably too aggressive for this -- keep inline compaction but make the LLM call async |

## Anti-Features

Features to explicitly NOT build during this async conversion. Adding these would increase scope, introduce unnecessary complexity, or conflict with architecture constraints.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Dual sync+async Protocol with runtime dispatch | Maintaining both `def chat()` and `async def chat()` on the same Protocol doubles the API surface and testing burden. Pydantic AI has `run_sync()` but internally everything is async | Make everything async. Provide `asyncio.run()` or `loop.run_until_complete()` wrappers at entry points (DevShell, tests). Pydantic AI's `run_sync()` is just `loop.run_until_complete(self.run())` |
| Full async generator kernel return type | Changing `run()` from returning `KernelRunResult` to `AsyncGenerator[AgentEvent, None]` redesigns the kernel-consumer contract. MessageBus already decouples event delivery | Keep `async def run() -> KernelRunResult`. Events flow through async MessageBus as today. Async generator pattern is a future optimization (v3) |
| Rewriting evomaster session to async | evomaster BaseSession.exec_bash(), file_read() etc. are deeply sync. Rewriting them is a separate project. Out of scope per v2.0 constraints | Use `asyncio.to_thread(session.exec_bash, ...)` to offload sync session calls to thread pool. This is what OpenAI Agents SDK does for sync function tools |
| Actor-model concurrency (per-agent event loop) | Running each agent in its own event loop or process for isolation. Over-engineering for v2.0 scope | Single event loop, multiple coroutines. SubAgents are coroutines in the same loop. Isolation via TaskGroup if needed |
| Thread pool for ALL tool execution | Blanket offloading all tools to ThreadPoolExecutor defeats the purpose of async. Only sync-bound tools need threads | Distinguish: tools with native async I/O run on event loop directly. Only tools wrapping sync session calls use `asyncio.to_thread()`. Pydantic AI: "always use async unless doing blocking I/O" |
| Distributed event bus (Redis Pub/Sub) | The current MessageBus is in-process. Adding distributed transport is a separate concern from async conversion | Keep asyncio.Queue for in-process bus. The existing Redis-based worker coordination (src/ layer) stays unchanged per v2.0 scope |
| async def for Guard.evaluate() | Guards are pure computation (fingerprint comparison, threshold check). No I/O involved. Making them async adds complexity with zero benefit | Keep `def evaluate()` synchronous. GuardPipeline.evaluate() stays sync, called from the async kernel via direct invocation (no await needed for CPU-only code) |
| Hook short-circuit via async gather | Running all hooks in parallel with gather and short-circuiting on first SKIP. Hooks are sequential by nature (observation order matters, intercepting hooks must short-circuit) | Keep sequential `for hook in hooks: await hook.pre_tool_call()` pattern. Hooks are cheap; parallelizing them risks ordering bugs |

## Feature Dependencies

```
AsyncOpenAI provider --> Async kernel loop (kernel awaits provider)
Async tool execute   --> Async kernel loop (kernel awaits tool dispatch)
asyncio.Queue bus    --> Async hooks (EventEmitterHook emits to async bus)
asyncio.Event stop   --> Async kernel loop (cancellation checks)
Async hooks          --> Async kernel loop (kernel awaits hooks)
Async compactor      --> Async LLM provider (compactor awaits summary LLM call)
Async Exp lifecycle  --> Async kernel + async tools + async hooks (Exp orchestrates all)
Async subagent spawn --> Async Exp.run() (spawn_fn calls child Exp.run())
Parallel tool dispatch --> Async tool execute (gather requires awaitable tools)
Sync-to-async bridge --> Async tool execute (session calls wrapped in to_thread)
Sequential tool mode --> Parallel tool dispatch (opt-out mechanism)
```

**Critical path (must be done in order):**
1. Async LLM provider (AsyncOpenAI) -- no other async component works without this
2. Async tool protocol + async kernel loop -- the core execution engine
3. asyncio.Queue MessageBus + async hooks -- event delivery
4. Async Exp lifecycle -- ties everything together
5. Async subagent spawn -- depends on async Exp.run()
6. Parallel tool dispatch -- optimization on top of working async kernel

## MVP Recommendation

### Phase 1: Core async infrastructure (must-have)

Prioritize these table-stakes features:

1. **Async LLM provider** -- Change `LLMProvider` Protocol to async methods. Implement `AsyncOpenAI` in `OpenAIProvider`. Change `chat_stream()` to return `AsyncIterator[StreamChunk]`. Replace `time.sleep()` with `await asyncio.sleep()` in retry logic.

2. **Async tool protocol + builtin tools** -- Change `Tool.execute()` to `async def execute()`. For BashTool and other session-dependent tools, wrap sync session calls in `asyncio.to_thread()`. Pure-compute tools (TaskTools, GlobTool) can just be `async def` with no real await.

3. **Async kernel execution loop** -- Change `AgentKernel.run()` to `async def`. `await` the LLM call, `await` each tool dispatch, `await` each hook. Replace `threading.Event` with `asyncio.Event` for stop_event.

4. **Async MessageBus + hooks** -- Replace `queue.Queue` with `asyncio.Queue`. Change all 7 Hook Protocol methods to `async def`. Update `EventEmitterHook` and `run_*` helper functions.

5. **Async context compactor** -- Change `compact_if_needed()` and `_summarize()` to async. The internal LLM call becomes `await self._summary_provider.chat()`.

6. **Async Exp lifecycle** -- Change `assemble()`, `build_runtime()`, `run()` to async. Cleanup callbacks support async callables.

### Phase 2: Differentiators (high-value optimization)

Defer these until Phase 1 is stable and tested:

7. **Parallel tool dispatch** -- When `response.tool_calls` has N > 1 items, use `asyncio.gather()` or `asyncio.TaskGroup` to execute concurrently. Add `sequential` flag to Tool Protocol for opt-out.

8. **Async subagent spawn** -- Change `spawn_fn` to `async def`. Child agent runs as coroutine in same event loop. Parent remains responsive.

9. **Per-tool timeout** -- Wrap tool execution in `asyncio.wait_for()`.

### Defer: Not in v2.0

- Async generator kernel return type (v3 consideration)
- Background compaction
- Distributed event bus
- Actor-model concurrency

## Patterns from Real Frameworks

### Pattern 1: Pydantic AI -- Async-First with Sync Wrapper

Pydantic AI's approach is the most instructive for matmaster:
- Everything internal is async (`async def run()`, async tools, async model calls)
- `run_sync()` is a thin wrapper: `loop.run_until_complete(self.run())`
- Tools: async functions run on event loop; sync functions auto-offloaded via threads
- Parallel tool execution is the DEFAULT, with opt-out to sequential
- Graph-based execution: UserPromptNode -> ModelRequestNode -> CallToolsNode -> loop

**Relevance:** matmaster should follow this pattern exactly. Make kernel fully async, provide sync entry point via `asyncio.run()` for DevShell/tests.

### Pattern 2: OpenAI Agents SDK -- Runner + Hooks + Streaming Events

- `Runner.run()` is async, `Runner.run_sync()` wraps it
- `Runner.run_streamed()` returns streaming events via async iteration
- RunHooks: 7 async lifecycle callbacks (on_agent_start, on_tool_start, on_llm_start, etc.)
- Sync function tools execute via `asyncio.to_thread()` to avoid blocking event loop
- max_turns limit with MaxTurnsExceeded exception

**Relevance:** The hook design is nearly identical to matmaster's 7-hook system. Direct mapping: pre_tool_call -> on_tool_start, post_tool_call -> on_tool_end, etc. All hooks are async.

### Pattern 3: Microsoft Agent Framework -- Async Middleware + Compaction

- Middleware pattern: `awrap_model_call()`, `awrap_tool_call()` as async wrappers
- CompactionStrategy is an async Protocol: `async def __call__(self, messages) -> bool`
- Pipeline compaction: chain strategies (tool_result -> summarization -> sliding_window -> truncation)
- Compaction as context provider, runs before each LLM call

**Relevance:** matmaster's ContextCompactor is already similar (summary + sliding_window fallback). The key insight: CompactionStrategy is async because summarization calls LLM. matmaster just needs to make `compact_if_needed()` async.

### Pattern 4: Thread-Safety Bridge (Janus Queue)

For the transition period where `src/` service layer (FastAPI) consumes events from a sync context while the kernel runs async:
- janus provides a dual-face queue: `sync_q` (put from thread) and `async_q` (get from coroutine)
- Alternative: use `asyncio.Queue` directly if both producer and consumer are in same event loop
- matmaster's current pattern: kernel in ThreadPoolExecutor emits to queue.Queue, SSEHandler consumes. After async conversion: kernel is coroutine, MessageBus is asyncio.Queue, SSEHandler awaits queue.get()

**Relevance:** If src/ layer (agent_run_service.py) still wraps kernel execution in `run_in_executor()`, janus bridges the gap. If kernel runs as coroutine in FastAPI's async handler, pure asyncio.Queue suffices.

## Complexity Assessment

| Feature | Lines Changed (est.) | Test Impact | Risk |
|---------|---------------------|-------------|------|
| Async LLM provider | ~100 (provider + protocol) | All provider tests need async | Low -- AsyncOpenAI is API-compatible |
| Async tool protocol | ~200 (12 tools + base + protocol) | All tool tests need async | Low -- most tools just add async def |
| Async kernel loop | ~150 (agent.py) | All kernel tests need async | Medium -- core control flow changes |
| Async MessageBus | ~30 (bus.py) | Bus tests need async | Low -- drop-in replacement |
| Async hooks | ~100 (hooks.py + protocol) | Hook tests need async | Medium -- 7 methods + run_* helpers |
| Async compactor | ~50 (context_compactor.py) | Compactor tests need async | Low -- one LLM call to async |
| Async Exp lifecycle | ~100 (exp.py) | Exp tests need async | Medium -- orchestration logic |
| Async cleanup | ~30 (exp.py) | Cleanup tests need async | Low |
| Async subagent spawn | ~50 (exp.py + spawn_tool.py) | Spawn tests need async | Medium -- async closure |
| Parallel tool dispatch | ~80 (agent.py) | New tests for parallel behavior | Medium -- ordering, error handling |

**Total estimated: ~900 lines of production code changes, plus test migration to async.**

## Sources

- Pydantic AI agent execution: https://ai.pydantic.dev/agent/
- Pydantic AI advanced tools (parallel execution): https://ai.pydantic.dev/tools-advanced/
- OpenAI Agents SDK running agents: https://openai.github.io/openai-agents-python/running_agents/
- OpenAI Agents SDK lifecycle hooks: https://openai.github.io/openai-agents-python/ref/lifecycle/
- OpenAI Agents SDK streaming: https://openai.github.io/openai-agents-python/streaming/
- OpenAI Python SDK async: https://github.com/openai/openai-python
- Microsoft Agent Framework compaction: https://learn.microsoft.com/en-us/agent-framework/agents/conversations/compaction
- Microsoft Agent Framework middleware: https://learn.microsoft.com/en-us/agent-framework/agents/middleware/
- Google ADK context compaction: https://google.github.io/adk-docs/context/compaction/
- Janus dual-face queue: https://github.com/aio-libs/janus
- Python asyncio subprocess: https://docs.python.org/3/library/asyncio-subprocess.html
- Python asyncio synchronization: https://docs.python.org/3/library/asyncio-sync.html
- PEP 525 Asynchronous Generators: https://peps.python.org/pep-0525/
