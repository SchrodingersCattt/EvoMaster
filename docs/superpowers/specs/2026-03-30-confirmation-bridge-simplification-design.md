# Confirmation Bridge Simplification

## Problem

`_start_confirmation_reply_bridge` 在 `agent_run_service.py` 中创建一个 daemon 线程，在整个 `run_agent_sync` 生命周期内持续轮询 `ReplyQueue.get(timeout=1)`，将结果转发给 `ConfirmationHook.resolve()`/`cancel()`。为了处理 bridge 线程和 `pre_tool_call` 之间的竞态（bridge 可能在 Future 创建前就拿到了回复），`ConfirmationHook` 引入了 `_buffered_reply`、`_state_lock`、`_pending_future` 三重机制。

这导致了两个问题：

1. **ConfirmationHook 承担了过多基础设施职责**：`set_loop()`、`resolve()`/`cancel()` 公开方法、线程安全锁、缓冲区——与其他 hook（如 EventEmitterHook）的简洁风格不一致
2. **`agent_run_service.py` 需要手动管理 bridge 线程生命周期**：`confirmation_reply_stop`/`confirmation_reply_thread` 变量、finally 块中的 `stop_event.set()` + `thread.join(timeout=2)` + 未退出告警

## Design Decisions

- **方案选择**：将 ConfirmationHook 改为依赖注入 async callable（方案 A 改进版），不引入 async ReplyQueue
- **职责划分**：ConfirmationHook 只负责 gate 决策（emit event → await reply → return action）；队列轮询逻辑留在 `agent_run_service.py` 作为 async callable 构造
- **轮询策略**：保持 1 秒间隔短轮询，通过 `loop.run_in_executor` 包装阻塞调用，`asyncio.wait_for` 控制整体超时。`poll_sec` 使用 `int` 而非 `float`，因为 `RedisReplyQueue.get()` 内部 `int(timeout)` 会将 0.5 截断为 0，导致 BLPOP 永久阻塞
- **线程回收**：`wait_for` 超时或 task 取消后，`CancelledError` 在 `await run_in_executor(...)` 处抛出，while 循环不会继续。executor 线程正在执行的 `queue.get(timeout=1)` 最多再等 1 秒后自然返回，返回值被丢弃（Future 已取消），线程池回收。超时后到达的回复会被丢弃，这是 by design 的行为（超时意味着用户未在规定时间内回复）
- **顺序确认安全性**：kernel 的 hook 执行是串行的（`run_pre_tool_call` 中 `for hook in hooks: await hook.pre_tool_call()`），同一时刻最多只有一个 `_poll_reply_queue` 在运行，不存在队列消费竞争
- **HTTP 端点兼容**：`chat_api.py` 通过 `ReplyQueueLike.put_content()`/`put_cancel()` 写入队列，`_poll_reply_queue` 通过 `ReplyQueueLike.get()` 读取队列，两侧接口不变，HTTP 端点不受影响
- **Scope**：不改动 ReplyQueue 实现、Redis DAO 层、HTTP 端点、Worker 路径

## Changes

### 1. ConfirmationHook Rewrite (`matmaster/hooks/confirmation.py`)

从 120 行缩减到约 40 行。删除所有线程桥接机制，改为接收 async callable：

```python
class ConfirmationHook(BaseHook):
    def __init__(
        self,
        bus: MessageBus,
        *,
        timeout_sec: float = 20,
        confirm_tools: set[str] | None = None,
        get_reply: Callable[[], Awaitable[str | None]],
        source: str = "MatMaster",
    ) -> None:
        self._bus = bus
        self._timeout_sec = timeout_sec
        self._confirm_tools = confirm_tools
        self._get_reply = get_reply
        self._source = source

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        if self._confirm_tools is not None and tool_call.name not in self._confirm_tools:
            return HookAction.CONTINUE

        await self._bus.emit(
            ConfirmationRequestEvent(
                source=self._source,
                question=f"Confirm tool call: {tool_call.name}?",
                mode="timeout",
                timeout_seconds=int(self._timeout_sec),
            )
        )

        try:
            reply = await asyncio.wait_for(self._get_reply(), timeout=self._timeout_sec)
        except asyncio.TimeoutError:
            logger.info("Confirmation timed out for tool %s", tool_call.name)
            return HookAction.SKIP

        if reply is None:
            logger.info("User cancelled tool call %s", tool_call.name)
            return HookAction.SKIP

        return HookAction.CONTINUE
```

**删除的成员**：
- `set_loop()` 方法
- `resolve()` / `cancel()` 公开方法
- `_deliver_reply()` 内部方法
- `_pending_future` / `_buffered_reply` / `_state_lock` / `_loop` / `_NO_REPLY` sentinel

### 2. Service Layer Adapter (`src/services/agent_run_service.py`)

新增模块级 async helper，替代 `_start_confirmation_reply_bridge`：

```python
async def _poll_reply_queue(
    reply_queue: ReplyQueueLike, poll_sec: int = 1
) -> str | None:
    """Await a blocking reply queue in executor. Returns content or None for cancel.

    Uses int poll_sec (not float) because RedisReplyQueue.get() coerces via
    int(timeout) -- 0.5 becomes 0, triggering BLPOP timeout=0 (block forever).
    """
    loop = asyncio.get_running_loop()
    while True:
        try:
            return await loop.run_in_executor(None, reply_queue.get, poll_sec)
        except Empty:
            continue
```

注意：参数名用 `reply_queue` 而非 `queue`，避免遮蔽 `import queue` 模块。`Empty` 从 `queue` 模块顶层导入（`from queue import Empty`）。

构造 hook 时注入：

```python
# Before (当前代码，约 15 行)
confirmation_hook = ConfirmationHook(bus=bus, confirm_tools=set(_CONFIRM_TOOLS))
confirmation_hook.set_loop(_loop)
confirmation_reply_stop, confirmation_reply_thread = (
    _start_confirmation_reply_bridge(reply_queue, confirmation_hook)
)

# After (约 5 行)
confirmation_hook = ConfirmationHook(
    bus=bus,
    confirm_tools=set(_CONFIRM_TOOLS),
    get_reply=lambda: _poll_reply_queue(reply_queue),
)
```

**删除**：
- `_start_confirmation_reply_bridge` 函数（34 行）
- `confirmation_reply_stop` / `confirmation_reply_thread` 变量声明
- finally 块中的 bridge 清理逻辑（6 行）

### 3. Delete `ConfirmationHookAdapter` (`src/services/stream_service.py`)

`ConfirmationHookAdapter`（约 30 行）直接调用 `hook.resolve()` 和 `hook.cancel()`。删除 `resolve()`/`cancel()` 后此类不再可用，且本次重构后也不再需要——确认回复直接通过 `ReplyQueueLike.put_content()`/`put_cancel()` 写入队列，`_poll_reply_queue` 消费。

### 4. Protocol Docstring Update (`src/services/agent_run_service.py`)

`ReplyQueueLike` Protocol 的 deprecated docstring 引用 Phase 15 的 `resolve()`/`cancel()` API，而这正是本次删除的东西。本次重构后 `ReplyQueueLike` 反而是核心接口（`_poll_reply_queue` 直接消费其 `.get()`），需要更新 docstring 移除 deprecated 标记。

### 5. Clean Up `set_loop` Injection (`matmaster/core/agent.py`)

`AgentKernel.run()` 中有通用的 `set_loop` 注入逻辑：
```python
for hook in spec.hooks:
    if hasattr(hook, "set_loop"):
        hook.set_loop(loop)
```
删除 `set_loop()` 后此代码不会报错（`hasattr` 检查会跳过），但如果没有其他 hook 使用 `set_loop`，应一并清理这段代码。

### 6. Tests

**重写 `tests/matmaster/hooks/test_confirmation.py`**（当前 7 个测试用例全部依赖 `set_loop()`、`resolve()`、`cancel()`）：

- `get_reply` 返回字符串 -> `HookAction.CONTINUE`
- `get_reply` 返回 `None` -> `HookAction.SKIP`
- `get_reply` 超时 -> `HookAction.SKIP`
- `confirm_tools` 过滤：不在集合中的 tool 直接 CONTINUE，不调用 `get_reply`
- 验证 `ConfirmationRequestEvent` 被 emit

**新增 `_poll_reply_queue` 单元测试**：

- 正常回复返回内容
- `Empty` 超时后重试直到收到回复
- 配合 `asyncio.wait_for` 超时时抛出 `TimeoutError`

**更新 `tests/matmaster/integration/test_upstream_scenarios.py`**：

- 替换 `test_confirmation_reply_bridge_thread_exits_with_redis_compatible_timeout`（直接导入 `_start_confirmation_reply_bridge`）为 `_poll_reply_queue` 集成测试
- 验证两个端到端测试（`test_run_agent_sync_approval_executes_gated_tool`、`test_run_agent_sync_cancel_skips_gated_tool`）在新构造方式下仍通过

**删除 `TestConfirmationHookAdapter`** 相关测试（随 `ConfirmationHookAdapter` 一起移除）

## Summary

| 维度 | Before | After |
|------|--------|-------|
| ConfirmationHook 行数 | ~120 | ~40 |
| 公开方法 | `set_loop`, `resolve`, `cancel`, `pre_tool_call` | `pre_tool_call` |
| 线程管理 | bridge thread + stop_event + join | 无（executor 自动回收） |
| 竞态处理 | `_buffered_reply` + `_state_lock` | 无（单条代码路径） |
| agent_run_service 涉及变量 | 4 个 + finally 清理 | 0 个 |
| 改动文件 | 2 源码 + 1 测试 | 4 源码 + 2 测试 |
| 净行数变化 | — | 约 -100 行 |
