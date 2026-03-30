# Confirmation Bridge Simplification

## Problem

`_start_confirmation_reply_bridge` 在 `agent_run_service.py` 中创建一个 daemon 线程，在整个 `run_agent_sync` 生命周期内持续轮询 `ReplyQueue.get(timeout=1)`，将结果转发给 `ConfirmationHook.resolve()`/`cancel()`。为了处理 bridge 线程和 `pre_tool_call` 之间的竞态（bridge 可能在 Future 创建前就拿到了回复），`ConfirmationHook` 引入了 `_buffered_reply`、`_state_lock`、`_pending_future` 三重机制。

这导致了两个问题：

1. **ConfirmationHook 承担了过多基础设施职责**：`set_loop()`、`resolve()`/`cancel()` 公开方法、线程安全锁、缓冲区——与其他 hook（如 EventEmitterHook）的简洁风格不一致
2. **`agent_run_service.py` 需要手动管理 bridge 线程生命周期**：`confirmation_reply_stop`/`confirmation_reply_thread` 变量、finally 块中的 `stop_event.set()` + `thread.join(timeout=2)` + 未退出告警

## Design Decisions

- **方案选择**：将 ConfirmationHook 改为依赖注入 async callable（方案 A 改进版），不引入 async ReplyQueue
- **职责划分**：ConfirmationHook 只负责 gate 决策（emit event → await reply → return action）；队列轮询逻辑留在 `agent_run_service.py` 作为 async callable 构造
- **轮询策略**：保持 1 秒间隔短轮询，通过 `loop.run_in_executor` 包装阻塞调用，`asyncio.wait_for` 控制整体超时
- **线程回收**：`wait_for` 超时或 task 取消后，executor 线程正在执行的 `queue.get(timeout=1)` 最多再等 1 秒后自然退出，线程池回收。无需手动管理
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
    queue: ReplyQueueLike, poll_sec: int = 1
) -> str | None:
    """Await a blocking reply queue in executor. Returns content or None for cancel."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            return await loop.run_in_executor(None, queue.get, poll_sec)
        except queue.Empty:
            continue
```

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

### 3. Protocol Cleanup (`src/services/agent_run_service.py`)

`ReplyQueueLike` Protocol 保持不变——它仍是 sync 协议，`_poll_reply_queue` 在 executor 中调用其 `.get()` 方法。

### 4. Tests

- `tests/matmaster/integration/test_upstream_scenarios.py`：更新直接导入 `_start_confirmation_reply_bridge` 的测试，改为测试 `_poll_reply_queue` 或通过 ConfirmationHook 的新构造方式集成测试
- `matmaster/hooks/confirmation.py` 的单元测试：验证 `get_reply` callable 的超时、取消、正常回复三种路径

## Summary

| 维度 | Before | After |
|------|--------|-------|
| ConfirmationHook 行数 | ~120 | ~40 |
| 公开方法 | `set_loop`, `resolve`, `cancel`, `pre_tool_call` | `pre_tool_call` |
| 线程管理 | bridge thread + stop_event + join | 无（executor 自动回收） |
| 竞态处理 | `_buffered_reply` + `_state_lock` | 无（单条代码路径） |
| agent_run_service 涉及变量 | 4 个 + finally 清理 | 0 个 |
| 改动文件 | 2 源码 + 1 测试 | — |
| 净行数变化 | 约 -80 行 | — |
