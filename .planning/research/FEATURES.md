# Feature Landscape: AgentKernel Generator-First 全链路改造

**Domain:** Generator-first agent kernel architecture -- streaming event production, tool execution delegation, and event bus retirement
**Researched:** 2026-04-02
**Scope:** How production agent frameworks (OpenAI Agents SDK, PydanticAI, LangGraph) implement generator-first execution loops, the run/run_stream interface split, tool runner delegation, and event bus vs generator-direct patterns. Applied to matmaster v2.2 milestone context.

## Table Stakes

Features that are fundamental to a correct generator-first kernel transformation. Without any of these, the architecture is broken or incomplete -- every framework that ships a generator-first model implements all of them.

| Feature | Why Expected | Complexity | Existing Equivalent | Notes |
|---------|--------------|------------|---------------------|-------|
| **Private inner generator (_run_items)** | Single source of truth for execution logic. Both run() and run_stream() must consume the same generator -- otherwise behavior diverges between batch and streaming callers. OpenAI Agents SDK and PydanticAI both use a single internal execution path consumed by multiple public interfaces | Medium | `_run_loop()` returning `KernelRunResult` directly | The core refactor. `_run_loop()` becomes `_run_items() -> AsyncIterator[_KernelItem]`. All termination paths (natural / max_turns / cancelled / hook_stopped) become `yield terminal + return` instead of `return self._finish()`. Every framework converges on this single-path-of-execution principle |
| **run_stream() public generator interface** | Generator-first public API. Every modern agent SDK provides a streaming execution method that yields typed events: OpenAI SDK has `Runner.run_streamed()`, PydanticAI has `Agent.run_stream()` / `run_stream_events()`, LangGraph has `astream()`. Without this, consumers cannot progressively consume execution events | Low | No equivalent -- events currently flow through Hook -> Bus -> EventRouter | Wraps `_run_items()`, filters to yield only `BusEvent` items (no internal messages_delta, no terminal internals). Last yield is always `RunResultEvent`. Signature: `async def run_stream(...) -> AsyncIterator[BusEvent]` |
| **run() backward-compatible wrapper** | Existing callers (Exp.run, DevRunner, AgentRunService) must keep working with zero changes. OpenAI SDK maintains `Runner.run()` alongside `Runner.run_streamed()`. PydanticAI maintains `Agent.run()` alongside `Agent.run_stream()`. Every framework preserves the batch interface | Low | Current `run()` returning `KernelRunResult` | Consumes `_run_items()`, collects `messages_delta` into transcript, extracts `terminal` into `KernelResult`. Signature and return type unchanged. All 50+ existing tests pass without modification |
| **Typed internal stream items** | Inner generator needs to carry three kinds of information simultaneously: events (for run_stream), message deltas (for run), and terminal state (for both). A single untyped yield would require downstream consumers to guess content type. PydanticAI uses distinct node types (UserPromptNode, ModelRequestNode, CallToolsNode). OpenAI SDK uses discriminated StreamEvent union | Low | No equivalent -- currently split across KernelRunResult fields and Hook callbacks | `_KernelItem` dataclass with optional fields: `event: BusEvent | None`, `messages_delta: list[Message] | None`, `terminal: _TerminalItem | None`. Private to agent.py, not public API |
| **ToolRunner Protocol extraction** | Tool execution logic must be separated from the kernel loop. OpenAI SDK delegates to tool execution handlers. PydanticAI has CallToolsNode as a distinct execution stage. LangGraph has explicit tool execution nodes. Inlining guard/execute/post_hook in the generator makes Phase 2 (ToolRunner replacement) require a second kernel refactor | Medium | Tool execution inline in `agent.py` L217-311 (guard -> pre_hook -> asyncio.gather -> post_hook -> append messages) | `ToolRunner` Protocol with `execute_batch()`. Phase 1 ships `InlineToolRunner` that wraps current logic behind the protocol interface. Kernel calls `tool_runner.execute_batch()` instead of inline code. Stateless/reentrant via explicit `ToolExecutionContext` parameter |
| **InlineToolRunner transition implementation** | Bridge between current inline tool logic and future Tool Runtime v2. Must preserve exact current behavior: serial guard evaluation, parallel execution of approved tools via `asyncio.gather`, post_hook only for actually-executed tools (not guard-denied or hook-skipped) | Medium | Same logic currently inline in `agent.py` | Wraps existing L217-311 logic. Three phases internally: (1) serial guard + pre_hook gating, (2) parallel execution of approved tools, (3) post_hook in original order. `on_result` callback for immediate event yield per completed tool |
| **Event reuse (no parallel event layer)** | Generator must yield the same event types already consumed by SSEHandler, PersistenceHandler, and integration tests. Creating parallel event types doubles migration surface for zero benefit. OpenAI SDK reuses its RunItemStreamEvent across both batch and streaming modes | Low | 18 BusEvent types in `events.py` (ThoughtEvent, ResponseEvent, ToolCallEvent, ToolResultEvent, RunResultEvent, etc.) | `_run_items()` yields `_KernelItem(event=ResponseEvent(...))`, `_KernelItem(event=ToolCallEvent(...))`, etc. Same Pydantic models, same discriminated union. No new event classes |
| **Dual-path coexistence (Phase 1)** | During transition, both the new generator yield path and the existing Hook -> Bus path must operate simultaneously. Removing Hook -> Bus before run_stream() has real consumers would break all event delivery. PydanticAI similarly maintained backward-compatible event delivery while introducing its graph-based streaming | Low | Hook -> EventEmitterHook -> MessageBus -> EventRouter -> handlers | Phase 1: _run_items() yields events AND hooks continue emitting to bus. Two paths produce semantically different events (generator: final snapshot; hooks: per-chunk streaming). No real consumer for run_stream() yet, so no duplication visible |
| **Per-invocation local state** | Generator state must be local to each `_run_items()` call, not stored on `self`. AgentKernel must remain stateless for concurrent safety (SubAgent spawn creates nested kernel calls). PydanticAI AgentRun is per-execution context. OpenAI SDK RunResultStreaming is per-run | Low | State currently managed as local variables in `_run_loop()` | `_KernelState` dataclass: messages list, turn counter, usage accumulator, stop_reason. Instantiated at start of `_run_items()`, not on `self`. Also pre-reserves `last_catalog_version` and `cached_tool_definitions` for Tool Runtime v2 |
| **AgentRuntimeSpec extension fields** | Spec must have slots for Tool Runtime v2 objects (tool_runner, tool_catalog, runtime_topology, capability_policy, structural_validation) so Phase 2 can inject them without Kernel code changes. All None in Phase 1 | Low | AgentRuntimeSpec has no placeholder fields | 5 new `Any | None` fields. `Any` because concrete types are defined in Tool Runtime v2, not yet created. Phase 2 replaces with precise type annotations |
| **Tool definitions resolution abstraction** | Kernel should not directly access `spec.tool_registry.get_tool_definitions()`. Abstracting this into a helper enables Phase 2 ToolCatalog injection without touching Kernel LLM call logic. Pattern from OpenAI SDK where tool definitions are resolved through a catalog interface | Low | Direct `spec.tool_registry.get_tool_definitions()` call in `_call_llm()` | `_resolve_tool_definitions(spec, state)` helper. Phase 1: falls back to tool_registry. Phase 2: checks spec.tool_catalog, uses version-based caching. Kernel's `_call_llm()` receives resolved defs as parameter |

## Differentiators

Features that go beyond the minimal generator-first conversion. Not strictly required for Phase 1, but each makes the architecture significantly better. Ordered by value for matmaster's specific context.

| Feature | Value Proposition | Complexity | Existing Equivalent | Notes |
|---------|-------------------|------------|---------------------|-------|
| **Exp.run_stream() generator passthrough** (Phase 2) | Exp layer exposes streaming to service layer. Without this, service layer cannot progressively consume kernel events -- it must wait for full run() completion. OpenAI SDK: Runner.run_streamed() passes through agent generator. PydanticAI: Agent.run_stream() wraps graph execution | Low-Med | Exp.run() calls kernel.run() and returns KernelRunResult | Exp.run_stream() wraps kernel.run_stream(), manages cleanup lifecycle (try/finally on generator exhaustion), delegates assembly to existing build_runtime(). Key complexity: ensuring _run_cleanup_callbacks() fires even if consumer abandons generator mid-stream |
| **AgentRunService.run_agent_stream()** (Phase 2) | Service layer consumes generator instead of setting up EventRouter + consumer task. First real consumer of the generator path. Directly enables streaming responses to API callers. OpenAI SDK: this is the primary interface (run_streamed). LangGraph: astream_events is the production API | Medium | AgentRunService.run_agent() sets up Bus + EventRouter + calls exp.run() | Consumes Exp.run_stream(), fans events to SSEHandler + PersistenceHandler inline (no bus/router). The critical migration step. Must handle: SSE first (latency sensitive), persistence can lag, workspace upload non-blocking |
| **_stream_llm_items() sub-generator** (Phase 2) | Full segment-complete streaming parity with EventEmitterHook. Phase 1 only yields final merged snapshots because _do_stream_llm() consumes segment boundaries internally. Sub-generator would yield per-chunk ThoughtEvent/ResponseEvent during streaming, matching EventEmitterHook's granularity. This enables full real-time UI streaming via run_stream() | High | `_do_stream_llm()` drives streaming internally, fires Hook callbacks for each chunk/segment | Requires restructuring _do_stream_llm()'s content_parts/reasoning_parts/tool_calls_acc accumulation logic into a generator. Each StreamChunk and segment boundary becomes a yield point instead of a hook callback. Most complex single feature in the milestone |
| **EventEmitterHook retirement** (Phase 2) | Remove the Hook -> Bus indirection for event delivery. Generator yield path becomes the sole event production mechanism. Eliminates a complete architectural layer (Hook -> Bus -> Router) from the critical path. Reduces latency: events flow directly from generator to consumer instead of through asyncio.Queue intermediary | Medium | EventEmitterHook bridges 4 hook points (pre_tool_call, post_tool_call, on_stream_chunk, on_segment_complete) to 4 BusEvent types | Prerequisite: _stream_llm_items() must achieve segment-complete parity. Then EventEmitterHook's on_stream_chunk and on_segment_complete are fully redundant with generator yields. pre_tool_call -> ToolCallEvent and post_tool_call -> ToolResultEvent are already yielded by _run_items() in Phase 1 |
| **AssistantStateHook retirement** (Phase 2) | Current hook emits AssistantStateEvent (full AssistantMessage with tool_calls) via Bus before each LLM call. chat_history.py depends on this for persistence. Generator alternative: _run_items() yields AssistantStateEvent when appending AssistantMessage with tool_calls | Low | AssistantStateHook.pre_llm_call() emits to Bus | Simple: yield _KernelItem(event=AssistantStateEvent(...)) at the same point where messages_delta includes an AssistantMessage with tool_calls. Exact same trigger condition |
| **SkillHitHook retirement** (Phase 2) | Emits SkillHitEvent when use_skill tool is invoked. Can be replaced by _run_items() yielding SkillHitEvent as part of tool result processing | Low | SkillHitHook.post_tool_call() checks tool_name == "use_skill" | Pattern: after tool execution, if tool_name == "use_skill", yield _KernelItem(event=SkillHitEvent(...)). Or move to InlineToolRunner/ToolRunner post-execute stage |
| **OutputProcessorHook migration** (Phase 2) | Currently inspects tool_name against patterns and emits ToolResultEvent with auto_save/summarize flags via Bus. More complex than other hooks because it adds business logic (pattern matching) to event emission | Medium | OutputProcessorHook.post_tool_call() matches patterns, emits flagged ToolResultEvent | Two migration options: (1) move pattern matching into ToolRunner post-execute stage (annotate ToolResult.info before yield), or (2) implement as event transformer in run_stream() consumer side. Option 1 is cleaner -- enrichment happens at the source |
| **ContextCompactor bus dependency removal** (Phase 2) | ContextCompactor currently emits ContextCompactionEvent to Bus (optional, `bus: MessageBus | None`). Generator alternative: _run_items() yields the event after compaction occurs | Low | `self._bus.emit(ContextCompactionEvent(...))` in compact_if_needed() | Compactor already handles `bus=None` gracefully. Change: pass a callback `on_compaction: Callable | None` instead of bus reference. _run_items() provides the callback that yields the event. Clean dependency inversion |
| **Async fanout for multi-consumer event dispatch** (Phase 3) | When Bus/Router are removed, run_stream() generator produces events. Multiple consumers (SSEHandler, PersistenceHandler, WorkspaceHandler) need them. Fanout distributes one generator's output to N consumers. Critical for: SSE first (latency), persistence can buffer, workspace non-blocking | Medium-High | EventRouter dispatches events from Bus to registered handlers via asyncio.Task | Options: (1) tee the generator into N async iterators, (2) inline fanout in service layer (await SSE, fire-and-forget persistence), (3) lightweight per-run asyncio.Queue per consumer. Option 2 is simplest but couples; option 3 preserves decoupling. LangGraph uses internal StreamManager with queue-based fanout |
| **MessageBus + EventRouter removal** (Phase 3) | Ultimate goal of the generator-first migration: eliminate the Bus/Router indirection entirely. Events flow directly from generator to consumers. Removes asyncio.Queue hop, consumer task management, drain timeout logic | Medium | MessageBus (asyncio.Queue wrapper) + EventRouter (asyncio.Task consumer + multi-handler dispatch) | Only possible after ALL Hook -> Bus paths are retired (Phase 2) AND async fanout replacement is in place. Must verify: no component still calls bus.emit() directly. DevShell path also needs migration (currently uses Bus for EventLogger) |
| **on_result callback in ToolRunner for immediate event yield** | Each tool result is yielded as an event immediately upon completion, before the entire batch finishes. Users see tool results appear one by one in real-time, not all at once after the batch. OpenAI SDK emits tool_output per tool, not per batch | Low | No equivalent -- current code waits for asyncio.gather then processes all results | InlineToolRunner.execute_batch() accepts `on_result: Callable` callback. Kernel _run_items() provides closure that appends _KernelItem(event=ToolResultEvent) to a pending list, yielded after execute_batch() returns. Phase 2 ToolRunner can stream results even more eagerly |
| **ToolExecutionContext explicit parameter** | All per-call state (turn, max_turns, stop_event) passes through a frozen dataclass, not via mutable side channels. Makes ToolRunner stateless/reentrant -- safe for SubAgent nested execution. PydanticAI uses RunContext for similar dependency injection | Low | Per-call state currently implicit in Kernel's local variables | `ToolExecutionContext(turn=..., max_turns=..., stop_event=...)`. Phase 2 extends with `runtime_topology`, `capability_policy`. ToolRunner Protocol signature stable across phases |

## Anti-Features

Features to explicitly NOT build in this milestone. Each represents a common mistake or premature optimization.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **New event types for streaming** | Creating StreamChunkEvent, SegmentCompleteEvent, GeneratorEvent etc. would duplicate existing events.py types and force all consumers to handle two parallel type hierarchies. Migration surface doubles for no benefit | Reuse existing events.py types (ThoughtEvent, ResponseEvent, ToolCallEvent, ToolResultEvent, RunResultEvent). Generator yields the same Pydantic models that Bus/Router already transport |
| **generator.aclose() for cancellation** | Tools execute via `asyncio.to_thread()` wrapping sync operations (BashTool subprocess, BuiltinTool.execute). `aclose()` cannot interrupt these. Would create partial-execution states (tool started but generator closed) with no cleanup guarantee | Keep `threading.Event` stop_event checked at loop boundaries in _run_items(). Tool Runtime v2's ToolBinding.stop_mode and SessionCapabilities.exec_cancel are the correct layer for graceful tool cancellation |
| **Phase 1 Hook removal** | Removing any Hook -> Bus path before run_stream() has real consumers would break ALL event delivery to SSE and persistence. Phase 1 has no run_stream() consumers | Dual-path coexistence: generator yields + Hook -> Bus both operate. Phase 2 switches consumers to generator, then retires hooks one by one |
| **Phase 1 Bus/Router removal** | Bus and Router are the only event delivery mechanism until Phase 2 wires service layer to run_stream(). Premature removal = no events to frontend or database | Keep Bus/Router fully operational. Phase 3 evaluates removal after all consumers migrate |
| **Segment-complete streaming in Phase 1** | `_do_stream_llm()` consumes segment boundaries internally via hook callbacks. Extracting them into generator yields requires restructuring the entire streaming accumulation logic. No consumer exists for run_stream() in Phase 1 | Phase 1: yield final merged snapshot (one ResponseEvent after _call_llm returns). Phase 2: convert _do_stream_llm() to _stream_llm_items() sub-generator for full streaming parity |
| **Changing Hook Protocol interface** | Adding/removing hook methods affects all Hook implementations (EventEmitterHook, DevStreamHook, ConfirmationHook, OutputProcessorHook, SkillHitHook, AssistantStateHook, ContextCompactor). Fragile during transition | Hook Protocol stays unchanged. Hooks are retired by removing their registration, not by changing the interface. BaseHook default no-ops make this safe |
| **Changing the cancellation mechanism** | Current Redis-driven `threading.Event` works across workers. Replacing with asyncio.Event or CancelledError would break the cross-worker stop signal. Tool Runtime v2's stop_mode is a different abstraction layer | Keep `threading.Event`. _run_items() checks it at the same points as current _run_loop(): each turn start, before tool batch, during stream chunks, during retry backoff |
| **Exposing _KernelItem as public API** | Internal stream items carry messages_delta and terminal metadata that should not leak to external consumers. Public API should only expose BusEvent (via run_stream) or KernelRunResult (via run) | _KernelItem, _KernelState, _TerminalItem are private dataclasses (underscore prefix). run_stream() filters to BusEvent only. run() extracts KernelRunResult only |
| **ToolRunner with internal stop_event management** | InlineToolRunner should not own cancellation logic. Cancellation is Kernel's responsibility -- it checks stop_event before calling execute_batch() and yields terminal if set. Mixing cancellation into ToolRunner creates two competing cancellation paths | InlineToolRunner.execute_batch() receives ToolExecutionContext which includes stop_event for Phase 2+ use, but Phase 1 InlineToolRunner ignores it. Kernel checks stop_event before invoking execute_batch() |
| **Type-precise AgentRuntimeSpec fields in Phase 1** | ToolCatalog, RuntimeTopology, CapabilityPolicy, StructuralValidation types do not exist yet (defined in Tool Runtime v2). Using precise types would create forward dependencies or force stub definitions | Use `Any | None` for all 5 new fields. Phase 2 replaces with concrete types when Tool Runtime v2 classes are implemented |

## Feature Dependencies

```
Phase 1 (Kernel layer, no external consumers affected):
  _KernelItem/_KernelState/_TerminalItem types
    -> _run_items() generator
      -> run() delegation (consumes _run_items)
      -> run_stream() public interface (consumes _run_items)

  ToolRunner Protocol definition
    -> InlineToolRunner implementation
      -> _run_items() delegates tool execution to ToolRunner

  AgentRuntimeSpec extension fields
    -> _resolve_tool_definitions() helper
      -> _run_items() uses helper for LLM tool defs

  LLM final completed snapshot events
    -> _run_items() yields ResponseEvent/ThoughtEvent after _call_llm()

Phase 2 (Exp + Service layer, Hook retirement):
  _stream_llm_items() sub-generator
    -> EventEmitterHook retirement (requires streaming parity)

  Exp.run_stream() passthrough
    -> AgentRunService.run_agent_stream()
      -> Async fanout to SSE + persistence consumers

  AssistantStateHook retirement (simple, independent)
  SkillHitHook retirement (simple, independent)
  OutputProcessorHook migration (medium, choice of location)
  ContextCompactor bus dependency removal (simple, independent)

Phase 3 (Bus removal, conditional):
  ALL Hook retirements complete
  + Async fanout replacement in place
    -> MessageBus removal
    -> EventRouter removal
```

## MVP Recommendation

Phase 1 is the MVP -- it establishes the generator-first foundation with zero external breakage.

Prioritize (in implementation order):

1. **ToolRunner Protocol + InlineToolRunner** -- independent of generator refactor, can be built and tested first
2. **AgentRuntimeSpec extension** -- simple field additions, unblocks _resolve_tool_definitions helper
3. **_KernelItem/_KernelState/_TerminalItem types** -- prerequisites for _run_items()
4. **_run_items() generator core** -- the central refactor, replaces _run_loop()
5. **run() delegation** -- must pass all 50+ existing kernel tests unchanged
6. **run_stream() public interface** -- new API, validated by new integration tests
7. **LLM final completed snapshot events** -- enriches run_stream() output
8. **Full regression suite** -- all existing tests green, new stream tests green

Defer to Phase 2:
- **_stream_llm_items() sub-generator**: highest complexity, no Phase 1 consumer. Needed for full streaming parity but not for the generator foundation
- **Hook retirement**: requires Phase 2 consumers. Cannot safely remove until run_stream() is wired through service layer
- **Exp.run_stream() and Service.run_agent_stream()**: Phase 2 scope per design spec

Defer to Phase 3:
- **MessageBus/EventRouter removal**: depends on all Phase 2 completions. May be kept if the operational overhead is acceptable

## Ecosystem Alignment

The three-interface design (_run_items / run_stream / run) aligns with industry patterns:

| Framework | Private execution | Public stream | Public batch | Tool delegation |
|-----------|------------------|---------------|--------------|-----------------|
| **OpenAI Agents SDK** | Internal loop producing StreamEvent | `Runner.run_streamed()` -> `AsyncIterator[StreamEvent]` | `Runner.run()` -> `RunResult` | Tool handlers invoked by runner |
| **PydanticAI** | Graph engine (UserPromptNode -> ModelRequestNode -> CallToolsNode) | `Agent.run_stream()` / `run_stream_events()` | `Agent.run()` -> `AgentRunResult` | CallToolsNode as distinct execution stage |
| **LangGraph** | Pregel engine executing graph nodes | `astream()` / `astream_events()` | `ainvoke()` | Tool nodes in graph, StreamManager for fanout |
| **matmaster v2.2** | `_run_items() -> AsyncIterator[_KernelItem]` | `run_stream() -> AsyncIterator[BusEvent]` | `run() -> KernelRunResult` | `ToolRunner.execute_batch()` delegation |

Key industry consensus:
- **Single execution path**: all frameworks route both batch and streaming through one internal engine. No framework has separate code paths for run() vs run_stream()
- **Event reuse**: no framework creates separate event types for streaming vs batch. Same event classes serve both paths
- **Tool delegation**: every framework separates tool execution from the main loop. None inline guard/execute/hook in the kernel
- **Progressive migration**: PydanticAI maintained backward-compatible run() throughout its streaming evolution. OpenAI SDK provides both run() and run_streamed() with identical semantics. matmaster follows the same pattern

## Sources

- [OpenAI Agents SDK Streaming](https://openai.github.io/openai-agents-python/streaming/) -- run_streamed() architecture, StreamEvent types
- [OpenAI Agents SDK Running Agents](https://openai.github.io/openai-agents-python/running_agents/) -- run() vs run_streamed() split
- [PydanticAI Agent API](https://ai.pydantic.dev/api/agent/) -- run() vs run_stream() vs run_stream_events()
- [PydanticAI Agent Lifecycle (DeepWiki)](https://deepwiki.com/pydantic/pydantic-ai/2.1-agent-run-lifecycle) -- graph node types, AgentRun execution model
- [AdalFlow: Agent Streaming Architecture Analysis](https://adalflow.sylph.ai/design/agent-streaming.html) -- OpenAI SDK internal event flow analysis
- [LangGraph Streaming Docs](https://docs.langchain.com/oss/python/langgraph/streaming) -- astream(), stream modes, StreamManager
- matmaster design spec: `docs/specs/2026-04-02-kernel-generator-first.md`
- matmaster Tool Runtime v2 spec: `docs/specs/2026-04-02-tool-runtime-v2.md`
