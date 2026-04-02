# Architecture Patterns: AgentKernel Generator-First 全链路改造

**Domain:** Agent framework internal architecture evolution (generator-first kernel + ToolRunner extraction + bus retirement)
**Researched:** 2026-04-02
**Confidence:** HIGH (based on complete codebase source analysis of all affected components)

## Current Architecture Snapshot

三层模型已全面 async 化 (v2.0)，matmaster/ 已完全独立于 evomaster (v2.1)。当前事件传递依赖 Hook 间接链路：

```
Playground.prepare() -> PlaygroundContext (frozen Pydantic)
    |
Exp.build_runtime(ctx, bus) -> AgentRuntime { kernel, spec, cleanup }
    |
AgentKernel.run(spec, task, history, stop_event) -> KernelRunResult
    +-- _run_loop(): while turn < max_turns
    |     +-- run_pre_llm_call(hooks)
    |     +-- run_should_continue(hooks)
    |     +-- compactor.compact_if_needed()
    |     +-- _call_llm() -> _do_stream_llm()
    |     |     +-- Hook.on_stream_chunk() --> EventEmitterHook --> Bus.emit()
    |     |     +-- Hook.on_segment_complete() --> EventEmitterHook --> Bus.emit()
    |     +-- guard_pipeline.evaluate() [sync]
    |     +-- run_pre_tool_call(hooks) --> EventEmitterHook --> Bus.emit(ToolCallEvent)
    |     +-- asyncio.gather(*tool_executions)
    |     +-- run_post_tool_call(hooks) --> EventEmitterHook --> Bus.emit(ToolResultEvent)
    +-- _finish() -> KernelRunResult { result: KernelResult, messages: list[Message] }

MessageBus (asyncio.Queue)
    |
EventRouter._consume_loop() (asyncio.Task, 100ms poll)
    +-- SSEHandler.handle()    -> send_cb -> frontend
    +-- PersistenceHandler.handle() -> events_table.add_event -> DB
```

### Current Event Path Problems

事件广播和消息转录是两条并行轨道，中间通过 EventEmitterHook 桥接：

1. **Kernel 内部**维护 `messages: list[Message]`，最终打包为 `KernelRunResult`
2. **EventEmitterHook**（7 个 hook 点中的 5 个被使用）翻译 hook 回调为 BusEvent，通过 MessageBus 送到 EventRouter
3. 两条轨道语义不同步：messages 包含完整对话历史，Bus 事件是增量流式推送

这意味着：
- 上层无法直接从 Kernel 获取结构化事件流
- 事件产出路径隐藏在 Hook 内部，不可组合
- 增加新事件类型需要同时修改 Hook 和 EventEmitterHook

## Recommended Architecture: Generator-First 三阶段改造

### Design Principle: 事件流成为主通路，而非 Hook 的副作用

Kernel 主循环变成 AsyncGenerator，直接 yield 事件。`run()` 和 `run_stream()` 都消费同一个 `_run_items()` generator，保证行为一致。工具执行链通过 ToolRunner Protocol 从 Kernel 解耦。

### Component Boundaries (Phase 1 完成态)

```
AgentKernel
  |
  +-- _run_items(spec, task, history, stop_event, source, spawn_id)
  |     AsyncGenerator[_KernelItem, None]
  |     +-- yield _KernelItem(event=ThoughtEvent/ResponseEvent)  [completed snapshot]
  |     +-- yield _KernelItem(event=ToolCallEvent)
  |     +-- yield _KernelItem(event=ToolResultEvent)             [via on_result callback]
  |     +-- yield _KernelItem(messages_delta=[...])              [message deltas]
  |     +-- yield _KernelItem(terminal=_TerminalItem)            [last yield]
  |
  +-- run_stream(spec, task, ...) -> AsyncIterator[BusEvent]      [new public interface]
  |     Filters _run_items() -> yields only event, ignores messages_delta
  |
  +-- run(spec, task, ...) -> KernelRunResult                     [compat interface, unchanged]
        Collects _run_items() -> accumulates messages_delta + terminal -> KernelRunResult

ToolRunner Protocol  [new]
  |
  +-- InlineToolRunner  [Phase 1 transition impl]
        Wraps current agent.py L217-311: guard -> pre_hook -> asyncio.gather -> post_hook

AgentRuntimeSpec  [extended]
  +-- 5 new Optional[Any] fields: tool_runner, tool_catalog, runtime_topology,
      capability_policy, structural_validation
```

### Full-Pipeline Data Flow (Three-Phase Evolution)

#### Phase 1: Kernel Layer (Pure Addition)

```
AgentRunService.run_agent()
  +-- Exp.build_runtime(ctx, bus) -> AgentRuntime
  +-- kernel.run(spec, task, ...) -> KernelRunResult        [unchanged]
  |     +-- internal: _run_items() generator
  |           +-- Hook -> Bus path still exists (EventEmitterHook untouched)
  |           +-- _run_items() also yields events (two paths coexist)
  |           +-- ToolRunner.execute_batch() delegates tool execution
  +-- bus.emit(run_result_event)                            [unchanged]

Consumers: no run_stream() consumers. run() behavior is fully equivalent to current.
Coexistence safety: two paths do not produce duplicate events because nobody consumes run_stream().
```

**Phase 1 Changed Files**:

| File | Change Type | Content |
|------|-------------|---------|
| `matmaster/core/agent.py` | Refactor | `_run_loop` -> `_run_items` generator; new `run_stream()`; `run()` delegates; tool execution delegates to ToolRunner |
| `matmaster/core/tool_runner.py` | New | ToolRunner Protocol + ToolExecutionContext + InlineToolRunner |
| `matmaster/types/runtime.py` | Extend | AgentRuntimeSpec adds 5 optional fields |
| `tests/matmaster/core/test_tool_runner.py` | New | InlineToolRunner unit tests |
| `tests/matmaster/core/test_agent_kernel_stream.py` | New | run_stream() integration tests |

**Unaffected Components** (Phase 1 zero changes):
- `matmaster/core/exp.py` -- Exp.run() continues calling kernel.run()
- `matmaster/devshell/runner.py` -- DevRunner continues calling kernel.run()
- `src/services/agent_run_service.py` -- continues calling kernel.run()
- `matmaster/core/bus.py` -- MessageBus retained
- `matmaster/integration/event_router.py` -- EventRouter retained
- `matmaster/core/hooks.py` -- Hook Protocol unchanged, EventEmitterHook retained
- `matmaster/hooks/*` -- all service-layer Hooks retained

#### Phase 2: Exp + Service Layer Consume Generator + Hook Retirement

```
AgentRunService.run_agent_stream()                         [new]
  +-- Exp.run_stream(ctx, task, ...)                       [new]
  |     +-- kernel.run_stream(spec, task, ...)
  |           +-- _run_items()
  |                 +-- _stream_llm_items() sub-generator    [replaces _do_stream_llm]
  |                 |     yield chunk-level ThoughtEvent/ResponseEvent
  |                 |     yield segment-complete events
  |                 +-- ToolRunner events (unchanged)
  |
  +-- async for event in Exp.run_stream():
        +-- SSEHandler.handle(event)                        [direct consume, no Bus]
        +-- PersistenceHandler.handle(event)                [direct consume, no Bus]

Hook retirement path (one by one):
1. EventEmitterHook.on_stream_chunk -> _stream_llm_items() yield replaces
2. EventEmitterHook.on_segment_complete -> _stream_llm_items() yield replaces
3. EventEmitterHook.pre_tool_call -> _run_items() yield ToolCallEvent replaces
4. EventEmitterHook.post_tool_call -> on_result callback yield replaces
5. EventEmitterHook deleted entirely

Hooks that remain:
- ConfirmationHook.pre_tool_call (intercepting, cannot be replaced by yield)
- OutputProcessorHook.post_tool_call (side-effect, needs business logic)
- SkillHitHook.post_tool_call (side-effect)
- AssistantStateHook.on_segment_complete (side-effect)
- should_continue (intercepting, control flow)
- pre_llm_call (observational, retained)
```

**Phase 2 Key Changes**:

| File | Change Type | Content |
|------|-------------|---------|
| `matmaster/core/agent.py` | Refactor | `_do_stream_llm()` -> `_stream_llm_items()` sub-generator |
| `matmaster/core/exp.py` | Extend | New `run_stream()` async generator |
| `src/services/agent_run_service.py` | Extend | New `run_agent_stream()` consuming generator |
| `matmaster/core/hooks.py` | Reduce | EventEmitterHook removed |
| `matmaster/hooks/*` | Migrate | Side-effect Hook event emission logic moves to service layer |

#### Phase 3: Bus Removal Assessment and Implementation

```
Pre-assessment: remaining Bus consumers after Phase 2
  +-- Service-layer events (ErrorEvent, CancelledEvent, StreamClosedEvent, ExpRunEvent)
  |     -> emitted by AgentRunService directly, not through Kernel
  +-- BohriumSetupService events (McpConnectEvent, BohriumNodeEvent)
  |     -> emitted by bohrium_svc outside Kernel
  +-- WorkspaceHandler events (WorkspaceUploadErrorEvent)
        -> emitted during cleanup phase

De-bus solution (if assessment passes):
  AgentRunService.run_agent_stream()
    +-- async for event in Exp.run_stream():    [Kernel events]
    |     +-- await fanout(event, handlers)      [replaces Bus+Router]
    |
    +-- service_events = [...ErrorEvent, etc]   [Service events]
    |     +-- await fanout(event, handlers)
    |
    +-- handlers = [SSEHandler, PersistenceHandler, WorkspaceHandler]

async def fanout(event: BusEvent, handlers: list[EventHandler]) -> None:
    for handler in handlers:
        await handler.handle(event)
```

**Phase 3 Conditional Changes** (depend on assessment conclusion):

| File | Change Type | Condition |
|------|-------------|-----------|
| `matmaster/core/bus.py` | Delete | If Bus has no remaining consumers |
| `matmaster/integration/event_router.py` | Delete/simplify | Replace with async fanout |
| `src/services/agent_run_service.py` | Refactor | Remove Bus/Router lifecycle management |
| `matmaster/devshell/runner.py` | Adapt | DevRunner if using bus=None path |

### Internal Types Design (Phase 1 New)

```python
# Kernel-private, not public -- all defined at top of agent.py

@dataclass
class _TerminalItem:
    """Kernel termination result, consumed only by run()"""
    status: str          # completed / cancelled / failed
    reason: str          # natural / max_turns / cancelled / hook_stopped / invalid_finish
    final_content: str | None
    num_turns: int
    stop_reason: str | None
    usage: dict[str, int]

@dataclass
class _KernelItem:
    """Kernel-private stream item"""
    event: BusEvent | None = None           # mappable to public event
    messages_delta: list[Message] | None = None  # messages append
    terminal: _TerminalItem | None = None   # termination item (last yield)

@dataclass
class _KernelState:
    """Per-_run_items() call local state"""
    messages: list[Message]
    turn: int = 0
    total_usage: dict[str, int] = field(default_factory=dict)
    last_stop_reason: str | None = None
    # Tool Runtime v2 reserved
    last_catalog_version: int | None = None
    cached_tool_definitions: list[dict[str, Any]] | None = None
```

Design points:
- `_KernelState` is a function-local variable, not on self, keeping Kernel stateless/concurrency-safe
- `_KernelItem` three fields are quasi-exclusive (event / messages_delta / terminal), uses dataclass rather than Union because event and messages may need to be yielded in the same batch
- `_TerminalItem` carries Kernel-private info (usage/stop_reason), not exposed to BusEvent

### ToolRunner Protocol Design

```python
# matmaster/core/tool_runner.py

@dataclass(frozen=True)
class ToolExecutionContext:
    """Per-batch execution context -- explicit, no side channels"""
    turn: int
    max_turns: int
    stop_event: threading.Event | None = None
    # Phase 2+: runtime_topology, capability_policy

@runtime_checkable
class ToolRunner(Protocol):
    """Tool execution chain interface -- Kernel only cares about (ToolCallData, ToolResult) result list"""
    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]: ...
```

Interface design points:
- **Explicit context**: all per-call state through ToolExecutionContext, ToolRunner is safe for concurrent/reuse (sub-agent scenarios)
- **Batch input**: receives entire batch of tool_calls, Phase 2+ can schedule by ResourceClaim
- **on_result callback**: notifies Kernel immediately as each tool completes to yield ToolResultEvent, does not wait for full batch
- **Original order return**: guarantees ToolMessage append order matches LLM request order
- **stop_event not checked in ToolRunner**: cancellation is Kernel _run_items() responsibility, checked before calling execute_batch()

### InlineToolRunner Implementation (Phase 1 Transition)

Logic equivalent to current `agent.py` L217-311 three phases:

1. **Serial guard + pre_hook gating**: per tool_call check guard deny and hook skip
2. **Parallel execution**: approved tool_calls use asyncio.gather for concurrent execution
3. **Post hooks**: post_tool_call hook on executed tools in original order

Key differences from current code:
- Does not maintain `_ToolOutcome` NamedTuple or construct `ToolMessage` -- that is Kernel `_run_items()` responsibility
- Returns only `(ToolCallData, ToolResult)` list
- `was_executed` flag distinguishes tools needing post_hook

## Patterns to Follow

### Pattern 1: Generator Consumption Isolation

`run()` and `run_stream()` each independently consume `_run_items()`, never sharing a generator instance. Each call creates a new generator.

```python
# Inside run()
async def run(self, spec, task, ...):
    async with spec.llm_provider:
        ...
        messages = []
        async for item in self._run_items(spec, task, ...):
            if item.messages_delta:
                messages.extend(item.messages_delta)
            if item.terminal:
                return KernelRunResult(
                    result=KernelResult(...from terminal...),
                    messages=messages,
                )

# Inside run_stream()
async def run_stream(self, spec, task, ...):
    async with spec.llm_provider:
        ...
        async for item in self._run_items(spec, task, ...):
            if item.event:
                yield item.event
            if item.terminal:
                yield RunResultEvent(...from terminal...)
```

**Key**: both methods share identical `spec/task/history/stop_event` parameters, since they pass to the same `_run_items()`.

### Pattern 2: ToolRunner Fallback Chain

Kernel internally decides which ToolRunner via spec fields:

```python
# _run_items() initialization
if spec.tool_runner is not None:
    tool_runner = spec.tool_runner           # Phase 2+: real ToolRunner injected by Exp
else:
    tool_runner = InlineToolRunner(           # Phase 1: inline transition
        tool_registry=spec.tool_registry,
        guard_pipeline=guard_pipeline,
        hooks=spec.hooks,
    )
```

Phase 2 only needs Exp to set `spec.tool_runner` in `build_runtime()`, Kernel code zero changes.

### Pattern 3: Tool Definitions Resolution Abstraction

```python
def _resolve_tool_definitions(spec, state) -> list[dict] | None:
    # Phase 2 path (after ToolCatalog injection)
    if spec.tool_catalog is not None:
        current_version = spec.tool_catalog.version
        if current_version != state.last_catalog_version:
            state.cached_tool_definitions = spec.tool_catalog.build_definitions()
            state.last_catalog_version = current_version
        return state.cached_tool_definitions
    # Phase 1 fallback
    if spec.tool_registry and hasattr(spec.tool_registry, 'get_tool_definitions'):
        return spec.tool_registry.get_tool_definitions()
    return None
```

Called before each LLM call. Phase 2 ToolCatalog injection auto-switches, no second Kernel change needed.

### Pattern 4: LLM Streaming Events Phased Strategy

| Phase | Event Emission Timing | Relationship to EventEmitterHook |
|-------|----------------------|----------------------------------|
| Phase 1 | Final completed snapshot after `_call_llm()` returns | Coexist: Hook sends streaming chunks, generator sends completed snapshot |
| Phase 2 | `_stream_llm_items()` sub-generator yields in real-time | Replace: EventEmitterHook deleted |

Phase 1's `_run_items()` cannot reproduce segment-complete semantics because `_do_stream_llm()` returns only concatenated content/reasoning_content. This is not a problem -- Phase 1 has no `run_stream()` consumers.

### Pattern 5: on_result Callback Pending Items Pattern

ToolRunner's on_result callback fires when a tool completes, but a generator cannot yield inside a callback. Solution is a pending items queue:

```python
# _run_items() tool execution section
pending_items: list[_KernelItem] = []

async def _on_tool_result(tc, result):
    pending_items.append(_KernelItem(event=ToolResultEvent(...)))

results = await tool_runner.execute_batch(response.tool_calls, exec_ctx, on_result=_on_tool_result)

# After execute_batch returns, yield all pending items
for item in pending_items:
    yield item
pending_items.clear()
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Generator Swallows Exceptions

**What:** `_run_items()` internally try/except swallows exceptions, attempts to yield error event then return.
**Why bad:** Kernel should let it fail. Exception handling is the upper layer's responsibility (Exp.run / AgentRunService). Generator internally handles only expected termination conditions (cancelled / max_turns / hook_stopped), does not catch LLMError and similar runtime exceptions.
**Instead:** `_run_items()` lets exceptions propagate naturally. `run()` and `run_stream()` each handle exceptions and cleanup.

### Anti-Pattern 2: Removing Any Hook in Phase 1

**What:** Delete EventEmitterHook in Phase 1 because _run_items() can also produce events.
**Why bad:** Phase 1 has no run_stream() consumers. All existing callers (AgentRunService / DevRunner / Exp.run) use run(), and run() does not consume item.event. Deleting EventEmitterHook means events no longer reach Bus -> SSEHandler -> frontend, users immediately lose streaming output.
**Instead:** Phase 1 two paths coexist. Phase 2 switches service layer to run_stream(), then gradually retire Hooks.

### Anti-Pattern 3: _KernelItem as Union Type Instead of Dataclass

**What:** Design _KernelItem as `event | messages_delta | terminal` Union type.
**Why bad:** Tool execution section may need to yield both event (ToolResultEvent) and messages_delta (ToolMessage append) simultaneously. Union type cannot carry two kinds of info in one yield. Even splitting into multiple yields adds yield points and complexity.
**Instead:** Dataclass with three optional fields, each yield fills as needed.

### Anti-Pattern 4: InlineToolRunner Checks stop_event Internally

**What:** Check stop_event inside InlineToolRunner.execute_batch() and return early.
**Why bad:** Current agent.py L222-229 cancellation behavior: detect stop_event -> directly return KernelRunResult (cancelled), does not produce ToolResult/ToolMessage for unexecuted tools. If ToolRunner checks internally, it needs to return partial results, and Kernel needs to handle partial cases, adding complexity.
**Instead:** Cancellation check is Kernel _run_items() responsibility, checks before calling execute_batch() and directly yields terminal.

### Anti-Pattern 5: run_stream() Implemented by Consuming run()

**What:** `run_stream()` internally calls `run()`, then reverse-engineers events from KernelRunResult.
**Why bad:** By the time run() returns, all events have already passed, streaming timing cannot be recovered. The value of run_stream() is precisely the real-time event stream.
**Instead:** Both `run()` and `run_stream()` consume `_run_items()`. _run_items() is the single execution path.

## Integration Points

### Point 1: Kernel Internal -- _run_items() is the Single Execution Path

```
run()          --+
                 +--> _run_items()   <-- single execution path
run_stream()   --+
```

Behavioral equivalence guarantee: all existing run() tests (50+) need no modification.

### Point 2: Kernel -> ToolRunner -- Delegation Replaces Inline

```
Current: Kernel._run_loop() directly executes guard -> gather -> append (L217-311)
After:   Kernel._run_items() -> ToolRunner.execute_batch(tool_calls, ctx, on_result=...)
```

InlineToolRunner wraps fully equivalent logic. Kernel no longer cares about guard/hook details.

### Point 3: Kernel -> LLM -- Tool Definitions Resolution Abstraction

```
Current: _call_llm() directly calls spec.tool_registry.get_tool_definitions()
After:   _call_llm() calls _resolve_tool_definitions(spec, state)
         Phase 1: falls back to tool_registry
         Phase 2: prefers tool_catalog.build_definitions() + version cache
```

### Point 4: Exp -> Kernel -- Phase 2 Adds run_stream() Path

```
Current:  Exp.run() -> kernel.run() -> KernelRunResult -> result.result
Phase 2:  Exp.run_stream() -> kernel.run_stream() -> async for event
```

Exp.run() retained unchanged, Exp.run_stream() is a new method.

### Point 5: Service -> Exp -- Phase 2 Adds run_agent_stream()

```
Current:  AgentRunService.run_agent() -> Exp.run() / kernel.run()
Phase 2:  AgentRunService.run_agent_stream() -> Exp.run_stream()
          async for event:
            SSEHandler.handle(event)
            PersistenceHandler.handle(event)
```

run_agent() retained unchanged, run_agent_stream() is a new method.

### Point 6: DevRunner -- Phase 2+ Adaptation

```
Current:  DevRunner.run() -> asyncio.run(kernel.run()) -> KernelRunResult
Phase 2 optional: DevRunner.run_stream() -> kernel.run_stream()
                  DevStreamHook can directly consume generator events
```

DevRunner is not a Phase 2 required change. Existing DevStreamHook works through Hook path, Phase 2 can choose to retain or migrate.

### Point 7: SubAgent Spawn -- Phase 1 Compatible

```
Exp._make_spawn_fn() internally calls child_exp.run() -> kernel.run()
spawn_fn returns result.final_content

Phase 1: unchanged. spawn_fn continues using run().
Phase 2+: can switch to run_stream() for real-time sub-agent event forwarding.
```

## Build Order (Recommended Sequence)

Suggested implementation order based on dependency analysis:

### Phase 1 Internal Order

```
Chunk 1: ToolRunner Protocol + InlineToolRunner        [no deps, can start first]
    |
Chunk 2: AgentRuntimeSpec extension                    [no deps, can parallel with Chunk 1]
    |
Chunk 3: _KernelItem/_KernelState/_TerminalItem types  [depends on design, not on Chunk 1-2]
    |
Chunk 4: _run_items() generator rewrite                [depends on Chunk 1+2+3]
    |
Chunk 5: run_stream() public interface                 [depends on Chunk 4]
    |
Chunk 6: run() delegates to _run_items()               [depends on Chunk 4]
    |
Verify: all existing tests pass + new tests pass
```

Key constraints:
- Chunk 1 and Chunk 2 have no mutual dependency, can be parallelized
- Chunk 4 is the largest single change (_run_loop -> _run_items), depends on all three prior Chunks
- Chunk 6 (run() delegation) must complete before running existing tests for compatibility verification

### Phase 2 Internal Order

```
Step 1: _stream_llm_items() sub-generator               [modify agent.py]
    |
Step 2: Exp.run_stream() new method                     [modify exp.py]
    |
Step 3: AgentRunService.run_agent_stream()               [modify agent_run_service.py]
    |
Step 4: Side-effect Hook event emission migration        [modify hooks/]
    |
Step 5: EventEmitterHook deletion                        [modify hooks.py]
    |
Step 6: Switch old run_agent() to run_agent_stream()     [modify agent_run_service.py]
```

### Phase 3 (Conditional)

```
Assess remaining Bus consumers
  +-- Only Service-layer direct emit remains -> can de-bus
  |     Step 1: async fanout replaces Bus+Router
  |     Step 2: delete bus.py + event_router.py
  |     Step 3: clean up bus parameter passing in Exp
  |
  +-- Non-Kernel events still need Bus -> retain simplified Bus
        Step 1: simplify Bus to service-layer only
        Step 2: retain Router but remove Kernel-related logic
```

## Scalability Considerations

| Concern | Phase 1 | Phase 2 | Phase 3 |
|---------|---------|---------|---------|
| Event latency | Unchanged (two paths coexist) | Reduced (generator direct consume, no Queue 100ms poll overhead) | Optimal (zero middleware) |
| Memory | Slight increase (_KernelState local var) | Decrease (delete EventEmitterHook + some Hooks) | Decrease (delete Bus + Router) |
| Code complexity | Increase (two paths coexist) | Decrease (unified path) | Simplest |
| Test surface | Increase (new ToolRunner + stream tests) | Net decrease (delete Hook mock tests) | Net decrease |
| SubAgent event isolation | Unchanged (spawn_id already in place) | Improved (generator chain enables precise forwarding) | Same as Phase 2 |

## Sources

- Complete source analysis of: agent.py, exp.py, bus.py, event_router.py, hooks.py, runtime.py, events.py, agent_run_service.py, runner.py
- Design spec: `docs/specs/2026-04-02-kernel-generator-first.md`
- Tool Runtime v2 spec: `docs/specs/2026-04-02-tool-runtime-v2.md`
- Python asyncio AsyncGenerator docs: https://docs.python.org/3/library/collections.abc.html#collections.abc.AsyncGenerator
- PEP 525 -- Asynchronous Generators: https://peps.python.org/pep-0525/
