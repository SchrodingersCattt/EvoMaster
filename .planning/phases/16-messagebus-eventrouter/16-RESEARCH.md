# Phase 16: MessageBus + EventRouter 异步化 - Research

**Researched:** 2026-03-28
**Domain:** asyncio.Queue, asyncio.Task, async event dispatch
**Confidence:** HIGH

## Summary

Phase 16 将事件传输链路从 threading+queue.Queue 全面改造为 asyncio.Queue+asyncio.Task。改造范围明确且边界清晰：MessageBus（50 行）、EventRouter（130 行）、SSEHandler（117 行）、PersistenceHandler（81 行），加上 matmaster/ 包内 13 个 bus.emit() 调用点的 await 添加。service 层 agent_run_service.py 中另有 10 个 bus.emit() 调用点位于同步函数中，需要通过 put_nowait 保持兼容（这些调用点在 Phase 19 service 层重构时统一处理）。

所有核心 asyncio 原语（Queue.put_nowait maxsize=0 永不 QueueFull、wait_for+get 超时、asyncio.Event 信号、get_nowait drain）已通过本地运行时验证。前置 Phase 12-15 已完成 Protocol async 签名、pytest-asyncio 基础设施、_sync_call_async 桥接模式、asyncio.to_thread 包装模式，本阶段可直接复用。

**Primary recommendation:** 严格按 CONTEXT.md D-01 到 D-10 决策执行，不探索替代方案。改造量小（每个文件改动 < 30 行），但测试迁移量大（58 个现有测试需要从 sync 改 async）。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** MessageBus 纯 async，不保留 sync 兼容接口。与 Protocol hard cut 决策一致。DevShell 不在 v2.0 范围，将来自行适配。
- **D-02:** emit() 签名为 async def，底层使用 put_nowait()（不实际 await）。maxsize=0 时 put_nowait 永不抛 QueueFull，避免不必要的协程切换。方法签名满足 async Protocol 要求。
- **D-03:** get() 使用 asyncio.wait_for(queue.get(), timeout) 包装。超时抛 asyncio.TimeoutError。EventRouter consume loop 用 try/except TimeoutError 替代当前 queue.Empty。get_nowait() 保留为同步方法（drain 场景使用）。
- **D-04:** start() 和 stop() 都是 async def。使用 asyncio.create_task 启动消费循环。service 层通过 _sync_call_async(router.start(), bridge_loop) 桥接调用，与 Phase 13-15 模式一致。Phase 17 Kernel async 化后直接 await。
- **D-05:** graceful stop 使用 asyncio.Event 信号。consume loop 用 asyncio.wait 同时等待 queue.get() 和 stop_event.wait()，任一完成则检查。收到 stop 信号后 drain 剩余事件再退出。
- **D-06:** EventRouter 内部 _stop_event 从 threading.Event 改为 asyncio.Event。_thread 字段改为 _task: asyncio.Task。
- **D-07:** service 层做最小桥接。agent_run_service.py 中 router.start()/stop() 调用点改为 _sync_call_async 桥接。构造函数签名不变（bus + handlers）。handler 创建逻辑不变。Phase 19 再整体重构 service 层。
- **D-08:** Phase 16 一起改所有 12 个 bus.emit() 调用点为 await bus.emit()。改动纯机械性（加 await 关键字）。分布在 4 个 Hook 实现 + EventEmitterHook（6 处）+ ContextCompactor（2 处）。所有调用点已在 async def 方法中（Phase 15 完成），无兼容问题。
- **D-09:** PersistenceHandler.handle() 改为 async def，内部 events_table.add_event() 用 asyncio.to_thread 包装。与 Phase 14 BuiltinTool 模式一致，DB 写入不阻塞 event loop。
- **D-10:** SSEHandler.handle() 改为 async def，统一 await self._send_cb(payload)。删除 run_coroutine_threadsafe 路径和 _loop/_is_async 字段。EventRouter 已在 async task 中运行，不需要跨线程调度。

### Claude's Discretion
- EventRouter consume loop 的 asyncio.wait 具体实现方式（wait vs wait_for vs gather + shield）
- drain 逻辑中 get_nowait 的循环边界和 timeout 处理
- _close_handlers 是否需要改 async（handler.close() 是否涉及 I/O）
- 测试迁移范围和 async mock 策略
- WorkspaceHandler 是否需要在本阶段一并改 async handle()

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INFR-01 | MessageBus 内部队列从 queue.Queue 改为 asyncio.Queue | asyncio.Queue 行为已验证：put_nowait maxsize=0 永不抛 QueueFull，wait_for+get 超时正确，get_nowait drain 正确。改造量 ~20 行。 |
| INFR-02 | EventRouter 适配 async MessageBus（drain 逻辑改为 async） | asyncio.Event + asyncio.create_task 模式已验证。两种 consume loop 方案（wait_for+timeout 轮询 vs asyncio.wait 双任务）均可行，已评估 tradeoff。 |
| INFR-03 | SSEHandler 和 PersistenceHandler 适配 async 事件消费 | SSEHandler 改造为纯 async 净减代码（删除 run_coroutine_threadsafe 路径）。PersistenceHandler 使用 asyncio.to_thread 复用 Phase 14 模式。WorkspaceHandler 分析见 Discretion。 |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| asyncio (stdlib) | Python 3.10+ | Queue, Event, Task, wait_for | 项目决策：坚持 asyncio 标准库，不引入 trio/anyio |
| pytest-asyncio | 已安装 (auto mode) | async 测试基础设施 | Phase 12 已配置 asyncio_mode=auto |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio.to_thread | Python 3.9+ (stdlib) | 包装 PersistenceHandler DB 写入 | 同步 I/O 调用不阻塞 event loop |

**No new dependencies required.** 全部使用 Python 标准库 asyncio 模块。

## Architecture Patterns

### 改造后的事件链路架构

```
Producer (Hook/Compactor)    Transport           Consumer
  await bus.emit(event)  -->  asyncio.Queue  -->  asyncio.Task (EventRouter._consume_loop)
                                                    |
                                                    +--> await handler.handle(event)
                                                    |      +--> SSEHandler: await _send_cb(payload)
                                                    |      +--> PersistenceHandler: await to_thread(add_event)
                                                    |      +--> WorkspaceHandler: handle() [sync or async]
                                                    |
                                                    +--> asyncio.Event (_stop_event) -> drain -> close
```

### Pattern 1: Async MessageBus (put_nowait + async get)

**What:** emit() 签名为 async def 但内部使用 put_nowait() 不做实际 await。get() 使用 asyncio.wait_for 包装。
**When to use:** maxsize=0 无界队列场景，emit 不需要 backpressure。
**Rationale:** 方法签名为 async 满足 Protocol 类型检查，但 put_nowait 避免不必要的协程切换开销。

```python
import asyncio
from matmaster.types.events import BusEvent

class MessageBus:
    """Async event bus backed by asyncio.Queue."""

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=maxsize)

    async def emit(self, event: BusEvent) -> None:
        """Emit event (non-blocking for unbounded queue)."""
        self._queue.put_nowait(event)

    async def get(self, timeout: float | None = None) -> BusEvent:
        """Consume next event with optional timeout.

        Raises asyncio.TimeoutError if timeout expires.
        """
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout)

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

### Pattern 2: EventRouter Consume Loop (asyncio.Task + stop signal)

**What:** 使用 asyncio.create_task 启动消费循环，asyncio.Event 作为停止信号。
**Discretion resolution:** 推荐 wait_for+timeout 轮询模式（方案 A），而非 asyncio.wait 双任务模式（方案 B）。

**方案 A（推荐）：wait_for + timeout 轮询**

```python
async def _consume_loop(self) -> None:
    """Main consume loop -- runs as asyncio.Task."""
    while not self._stop_event.is_set():
        try:
            event = await self._bus.get(timeout=0.1)
            await self._dispatch(event)
        except asyncio.TimeoutError:
            continue
```

优势：
- 与当前同步模式（queue.get(timeout=0.1) + queue.Empty）结构完全对称，代码审查容易
- 无需管理额外的 stop_task 生命周期
- 0.1s 轮询间隔已被证明足够（当前生产环境即使用此间隔）

**方案 B（asyncio.wait 双任务）：**

```python
async def _consume_loop(self) -> None:
    while True:
        get_task = asyncio.create_task(self._bus._queue.get())
        stop_task = asyncio.create_task(self._stop_event.wait())
        done, pending = await asyncio.wait(
            {get_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for p in pending:
            p.cancel()
        if stop_task in done:
            break
        if get_task in done:
            await self._dispatch(get_task.result())
```

劣势：
- 每次循环创建 2 个 Task（额外 GC 压力）
- asyncio.Event.set() 后 stop_task 立即完成，需要额外处理已完成的 get_task（可能已拿到 event 但被丢弃）
- 经验证：当 stop 和 get 同时就绪时，asyncio.wait 可能在 done 中返回两者，需要额外逻辑处理

**结论：方案 A 更简单、更安全、与现有代码风格一致。**

### Pattern 3: Async Dispatch with Exception Isolation

**What:** _dispatch 改为 async def，逐个 await handler.handle()，异常不传播。

```python
async def _dispatch(self, event: BusEvent) -> None:
    handlers = self._handlers
    for handler in handlers:
        try:
            await handler.handle(event)
        except Exception:
            logger.warning(
                "Handler %s raised exception for event type=%s",
                type(handler).__name__,
                getattr(event, "type", "?"),
                exc_info=True,
            )
```

### Pattern 4: Graceful Stop + Drain

```python
async def stop(self, drain_timeout: float = 2.0) -> None:
    self._stop_event.set()

    if self._task is not None:
        await self._task  # wait for consume loop to exit
        self._task = None

    # Drain remaining events within deadline
    deadline = asyncio.get_event_loop().time() + drain_timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            event = self._bus.get_nowait()
            await self._dispatch(event)
        except asyncio.QueueEmpty:
            break

    await self._close_handlers()
```

### Anti-Patterns to Avoid
- **在 async def 中使用 queue.Queue:** asyncio.Queue 和 queue.Queue 不可混用。queue.Queue.get() 会阻塞 event loop。
- **asyncio.Event 跨 event loop:** asyncio.Event 绑定到创建它的 event loop，不能跨 loop 使用。EventRouter 的 _stop_event 必须在运行它的 loop 中创建。
- **忘记 drain 已完成 get_task:** 如果使用方案 B 的 asyncio.wait，cancel pending 时要注意 get_task 可能已完成但未被消费，事件会丢失。
- **handler.handle 并行执行:** 当前设计是顺序 dispatch（for handler in handlers: await handler.handle），不要改为 gather 并行。顺序执行保证 handler 间的执行顺序确定性（PersistenceHandler 先于 SSEHandler 完成是有意义的）。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| async 超时等待 | 手写 time.monotonic + polling | asyncio.wait_for(coro, timeout) | 标准原语，正确处理取消 |
| 后台任务生命周期 | 手动 loop.run_in_executor | asyncio.create_task + await task | 标准 Task 管理，异常传播正确 |
| 同步 I/O 不阻塞 | 手写 ThreadPoolExecutor | asyncio.to_thread(fn, *args) | Python 3.9+ 标准，自动管理线程池 |
| 停止信号 | 手写 flag + polling | asyncio.Event | 标准信号原语，await 语义正确 |

## Common Pitfalls

### Pitfall 1: service 层 bus.emit() 在同步函数中调用

**What goes wrong:** CONTEXT.md D-08 列出 matmaster/ 包内 13 个 emit 调用点（Hook + Compactor），但 agent_run_service.py 中还有 10 个 bus.emit() 调用点位于同步函数 run_agent_sync() 中。如果 MessageBus.emit() 改为 async def，这些同步调用点会报语法错误（不能在非 async 函数中 await）。

**Why it happens:** CONTEXT.md D-08 明确只列出 matmaster/ 包内的 12 个调用点（实际验证为 13 个），没有包含 service 层。D-07 说 service 层做最小桥接。

**How to avoid:** 有两个方案：
1. MessageBus.emit() 内部使用 put_nowait()（D-02 已决定），所以可以提供一个 sync wrapper `emit_sync()` 供 service 层使用，或
2. service 层直接调用 `bus._queue.put_nowait()` 或
3. 保留 put_nowait 作为独立同步方法

**推荐方案：** 添加 `emit_nowait(event)` 同步方法（直接调用 `self._queue.put_nowait(event)`）。service 层调用 emit_nowait()，matmaster/ 包内调用 await emit()。这与 D-01 不冲突（D-01 说不保留 sync 兼容接口，但 emit_nowait 是一个新的明确同步方法，不是 emit 的 sync 版本）。

**Warning signs:** 改完 MessageBus.emit 为 async 后，运行 pytest 时 agent_run_service.py import 不会报错（只有调用时才报），需要有 service 层集成测试覆盖。

### Pitfall 2: asyncio.Queue 必须在 event loop 上下文中创建

**What goes wrong:** asyncio.Queue() 在 Python 3.10+ 不再绑定特定 loop（PEP 594 deprecation），但在更老版本中可能有问题。

**Why it happens:** 项目要求 Python 3.10+，所以这不是实际问题。但如果 MessageBus 在模块级别创建（而非运行时），需要确认没有 loop 绑定问题。

**How to avoid:** MessageBus 在 Exp.assemble() 中运行时创建，不在模块级别。已确认安全。

**Warning signs:** "RuntimeError: no running event loop" 出现在 Queue 创建时。

### Pitfall 3: asyncio.QueueEmpty vs queue.Empty

**What goes wrong:** asyncio.Queue.get_nowait() 抛 asyncio.QueueEmpty，而 queue.Queue 抛 queue.Empty。现有测试和 drain 逻辑 catch queue.Empty 需要改为 asyncio.QueueEmpty。

**Why it happens:** 不同模块的异常类型不同。

**How to avoid:** 全局搜索 `queue.Empty`，替换为 `asyncio.QueueEmpty`。测试中同样替换。

**Warning signs:** `except queue.Empty` 不再捕获异常，drain 循环提前退出。

### Pitfall 4: EventRouter._close_handlers 需要改 async

**What goes wrong:** WorkspaceHandler.close() 调用 executor.shutdown(wait=True)，这是同步阻塞调用。如果 _close_handlers 不用 to_thread 包装，会阻塞 event loop。

**Why it happens:** handler.close() 当前是同步方法，部分 handler（如 WorkspaceHandler）在 close 中做 I/O。

**How to avoid:** _close_handlers 改为 async，对每个 handler 的 close() 使用 `await asyncio.to_thread(close)` 包装。或者检测 close 是否为 coroutine function 分别处理。

**推荐方案：** 由于 close() 调用发生在 stop() 流程尾部（已经在 drain 之后），使用 to_thread 包装是最安全的。

```python
async def _close_handlers(self) -> None:
    for handler in self._handlers:
        close = getattr(handler, "close", None)
        if not callable(close):
            continue
        try:
            if asyncio.iscoroutinefunction(close):
                await close()
            else:
                await asyncio.to_thread(close)
        except Exception:
            logger.warning(...)
```

### Pitfall 5: WorkspaceHandler 的 handle() 方法是否需要改 async

**What goes wrong:** EventRouter._dispatch 现在是 `await handler.handle(event)`。如果 WorkspaceHandler.handle() 保持 sync def，await 一个非 coroutine 会 TypeError。

**Why it happens:** EventHandler Protocol 声明 handle 为 async def，但 WorkspaceHandler 当前实现为 sync def handle()。Phase 12 的 Protocol hard cut 要求所有实现都满足 async Protocol。

**How to avoid:** WorkspaceHandler.handle() 必须改为 async def。内部逻辑（debounce check、snapshot、upload queue）全部是 CPU/内存操作或 ThreadPoolExecutor 提交，改 async 只需添加 async 关键字，内部无需 await（除非 snapshot 或 upload 改为 async）。

**推荐方案：** WorkspaceHandler.handle() 改为 async def，内部保持同步逻辑不变（snapshot 和 debounce 是纯 CPU 操作，upload 已经通过 ThreadPoolExecutor 异步提交）。这是本阶段应该一并完成的。

### Pitfall 6: 测试中 handler.handle() 调用方式

**What goes wrong:** 现有 55+ 个 handler 测试直接调用 `handler.handle(event)` 为同步调用。改为 async 后需要 `await handler.handle(event)` 或用 `asyncio.run(handler.handle(event))`。

**Why it happens:** Phase 12 配置了 pytest-asyncio asyncio_mode=auto，但现有测试是 sync def。

**How to avoid:** 测试方法改为 async def，pytest-asyncio auto 模式会自动处理。对于简单的 handler 单元测试，改为 `async def test_xxx` 即可。EventRouter 集成测试需要更大改造（内部 sleep/thread 逻辑全部改为 asyncio 原语）。

## Code Examples

### MessageBus 完整改造

```python
"""Async event bus backed by asyncio.Queue."""

import asyncio

from matmaster.types.events import BusEvent


class MessageBus:
    """异步事件总线。

    Agent kernel 调用 await emit() 发射 BusEvent，
    EventRouter 在 async task 中调用 await get() 消费事件。
    基于 asyncio.Queue，event loop 内单线程安全。
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=maxsize)

    async def emit(self, event: BusEvent) -> None:
        """发射事件（put_nowait，无界队列不阻塞）。"""
        self._queue.put_nowait(event)

    def emit_nowait(self, event: BusEvent) -> None:
        """同步发射事件。供 service 层同步代码使用。

        Phase 19 service 层 async 化后移除。
        """
        self._queue.put_nowait(event)

    async def get(self, timeout: float | None = None) -> BusEvent:
        """消费下一个事件（async 阻塞直到有事件或超时）。

        超时抛出 asyncio.TimeoutError。
        """
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout)

    def get_nowait(self) -> BusEvent:
        """非阻塞消费。队列为空时抛出 asyncio.QueueEmpty。"""
        return self._queue.get_nowait()

    @property
    def pending(self) -> int:
        """待消费事件数量（近似值）。"""
        return self._queue.qsize()

    @property
    def empty(self) -> bool:
        return self._queue.empty()
```

### EventRouter 改造骨架

```python
class EventRouter:
    """Async task consumer that dispatches events to handlers."""

    def __init__(self, bus: MessageBus, handlers: list[EventHandler]) -> None:
        self._bus = bus
        self._handlers = handlers
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._consume_loop(), name="event-router"
        )

    async def stop(self, drain_timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None
        # Drain
        loop = asyncio.get_running_loop()
        deadline = loop.time() + drain_timeout
        while loop.time() < deadline:
            try:
                event = self._bus.get_nowait()
                await self._dispatch(event)
            except asyncio.QueueEmpty:
                break
        await self._close_handlers()

    async def _consume_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                event = await self._bus.get(timeout=0.1)
                await self._dispatch(event)
            except asyncio.TimeoutError:
                continue

    async def _dispatch(self, event: BusEvent) -> None:
        handlers = self._handlers
        for handler in handlers:
            try:
                await handler.handle(event)
            except Exception:
                logger.warning(...)

    async def _close_handlers(self) -> None:
        for handler in self._handlers:
            close = getattr(handler, "close", None)
            if not callable(close):
                continue
            try:
                if asyncio.iscoroutinefunction(close):
                    await close()
                else:
                    await asyncio.to_thread(close)
            except Exception:
                logger.warning(...)
```

### SSEHandler 改造骨架

```python
class SSEHandler:
    """Pushes events to SSE send_cb -- pure async."""

    def __init__(
        self,
        send_cb: Callable,  # async callable
        session_id: str,
        task_id: str,
        invocation_id: str | None,
        mode: str,
    ) -> None:
        self._send_cb = send_cb
        # 删除 _loop 和 _is_async 字段
        self._session_id = session_id
        self._task_id = task_id
        self._invocation_id = invocation_id
        self._mode = mode

    async def handle(self, event: BusEvent) -> None:
        if self._should_skip(event):
            return
        raw = event.model_dump(mode='json')
        payload = build_public_sse_payload_from_bus_dump(
            raw,
            session_id=self._session_id,
            task_id=self._task_id,
            invocation_id=self._invocation_id,
            spawn_id=getattr(event, 'spawn_id', None),
        )
        await self._send_cb(payload)
```

**注意：** SSEHandler 构造函数签名变化（删除 loop 参数），service 层创建 SSEHandler 的代码需要同步更新。

### PersistenceHandler 改造骨架

```python
class PersistenceHandler:
    """Persists events to database -- async with to_thread for DB I/O."""

    async def handle(self, event: BusEvent) -> None:
        event_type = getattr(event, "type", "")
        if not self._should_persist_type(event_type):
            return
        if isinstance(event, (ThoughtEvent, ResponseEvent)) and event.stream_state in self._STREAMING_STATES:
            return

        payload = event.model_dump(mode="json")
        content = _public_content_for_event(event_type, payload)

        try:
            await asyncio.to_thread(
                self._events_table.add_event,
                self._session_id,
                event.source,
                event_type,
                content,
                task_id=self._task_id,
                invocation_id=self._invocation_id,
                spawn_id=getattr(event, "spawn_id", None),
            )
        except Exception:
            logger.error(...)
```

### Service 层桥接

```python
# agent_run_service.py 中 router.start()/stop() 改为桥接
from matmaster.core.agent import _sync_call_async

# 在 run_agent_sync 中：
_sync_call_async(router.start(), bridge_loop)
# ...
# finally:
_sync_call_async(router.stop(), bridge_loop)

# bus.emit 改为 bus.emit_nowait（service 层同步代码）
bus.emit_nowait(ErrorEvent(source='System', message=str(exc)))
```

## Discretion Resolutions

基于代码分析和运行时验证，以下是对 CONTEXT.md 中 Claude's Discretion 项的推荐：

### 1. EventRouter consume loop 实现方式
**推荐：** wait_for + timeout 轮询（方案 A）。原因见 Architecture Patterns 部分。

### 2. drain 逻辑中 get_nowait 的循环边界和 timeout 处理
**推荐：** 保持与当前代码相同的 deadline 模式。使用 `loop.time() + drain_timeout` 设置截止时间，循环调用 `get_nowait()` 直到 QueueEmpty 或超时。不使用 asyncio.wait_for 包装 drain（drain 中的每个 get_nowait 是瞬时的，不需要 async timeout）。

### 3. _close_handlers 是否需要改 async
**推荐：** 改为 async。WorkspaceHandler.close() 内部调用 `executor.shutdown(wait=True)` 是阻塞 I/O。使用 `asyncio.to_thread(close)` 包装所有 sync close，同时支持 async close。

### 4. 测试迁移范围和 async mock 策略
**推荐：**
- test_bus.py（8 tests）：全部改 async def，使用 asyncio.Queue 原语
- test_event_router.py 中 TestEventRouter（7 tests）：全部改 async，time.sleep 改 asyncio.sleep，threading 改 asyncio
- test_event_router.py 中 TestPersistenceHandler（14 tests）：handle 调用改 await，用 async def test
- test_event_router.py 中 TestSSEHandler（18 tests）：handle 调用改 await，send_cb 改 AsyncMock。删除 test_async_send_with_loop 和 test_sync_send_without_loop（双路径不再存在），替换为纯 async send_cb 测试
- conftest.py：无需改动（mock factories 已是 async）

### 5. WorkspaceHandler 是否需要在本阶段一并改 async handle()
**推荐：** 必须改。EventRouter._dispatch 使用 `await handler.handle(event)`，所有 handler 必须满足 async def handle() Protocol。WorkspaceHandler.handle() 只需添加 async 关键字，内部逻辑不变。

## emit 调用点完整清单

### matmaster/ 包内（CONTEXT.md D-08 范围，改为 await bus.emit）

| File | Line | Context | Change |
|------|------|---------|--------|
| matmaster/core/hooks.py | 199 | EventEmitterHook.pre_tool_call | `self._bus.emit(...)` -> `await self._bus.emit(...)` |
| matmaster/core/hooks.py | 212 | EventEmitterHook.post_tool_call | 同上 |
| matmaster/core/hooks.py | 227 | EventEmitterHook.on_stream_chunk (thought) | 同上 |
| matmaster/core/hooks.py | 238 | EventEmitterHook.on_stream_chunk (response) | 同上 |
| matmaster/core/hooks.py | 253 | EventEmitterHook.on_segment_complete (thought) | 同上 |
| matmaster/core/hooks.py | 266 | EventEmitterHook.on_segment_complete (response) | 同上 |
| matmaster/hooks/output_processor.py | 47 | post_tool_call (auto_save) | 同上 |
| matmaster/hooks/output_processor.py | 60 | post_tool_call (summarize) | 同上 |
| matmaster/hooks/confirmation.py | 62 | pre_tool_call (confirm request) | 同上 |
| matmaster/hooks/skill_hit.py | 42 | post_tool_call (skill_hit event) | 同上 |
| matmaster/hooks/assistant_state.py | 43 | pre_llm_call (state event) | 同上 |
| matmaster/core/context_compactor.py | 205 | compact_if_needed (tool_truncation) | 同上 |
| matmaster/core/context_compactor.py | 251 | compact_if_needed (summary/sliding_window) | 同上 |

Total: 13 个调用点（CONTEXT.md 说 12 个，实际为 13 个 -- EventEmitterHook.on_stream_chunk 有 ThoughtEvent 和 ResponseEvent 两个分支各一个 emit）。

### src/ service 层（D-07 范围，改为 bus.emit_nowait）

| File | Lines | Context | Change |
|------|-------|---------|--------|
| src/services/agent_run_service.py | 323,327,337 | _bohrium_event_cb 闭包 | `bus.emit(...)` -> `bus.emit_nowait(...)` |
| src/services/agent_run_service.py | 469,472 | cancelled post-processing | 同上 |
| src/services/agent_run_service.py | 485 | natural finish ResponseEvent | 同上 |
| src/services/agent_run_service.py | 491,492 | run_result + stream_closed | 同上 |
| src/services/agent_run_service.py | 525,526 | exception handler | 同上 |

Total: 10 个调用点。

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | pytest.ini (asyncio_mode=auto) |
| Quick run command | `uv run pytest tests/matmaster/core/test_bus.py tests/matmaster/integration/test_event_router.py -x -q` |
| Full suite command | `uv run pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INFR-01 | MessageBus async emit/get/get_nowait/pending/empty | unit | `uv run pytest tests/matmaster/core/test_bus.py -x` | Exists (8 tests, need async migration) |
| INFR-01 | MessageBus emit_nowait for service layer | unit | `uv run pytest tests/matmaster/core/test_bus.py -x` | Wave 0: add test_emit_nowait |
| INFR-02 | EventRouter async start/stop/dispatch/drain | integration | `uv run pytest tests/matmaster/integration/test_event_router.py::TestEventRouter -x` | Exists (7 tests, need async migration) |
| INFR-03 | PersistenceHandler async handle + to_thread DB write | unit | `uv run pytest tests/matmaster/integration/test_event_router.py::TestPersistenceHandler -x` | Exists (14 tests, need async migration) |
| INFR-03 | SSEHandler async handle, pure async send_cb | unit | `uv run pytest tests/matmaster/integration/test_event_router.py::TestSSEHandler -x` | Exists (18 tests, need async migration + delete 2 obsolete) |
| D-08 | emit callers in matmaster/ use await | integration | `uv run pytest tests/matmaster/ -x` | Covered by existing hook/compactor tests |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/core/test_bus.py tests/matmaster/integration/test_event_router.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- [ ] `tests/matmaster/core/test_bus.py` -- 8 tests 从 sync 迁移 async（queue.Empty -> asyncio.QueueEmpty, sync def -> async def）
- [ ] `tests/matmaster/core/test_bus.py::test_emit_nowait` -- 新增 emit_nowait 测试
- [ ] `tests/matmaster/integration/test_event_router.py::TestEventRouter` -- 7 tests 从 threading 迁移 asyncio（threading.Thread -> asyncio.Task, time.sleep -> asyncio.sleep）
- [ ] `tests/matmaster/integration/test_event_router.py::TestPersistenceHandler` -- 14 tests 改 async def + await handle
- [ ] `tests/matmaster/integration/test_event_router.py::TestSSEHandler` -- 18 tests 改 async def + await handle + AsyncMock send_cb。删除 test_async_send_with_loop 和 test_sync_send_without_loop，替换为纯 async test
- [ ] `tests/matmaster/integration/test_event_router.py::TestPublicContentForEvent` -- 11 tests 不需要改动（纯函数测试，不涉及 async）
- [ ] WorkspaceHandler.handle() async 化后，`tests/matmaster/integration/test_workspace_handler.py` 测试需要同步更新

**Baseline:** 当前 58 tests 全部 PASS (1.86s)。改造后应 >= 58 tests（新增 emit_nowait 测试，删除 2 个 obsolete 双路径测试，净变化 >=57）。

## Open Questions

1. **bridge_loop 的创建位置**
   - What we know: agent_run_service.py 调用 router.start()/stop() 需要 bridge_loop。当前 Kernel.run() 中创建 _bridge_loop。但 router.start() 在 Kernel.run() 之前调用（agent_run_service.py:301）。
   - What's unclear: service 层是否需要创建自己的 bridge_loop 用于 router.start()/stop()，还是复用 Kernel 的。
   - Recommendation: service 层创建独立的 bridge_loop 用于 router.start()/stop()。这与 Phase 13-15 的 _sync_call_async 模式一致。router 的 consume loop task 会在这个 loop 中运行。Kernel 内部的 _bridge_loop 是独立的（Phase 17 会移除）。但这意味着 MessageBus（asyncio.Queue）需要在 router 的 bridge_loop 所在的 event loop 中创建，**而 bus 在 Exp.assemble() 中创建，此时 bridge_loop 可能还不存在**。这是一个关键约束。
   - **Deep analysis:** asyncio.Queue 在 Python 3.10+ 不绑定 event loop（`_get_loop` 已移除），所以 Queue 可以在任何地方创建，只要 put_nowait/get 在同一个 event loop 中调用。put_nowait 不需要 running loop（它是同步的）。get() 在 EventRouter 的 async task 中调用，该 task 运行在 bridge_loop 上。所以：**bus 可以在 Exp.assemble() 中正常创建，不受 loop 约束**。

2. **SSEHandler 构造函数签名变化的影响**
   - What we know: SSEHandler 删除 loop 参数后，agent_run_service.py 创建 SSEHandler 的代码需要更新（:291-298）。
   - What's unclear: 这是否属于 Phase 16 的 D-07 最小桥接范围。
   - Recommendation: 属于。SSEHandler 构造函数变化是 INFR-03 的直接结果，service 层调用点必须同步更新。这是机械性改动（删除 loop 参数）。

## Sources

### Primary (HIGH confidence)
- Python asyncio documentation -- Queue, Event, Task, wait_for behavior
- Local runtime verification -- 4 个 asyncio 原语行为测试全部通过
- Source code analysis -- bus.py (50 lines), event_router.py (130 lines), sse_handler.py (117 lines), persistence_handler.py (81 lines), workspace_handler.py (184 lines)
- Source code analysis -- 13 emit callers in matmaster/, 10 emit callers in src/

### Secondary (MEDIUM confidence)
- Phase 12-15 CONTEXT.md -- 已建立的模式（_sync_call_async, asyncio.to_thread, async Protocol）

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 全部使用 Python 标准库 asyncio，无新依赖
- Architecture: HIGH -- 改造模式与 Phase 13-15 完全一致，asyncio 原语行为已验证
- Pitfalls: HIGH -- 通过源码审计发现 service 层 10 个额外 emit 调用点和 WorkspaceHandler handle() 必须改 async 两个关键遗漏

**Research date:** 2026-03-28
**Valid until:** 2026-04-28 (stable -- asyncio API 不会变)
