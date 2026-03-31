# Phase 15: Hook 系统异步化 - Research

**Researched:** 2026-03-27
**Domain:** Python asyncio -- Hook 实现 async 化、asyncio.Future 跨线程回复、run_* helper async 化
**Confidence:** HIGH

## Summary

Phase 15 的核心任务是将 5 个具体 Hook 实现和 7 个 run_* helper 函数从 sync 改为真正的 async def。这是 Phase 12 建立 async Protocol 签名后的"填充实现"阶段。当前状态：Hook Protocol 和 BaseHook 已经是 async def（Phase 12 完成），但 5 个具体实现（EventEmitterHook、ConfirmationHook、OutputProcessorHook、AssistantStateHook、SkillHitHook）仍然是 sync def，run_* helpers 也是 sync，导致调用链断裂（sync 函数调 async 方法不 await，返回的是 coroutine 对象而非实际值）。

技术复杂度集中在 ConfirmationHook：当前通过 `queue.Queue.get(timeout=N)` 阻塞等待用户回复，需要改为 `asyncio.Future + asyncio.wait_for()` 挂起等待。跨线程推送（src/ 层 HTTP handler 线程 -> Kernel event loop 线程）使用 `loop.call_soon_threadsafe(future.set_result, reply)` 解决。其余 4 个 Hook 只需在方法签名前加 `async`，内部逻辑不变（bus.emit() 是同步调用，Phase 16 才改 bus 为 async）。

**Primary recommendation:** 分两个 plan 执行。Plan 01 处理 run_* helpers async 化 + 4 个简单 Hook（签名改 async）+ Kernel 桥接。Plan 02 处理 ConfirmationHook Future 重构 + src/ 层调用点适配 + ReplyQueueLike 废弃。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** ConfirmationHook.pre_tool_call() 每次创建 asyncio.Future，通过 `await asyncio.wait_for(future, timeout)` 挂起等待用户回复。超时时 asyncio.TimeoutError -> 返回 HookAction.SKIP（映射当前 queue.Empty 行为）。
- **D-02:** 外部线程（src/ 服务层、Redis worker）通过 `loop.call_soon_threadsafe(future.set_result, reply)` 跨线程推送回复。取消时 `loop.call_soon_threadsafe(future.set_result, None)`。
- **D-03:** event loop 引用通过构造函数注入：`ConfirmationHook.__init__(loop: asyncio.AbstractEventLoop, ...)`。与 Kernel 的 _bridge_loop 一致。
- **D-04:** 废弃 ReplyQueueLike Protocol（hooks/ 版和 src/ 版都废弃）。ConfirmationHook 不再接收 reply_queue 参数，改为暴露 `resolve(reply: str)` 和 `cancel()` 方法。这两个方法内部使用 `loop.call_soon_threadsafe(future.set_result, ...)` 推送。
- **D-05:** src/ 层 agent_run_service.py 的调用点在 Phase 15 一并改动：`reply_queue.put_content(x)` -> `hook.resolve(x)`，`reply_queue.put_cancel()` -> `hook.cancel()`。改动量极小，不算服务层重构。
- **D-06:** 6 个 run_* helper 函数全部改为 async def，内部 await 每个 hook 的对应方法。run_guard_blocked 同理。
- **D-07:** Kernel 调用 run_* helpers 的位置改为 `_sync_call_async(run_pre_tool_call(hooks, tc), _bridge_loop)`，复用 Phase 13/14 建立的桥接模式。Phase 17 Kernel async 化时直接 await，去掉桥接。
- **D-08:** EventEmitterHook、OutputProcessorHook、AssistantStateHook、SkillHitHook 方法签名改 async def，但内部 `self._bus.emit(event)` 保持 sync 调用。Phase 16 改 MessageBus 为 async 时再统一改 await。
- **D-09:** OutputProcessorHook、AssistantStateHook、SkillHitHook 三个 hook 内部只做简单计算和 bus.emit()，直接改 async def 签名即可。EventEmitterHook 同理。

### Claude's Discretion
- ConfirmationHook.resolve()/cancel() 的具体实现细节（pending future 引用管理、多次调用防护）
- asyncio.TimeoutError 到 HookAction.SKIP 的错误消息格式
- run_* helpers 中对 HookAction/bool 返回值的短路逻辑在 async 下的实现方式
- 测试迁移范围和 async mock 策略
- EventEmitterHook 中 on_segment_complete 的向后兼容签名处理
- BaseHook 默认实现是否需要调整（当前已是 async def，应保持不变）

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HOOK-01 | 5 个具体 Hook 实现全部改为 async | CONTEXT.md 明确了 5 个实际 Hook（EventEmitter、Confirmation、OutputProcessor、AssistantState、SkillHit）。REQUIREMENTS.md 中的 HistoryHook/DirectHook 不存在于代码库中，以 CONTEXT.md 为准。D-08/D-09 决定了简单 Hook 直接改签名，bus.emit() 保持 sync。 |
| HOOK-02 | ConfirmationHook reply queue 适配 async | D-01~D-04 锁定了 asyncio.Future + wait_for + loop.call_soon_threadsafe 方案。D-05 锁定了 src/ 层调用点同步适配。研究已确认 _bridge_loop 在 Kernel.run() 中创建，ConfirmationHook 需要接收同一个 loop 引用。 |
| HOOK-03 | EventEmitterHook 适配 async MessageBus | D-08 锁定了过渡策略：async def 签名内调 sync bus.emit()，Phase 16 统一改 await。当前 MessageBus.emit() 是 queue.Queue.put()，几乎不阻塞，async def 内调 sync 完全安全。 |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| asyncio (stdlib) | Python 3.13.2 builtin | Future/wait_for/call_soon_threadsafe | 项目规范明确不引入 anyio/trio |
| pytest-asyncio | >= 0.25.0 (已安装) | async 测试运行 | asyncio_mode=auto 已配置在 pytest.ini |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| unittest.mock | stdlib | MagicMock for bus/hook | 所有 hook 测试中 mock MessageBus |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| asyncio.Future | asyncio.Event + shared state | Future 更直接映射"一次性回复"语义，Event 需要额外状态管理 |
| loop.call_soon_threadsafe | janus queue | janus 是额外依赖，REQUIREMENTS.md Out of Scope 明确排除 |
| asyncio.Queue | asyncio.Future | Queue 支持多次 put/get，但 ConfirmationHook 每次只需一个回复，Future 更精确 |

**Installation:**
```bash
# 无需新增依赖，所有所需包已安装
uv sync --extra dev
```

**Version verification:** Python 3.13.2 stdlib asyncio 完全支持 Future/wait_for/call_soon_threadsafe。pytest-asyncio 已在 pyproject.toml dev 依赖中。

## Architecture Patterns

### 改造目标文件结构
```
matmaster/
├── core/
│   ├── hooks.py           # run_* helpers: sync -> async def + await
│   │                      # EventEmitterHook: sync -> async def (bus.emit stays sync)
│   └── agent.py           # Kernel._run_loop: run_* 调用包裹 _sync_call_async
├── hooks/
│   ├── confirmation.py    # ConfirmationHook: 全面重构 (Future 模式)
│   ├── output_processor.py # async def 签名
│   ├── assistant_state.py  # async def 签名
│   └── skill_hit.py        # async def 签名
src/
├── services/
│   ├── agent_run_service.py # ReplyQueueLike 废弃, hook.resolve()/cancel() 调用
│   └── stream_service.py   # InMemoryReplyQueue/RedisReplyQueue/ReplyQueueNotifyOnGet 调用点适配
├── apis/
│   └── chat_api.py         # reply_queue.put_content() -> hook.resolve() 调用点
tests/
├── matmaster/
│   ├── core/test_hooks.py   # run_* helpers 和 EventEmitterHook 测试 -> async
│   ├── hooks/               # 4 个 hook 测试文件 -> async
│   └── integration/test_upstream_scenarios.py  # cross-pod 测试 -> 适配 Future
```

### Pattern 1: 简单 Hook async 化（4 个 Hook）
**What:** 方法签名前加 `async`，内部逻辑不变
**When to use:** Hook 方法内部只有纯计算和 sync 函数调用（bus.emit），无阻塞 I/O
**Example:**
```python
# Before (current)
class OutputProcessorHook(BaseHook):
    def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        self._bus.emit(...)  # sync call, nearly instant

# After
class OutputProcessorHook(BaseHook):
    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        self._bus.emit(...)  # sync call inside async def is perfectly valid
```
**Confidence:** HIGH -- Python 语义保证 async def 内调用 sync 函数完全合法，不需要 to_thread 包装（bus.emit 只是 queue.Queue.put，微秒级）。

### Pattern 2: run_* helper async 化
**What:** helper 函数改为 async def，内部 `for hook in hooks: await hook.method()`
**When to use:** 所有 7 个 run_* helpers
**Example:**
```python
# Before (current, broken -- doesn't await coroutine)
def run_pre_tool_call(hooks: list[Hook], tool_call: ToolCallData) -> HookAction:
    for hook in hooks:
        action = hook.pre_tool_call(tool_call)  # returns coroutine, not HookAction!
        if action == HookAction.SKIP:
            return HookAction.SKIP
    return HookAction.CONTINUE

# After (correct)
async def run_pre_tool_call(hooks: list[Hook], tool_call: ToolCallData) -> HookAction:
    for hook in hooks:
        action = await hook.pre_tool_call(tool_call)
        if action == HookAction.SKIP:
            return HookAction.SKIP
    return HookAction.CONTINUE
```
**Confidence:** HIGH -- 直接修复当前 broken 状态（sync 调 async 方法不 await）。

### Pattern 3: ConfirmationHook Future 模式
**What:** 每次 pre_tool_call 创建 asyncio.Future，await 等待回复，外部线程通过 loop.call_soon_threadsafe 推送
**When to use:** ConfirmationHook.pre_tool_call()
**Example:**
```python
class ConfirmationHook(BaseHook):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        bus: MessageBus,
        *,
        timeout_sec: int = 20,
        confirm_tools: set[str] | None = None,
        source: str = "MatMaster",
    ) -> None:
        self._loop = loop
        self._bus = bus
        self._timeout_sec = timeout_sec
        self._confirm_tools = confirm_tools
        self._source = source
        self._pending_future: asyncio.Future[str | None] | None = None

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        if self._confirm_tools is not None and tool_call.name not in self._confirm_tools:
            return HookAction.CONTINUE

        self._bus.emit(ConfirmationRequestEvent(
            source=self._source,
            question=f"Confirm tool call: {tool_call.name}?",
            mode="timeout",
            timeout_seconds=self._timeout_sec,
        ))

        future: asyncio.Future[str | None] = self._loop.create_future()
        self._pending_future = future
        try:
            reply = await asyncio.wait_for(future, timeout=self._timeout_sec)
        except asyncio.TimeoutError:
            logger.info("Confirmation timed out for tool %s", tool_call.name)
            return HookAction.SKIP
        finally:
            self._pending_future = None

        if reply is None:
            logger.info("User cancelled tool call %s", tool_call.name)
            return HookAction.SKIP
        return HookAction.CONTINUE

    def resolve(self, reply: str) -> None:
        """Thread-safe: resolve pending confirmation with user reply."""
        future = self._pending_future
        if future is not None and not future.done():
            self._loop.call_soon_threadsafe(future.set_result, reply)

    def cancel(self) -> None:
        """Thread-safe: cancel pending confirmation."""
        future = self._pending_future
        if future is not None and not future.done():
            self._loop.call_soon_threadsafe(future.set_result, None)
```
**Confidence:** HIGH -- asyncio.Future + call_soon_threadsafe 是 Python 官方文档推荐的跨线程 coroutine 唤醒模式。

### Pattern 4: Kernel 桥接扩展
**What:** Kernel 调用 run_* helpers 的位置使用 _sync_call_async 桥接
**When to use:** Phase 15 期间，Kernel 仍然是 sync（Phase 17 才改）
**Example:**
```python
# Before (current, broken)
run_pre_llm_call(spec.hooks, messages, turn)

# After (bridged)
_sync_call_async(run_pre_llm_call(spec.hooks, messages, turn), _bridge_loop)
```
**Confidence:** HIGH -- 复用 Phase 13/14 建立的 _sync_call_async 模式，agent.py 已有多处使用。

### Anti-Patterns to Avoid
- **在 async def 内使用 asyncio.to_thread 包装 bus.emit():** bus.emit() 是 queue.Queue.put()，微秒级完成，to_thread 的线程切换开销远超 emit 本身。直接调用即可。
- **创建新 event loop 给 ConfirmationHook:** 必须复用 Kernel 的 _bridge_loop，否则 call_soon_threadsafe 目标 loop 与 await 所在 loop 不一致，Future 永远等不到结果。
- **在 resolve()/cancel() 中直接调 future.set_result() 而不用 call_soon_threadsafe:** 这些方法从 src/ 层的 HTTP handler 线程调用，而 Future 绑定在 _bridge_loop 线程，直接调用会导致线程安全问题。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 跨线程 async 唤醒 | 自己管理 threading.Condition + asyncio.Event 组合 | asyncio.Future + loop.call_soon_threadsafe | 标准库原生支持，一行代码解决 |
| async 超时控制 | 手写 time.monotonic() + 定期检查 | asyncio.wait_for(future, timeout) | 标准库提供，自动抛 TimeoutError |
| sync-async 桥接 | 手写 threading + queue 通信 | _sync_call_async(coro, loop) | 项目已有成熟实现（Phase 13） |

**Key insight:** ConfirmationHook 的跨线程问题看似复杂，但 asyncio 标准库已经提供了完整的原语：Future（单次值传递）、call_soon_threadsafe（跨线程调度）、wait_for（超时控制）。不需要引入任何第三方库。

## Common Pitfalls

### Pitfall 1: run_* helpers 当前已经 broken
**What goes wrong:** Phase 12 把 Hook Protocol 和 BaseHook 方法改成了 async def，但 run_* helpers 仍然是 sync，没有 await。调用 `hook.pre_tool_call(tc)` 返回的是 coroutine 对象，不是 HookAction。`action == HookAction.SKIP` 永远为 False（coroutine != enum value）。
**Why it happens:** Phase 12 有意保留 run_* helpers 为 sync（分阶段迁移），但 5 个具体 Hook 实现也保持了 sync（sync def override async def parent 在 Python 中合法），所以当前实际不会触发这个问题。但一旦任何 hook 实现改成 async def，run_* helpers 必须同步改为 async def + await。
**How to avoid:** run_* helpers 的 async 化必须在同一个 plan 中与 hook 实现的 async 化同步完成。不能先改 hook 再改 helper，否则中间状态会导致生产环境 bug。
**Warning signs:** 测试中发现 coroutine 对象被当作 HookAction/bool 比较。

### Pitfall 2: ConfirmationHook 的 _pending_future 多次调用防护
**What goes wrong:** 如果用户在同一时间快速提交两次 reply（网络重传、前端重复点击），resolve() 可能被调用两次。第二次调 `future.set_result()` 在 future 已经 done 的情况下会抛 `asyncio.InvalidStateError`。
**Why it happens:** HTTP API 是无状态的，同一个 confirmation 可能收到多次 reply。
**How to avoid:** resolve()/cancel() 中检查 `if future is not None and not future.done()` 再调用 set_result。这个检查在 CONTEXT.md D-04 的 Claude's Discretion 中已经标记。
**Warning signs:** 日志中出现 InvalidStateError 异常。

### Pitfall 3: Kernel _bridge_loop 生命周期与 ConfirmationHook loop 引用
**What goes wrong:** _bridge_loop 在 Kernel.run() 开始时创建，结束时 close()。如果 ConfirmationHook 持有的 loop 引用在 Kernel.run() 结束后仍被使用（比如延迟的 cancel() 调用），loop.call_soon_threadsafe 会抛 RuntimeError（"Event loop is closed"）。
**Why it happens:** src/ 层的 HTTP handler 线程可能在 Kernel.run() 返回后才收到 cancel 请求。
**How to avoid:** resolve()/cancel() 中额外检查 `loop.is_closed()`，或者在 Kernel 结束后将 _pending_future 置为 None。由于 Kernel 结束意味着 agent 执行结束，此时的 resolve/cancel 本身就没有意义，静默忽略即可。
**Warning signs:** agent 运行结束后日志中出现 RuntimeError("Event loop is closed")。

### Pitfall 4: run_on_segment_complete 的 getattr 向后兼容逻辑
**What goes wrong:** 当前 run_on_segment_complete 和 run_guard_blocked 使用 `getattr(hook, "on_segment_complete", None)` 做向后兼容检查。改为 async 后，getattr 返回的函数需要被 await。
**Why it happens:** 旧 Hook 实现可能没有 on_segment_complete/on_guard_blocked 方法。
**How to avoid:** async 化后保持 getattr 检查逻辑，但对返回的函数用 `await fn(...)` 调用。由于 Hook Protocol 现在已定义了全部 7 个方法，且 BaseHook 提供默认实现，实际上不应该存在缺少方法的 Hook 实现。可以考虑移除 getattr 检查，直接 `await hook.on_segment_complete(...)`，简化代码。但保险起见可保留。
**Warning signs:** TypeError: object NoneType can't be used in 'await' expression（如果 getattr 返回 None 然后尝试 await）。

### Pitfall 5: 测试中 sync hook override 的隐蔽问题
**What goes wrong:** 当前测试文件中大量使用 sync def 覆盖 BaseHook async def 方法的自定义 Hook 类（如 SkipHook、StopHook、TrackingHook）。Phase 12 时这是有意为之（transition period），但 Phase 15 改 run_* helpers 为 async + await 后，这些 sync override 返回的不是 coroutine，`await` 一个普通值会抛 TypeError。
**Why it happens:** Python 允许 sync def 覆盖 async def parent 方法，不报错。但 `await sync_function_result` 需要结果是 awaitable。
**How to avoid:** 测试中的自定义 Hook 类必须同步改为 async def，或者改用 conftest.py 中的 MockAsyncHook 模式。
**Warning signs:** pytest 中 TypeError: object HookAction can't be used in 'await' expression。

### Pitfall 6: src/ 层 ReplyQueueLike 废弃的影响范围
**What goes wrong:** ReplyQueueLike 不只在 agent_run_service.py 定义，stream_service.py 也大量使用。InMemoryReplyQueue、RedisReplyQueue、ReplyQueueNotifyOnGet 都实现了 ReplyQueueLike。废弃后这些类的调用点需要适配。
**Why it happens:** ReplyQueueLike 是跨两层（matmaster hooks/ 和 src/ services/）的共享协议。
**How to avoid:** 需要明确哪些 src/ 层的代码需要改动。关键路径：
  1. chat_api.py: `reply_queue.put_content()` -> `hook.resolve()`、`reply_queue.put_cancel()` -> `hook.cancel()`
  2. stream_service.py: StreamQueueManager 的 reply_queue 管理需要替换为 ConfirmationHook 引用管理
  3. agent_worker.py: Redis worker 路径中 RedisReplyQueue 的使用需要适配
**Warning signs:** import error 或 AttributeError（调用不存在的 put_content 方法）。

## Code Examples

### Example 1: run_should_continue async 化（intercepting hook，短路语义）
```python
# Source: matmaster/core/hooks.py (改造后)
async def run_should_continue(
    hooks: list[Hook], messages: list[Message], turn: int
) -> bool:
    """Run should_continue on all hooks with short-circuit on False."""
    for hook in hooks:
        if not await hook.should_continue(messages, turn):
            return False
    return True
```

### Example 2: run_on_stream_chunk async 化（observation hook，无短路）
```python
# Source: matmaster/core/hooks.py (改造后)
async def run_on_stream_chunk(hooks: list[Hook], chunk: StreamChunk) -> None:
    """Run on_stream_chunk on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        await hook.on_stream_chunk(chunk)
```

### Example 3: Kernel 桥接调用
```python
# Source: matmaster/core/agent.py (改造后)
# Before:
run_pre_llm_call(spec.hooks, messages, turn)
# After:
_sync_call_async(run_pre_llm_call(spec.hooks, messages, turn), _bridge_loop)

# Before:
if not run_should_continue(spec.hooks, messages, turn):
# After:
if not _sync_call_async(run_should_continue(spec.hooks, messages, turn), _bridge_loop):

# Before:
action = run_pre_tool_call(spec.hooks, tc)
# After:
action = _sync_call_async(run_pre_tool_call(spec.hooks, tc), _bridge_loop)
```

### Example 4: EventEmitterHook async 化（最典型的简单 Hook）
```python
# Source: matmaster/core/hooks.py (改造后)
class EventEmitterHook(BaseHook):
    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self._bus.emit(ToolCallEvent(...))  # sync call, OK in async def
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        self._bus.emit(ToolResultEvent(...))  # sync call, OK in async def

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        if chunk.reasoning_content:
            self._bus.emit(ThoughtEvent(...))
        if chunk.content:
            self._bus.emit(ResponseEvent(...))

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None:
        # Same logic, just async def signature
        if segment_type == "thought":
            self._bus.emit(ThoughtEvent(...))
            return
        if segment_type == "response":
            self._bus.emit(ResponseEvent(...))

    async def on_guard_blocked(
        self, tool_call: ToolCallData, result: GuardResult
    ) -> None:
        pass  # No-op, same as before
```

### Example 5: 测试迁移模式（sync test -> async test）
```python
# Before (sync test)
class TestEventEmitterHook:
    def test_pre_tool_call_emits_event(self, sample_tool_call):
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1")
        result = hook.pre_tool_call(sample_tool_call)
        assert result == HookAction.CONTINUE

# After (async test, asyncio_mode=auto handles it)
class TestEventEmitterHook:
    async def test_pre_tool_call_emits_event(self, sample_tool_call):
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1")
        result = await hook.pre_tool_call(sample_tool_call)
        assert result == HookAction.CONTINUE
```

### Example 6: src/ 层调用点适配
```python
# Before (stream_service.py / chat_api.py)
reply_queue = stream_svc.get_reply_queue(sid)
if reply_queue is not None:
    reply_queue.put_content(content)

# After (Phase 15)
# ConfirmationHook 实例需要从某个注册表获取（替代 reply_queue）
confirmation_hook = stream_svc.get_confirmation_hook(sid)
if confirmation_hook is not None:
    confirmation_hook.resolve(content)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| queue.Queue 阻塞等待 | asyncio.Future + wait_for | Phase 15 | ConfirmationHook 不再阻塞线程 |
| ReplyQueueLike Protocol (两层重复定义) | ConfirmationHook.resolve()/cancel() 方法 | Phase 15 | 去除间接层，调用链更直接 |
| sync run_* helpers | async run_* helpers + _sync_call_async 桥接 | Phase 15 | 修复当前 broken 调用链 |

**Deprecated/outdated:**
- `matmaster/hooks/confirmation.py::ReplyQueueLike` Protocol -- Phase 15 废弃
- `src/services/agent_run_service.py::ReplyQueueLike` Protocol -- Phase 15 废弃
- `src/services/stream_service.py::InMemoryReplyQueue` -- 需要适配或重构为直接持有 ConfirmationHook 引用
- `src/services/stream_service.py::ReplyQueueNotifyOnGet` -- 包装层，可能不再需要

## Open Questions

1. **src/ 层 ReplyQueue 管理器的完整适配范围**
   - What we know: chat_api.py 和 stream_service.py 中有大量 reply_queue 相关代码。agent_run_service.py 当前 ConfirmationHook 被注释掉了（第 420 行 TODO）。
   - What's unclear: StreamQueueManager 中的 `_reply_queues: dict[str, ReplyQueueLike]` 需要替换为什么？是改为 `dict[str, ConfirmationHook]` 还是保留 queue 机制只在 ConfirmationHook 内部适配？
   - Recommendation: 由于 ConfirmationHook 暴露了 resolve()/cancel() 方法，StreamQueueManager 应该改为管理 ConfirmationHook 引用。但这涉及 src/ 层的改动范围评估。D-05 决定"改动量极小"，但实际上 stream_service.py 中的 reply_queue 管理代码相当多。建议 Plan 02 中仔细评估。

2. **RedisReplyQueue (Worker 模式) 的适配**
   - What we know: 生产环境使用 Redis 做跨 Worker 通信。RedisReplyQueue 通过 Redis List BLPOP 实现阻塞等待。
   - What's unclear: ConfirmationHook 的 resolve()/cancel() 是通过 loop.call_soon_threadsafe 推送到 Kernel 所在的 event loop。在 Worker 模式下，POST /confirmation_reply 可能打到不同的 Worker 进程。这时候 resolve() 无法直接调用（不同进程没有共享内存）。
   - Recommendation: Worker 模式下，需要保留 Redis List 作为跨进程通信通道。ConfirmationHook 内部可以启动一个 background task 监听 Redis List，收到消息后调用 self.resolve()。或者保留一个轻量级的 Redis polling 机制。这个问题在 D-05 中没有被完全覆盖。建议在 Plan 02 的 ConfirmationHook 重构中处理。

3. **ConfirmationHook 当前被注释掉的 TODO**
   - What we know: agent_run_service.py 第 420 行 `# ConfirmationHook(reply_queue, bus),` 被注释掉了，有 TODO 说"re-enable with confirm_tools=<async MCP tools> once MCP registration lands"。
   - What's unclear: 这意味着当前生产环境可能没有实际使用 ConfirmationHook。如果是这样，Phase 15 的 ConfirmationHook 改造风险更低（不影响生产），但也意味着 end-to-end 测试更难验证。
   - Recommendation: 改造 ConfirmationHook 时仍然按照它会被使用的假设设计，但注意 agent_run_service.py 中的注释行也需要更新（改为新的构造方式）。

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >= 9.0.2 + pytest-asyncio >= 0.25.0 |
| Config file | pytest.ini (asyncio_mode=auto) |
| Quick run command | `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HOOK-01 | run_* helpers async + 4 个简单 Hook async | unit | `uv run pytest tests/matmaster/core/test_hooks.py -x` | Exists, needs async migration |
| HOOK-01 | OutputProcessorHook async | unit | `uv run pytest tests/matmaster/hooks/test_output_processor.py -x` | Exists, needs async migration |
| HOOK-01 | AssistantStateHook async | unit | `uv run pytest tests/matmaster/hooks/test_assistant_state.py -x` | Exists, needs async migration |
| HOOK-01 | SkillHitHook async | unit | `uv run pytest tests/matmaster/hooks/test_skill_hit.py -x` | Exists, needs async migration |
| HOOK-02 | ConfirmationHook Future 模式 | unit | `uv run pytest tests/matmaster/hooks/test_confirmation.py -x` | Exists, needs full rewrite |
| HOOK-02 | Cross-pod reply queue (upstream) | integration | `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py::TestCrossPodReplyQueue -x` | Exists, needs adaptation |
| HOOK-03 | EventEmitterHook async (bus.emit sync) | unit | `uv run pytest tests/matmaster/core/test_hooks.py::TestEventEmitterHook -x` | Exists, needs async migration |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ -x`
- **Per wave merge:** `uv run pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [x] Framework install: pytest-asyncio already installed, asyncio_mode=auto configured
- [x] tests/conftest.py: MockAsyncHook already exists (Phase 12)
- [ ] tests/matmaster/core/test_hooks.py: TrackingHook/SkipHook/StopHook 等自定义 Hook 类需要改为 async def -- 当前是 sync override
- [ ] tests/matmaster/hooks/test_confirmation.py: 需要完全重写为 async（Future 模式测试，不再 mock reply_queue.get）
- [ ] tests/matmaster/integration/test_upstream_scenarios.py: TestCrossPodReplyQueue 需要适配（_MockReplyQueue 废弃，改为直接测试 resolve()/cancel()）

## Detailed Codebase Analysis

### 当前 Hook 调用链的完整路径
```
src/services/agent_run_service.py
  -> Kernel.run(spec, task, history, stop_event)
    -> Kernel._run_loop()
      -> run_pre_llm_call(spec.hooks, messages, turn)        # 7 个调用点
      -> run_should_continue(spec.hooks, messages, turn)
      -> Kernel._call_llm() -> _do_stream_llm()
        -> run_on_stream_chunk(spec.hooks, chunk)             # 多处
        -> run_on_segment_complete(spec.hooks, ...)           # 多处
      -> run_guard_blocked(spec.hooks, tc, guard_result)
      -> run_pre_tool_call(spec.hooks, tc)
      -> run_post_tool_call(spec.hooks, tc, tool_result)
```

### agent.py 中所有 run_* 调用点精确位置
| Line | Function | run_* helper | Hook 类型 |
|------|----------|-------------|-----------|
| 171 | _run_loop | run_pre_llm_call | observation |
| 174 | _run_loop | run_should_continue | intercepting |
| 226 | _run_loop | run_guard_blocked | observation |
| 240 | _run_loop | run_pre_tool_call | intercepting |
| 274 | _run_loop | run_post_tool_call | observation |
| 370 | _do_stream_llm | run_on_stream_chunk | observation |
| 380 | _do_stream_llm | run_on_stream_chunk | observation |
| 396 | _do_stream_llm | run_on_segment_complete | observation |
| 412 | _do_stream_llm | run_on_segment_complete | observation |
| 420 | _do_stream_llm | run_on_segment_complete | observation |
| 443 | _do_stream_llm (finally) | run_on_segment_complete | observation |
| 450 | _do_stream_llm (finally) | run_on_segment_complete | observation |
| 456 | _do_stream_llm (finally) | run_on_stream_chunk | observation |

共 13 个调用点，全部需要包裹 `_sync_call_async(..., _bridge_loop)`。

### ConfirmationHook 的 loop 注入路径
```
Kernel.run()
  -> _bridge_loop = asyncio.new_event_loop()  # 创建 loop
  -> _run_loop(..., _bridge_loop)
       # ConfirmationHook 需要在 _bridge_loop 创建之后构造
       # 但当前 Hook 在 Exp.build_runtime() 阶段组装
       # Exp.build_runtime() 在 Kernel.run() 之前执行
       # 所以 ConfirmationHook 在构造时拿不到 _bridge_loop
```

**关键问题:** ConfirmationHook 在 Exp.build_runtime() 或 agent_run_service.py 中创建，但 _bridge_loop 在 Kernel.run() 中才创建。时序不匹配。

**解决方案（两种）：**
1. **延迟注入:** ConfirmationHook.__init__ 不传 loop，提供 `set_loop(loop)` 方法。Kernel.run() 创建 _bridge_loop 后，遍历 spec.hooks 找到 ConfirmationHook 实例并注入 loop。
2. **提前创建 loop:** 在 agent_run_service.py 层创建 _bridge_loop，传给 ConfirmationHook 构造函数，再传给 Kernel.run()。

方案 1 更符合当前架构（Kernel 不应知道具体 Hook 类型），但需要在 Kernel 中加入 Hook 初始化步骤。
方案 2 更直接，但改变了 _bridge_loop 的创建位置。

**Recommendation:** 方案 1 更干净。Kernel.run() 开头加一个 `_inject_loop_to_hooks(spec.hooks, _bridge_loop)` 调用，通过 hasattr 检查 `set_loop` 方法存在性，避免 Kernel 依赖具体 Hook 类型。

### src/ 层 ReplyQueue 相关代码的完整影响分析

| 文件 | 相关代码 | 需要改动 | 说明 |
|------|---------|---------|------|
| agent_run_service.py:147-156 | ReplyQueueLike Protocol 定义 | 删除 | hooks/ 版同时废弃 |
| agent_run_service.py:213 | reply_queue: ReplyQueueLike 参数 | 保留参数名但类型改为 ConfirmationHook or None | 调用签名不变 |
| agent_run_service.py:420 | ConfirmationHook 注释行 | 更新构造方式 | 需要 loop 参数 |
| stream_service.py:131-149 | InMemoryReplyQueue 类 | 不删除 | 仍被 stream_service 内部使用，src/ 层的 queue 管理是独立关注点 |
| stream_service.py:152-173 | RedisReplyQueue 类 | 不删除 | Worker 模式仍需要 |
| stream_service.py:176-199 | ReplyQueueNotifyOnGet 类 | 评估是否还需要 | 如果 ConfirmationHook 直接暴露 resolve/cancel，通知逻辑可以内聚到 hook 中 |
| stream_service.py:202-241 | StreamQueueManager reply queue 管理 | 改为管理 ConfirmationHook 引用 | set_reply_queue -> set_confirmation_hook |
| chat_api.py:282-285 | reply_queue.put_cancel() | 改为 hook.cancel() | 直接替换 |
| chat_api.py:315-323 | reply_queue.put_content() | 改为 hook.resolve() | 直接替换 |
| agent_worker.py:181 | delete_confirmation_reply_list | 保留 | Redis 清理不变 |
| agent_worker.py:196 | RedisReplyQueue 创建 | 需要评估 | Worker 路径需要特殊处理 |

**重要发现:** src/ 层的 ReplyQueue 管理比预期复杂。特别是 Worker 模式下的 Redis 路径。D-05 说"改动量极小"，但实际上 stream_service.py 中有大量 reply_queue 管理代码。建议 Phase 15 只改 matmaster/ 层（hooks 实现 + run_* helpers + Kernel 桥接），src/ 层的 ReplyQueue 适配作为 Phase 15 的"最小化适配"只改 agent_run_service.py 中的直接调用点，stream_service.py 的深层重构留给后续阶段。

## Sources

### Primary (HIGH confidence)
- 项目源码 matmaster/core/hooks.py -- Hook Protocol/BaseHook/run_* helpers 当前实现
- 项目源码 matmaster/hooks/*.py -- 5 个具体 Hook 实现当前状态
- 项目源码 matmaster/core/agent.py -- Kernel run_* 调用点和 _sync_call_async 桥接
- 项目源码 src/services/agent_run_service.py -- ReplyQueueLike 定义和使用
- 项目源码 src/services/stream_service.py -- InMemory/Redis ReplyQueue 实现
- 项目源码 tests/ -- 所有现有 hook 测试文件

### Secondary (MEDIUM confidence)
- Python 3.13 asyncio 文档: Future, wait_for, call_soon_threadsafe 用法
- .planning/phases/15-hook/15-CONTEXT.md -- 用户决策（D-01 ~ D-09）

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 纯 stdlib asyncio，无外部依赖
- Architecture: HIGH -- 基于项目已建立的 Phase 13/14 桥接模式，模式成熟
- Pitfalls: HIGH -- 通过代码阅读直接发现，特别是 run_* helpers 的 broken 状态和 loop 时序问题
- ConfirmationHook 重构: MEDIUM -- loop 注入时序需要在实现时验证，src/ 层影响范围可能比 D-05 预估更大

**Research date:** 2026-03-27
**Valid until:** 2026-04-27 (stable domain, asyncio API 稳定)
