# Phase 19: 服务层桥接 + 并行 Tool Dispatch - Research

**Researched:** 2026-03-29
**Domain:** asyncio bridge architecture, parallel coroutine dispatch, cross-thread event loop communication
**Confidence:** HIGH

## Summary

Phase 19 将 agent_run_service.py 中的双 event loop 架构（_router_loop + _loop）统一为单 daemon thread + run_forever 模式，所有 async 调用通过 `asyncio.run_coroutine_threadsafe()` 提交。同时将 AgentKernel 中的串行 tool dispatch 改为 `asyncio.gather()` 并行执行，DevShell runner 简化为 `asyncio.run()`。

现有代码已高度就绪。agent_run_service.py 中已有 run_coroutine_threadsafe 使用先例（:312 router.start()），需要做的是将 _router_loop 和 _loop 合并为一个 loop，删除 _loop.run_until_complete() 调用，统一使用 run_coroutine_threadsafe。Kernel tool dispatch 改造集中在 agent.py:176 的 `for tc in response.tool_calls:` 循环。DevShell 是一行替换。

**Primary recommendation:** 先统一 service 层 loop（删除代码量大于新增量），再改 kernel 并行 dispatch（agent.py 内部局部改造），最后 DevShell（一行替换）。每步独立可测试。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** agent_run_service.py 统一为单 event loop。一个 daemon thread 运行 `_loop.run_forever()`，所有 async 调用（router.start、exp.build_runtime、kernel.run、exp cleanup、router.stop）通��� `asyncio.run_coroutine_threadsafe(coro, _loop).result()` 提交到该 loop。消除当前双 loop 架构（`_router_loop` + `_loop`）。
- **D-02:** Kernel 执行通过 `run_coroutine_threadsafe(...).result()` 阻塞等待，与当前 `run_until_complete()` 语义等价。ThreadPoolExecutor 线程阻塞等待是预期行为。
- **D-03:** ConfirmationHook 的 set_loop() 注入：kernel async context 中 `asyncio.get_running_loop()` 自然获取到统一 loop，无需特殊处理。
- **D-04:** 全部 tool 并行执行，不按类别区分（只读 vs 有副作用）。信任 LLM 不会在同一轮发出冲突的 tool_call 组合。
- **D-05:** asyncio.gather(return_exceptions=True)，所有 tool 执行完毕后统一收集结果。失败的 tool 返回 exception 对象，转换为 ToolResult(status='error', content=str(exception))。
- **D-06:** Guard 评估和 pre_tool_call hook 保持串行（决策门控）。只有通过 guard + pre_hook 的 tool 进入并行执行。post_tool_call hook 在各 tool 完成后调用。
- **D-07:** DevShell runner.py 将现有 `new_event_loop() + run_until_complete()` 替���为 `asyncio.run()`。最小改动。

### Claude's Discretion
- 并行 dispatch 后 ToolMessage 的追加顺序（保持 tool_calls 原序 vs 按完成顺序）
- stop_event 检查点是否需要在并行 dispatch 前/后增加
- 统一 loop 的生命周期管理细节（创建时机、cleanup 顺序、异常时的 loop 关闭）
- 并行 dispatch 是否需要 asyncio.Semaphore 限制并发数

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BRDG-01 | src/ 服务层通过 asyncio bridge 调用 async matmaster | 统一 loop 架构：单 daemon thread + run_forever + run_coroutine_threadsafe。已验证 Python 3.13 兼容性。 |
| BRDG-02 | stop_event 跨线程传播机制适配 | threading.Event.is_set() 是线程安全的同步调用，Kernel 在每轮开头检查。统一 loop 后传播路径不变：service 线程 set() -> kernel async loop 中 is_set() 同步检查。 |
| TOOL-06 | 并行 Tool Dispatch -- 同一轮多个 tool_call 使用 asyncio.gather 并行执行 | 拆分串行 for 循环为：串行 guard+hook 门控 -> 并行 asyncio.gather(tool executions) -> 串行 post_hook + message append。 |
</phase_requirements>

## Architecture Patterns

### Current Architecture (Before Phase 19)

```
agent_run_service.run_agent_sync()    # ThreadPoolExecutor thread
    |
    +-- _router_loop (asyncio.new_event_loop)     # Dedicated loop for EventRouter
    |   +-- daemon thread: _router_loop.run_forever()
    |   +-- router.start() via run_coroutine_threadsafe
    |
    +-- _loop (asyncio.new_event_loop)            # Dedicated loop for Exp + Kernel
    |   +-- _loop.run_until_complete(exp.build_runtime())
    |   +-- _loop.run_until_complete(kernel.run())
    |   +-- _loop.run_until_complete(exp._run_cleanup_callbacks())
    |   +-- _loop.close()
    |
    +-- router.stop() via run_coroutine_threadsafe(_router_loop)
    +-- _router_loop.stop() via call_soon_threadsafe
```

**Problem:** Two separate event loops. EventRouter runs in one loop (daemon thread), Kernel runs in another (run_until_complete blocking). bus.emit_nowait() uses _router_loop for thread-safe put, but Kernel coroutines run in _loop -- they cannot share asyncio primitives.

### Target Architecture (After Phase 19)

```
agent_run_service.run_agent_sync()    # ThreadPoolExecutor thread
    |
    +-- _loop (asyncio.new_event_loop)            # SINGLE unified loop
    |   +-- daemon thread: _loop.run_forever()
    |   +-- router.start() via run_coroutine_threadsafe
    |   +-- exp.build_runtime() via run_coroutine_threadsafe
    |   +-- kernel.run() via run_coroutine_threadsafe  << blocks on .result()
    |   +-- exp._run_cleanup_callbacks() via run_coroutine_threadsafe
    |   +-- router.stop() via run_coroutine_threadsafe
    |   +-- _loop.stop() via call_soon_threadsafe
```

**Key insight:** run_coroutine_threadsafe(...).result() blocks the calling thread (ThreadPoolExecutor worker) while the coroutine executes in the daemon thread's event loop. This is semantically equivalent to run_until_complete() but allows the loop to remain running for EventRouter's async task.

### Pattern 1: Unified Loop Bridge

**What:** Single daemon thread event loop servicing all async operations
**When to use:** When sync code needs to call async code AND async tasks need to coexist (EventRouter consume task + Kernel run)

```python
# Creation
_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_loop.run_forever, daemon=True, name="agent-loop")
_loop_thread.start()

# Usage -- blocking from calling thread
future = asyncio.run_coroutine_threadsafe(some_coroutine(), _loop)
result = future.result(timeout=None)  # blocks calling thread, not event loop

# Cleanup
_loop.call_soon_threadsafe(_loop.stop)
_loop_thread.join(timeout=5)
_loop.close()
```

### Pattern 2: Parallel Tool Dispatch with Serial Guard Gate

**What:** Guard + pre_hook evaluated serially per tool, then all approved tools executed in parallel, then post_hook called per result
**When to use:** Multiple tool_calls in same LLM response

```python
# Phase 1: Serial gate evaluation
approved: list[tuple[ToolCallData, int]] = []  # (tc, original_index)
for i, tc in enumerate(response.tool_calls):
    guard_result = guard_pipeline.evaluate(tc, turn, spec.max_turns)
    if not guard_result.allowed:
        # append blocked ToolMessage immediately
        messages.append(ToolMessage(...blocked...))
        continue
    action = await run_pre_tool_call(spec.hooks, tc)
    if action == HookAction.SKIP:
        messages.append(ToolMessage(...skipped...))
        continue
    approved.append((tc, i))

# Phase 2: Parallel execution
async def _exec_one(tc: ToolCallData) -> ToolResult:
    try:
        return await spec.tool_registry.execute(tc.name, tc.arguments)
    except Exception as e:
        return ToolResult(status="error", content=f"Error executing tool '{tc.name}': {type(e).__name__}: {e}")

results = await asyncio.gather(
    *[_exec_one(tc) for tc, _ in approved],
    return_exceptions=True,  # D-05
)

# Phase 3: Serial post-processing (preserve original order)
for (tc, idx), result in zip(approved, results):
    if isinstance(result, BaseException):
        result = ToolResult(status="error", content=str(result))
    messages.append(ToolMessage(
        tool_call_id=tc.id,
        tool_name=tc.name,
        content=result.content,
    ))
    await run_post_tool_call(spec.hooks, tc, result)
```

### Pattern 3: DevShell asyncio.run()

**What:** Simplest bridge for development tools
**When to use:** Single-shot async invocation from sync context, no need for concurrent async tasks

```python
# Before
_loop = asyncio.new_event_loop()
try:
    runtime = _loop.run_until_complete(exp.build_runtime(...))
    result = _loop.run_until_complete(runtime.kernel.run(...))
finally:
    _loop.run_until_complete(exp._run_cleanup_callbacks())
    _loop.close()

# After
async def _run_once(exp, pg_ctx, ...):
    try:
        runtime = await exp.build_runtime(pg_ctx, ...)
        spec = runtime.spec.model_copy(...)
        return await runtime.kernel.run(spec, task, ...)
    finally:
        await exp._run_cleanup_callbacks()

result = asyncio.run(_run_once(exp, pg_ctx, ...))
```

### Anti-Patterns to Avoid
- **Nested event loops:** Never call `asyncio.run()` or `loop.run_until_complete()` from within an already-running event loop. This raises RuntimeError in Python 3.10+.
- **Sharing asyncio.Queue across loops:** asyncio.Queue is NOT thread-safe. MessageBus already handles this via call_soon_threadsafe. Do not create direct cross-loop queue access.
- **Forgetting to stop the daemon loop:** If the loop thread is not stopped and joined, the process may hang on exit. Always include cleanup in finally block.
- **Using asyncio.gather without return_exceptions:** Without `return_exceptions=True`, the first exception cancels all other tasks. D-05 explicitly requires all tools to complete regardless of individual failures.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cross-thread async dispatch | Custom thread-safe wrapper | `asyncio.run_coroutine_threadsafe()` | Standard library, returns `concurrent.futures.Future` with `.result()` blocking |
| Parallel async execution | Manual task management | `asyncio.gather(*coros, return_exceptions=True)` | Standard library, handles exception collection |
| Thread-safe loop shutdown | Manual loop.stop() | `loop.call_soon_threadsafe(loop.stop)` + `thread.join()` | Avoids race condition with pending callbacks |
| Exception-to-ToolResult conversion | Custom exception handler per tool | `return_exceptions=True` + isinstance check | gather's return_exceptions already collects exceptions as return values |

## Common Pitfalls

### Pitfall 1: bus.set_loop() must use the unified loop
**What goes wrong:** MessageBus.emit_nowait() uses call_soon_threadsafe on the stored loop. If set_loop() is called with the wrong loop, events from service-layer sync code go to the wrong loop.
**Why it happens:** Currently router.start() calls bus.set_loop(asyncio.get_running_loop()) internally. After unification, the bus's loop will be the unified _loop -- this is correct.
**How to avoid:** Verify that router.start() is called via run_coroutine_threadsafe on the unified _loop. The existing bus.set_loop() call inside router.start() will then naturally capture the correct loop.
**Warning signs:** Events emitted from service layer (bus.emit_nowait) don't reach EventRouter handlers.

### Pitfall 2: Quota code path has dual bridge
**What goes wrong:** Lines 527-533 in agent_run_service.py have two quota paths: one uses `run_coroutine_threadsafe(use_quota, loop)` (when `loop` parameter is not None), the other uses `asyncio.run()`. The `loop` parameter comes from the caller (stream_service via FastAPI's event loop).
**Why it happens:** Historical design -- the `loop` parameter was the FastAPI event loop passed to run_agent_sync.
**How to avoid:** After unification, the `loop` parameter from the caller is still the FastAPI event loop (for quota deduction). This is SEPARATE from the agent's unified _loop. The quota call must still use the FastAPI loop (or asyncio.run as fallback). Do NOT change quota to use the agent's _loop.
**Warning signs:** use_quota coroutine never completes or raises "loop is closed".

### Pitfall 3: Cleanup ordering matters
**What goes wrong:** If router.stop() runs before Bohrium cleanup or Exp cleanup, final events are lost.
**Why it happens:** Unification puts all async operations on one loop. If the loop stops too early, remaining cleanup coroutines cannot execute.
**How to avoid:** Maintain the existing cleanup order: (1) Bohrium cleanup (sync, may emit events), (2) Exp cleanup via run_coroutine_threadsafe, (3) router.stop() via run_coroutine_threadsafe (drains queue), (4) loop.stop() + thread.join().
**Warning signs:** Missing final events in SSE stream, PersistenceHandler doesn't persist terminal events.

### Pitfall 4: Guard pipeline records calls for blocked tools
**What goes wrong:** GuardPipeline._record_call() is called only after ALL guards pass. With parallel dispatch, if guard evaluation moves out of the execution path, recording might happen at the wrong time.
**Why it happens:** Guard evaluation must remain serial (D-06). The existing serial guard loop already handles recording correctly.
**How to avoid:** Keep guard evaluation in the serial Phase 1 loop. Only approved tools enter Phase 2 (parallel). Guard pipeline's _record_call is unaffected since it records during evaluate() which stays serial.
**Warning signs:** LoopDetectionGuard fails to detect repeated calls.

### Pitfall 5: ToolMessage ordering for LLM context
**What goes wrong:** LLM expects ToolMessages in the same order as tool_calls in its response. If parallel execution appends messages by completion order, the LLM receives mismatched tool_call_id <-> ToolMessage sequences.
**Why it happens:** asyncio.gather returns results in the same order as input coroutines, but if messages are appended as each tool completes (not using gather), ordering is lost.
**How to avoid:** Use asyncio.gather which preserves input order in its result list. Append all ToolMessages after gather completes, iterating in the original tool_calls order.
**Warning signs:** LLM confusion about which tool produced which result, incorrect reasoning about tool outputs.

### Pitfall 6: run_coroutine_threadsafe .result() timeout
**What goes wrong:** kernel.run() can take minutes (700 turns maximum). If .result(timeout=X) is set too low, it raises TimeoutError while the kernel is still running, leaving orphaned coroutines.
**Why it happens:** Unlike run_until_complete which inherently waits, .result() requires explicit timeout management.
**How to avoid:** Use .result() without timeout (or with a very large timeout matching the overall run timeout). The external stop_event mechanism handles cancellation, not future timeout.
**Warning signs:** TimeoutError from concurrent.futures, kernel continues running after caller gives up.

## Code Examples

### Example 1: Unified Loop Lifecycle in agent_run_service

```python
# -- Setup --
bus = MessageBus()
_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(
    target=_loop.run_forever, daemon=True, name="agent-loop"
)
_loop_thread.start()

try:
    # Router start
    asyncio.run_coroutine_threadsafe(router.start(), _loop).result()

    # Exp build_runtime
    runtime = asyncio.run_coroutine_threadsafe(
        exp.build_runtime(pg_ctx, bus=bus, ...),
        _loop,
    ).result()

    # Kernel run (long-running, no timeout on .result())
    kernel_result = asyncio.run_coroutine_threadsafe(
        runtime.kernel.run(spec=spec, task=user_prompt, history=history, stop_event=stop_event),
        _loop,
    ).result()

finally:
    # Exp cleanup
    try:
        asyncio.run_coroutine_threadsafe(
            exp._run_cleanup_callbacks(), _loop
        ).result(timeout=30)
    except Exception:
        logger.warning("Exp cleanup failed", exc_info=True)

    # Router stop (drains remaining events)
    try:
        asyncio.run_coroutine_threadsafe(
            router.stop(), _loop
        ).result(timeout=10)
    except Exception:
        logger.warning("router.stop() failed", exc_info=True)

    # Loop shutdown
    _loop.call_soon_threadsafe(_loop.stop)
    _loop_thread.join(timeout=5)
    _loop.close()
```

### Example 2: Parallel Tool Dispatch in AgentKernel._run_loop

```python
# Replace agent.py:176 for-loop with:

# --- Phase 1: Serial gate ---
approved_tools: list[ToolCallData] = []
for tc in response.tool_calls:
    guard_result = guard_pipeline.evaluate(tc, turn, spec.max_turns)
    if not guard_result.allowed:
        await run_guard_blocked(spec.hooks, tc, guard_result)
        blocked_content = f"BLOCKED: {guard_result.reason}"
        if guard_result.guidance:
            blocked_content += f"\n{guard_result.guidance}"
        messages.append(ToolMessage(
            tool_call_id=tc.id, tool_name=tc.name, content=blocked_content,
        ))
        continue

    action = await run_pre_tool_call(spec.hooks, tc)
    if action == HookAction.SKIP:
        messages.append(ToolMessage(
            tool_call_id=tc.id, tool_name=tc.name, content="Tool call skipped by hook.",
        ))
        continue
    approved_tools.append(tc)

# --- Phase 2: Parallel execution ---
if approved_tools:
    async def _execute_tool(tc: ToolCallData) -> ToolResult:
        try:
            return await spec.tool_registry.execute(tc.name, tc.arguments)
        except Exception as e:
            logger.exception("Tool execution failed: %s", tc.name)
            return ToolResult(
                status="error",
                content=f"Error executing tool '{tc.name}': {type(e).__name__}: {e}",
            )

    results = await asyncio.gather(
        *[_execute_tool(tc) for tc in approved_tools],
        return_exceptions=True,
    )

    # --- Phase 3: Serial post-processing ---
    for tc, raw_result in zip(approved_tools, results):
        if isinstance(raw_result, BaseException):
            tool_result = ToolResult(
                status="error",
                content=f"Error executing tool '{tc.name}': {type(raw_result).__name__}: {raw_result}",
            )
        else:
            tool_result = raw_result
        messages.append(ToolMessage(
            tool_call_id=tc.id, tool_name=tc.name, content=tool_result.content,
        ))
        await run_post_tool_call(spec.hooks, tc, tool_result)
```

### Example 3: DevShell asyncio.run() Simplification

```python
def run(self, task: str, *, stop_event=None, bus=None) -> KernelRunResult:
    exp = Exp(self._exp_config)

    async def _run_once() -> KernelRunResult:
        try:
            runtime = await exp.build_runtime(self._pg_ctx, bus=bus)
            spec = runtime.spec.model_copy(
                update={"hooks": [*runtime.spec.hooks, self._stream_hook]}
            )
            return await runtime.kernel.run(
                spec, task, history=self.history, stop_event=stop_event
            )
        finally:
            await exp._run_cleanup_callbacks()

    result = asyncio.run(_run_once())
    # history accumulation unchanged
    if result.result.status != "cancelled":
        skip_count = 1 + len(self.history) + 1
        new_messages = result.messages[skip_count:]
        self.history.append(UserMessage(content=task))
        self.history.extend(new_messages)
    return result
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| run_until_complete() for one-shot async | run_coroutine_threadsafe() for shared loop | Python 3.4+ (concurrent.futures integration) | Allows event loop to remain active for other tasks |
| Dual event loops for isolation | Single loop with daemon thread | Standard practice in mixed sync/async codebases | Simplifies resource management, enables asyncio primitive sharing |
| asyncio.run() in threads | Discouraged in Python 3.12+ (RuntimeWarning) | Python 3.10 added "no nested run" check | Use run_coroutine_threadsafe for threads that need an active loop |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | pyproject.toml (`asyncio_mode = "auto"`) |
| Quick run command | `uv run pytest tests/matmaster/core/test_agent.py -x -q` |
| Full suite command | `uv run pytest tests/matmaster/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BRDG-01 | run_agent_sync() 通过 run_coroutine_threadsafe 调用 async matmaster，无 RuntimeError | integration | `uv run pytest tests/matmaster/integration/test_e2e_minimal.py -x` | Existing (needs adaptation) |
| BRDG-02 | stop_event 跨线程传播到 async kernel | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestExternalCancel -x` | Existing (no change needed) |
| TOOL-06 | 多 tool_call 并行执行，总耗时接近最慢单 tool | unit | `uv run pytest tests/matmaster/core/test_agent.py -x` | New test needed |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/core/test_agent.py -x -q`
- **Per wave merge:** `uv run pytest tests/matmaster/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch` -- covers TOOL-06 parallel execution
- [ ] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_parallel_faster_than_serial` -- timing assertion
- [ ] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_gather_return_exceptions` -- exception handling per D-05
- [ ] `tests/matmaster/core/test_agent.py::TestParallelToolDispatch::test_preserves_tool_call_order` -- message ordering

## Key Technical Details

### run_coroutine_threadsafe Semantics

`asyncio.run_coroutine_threadsafe(coro, loop)` returns `concurrent.futures.Future`. Calling `.result()` blocks the calling thread until the coroutine completes on the target loop. This is the standard CPython mechanism for submitting work to a running event loop from another thread.

Critical properties:
- Thread-safe: designed for cross-thread submission
- Non-blocking for the event loop: the loop continues processing other tasks
- `.result()` propagates exceptions raised by the coroutine
- `.result(timeout=N)` raises `concurrent.futures.TimeoutError` if not completed in N seconds

### MessageBus Thread Safety

MessageBus (bus.py) already handles cross-thread emit via `call_soon_threadsafe`. After unification:
- `bus.set_loop()` is called during `router.start()` which runs on the unified loop
- Kernel hooks call `bus.emit_nowait()` from within the unified loop -- this uses direct `put_nowait` (same loop, safe)
- Service layer sync code calls `bus.emit_nowait()` from ThreadPoolExecutor thread -- this uses `call_soon_threadsafe` (cross-thread, safe)
- No changes needed to MessageBus itself

### stop_event Threading Model

stop_event is `threading.Event` (KERN-05 decision). The kernel checks `stop_event.is_set()` synchronously at the top of each turn loop iteration. This is a GIL-protected read that works correctly across threads without any async adapter.

Sources of stop_event.set():
1. **Single-process mode:** stream_service creates threading.Event, passes to run_agent_sync, sets it on SSE disconnect or stop API call
2. **Worker mode:** RedisBackedStopEvent wraps Redis polling behind `is_set()` interface -- not a real threading.Event but duck-typed

After Phase 19, stop_event propagation is unchanged. Kernel reads `is_set()` from within the unified loop's daemon thread; callers set it from the service thread. threading.Event is designed for exactly this cross-thread use case.

### Quota Deduction Path

agent_run_service.py:527-533 has a special quota path:
```python
if loop is not None:
    future = asyncio.run_coroutine_threadsafe(use_quota(user_id), loop)
    future.result(timeout=10)
else:
    asyncio.run(use_quota(user_id))
```

The `loop` parameter here is the FastAPI event loop passed from stream_service.py. This is NOT the agent's unified loop. The quota deduction must run on the FastAPI loop (or standalone asyncio.run) because it may interact with FastAPI-bound async resources. This path should remain unchanged or simplified to always use `asyncio.run()` since the agent runs in a separate thread anyway.

### Deletions and Removals

The following code segments will be DELETED (net negative line count):
1. `_router_loop = asyncio.new_event_loop()` (line 305)
2. `_router_loop_thread = threading.Thread(target=_router_loop.run_forever, ...)` (lines 306-310)
3. `_router_loop_thread.start()` (line 311)
4. `_loop.run_until_complete(exp.build_runtime(...))` (lines 432-438) -- replaced by run_coroutine_threadsafe
5. `_loop.run_until_complete(kernel.run(...))` (lines 477-484) -- replaced by run_coroutine_threadsafe
6. `_loop.run_until_complete(exp._run_cleanup_callbacks())` (line 486) -- replaced by run_coroutine_threadsafe
7. `_loop.close()` (line 487) -- moved to unified cleanup
8. router.stop() cleanup referencing `_router_loop` (lines 583-584) -- unified to _loop
9. `_router_loop.call_soon_threadsafe(_router_loop.stop)` (line 590)
10. `_router_loop_thread.join(timeout=5)` (line 591)

## Open Questions

1. **Quota loop parameter deprecation**
   - What we know: `loop` parameter is passed from stream_service (FastAPI event loop) or None (Worker). After unification, the agent has its own loop but quota must not run on it.
   - What's unclear: Should the `loop` parameter be kept as-is or simplified?
   - Recommendation: Keep existing dual path for now (matches "最小桥接" principle). Can be simplified in a future cleanup pass.

2. **LazyMCPConnector's own event loop**
   - What we know: LazyMCPConnector (lazy_mcp.py:154) creates its own daemon thread + event loop for MCP server communication.
   - What's unclear: Should this be migrated to use the unified loop?
   - Recommendation: Out of scope for Phase 19. LazyMCPConnector's loop is self-contained and manages its own lifecycle. Migrating it would require touching evomaster MCPToolManager internals.

## Sources

### Primary (HIGH confidence)
- Source code analysis: agent_run_service.py, agent.py, runner.py, bus.py, event_router.py, hooks.py, guard_pipeline.py, tool_registry.py
- Source code analysis: tests/matmaster/core/test_agent.py, tests/conftest.py, tests/matmaster/core/conftest.py
- Python 3.13.2 runtime verification: asyncio.run_coroutine_threadsafe + daemon thread pattern confirmed working
- Full test suite baseline: 1057 passed, 3 skipped (2026-03-29)

### Secondary (MEDIUM confidence)
- CPython asyncio documentation: run_coroutine_threadsafe, gather, new_event_loop semantics
- Phase 16-18 CONTEXT.md files: architectural evolution history

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new libraries needed, pure asyncio standard library
- Architecture: HIGH - pattern already partially implemented (router.start uses run_coroutine_threadsafe), extending to all calls
- Pitfalls: HIGH - based on direct code reading and codebase-specific analysis

**Research date:** 2026-03-29
**Valid until:** 2026-04-28 (stable domain, no external dependency changes expected)
