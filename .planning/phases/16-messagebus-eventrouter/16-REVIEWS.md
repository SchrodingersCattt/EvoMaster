---
phase: 16
reviewers: [gemini, codex]
reviewed_at: 2026-03-28T18:00:00+08:00
plans_reviewed: [16-01-PLAN.md, 16-02-PLAN.md]
---

# Cross-AI Plan Review — Phase 16

## Gemini Review

This review evaluates the implementation plans for **Phase 16: MessageBus + EventRouter 异步化**.

### Summary
The plans are exceptionally well-structured, demonstrating a deep understanding of the project's "bottom-up" async migration strategy. By opting for a "hard cut" on the Protocol and addressing the service-layer bridging immediately in Wave 2, the plan minimizes the duration of "broken" states. The strategy for handling blocking I/O in the `PersistenceHandler` and the cleanup of the `SSEHandler` dual-path logic is technically sound and aligns with modern `asyncio` best practices.

### Strengths
- **Robust Shutdown Logic:** The use of `asyncio.Event` combined with a `wait_for` timeout in the consume loop is superior to bare `asyncio.wait` or infinite `get()` calls, as it ensures the router remains responsive to shutdown signals while allowing for a graceful "drain" phase.
- **Surgical Handler Refactoring:** Converting `PersistenceHandler` to use `asyncio.to_thread` for DB operations correctly isolates blocking I/O without stalling the main event loop.
- **Complexity Reduction:** Deleting the `run_coroutine_threadsafe` path in `SSEHandler` significantly simplifies the transport layer and removes a common source of deadlocks and race conditions.
- **TDD Focus:** Defining 8 specific behavior specifications for the `MessageBus` before implementation ensures the foundational transport layer is verified under edge cases (e.g., empty queue, timeout).
- **Lifecycle Safety:** Plan 16-02 Task 2's focus on `try/finally` blocks for the service-layer bridge is critical for preventing orphaned threads or hung event loops during application crashes.

### Concerns
- **Thread-Safety of `emit_nowait` (HIGH):** The plan introduces `emit_nowait()` for sync callers in the service layer. However, `asyncio.Queue.put_nowait()` is **not thread-safe**. If the service layer calls this from a different thread (e.g., a FastAPI worker thread) than the one running the `EventRouter` loop, it will cause internal `asyncio` state corruption.
- **Loop Affinity (MEDIUM):** While `asyncio.Queue` creation in 3.10+ is loop-agnostic, its *usage* is not. If the `MessageBus` is created in `Exp.assemble()` but used in a different loop context in the service layer, it may raise `RuntimeError`.
- **Testing Overhead (LOW):** Migrating 58 tests is a high-volume mechanical task. While the TDD approach mitigates risk, there is a possibility of "false positives" if mocks are not correctly updated to return awaitables.

### Suggestions
- **Make `emit_nowait` Thread-Safe:** Implement `MessageBus.emit_nowait` using `self._loop.call_soon_threadsafe(self._queue.put_nowait, event)`. This ensures that sync callers from any thread can safely push events into the async queue.
- **Explicit Loop Capture:** Capture the running loop in `MessageBus.__init__` or `EventRouter.start` to ensure all internal `asyncio` primitives (Queue, Event) are bound to the correct execution context.
- **Handle `CancelledError`:** In the `EventRouter` consume loop, explicitly catch `asyncio.CancelledError`. While the `_stop_event` handles graceful stops, an external shutdown of the `asyncio.Task` should still be handled to ensure `_close_handlers` is called.
- **Grep for `queue.Queue`:** After Wave 1, add a verification step to ensure no instances of `queue.Queue` or `threading.Thread` remain in the `matmaster/core` or `matmaster/integration` directories.

### Risk Assessment: MEDIUM
The overall risk is **Medium**, primarily due to the **thread-safety requirements** of bridging sync service calls to an async message bus. If `emit_nowait` is implemented without `call_soon_threadsafe`, the system may exhibit intermittent crashes or lost events that are difficult to debug in production. Once thread-safety for the sync-to-async bridge is addressed, the risk drops to Low.

---

## Codex Review

(Based on actual codebase analysis, not just plan text)

### Plan 16-01

**Summary**
这份计划方向是对的，分层也基本符合当前 v2.0 自底向上的迁移节奏，尤其把 MessageBus、EventRouter、3 个 handler 一次性纳入是合理的。但它有两个明显短板：一是作为独立 Wave 时不可安全落地，二是对真正的非阻塞链路定义还不够彻底，WorkspaceHandler、handler 调度顺序、测试面都还有漏项。

**Strengths**
- 贴合路线图目标，先改传输层核心对象，再推进调用方，拆分思路清晰。
- 把 WorkspaceHandler 明确纳入范围是正确的，不然 EventRouter 改成 await handler.handle() 后它会变成隐式断点。
- 选择 wait_for + timeout 做 consume loop，比 asyncio.wait 更直接，出错面更小。
- 明确删除 SSEHandler 旧的双路径逻辑，有助于把历史线程模型彻底收口。
- 给 MessageBus 先做行为规格测试，这一步很值，能提前锁住超时、FIFO、drain 等语义。

**Concerns**
- `HIGH`: Wave 1 不是可独立合并的增量。一旦 bus.py 的 emit() 变成 async def，现有 hooks.py、output_processor.py、assistant_state.py、agent_run_service.py 里的同步调用都会立刻变成未等待协程，功能上已经不成立。
- `HIGH`: WorkspaceHandler 现在的快照逻辑仍是同步文件遍历（rglob/stat）。如果只把 handle() 改成 async 而不把快照本身挪到 to_thread，大 workspace 下仍然会卡住 router。
- `MEDIUM`: 测试范围被低估了。除了 test_event_router.py，至少还有 test_chat_stream_direct.py、test_sse_skill_hit.py、test_upstream_scenarios.py、test_workspace_handler.py 直接依赖这些 handler。
- `MEDIUM`: _close_handlers 如果靠 iscoroutinefunction 判断 sync/async，会漏掉 AsyncMock、返回 awaitable 的普通可调用对象、partial 包装等情况。
- `MEDIUM`: 异步化后如果仍按当前顺序串行分发 handler，PersistenceHandler 先 await asyncio.to_thread(...)，SSEHandler 后执行，那么前端实时性仍然会被 DB 延迟拖住。
- `MEDIUM`: 当前 add_handler() 与 _dispatch() 的语义依赖对 handler 列表做快照。如果异步版直接迭代可变列表，新增 handler 可能会错误收到当前正在分发的事件。
- `LOW`: 如果保留 maxsize 可配置，同时 emit() 内部固定 put_nowait()，那 QueueFull 的语义要么被明确测试，要么直接禁止有界队列。

**Suggestions**
- 把 16-01 和 16-02 视为一个原子落地单元，至少不要把 16-01 当成可单独合并的绿灯 checkpoint。
- WorkspaceHandler 的快照阶段也放进 asyncio.to_thread()，不要只保留上传线程池。
- _close_handlers 用调用结果判断：先 result = close()，再检查 inspect.isawaitable(result)，不要只看函数定义形态。
- 明确 handler 调度策略。如果坚持串行，建议把 SSEHandler 排到 PersistenceHandler 前面；如果允许并发，就要把异常隔离和 stop/drain 语义一起写清楚。
- 把现有直接依赖 handler 的测试文件先一次性枚举出来，再估算迁移工作量。

**Risk Assessment: MEDIUM**
方向正确、分层也合理，但它不是一个可以单独站住的中间状态。如果把它和 Wave 2 打包落地，风险会明显下降。

### Plan 16-02

**Summary**
这份计划抓到了真正剩下的调用面，尤其是 service 层那 10 个同步 emit，这一点比表面看起来更重要。但当前最大的结构性问题在于：专门起一个 _router_loop_thread，再让别的线程或别的 loop 直接对 asyncio.Queue 做 emit_nowait()，这个模型本身就不安全，而且和现有 _sync_call_async 的实现可能冲突。

**Strengths**
- 把机械式 await bus.emit(...) 和 service 层桥接分开处理，便于识别真正的风险集中区。
- 明确把异常安全的 try/finally 生命周期管理提到计划里，尤其 stop 阶段最容易掩盖原始异常。
- 接受标准里加入 grep 检查，有助于防止漏网的同步调用点。
- 注意到了 SSEHandler 构造参数收敛，准备清理接口面。

**Concerns**
- `HIGH`: asyncio.Queue 不是线程安全队列。研究结论里说 Python 3.10+ 可以跨 loop 创建 queue，这只说明构造阶段不绑定 loop，不代表可以让 worker 线程直接 emit_nowait()，同时让另一个 _router_loop_thread 在 await queue.get()。
- `HIGH`: 当前 _sync_call_async 本质是 loop.run_until_complete(coro)。如果 _router_loop_thread 里已经 run_forever()，再从外线程对同一个 loop 调 _sync_call_async，会撞上 event loop already running 错误。
- `HIGH`: 这份设计实际上没有满足成功标准 4。Kernel hooks 会在自己的 bridge loop 上 await bus.emit(...)，router 却在另一条 loop/thread 上消费，producer 和 consumer 并不在同一个 event loop 中协作。
- `MEDIUM`: SSEHandler 删除 loop 参数不等于发送链路就安全了。Worker 里的 send_cb 是同步 Redis publish，async handle() 直接调用它会绑住 router loop。
- `MEDIUM`: service finally 里的停止顺序仍然是隐患。先 router.stop()，后 bohrium_svc.cleanup()；而 cleanup 仍然拿着 _bohrium_event_cb，理论上还能继续往 bus 发事件。
- `MEDIUM`: emit_nowait() 作为 service 层逃生口，和 D-01 的 pure async hard cut 有一点自相矛盾。必须明确它只是内部桥接接口。
- `LOW`: grep 能证明文本层面的迁移，不足以证明跨线程 emit、取消、异常收尾、terminal event 不丢失这些运行时性质。

**Suggestions**
- 在写代码前先重新定 loop 所有权。最稳妥的方案只有两类：要么 bus/router 绑定到一个专用 loop，并且所有跨线程 emit 都走 call_soon_threadsafe 或 run_coroutine_threadsafe；要么承认 Phase 16 还做不到 strict same-loop，推迟 asyncio.Queue 到 Kernel async 化之后。
- 不要把 _sync_call_async 直接复用到一个已经 run_forever 的 loop 上。如果保留专用 router loop，就该用线程安全调度原语。
- 给 SSEHandler 明确 send contract。要么统一要求 async callback，让 worker/API 两边都做适配；要么允许 sync callback，但对真正阻塞的 callback 明确用 to_thread 包起来。
- 增加 service 级集成测试。最关键的是跨线程 producer -> router consumer、stop 时有 inflight handler、cleanup 晚到事件、异常路径 terminal event 这几类。
- 重新审视 finally 顺序，保证不会在 router 关闭后还有组件继续向 bus 发事件。

**Risk Assessment: HIGH**
调用点迁移本身不难，但当前桥接设计在 asyncio 线程模型上有真实的结构性风险。真正的 blocker 不是多少个 await，而是 bus 到 router 这条链路到底由哪个 event loop 拥有。

---

## Consensus Summary

### Agreed Strengths
(Mentioned by both reviewers)

1. **wait_for + timeout consume loop** — Both reviewers agree this is superior to asyncio.wait, simpler and less error-prone
2. **SSEHandler dual-path deletion** — Both agree removing run_coroutine_threadsafe simplifies the transport layer significantly
3. **try/finally lifecycle safety** — Both highlight the exception-safe router loop management as critical and well-designed
4. **TDD behavior specs for MessageBus** — Both value the upfront behavioral test definitions

### Agreed Concerns
(Raised by both reviewers — highest priority)

1. **asyncio.Queue thread-safety (HIGH)** — Both Gemini and Codex flag that asyncio.Queue.put_nowait() is NOT thread-safe. Service layer calling emit_nowait() from a different thread while EventRouter consumes from _router_loop creates undefined behavior. **This is the #1 structural risk.** Gemini suggests call_soon_threadsafe; Codex suggests rethinking loop ownership entirely.

2. **Loop ownership / event loop isolation (HIGH)** — Codex raises that _sync_call_async (run_until_complete) cannot be called on a loop that is already run_forever(). Both reviewers question whether success criteria #4 (same event loop cooperation) is actually achievable with the current bridge design where Kernel hooks emit on _bridge_loop and router consumes on _router_loop.

3. **Test scope underestimate (MEDIUM)** — Both note the test migration is larger than planned. Codex specifically identified 4+ additional test files beyond test_event_router.py that directly depend on handlers.

### Divergent Views

1. **Risk Level** — Gemini rates overall MEDIUM, Codex rates Plan 16-02 HIGH. The divergence is on whether the thread-safety issue is a "fix it during implementation" problem (Gemini) or a "redesign loop ownership first" problem (Codex).

2. **WorkspaceHandler blocking I/O** — Codex specifically flags that WorkspaceHandler's rglob/stat operations will block the event loop even with async def handle(). Gemini does not raise this. Codex suggests wrapping snapshot logic in to_thread.

3. **Handler dispatch order** — Codex raises that serial dispatch (PersistenceHandler before SSEHandler) will delay frontend events by DB write latency. Gemini does not mention dispatch ordering.

4. **_close_handlers detection** — Codex flags that iscoroutinefunction misses AsyncMock/partial/awaitable callables. Gemini suggests handling CancelledError instead.

---

*Phase: 16-messagebus-eventrouter*
*Reviewed: 2026-03-28 by Gemini CLI + Codex CLI*
