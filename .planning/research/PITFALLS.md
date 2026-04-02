# Domain Pitfalls: AgentKernel Generator-First 全链路改造

**Domain:** Python AsyncGenerator refactoring of agent execution kernel (brownfield, 1,294 passing tests)
**Researched:** 2026-04-02
**Overall Confidence:** HIGH (codebase analysis + Python official docs + PEP 525/789 + community patterns)

---

## Critical Pitfalls

Mistakes that cause silent behavior changes, test regressions, or architectural dead ends in a system with 1,294 passing tests.

---

### Pitfall 1: ContextCompactor In-Place Mutation vs. Generator messages_delta

**What goes wrong:** ContextCompactor.compact_if_needed() modifies the messages list IN PLACE via `messages[:] = [system_msg, compact_msg, ...]` (context_compactor.py L228, L236). In the generator architecture, `_run_items()` maintains a local `_KernelState.messages` and yields `messages_delta` for `run()` to collect incrementally. If the compactor replaces the backing list while `run()` is accumulating deltas into a SEPARATE list, the two lists diverge. The kernel proceeds with a compacted list; `run()` still holds pre-compaction messages plus new deltas. The resulting `KernelRunResult.messages` will be wrong -- containing both the original (now-compressed) messages AND the new ones.

**Why it happens:** The current `_run_loop()` uses a single `messages` list that both the kernel and compactor share. The generator architecture introduces a split: `_KernelState.messages` (the kernel's working copy) and the caller's accumulated-from-deltas copy.

**Consequences:**
- `run()` returns messages that don't match what the LLM actually saw, breaking conversation history replay
- CompactionEvent metadata (retained_turns, trigger_tokens) won't match the actual messages returned
- Test failures in compaction-dependent tests (test_context_compactor.py, test_compaction_real_api.py, test_compaction_via_devshell.py)

**Prevention:**
- `_run_items()` must yield a "compaction reset" delta type that tells `run()` to replace its accumulated messages, not just extend. Add a `messages_reset: list[Message] | None` field to `_KernelItem` alongside `messages_delta`
- OR: `run()` must share the same list reference as `_run_items()` -- collect from `_KernelState.messages` directly in the terminal item rather than accumulating deltas
- The second approach is simpler and matches the spec's `_TerminalItem` design: the terminal can carry the final messages snapshot

**Detection:** Run `pytest tests/matmaster/core/test_context_compactor.py tests/matmaster/integration/test_compaction_real_api.py -v` after refactoring. If messages diverge, compaction tests will fail because `KernelRunResult.messages` won't contain the `[Compacted Context]` SystemMessage.

**Phase:** Phase 1 (must be solved during `_run_items()` implementation, not deferred)

**Confidence:** HIGH -- verified from codebase: agent.py L131/164/181 + context_compactor.py L228/236

---

### Pitfall 2: AsyncGenerator aclose() Cannot Cancel In-Flight Tool Execution

**What goes wrong:** When a consumer of `run_stream()` decides to stop iteration early (e.g., client disconnect), Python calls `aclose()` on the async generator, which throws `GeneratorExit` at the current suspension point. If `_run_items()` is awaiting `tool_runner.execute_batch()` -- which internally uses `asyncio.gather()` and may wrap sync tool code via `asyncio.to_thread()` -- the `GeneratorExit` cannot meaningfully cancel those in-flight operations. The gather tasks continue running in the background, but the generator is dead. Resources leak, tools run to completion with nobody consuming their results.

**Why it happens:** Python's GeneratorExit is a synchronous exception mechanism transplanted into async context. `asyncio.gather()` tasks are not linked to the generator's lifetime. `asyncio.to_thread()` wrappers for sync subprocess calls (BashTool) cannot be interrupted by GeneratorExit at all -- they run in a thread pool executor.

**Consequences:**
- Tools (especially BashTool running shell commands) continue executing after the generator is closed
- Resource cleanup callbacks registered via `Exp._register_cleanup()` may not fire if `run_stream()` is abandoned mid-execution
- In SubAgent scenarios, child agent spawns continue running after parent abandonment

**Prevention:**
- Do NOT rely on `aclose()` for cancellation. The spec correctly identifies this (section 4.9): "不使用 generator.aclose()"
- Use the existing `threading.Event` (stop_event) mechanism for cancellation. When the consumer wants to stop, set the stop_event. `_run_items()` checks it at each turn boundary and between tool batches
- Document that `run_stream()` consumers must use stop_event for graceful cancellation, not `break` from `async for`
- If consumer breaks early, that's fine for the consumer side, but the generator's background state must still be cleaned up. Wrap `run_stream()` consumption in a pattern where stop_event is always set on exit

**Detection:** Write a test that breaks out of `async for event in kernel.run_stream(...)` mid-tool-execution, then verifies that pending gather tasks completed and resources were cleaned up.

**Phase:** Phase 1 (must be addressed in `run_stream()` design, documented in API contract)

**Confidence:** HIGH -- PEP 525 explicitly describes this limitation; the spec already accounts for it

---

### Pitfall 3: Dual Event Paths Producing Duplicate Events During Transition

**What goes wrong:** During Phase 1, both `_run_items()` yield (new path) and Hook -> Bus (old path) coexist. If a Phase 2 consumer starts using `run_stream()` before all Hook->Bus emission is removed, it receives events from BOTH paths: once from the generator yield, once from EventEmitterHook -> MessageBus -> EventRouter. For `ToolCallEvent` and `ToolResultEvent`, this means downstream consumers (PersistenceHandler, SSEHandler) process the same event twice -- duplicate database records, duplicate SSE pushes to the frontend.

**Why it happens:** The spec acknowledges this (section 4.8) and states "Phase 1 没有消费者使用 run_stream()" -- but the window between Phase 1 and Phase 2 (when service layer switches to run_stream()) is the danger zone. If ANY consumer starts consuming run_stream() events before the Hook->Bus path is fully retired, duplicates appear.

**Consequences:**
- Duplicate ToolResultEvent entries in persistence (database bloat, confusing history replay)
- Duplicate SSE pushes to frontend (visual artifacts, state confusion)
- ToolCallEvent->ToolResultEvent ordering may differ between the two paths (generator yields in tool_calls order; Hook fires in asyncio.gather completion order)

**Prevention:**
- Strict ordering: Phase 2 MUST retire EventEmitterHook BEFORE any service-layer consumer switches to run_stream(). Never have both active simultaneously for the same consumer
- Add a `_emit_to_hooks: bool` flag to `_run_items()` that Phase 1 sets to True (keep Hook calls) and Phase 2 sets to False (skip Hook calls). This is a single control point for the transition
- Integration test: assert that the total event count from run_stream() matches expectations. If duplicates appear, the count will be 2x expected

**Detection:** Count events per type in integration tests. If `ToolResultEvent` count is 2x the number of tool_calls, the dual-path is leaking.

**Phase:** Phase 2 boundary (the handoff moment between Phase 1 and Phase 2)

**Confidence:** HIGH -- architectural analysis of the two paths

---

### Pitfall 4: InlineToolRunner Losing Stop-Event Cancellation Semantics

**What goes wrong:** In the current agent.py (L222-229), the stop_event check is INSIDE the tool_calls serial loop -- between each tool_call's guard evaluation. This means if the user cancels mid-batch, remaining tools are skipped and the kernel immediately returns `cancelled`. When extracting to InlineToolRunner, this per-tool-call cancellation check can be lost. The spec explicitly states InlineToolRunner does NOT check stop_event (section 4.9), and Kernel checks BEFORE calling execute_batch(). But this changes the granularity: the current code checks between each tool_call within a batch; the new code only checks before the entire batch starts.

**Why it happens:** The extraction boundary is drawn at the batch level (execute_batch takes ALL tool_calls at once), but the current code checks at the individual tool_call level within the batch.

**Consequences:**
- If the LLM returns 5 tool_calls and the user cancels after tool_call 2 is guard-evaluated, the current code skips tools 3-5. With InlineToolRunner, ALL 5 tools may execute because the stop_event was only checked before the batch started
- Long-running tool batches (e.g., 5 bash commands) become non-cancellable during execution
- Degraded user experience: "cancel" feels unresponsive

**Prevention:**
- Accept the granularity change IF tools are truly independent (they are -- asyncio.gather runs them in parallel, so there's no sequential point to check between them)
- BUT: the guard evaluation loop in InlineToolRunner IS sequential (L299-323 in the spec). Add stop_event checking in `ToolExecutionContext` and check it between guard evaluations in InlineToolRunner, even if the spec says not to. The cost is minimal (checking a threading.Event is sub-microsecond)
- Alternative: keep the spec's design (no stop_event in InlineToolRunner) but document that cancellation responsiveness changes from per-tool to per-batch. If this is unacceptable, add it back

**Detection:** Test: create 5 tool_calls, set stop_event after 1 second, verify that not all 5 are executed. Compare behavior before/after refactoring.

**Phase:** Phase 1 (InlineToolRunner implementation)

**Confidence:** HIGH -- direct code comparison agent.py L222-229 vs spec section 4.9

---

### Pitfall 5: Generator State Corruption on Concurrent run() Calls

**What goes wrong:** AgentKernel is currently stateless -- no instance attributes are set during `run()`. The generator architecture maintains this (using local `_KernelState` per `_run_items()` invocation). But if a developer accidentally stores generator-related state on `self` (e.g., a "current generator" reference, or debug counters), concurrent calls from SubAgent spawns will corrupt shared state. SubAgent spawns use the same AgentKernel class (Exp creates `AgentKernel()` per run, but a future optimization might reuse instances).

**Why it happens:** Generator-based designs invite storing "current iteration state" at the class level for debugging or observation. Any such attribute breaks reentrancy.

**Consequences:**
- Intermittent failures in SubAgent scenarios where parent and child share a kernel instance
- Race conditions that only manifest under concurrent load, never in unit tests

**Prevention:**
- Enforce the invariant: AgentKernel has NO instance attributes. `_run_items()` is a pure generator using only local `_KernelState`
- Add a test that runs two `_run_items()` generators concurrently on the same AgentKernel instance and verifies they produce independent results
- Code review gate: any PR that adds `self._xxx` to AgentKernel should be flagged

**Detection:** `assert not vars(kernel)` after construction -- AgentKernel.__init__ should set nothing.

**Phase:** Phase 1 (architectural invariant, test from the start)

**Confidence:** HIGH -- the spec explicitly calls this out in section 4.2

---

## Moderate Pitfalls

---

### Pitfall 6: OutputProcessorHook Emitting Duplicate ToolResultEvent

**What goes wrong:** OutputProcessorHook (output_processor.py) emits its own `ToolResultEvent` with `auto_save: True` or `summarize: True` info flags. This is a SECOND `ToolResultEvent` for the same tool_call -- the first one comes from EventEmitterHook. In the generator architecture, `_run_items()` also yields a `ToolResultEvent` via `on_result` callback. When OutputProcessorHook is not removed during Hook retirement (Phase 2), the same tool_call produces THREE ToolResultEvents: one from generator yield, one from EventEmitterHook, one from OutputProcessorHook.

**What makes this subtle:** OutputProcessorHook's event has DIFFERENT info flags (auto_save/summarize) than the standard ToolResultEvent. It's not a pure duplicate -- it carries business metadata. Naive deduplication by call_id would suppress the business metadata.

**Prevention:**
- When retiring EventEmitterHook (Phase 2), also retire OutputProcessorHook. The auto_save/summarize metadata must be moved to a different mechanism: either as fields on the standard ToolResultEvent yielded by the generator, or as a separate post-processing step in the service layer
- Do not assume "retire all Hooks" is a single atomic step. Each Hook has unique side-effects that need replacement mechanisms

**Phase:** Phase 2 (Hook retirement)

**Confidence:** HIGH -- direct code analysis of output_processor.py

---

### Pitfall 7: ConfirmationHook Blocking the Generator

**What goes wrong:** ConfirmationHook (confirmation.py) is an intercepting hook that BLOCKS execution by awaiting `get_reply()` with a timeout (up to 20 seconds). In the current architecture, the LLM call and tool execution are sequential within `_run_loop()`, so blocking is fine. In the generator architecture, `_run_items()` is an AsyncGenerator. When ConfirmationHook blocks inside `pre_tool_call()`, the generator is suspended at that yield point. This is correct behavior, but:
1. Any `run_stream()` consumer waiting for the next event will see a multi-second gap with no yields
2. If the consumer has its own timeout (e.g., SSE keepalive), it may think the stream is dead

**Why it happens:** The generator's yield cadence is determined by internal execution speed. A 20-second confirmation timeout creates a 20-second gap between events.

**Prevention:**
- When ConfirmationHook moves to a generator-aware design, it should yield a `ConfirmationRequestEvent` THROUGH the generator (not through the Bus), then suspend
- Before that transition, document that `run_stream()` consumers must handle event gaps gracefully (SSE keepalive pings, frontend loading indicators)
- ConfirmationHook is one of the LAST hooks to retire because it has complex bidirectional flow (emit request -> wait for reply). Plan its migration separately

**Phase:** Phase 2 (late-stage Hook retirement)

**Confidence:** MEDIUM -- the blocking behavior is verified, but the exact impact on run_stream() consumers depends on their timeout configuration

---

### Pitfall 8: AssistantStateHook Side-Effect on messages List

**What goes wrong:** AssistantStateHook (assistant_state.py L35) reads `messages[-1]` to find the last AssistantMessage. In the generator architecture, the `messages` list passed to `pre_llm_call` hooks is `_KernelState.messages`, which is the kernel's working copy. If `run()` accumulates a separate copy via `messages_delta`, the AssistantStateHook is reading from the correct list (the kernel's). But when this hook is eventually retired and replaced with a generator yield, the service layer must reconstruct the "last assistant state" from the event stream -- which is harder than reading the last message from a list.

**Prevention:**
- Before retiring AssistantStateHook, ensure the generator yields enough information for the service layer to reconstruct assistant state. The `messages_delta` in `_KernelItem` provides the raw AssistantMessage, so this is feasible
- Test: verify that the service layer can produce identical AssistantStateEvent content from generator events alone

**Phase:** Phase 2 (Hook retirement)

**Confidence:** MEDIUM -- the issue is architectural, not a bug risk

---

### Pitfall 9: asyncio.gather return_exceptions Behavior Inside Generator

**What goes wrong:** InlineToolRunner uses `asyncio.gather(*tasks, return_exceptions=True)` for parallel tool execution. When called inside an AsyncGenerator that receives `aclose()` (GeneratorExit), the gather() call may be in progress. GeneratorExit is not an Exception (it's BaseException), so `return_exceptions=True` does NOT catch it. The gather tasks are cancelled, but their cancellation may be incomplete (especially for `asyncio.to_thread()` wrappers).

Additionally, there's a known CPython issue (cpython#112572): `asyncio.gather` with generator expressions and `return_exceptions=True` can behave unexpectedly. While InlineToolRunner uses a list comprehension (not a generator expression), this is worth noting.

**Why it happens:** `return_exceptions=True` catches `Exception` subclasses, not `BaseException` subclasses. `GeneratorExit` and `CancelledError` (which is `BaseException` since Python 3.9) bypass this catch.

**Prevention:**
- Wrap the `asyncio.gather()` call in a try/except that handles `BaseException`, ensuring partial results are returned even on cancellation
- The `_run_items()` generator should handle GeneratorExit in its cleanup path (not by yielding, which is forbidden, but by logging and allowing cleanup to proceed)
- Test: verify that when GeneratorExit occurs during gather(), no unhandled exceptions propagate and no tasks leak

**Phase:** Phase 1 (InlineToolRunner implementation)

**Confidence:** MEDIUM -- PEP 525 and CPython issue tracker confirm the GeneratorExit/BaseException distinction, but the exact interaction depends on timing

---

### Pitfall 10: _do_stream_llm() Hook Calls Becoming Unreachable in Phase 2

**What goes wrong:** The spec plans to convert `_do_stream_llm()` into a sub-generator `_stream_llm_items()` in Phase 2 (section 4.7). The current `_do_stream_llm()` has carefully ordered hook calls: `on_stream_chunk()` for each chunk, `on_segment_complete()` at segment boundaries, and `start`/`end` lifecycle markers. When converting to a generator, each of these hook calls must become either a yield or a local call. If a hook call is accidentally omitted during conversion, the corresponding event type silently disappears from the stream.

**Why it happens:** `_do_stream_llm()` is 130+ lines (agent.py L482-643) with interleaved segment tracking state (`producing_reasoning`, `producing_content`) and hook calls in `try/finally` blocks. Converting this to yield-based emission while preserving EVERY hook call point requires careful mapping.

**Prevention:**
- Before converting, create a test that captures EVERY event type emitted by `_do_stream_llm()` through hooks, including segment-complete events for thought/response transitions
- Map each hook call in the current code to the corresponding yield in the new generator. Use a checklist:
  - `run_on_stream_chunk(start)` -> yield StreamStartEvent
  - `run_on_stream_chunk(streaming)` per chunk -> yield chunk events
  - `run_on_segment_complete('thought', ...)` -> yield ThoughtEvent(complete)
  - `run_on_segment_complete('response', ...)` -> yield ResponseEvent(complete)
  - `run_on_stream_chunk(end)` -> yield StreamEndEvent
- The `finally` block in `_do_stream_llm()` (L588-606) handles edge cases where stream ends mid-segment. The generator's cleanup path must replicate this

**Phase:** Phase 2 (sub-generator conversion)

**Confidence:** HIGH -- code analysis of agent.py L482-643

---

### Pitfall 11: ContextCompactor's MessageBus Dependency

**What goes wrong:** ContextCompactor holds a direct reference to MessageBus and emits `ContextCompactionEvent` (context_compactor.py L206-217, L252-263). When the Bus is removed (Phase 3), ContextCompactor's emission path breaks. If the compactor was not refactored to use the generator's yield path, compaction events silently disappear.

**Prevention:**
- Phase 3 must audit ALL direct Bus references, not just Hook-based ones. The compactor is an easily-missed case because it's not a Hook -- it's a standalone component that happens to hold a Bus reference
- Refactor: ContextCompactor should yield/return a `ContextCompactionEvent` from `compact_if_needed()` rather than emitting directly. The caller (`_run_items()`) can then yield it through the generator
- List of components with direct Bus references to audit: ContextCompactor, ConfirmationHook, OutputProcessorHook, SkillHitHook, AssistantStateHook, Exp._make_spawn_fn

**Phase:** Phase 3 (Bus removal)

**Confidence:** HIGH -- grep confirmed `self._bus.emit()` in context_compactor.py

---

### Pitfall 12: threading.Event is_set() Check Timing in Generator Turn Boundary

**What goes wrong:** In the current code, `stop_event.is_set()` is checked at the TOP of each while-loop iteration (agent.py L138). In the generator, the same check happens at the same logical point, but now the generator may be suspended between yields. If the consumer takes a long time between `__anext__()` calls (e.g., slow persistence handler processing the previous event), the stop_event could be set during this gap, but the generator won't see it until the consumer requests the next item.

This is different from the current architecture where the kernel runs without pausing between turns (it only pauses for awaits, not for consumer pull).

**Why it happens:** Pull-based (generator) vs push-based (current loop) execution models have different timing characteristics. A push-based loop checks stop_event as fast as it can loop; a pull-based generator only checks when the consumer asks for the next item.

**Prevention:**
- For `run()` (which eagerly consumes the generator), this is a non-issue -- it pulls items as fast as the generator yields them
- For `run_stream()` consumers that process events slowly, add documentation that stop_event responsiveness depends on consumer pull speed
- The stream chunk checking (every 8 chunks, agent.py L54) remains unaffected because it's within the `_do_stream_llm()` await chain, not at a yield point

**Phase:** Phase 1 (awareness, documentation)

**Confidence:** MEDIUM -- the timing change is real but may not matter in practice if consumers are fast

---

## Minor Pitfalls

---

### Pitfall 13: _KernelItem Dataclass Field Ordering

**What goes wrong:** `_KernelItem` has three optional fields (event, messages_delta, terminal). If a developer constructs an item with positional arguments instead of keyword arguments, they can accidentally assign an event to messages_delta or vice versa. This is a classic dataclass pitfall with multiple optional fields of compatible types.

**Prevention:** Use keyword-only arguments: `@dataclass` with `__init__` that requires keyword arguments, or simply enforce keyword-only in code review. All internal construction sites should use `_KernelItem(event=..., messages_delta=..., terminal=...)`.

**Phase:** Phase 1 (trivial but worth noting)

**Confidence:** HIGH

---

### Pitfall 14: yield Inside try/finally in _run_items()

**What goes wrong:** If `_run_items()` uses `try/finally` to guarantee cleanup (e.g., emitting a terminal item), and a `yield` appears inside the `try` block, GeneratorExit during that yield will trigger the `finally` block. If the `finally` block tries to yield (e.g., yielding a terminal item on error), it raises `RuntimeError: async generator ignored GeneratorExit`. This is explicitly forbidden by PEP 525.

**Why it happens:** It's natural to want "always emit a terminal event, even on error" via try/finally. But yield-in-finally is illegal for async generators.

**Prevention:**
- Never yield in `finally` blocks within `_run_items()`
- For terminal event emission on error: use a local variable to track whether a terminal was yielded, and yield it BEFORE the generator function returns. If GeneratorExit occurs, accept that no terminal event is produced (the consumer already abandoned the stream)
- The spec's `_TerminalItem` design avoids this: terminal is yielded as the last item in normal flow, not in a finally block

**Phase:** Phase 1 (generator implementation)

**Confidence:** HIGH -- PEP 525 explicitly documents this restriction

---

### Pitfall 15: Exp.run_stream() Cleanup Guarantee

**What goes wrong:** `Exp.run()` uses `try/finally` (exp.py L285-306) to guarantee `_run_cleanup_callbacks()` always runs, even when the kernel raises. When `Exp.run_stream()` is added (Phase 2), the generator-based design means cleanup must happen when the CONSUMER is done iterating, not when the kernel is done yielding. If the consumer abandons the generator without exhausting it, `_run_cleanup_callbacks()` may never fire, leaving MCP connections (LazyMCPConnector), ReadTracker state, and other resources uncleaned.

**Prevention:**
- `Exp.run_stream()` must use `async with` or `try/finally` around the kernel's `run_stream()` generator. Use `contextlib.asynccontextmanager` or explicitly manage the generator lifecycle
- Pattern: `Exp.run_stream()` itself should be an async context manager that guarantees cleanup on exit, not a bare generator

**Phase:** Phase 2 (Exp layer integration)

**Confidence:** HIGH -- direct code analysis of exp.py cleanup pattern

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation | Severity |
|-------------|---------------|------------|----------|
| Phase 1: `_run_items()` implementation | ContextCompactor messages divergence (P1) | Use terminal item with final messages snapshot, or share list reference | CRITICAL |
| Phase 1: `_run_items()` implementation | yield inside try/finally (P14) | Never yield in finally; yield terminal before return | HIGH |
| Phase 1: InlineToolRunner extraction | Stop-event granularity change (P4) | Accept batch-level granularity OR add per-tool check in guard loop | HIGH |
| Phase 1: InlineToolRunner extraction | asyncio.gather + GeneratorExit (P9) | Handle BaseException around gather; test cancellation paths | MODERATE |
| Phase 1: `run_stream()` API design | aclose() cannot cancel tools (P2) | Document stop_event as cancellation mechanism; never rely on aclose() | HIGH |
| Phase 1: Kernel statelessness | Concurrent generator corruption (P5) | Enforce no self attributes; test concurrent _run_items() | HIGH |
| Phase 1-2 boundary: Service layer switch | Dual event path duplicates (P3) | Retire EventEmitterHook BEFORE service switches to run_stream() | CRITICAL |
| Phase 2: Hook retirement | OutputProcessorHook business metadata loss (P6) | Move auto_save/summarize to generator event info or service layer | MODERATE |
| Phase 2: Hook retirement | ConfirmationHook blocking gap (P7) | Document event gap tolerance; plan bidirectional flow migration last | MODERATE |
| Phase 2: `_stream_llm_items()` conversion | Missing hook call in generator (P10) | Pre-conversion event capture test; checklist mapping | HIGH |
| Phase 2: Exp integration | Cleanup guarantee for generator consumers (P15) | Context manager pattern for Exp.run_stream() | HIGH |
| Phase 3: Bus removal | ContextCompactor Bus dependency (P11) | Audit ALL direct Bus.emit() calls, not just Hook-based ones | MODERATE |
| Phase 3: Bus removal | Silent event loss | Integration test asserting event counts match pre-refactor baseline | HIGH |

---

## Sources

- [PEP 525 - Asynchronous Generators](https://peps.python.org/pep-0525/) -- yield-in-finally prohibition, aclose() semantics, GeneratorExit behavior
- [PEP 789 - Preventing task-cancellation bugs by limiting yield in async generators](https://peps.python.org/pep-0789/) -- cancel scope corruption with generators
- [CPython Issue #79590 - Async generator might re-throw GeneratorExit on aclose()](https://github.com/python/cpython/issues/79590) -- known aclose() edge case
- [CPython Issue #112572 - asyncio.gather with generator and return_exceptions](https://github.com/python/cpython/issues/112572) -- gather/generator interaction
- [CPython Issue #117714 - closing async_generator_athrow on suppressed GeneratorExit](https://github.com/python/cpython/issues/117714) -- GeneratorExit suppression edge case
- Codebase analysis: agent.py (700 lines), context_compactor.py (367 lines), hooks.py (287 lines), exp.py (517 lines), bus.py (64 lines), all 5 service-layer hooks
- Design spec: docs/specs/2026-04-02-kernel-generator-first.md (1500+ lines)
