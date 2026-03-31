# Domain Pitfalls: Sync-to-Async Migration

**Domain:** Python agent framework sync-to-async conversion (matmaster v2.0)
**Researched:** 2026-03-26
**Overall Confidence:** HIGH (based on codebase analysis + official Python docs + community patterns)

---

## Critical Pitfalls

Mistakes that cause rewrites, deadlocks, or silent data corruption.

### Pitfall C1: runtime_checkable Protocol 不检查 async/sync 签名差异

**What goes wrong:** 将 Hook Protocol 和 LLMProvider Protocol 的方法改为 `async def` 后，`isinstance()` 仍然对实现了同名 sync 方法的旧类返回 True。runtime_checkable 只检查方法名是否存在，不检查方法签名是否是 coroutine function。一个实现了 sync `def chat()` 的旧 Provider 会通过 `isinstance(obj, LLMProvider)` 检查，但在 kernel 中 `await obj.chat()` 时得到的不是 coroutine 而是直接返回值，await 一个非 awaitable 对象会抛出 TypeError。

**Why it happens:** Python 的 `@runtime_checkable` Protocol 设计上只做 structural 存在性检查（属性/方法名存在），不检查类型签名。这是 PEP 544 的明确设计决策。对于 matmaster 来说，当前有 6 个 `@runtime_checkable` Protocol（LLMProvider, Hook, Guard, EventHandler, ReplyQueueLike, WorkerRegistry）和大量 mock 实现（MockLLMProvider, CompleteLLMProvider 等），它们全部需要同步更新。

**Consequences:**
- 所有 863 个测试中使用 sync mock 的测试会在 runtime 静默通过 isinstance 检查但在 await 时崩溃
- 第三方或上游 evomaster 代码如果实现了旧 sync Protocol，在 isinstance 检查时不会报错，只在实际调用时才暴露
- 错误消息 `TypeError: object LLMResponse can't be used in 'await' expression` 不直观，难以定位到 Protocol 不匹配

**Prevention:**
1. 在所有 Protocol 中 async 方法旁加 runtime validation helper：
```python
import inspect

def _validate_async_provider(obj: object) -> None:
    """Verify all Protocol methods are actually coroutines, not sync functions."""
    for method_name in ("chat", "chat_stream", "chat_with_retry"):
        method = getattr(obj, method_name, None)
        if method is not None and not inspect.iscoroutinefunction(method):
            raise TypeError(
                f"{type(obj).__name__}.{method_name} must be async, "
                f"got sync function"
            )
```
2. 在 Exp.build_runtime() 和 AgentKernel.__init__() 中加入 validation call
3. 在 conftest.py 中加一个 session-scope fixture 或 autouse fixture 验证所有 mock 是否满足 async Protocol
4. 考虑使用 ABC 而非 Protocol 来获得子类构造时的静态检查（但这破坏了 structural subtyping 的设计意图，权衡后建议保留 Protocol + runtime validation helper）

**Detection:** 搜索所有 `isinstance(xxx, LLMProvider)`, `isinstance(xxx, Hook)`, `isinstance(xxx, Guard)` 调用点，确保每个点之后都有 async validation。在 CI 中加一个测试专门验证所有已知 Provider/Hook 实现的方法是 coroutine function。

**Confidence:** HIGH -- 直接由 Python typing 规范确认 (PEP 544)，且已在 codebase 中确认 6 个 runtime_checkable Protocol。

**Phase mapping:** 最早阶段（Protocol 定义改造阶段）必须解决。

---

### Pitfall C2: asyncio.run() 嵌套导致 RuntimeError

**What goes wrong:** 在已有 event loop 运行的环境中调用 `asyncio.run()` 会抛出 `RuntimeError: asyncio.run() cannot be called from a running event loop`。matmaster 有两个关键的嵌套场景：

1. **DevShell REPL:** 当前 DevShell 计划用 `asyncio.run()` 包装调用 async matmaster（PROJECT.md 明确说了 DevShell async 改造延后，用 asyncio.run() wrapper）。但如果 REPL 本身在某个 async 上下文中运行（比如 IPython/Jupyter），就会嵌套失败。
2. **src/ service layer:** `agent_run_service.py` 中的 `run_agent_sync()` 在 ThreadPoolExecutor 线程中同步执行 kernel。如果 matmaster/ 层变成 async，service 层需要在线程中运行 event loop 来调用 async matmaster -- 这需要在该线程中创建新的 event loop（可以，因为线程没有 running loop），但不能复用主线程的 event loop。
3. **SubAgent spawn:** 当前 spawn_fn 是同步闭包。改为 async 后，如果 parent agent 已在 event loop 中运行，spawn_fn 作为 coroutine 可以直接 await -- 但如果有人在同步上下文中调用 spawn_fn 则会嵌套。

**Why it happens:** Python 3.10+ 强制每线程最多一个运行中的 event loop。`asyncio.run()` 会创建新 loop 并运行直到 complete，但如果当前线程已有 running loop 则拒绝执行。

**Consequences:**
- DevShell 在 Jupyter/IPython 环境中完全无法使用
- 如果 service 层错误地在 main thread（已有 FastAPI event loop）中直接调用 asyncio.run()，整个 API 服务崩溃
- nest_asyncio 虽能绕过但会掩盖架构问题，不推荐在生产中使用

**Prevention:**
1. **DevShell wrapper 明确使用 `asyncio.run()` 但只在 main thread 无 running loop 时调用：**
```python
def run_sync_wrapper(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        # Already in async context -- cannot nest
        raise RuntimeError(
            "DevShell cannot run inside an existing event loop. "
            "Use 'await runner.run_async(task)' directly."
        )
```
2. **src/ service layer 保持 ThreadPoolExecutor 模式，在 worker 线程中用 `asyncio.run()`：**
   - 每个 worker 线程天然没有 running loop，所以 `asyncio.run()` 安全
   - 关键是不要在 FastAPI 的 event loop 线程中直接调用
3. **SubAgent spawn 作为 async coroutine 直接 await，不需要 asyncio.run()：**
   - 因为 parent kernel 已经是 async，spawn_fn 变成 async 后直接 `result = await spawn_fn(...)` 即可

**Detection:** grep 所有 `asyncio.run(` 调用，确认每一个都在确定没有 running loop 的上下文中。

**Confidence:** HIGH -- Python 官方文档明确记录。

**Phase mapping:** Integration boundary 阶段（src/ 层适配 + DevShell wrapper）。

---

### Pitfall C3: 在 async 代码中调用阻塞 I/O，冻结 event loop

**What goes wrong:** 将 AgentKernel.run() 改为 async 后，如果内部仍然调用同步阻塞操作（file I/O, subprocess.run, time.sleep, sync HTTP），整个 event loop 被阻塞。matmaster 当前的阻塞点包括：

| 阻塞调用 | 位置 | 影响范围 |
|---------|------|---------|
| `session.exec_bash()` | BashTool._execute | 每次 bash 命令阻塞整个 loop |
| `subprocess.run()` | evomaster session 内部 | bash 执行 |
| `open().read()` / `Path.read_text()` | ReadTool, WriteTool, EditTool, GlobTool, GrepTool | 文件操作 |
| `time.sleep(backoff)` | OpenAIProvider.chat_with_retry, AgentKernel._call_llm | LLM 重试 |
| `self._client.chat.completions.create()` | OpenAIProvider.chat/chat_stream | 同步 OpenAI SDK |
| `self._summary_provider.chat()` | ContextCompactor._summarize | 压缩用 LLM 调用 |
| `json.loads(config_path.read_text())` | Exp._init_skill_tools | 配置加载 |
| `connector.cleanup()` | LazyMCPConnector | MCP 清理 |

**Why it happens:** async/await 只是协作式并发，没有抢占。一个不 yield 的同步调用会 hold 住 event loop 线程，导致所有其他 coroutine（包括 SSE 流、timeout 检测、其他 agent 的执行）全部停滞。

**Consequences:**
- 如果多个 agent 共享一个 event loop（多 agent 编排的目标），一个 agent 的 bash 命令会冻结所有 agent
- SSE 流心跳丢失，前端判定连接超时
- stop_event（改为 asyncio.Event 后）无法被检测到，取消操作失效
- 对单 agent 场景影响较小（因为 agent loop 本身是顺序执行的），但对未来多 agent 场景是致命的

**Prevention:**
1. **Phase 1 必须改的（高优先级）：**
   - OpenAI SDK: `openai.OpenAI` -> `openai.AsyncOpenAI`，所有 API 调用变 async
   - `time.sleep()` -> `await asyncio.sleep()`
   - ContextCompactor._summarize: 改用 async LLM provider
2. **Phase 2 改的（中优先级）：**
   - BashTool: `subprocess.run` -> `asyncio.create_subprocess_exec`
   - File I/O tools: 使用 `await asyncio.to_thread(Path.read_text, ...)` 或 aiofiles
3. **可延后的（低优先级）：**
   - 配置文件加载（一次性，启动时）
   - MCP connector cleanup
4. **Blocking detector for development:**
```python
import asyncio
import time

class BlockingDetector:
    """Warn when event loop is blocked for too long."""
    def __init__(self, threshold: float = 0.1):
        self._threshold = threshold

    async def monitor(self):
        while True:
            t0 = time.monotonic()
            await asyncio.sleep(0)  # yield to loop
            elapsed = time.monotonic() - t0
            if elapsed > self._threshold:
                logger.warning(
                    "Event loop blocked for %.3fs (threshold=%.3fs)",
                    elapsed, self._threshold
                )
```

**Detection:** 使用 asyncio debug mode (`PYTHONASYNCIODEBUG=1`) 自动检测超过 100ms 的阻塞调用。或者在 CI 中 enable asyncio debug mode 运行测试。

**Confidence:** HIGH -- asyncio 基础机制，官方文档明确警告。

**Phase mapping:** 贯穿所有阶段，但 LLM 和 sleep 必须在第一阶段完成。

---

### Pitfall C4: threading.Event vs asyncio.Event -- stop_event 跨线程问题

**What goes wrong:** 当前 `AgentKernel.run()` 接受 `threading.Event` 作为 `stop_event`。改为 async 后如果换成 `asyncio.Event`，而 src/ service 层仍然在另一个线程中通过 `stop_event.set()` 来取消 agent -- `asyncio.Event` 不是线程安全的，跨线程 set() 行为未定义（可能丢失信号，可能引发 RuntimeError）。

**Why it happens:** `asyncio.Event` 是为单线程 event loop 内的 coroutine 间通信设计的。它内部使用的条件变量机制依赖于 event loop 的调度，不支持跨线程操作。而 `threading.Event` 是内核级线程同步原语，天然线程安全。

matmaster 的 stop_event 使用链：
```
src/services/agent_run_service.py (thread A)
  -> stop_event.set()  # 来自 Redis 轮询或 API cancel 请求
  -> AgentKernel.run() (thread B / event loop)
     -> checks stop_event.is_set()
     -> SpawnTool._stop_event (same context as kernel)
```

**Consequences:**
- 取消信号丢失：agent 无法停止，用户按取消无反应
- 竞态条件：偶发的 `RuntimeError` 在高并发时出现
- 子 agent 级联取消失效

**Prevention:**
1. **保留 threading.Event 作为跨线程信号：** kernel async 代码中用 `stop_event.is_set()`（非阻塞，线程安全）来轮询检查。
2. **不要替换为 asyncio.Event：** 在 async kernel 中，每次 loop iteration 开头检查 threading.Event：
```python
async def run(self, spec, task, *, stop_event=None):
    while turn < spec.max_turns:
        if stop_event and stop_event.is_set():  # threading.Event.is_set() is thread-safe
            return self._finish(...)
        # ... async loop body
```
3. **如果未来需要 async-native cancellation：** 使用 `asyncio.Task.cancel()` 机制，不用 Event。但这需要 src/ 层持有 task reference，更复杂。

**Detection:** grep `asyncio.Event` 确保没有被误用于跨线程场景。grep `threading.Event` 确认所有 is_set() 调用都在正确的线程模型中。

**Confidence:** HIGH -- Python asyncio 官方文档明确说明 asyncio sync primitives 不是线程安全的。

**Phase mapping:** Kernel async 化阶段必须决策。

---

### Pitfall C5: sync Generator -> async Generator 的 LLM streaming 生命周期问题

**What goes wrong:** 当前 `LLMProvider.chat_stream()` 返回 `Iterator[StreamChunk]`（sync generator）。改为 `AsyncIterator[StreamChunk]`（async generator）后，如果 iteration 被提前中断（exception, break, cancel），async generator 的 cleanup (finally block, aclose()) 行为和 sync generator 不同：

1. Sync generator: GC 会调用 `.close()` 触发 GeneratorExit，finally 块同步执行
2. Async generator: GC 调用 finalizer schedule `aclose()` 到 event loop，但如果 loop 已关闭或在不同线程，finally 块可能永远不执行

**Why it happens:** Async generator finalization 依赖 event loop 存活。PEP 525 设计了 `sys.set_asyncgen_finalizer()` 和 `loop.shutdown_asyncgens()` 来处理，但这些机制脆弱且容易被遗忘。

matmaster 的 LLM streaming 路径：
```python
# 当前 (sync): AgentKernel._do_stream_llm
for chunk in spec.llm_provider.chat_stream(api_messages, tool_defs):
    ...  # 如果这里抛异常，sync generator 的 finally 立即执行

# 改后 (async):
async for chunk in spec.llm_provider.chat_stream(api_messages, tool_defs):
    ...  # 如果这里抛异常，async generator 需要 aclose() 来触发 finally
```

OpenAI SDK 的 async stream 包含 httpx.AsyncClient 连接资源。如果 async generator 的 finally 不执行，HTTP 连接泄漏。

**Consequences:**
- HTTP 连接泄漏，connection pool 耗尽
- OpenAI SDK 的 response body 未消费完毕，触发 httpx 告警
- 在 pytest 中表现为 ResourceWarning 或 test 结束时的 asyncio 错误

**Prevention:**
1. **在 _do_stream_llm 中使用 contextlib.aclosing：**
```python
from contextlib import aclosing

async def _do_stream_llm(self, spec, messages, *, timeout=None):
    async with aclosing(spec.llm_provider.chat_stream(...)) as stream:
        async for chunk in stream:
            ...
```
2. **OpenAIProvider.chat_stream 内部用 try/finally 确保 SDK stream 关闭：**
```python
async def chat_stream(self, messages, tools=None, *, timeout=None):
    stream = await self._client.chat.completions.create(**kwargs)
    try:
        async for chunk in stream:
            yield ...
    finally:
        await stream.close()  # 确保 httpx response 被关闭
```
3. **在 pytest 中 enable asyncio debug mode + 严格 ResourceWarning 检测：**
```ini
# pyproject.toml
[tool.pytest.ini_options]
filterwarnings = ["error::ResourceWarning"]
```

**Detection:** 在 CI 中运行 `python -W error::ResourceWarning -m pytest`。在 asyncio debug mode 下运行测试检查未关闭的 async generator 警告。

**Confidence:** HIGH -- PEP 525 明确记录 async generator finalization 机制。

**Phase mapping:** LLMProvider async 化阶段，与 OpenAI SDK 迁移同步完成。

---

## Moderate Pitfalls

### Pitfall M1: 863 个 sync 测试的批量迁移策略错误

**What goes wrong:** 把 863 个测试全部加 `async def` + `@pytest.mark.asyncio` 导致：
- 所有 mock 需要同步更新为 async 版本
- conftest.py 中的 fixture 需要 `@pytest_asyncio.fixture` 标记
- event loop scope 配置不当导致测试间污染
- 大量 coroutine was never awaited 告警淹没有用的错误信息

**Why it happens:** pytest-asyncio 1.0 (2025-05) 做了重大 API 变更，移除了 event_loop fixture。如果一次性迁移所有测试，很容易在配置和标记上出错。

**Prevention:**
1. **分层迁移，不一次性全改：**
   - Phase 1: matmaster/types/ 的纯数据类测试 -- 不需要 async，保持 sync
   - Phase 2: matmaster/core/ 的 kernel/hook/guard 测试 -- 需要 async
   - Phase 3: matmaster/tools/ 的 tool 测试 -- 按 tool 逐个迁移
   - Phase 4: matmaster/integration/ 和 e2e 测试 -- 最后
2. **pyproject.toml 配置：**
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"  # 自动检测 async def test
asyncio_default_fixture_loop_scope = "function"  # 每个测试独立 loop
```
3. **conftest.py 中所有 async fixture 使用 `@pytest_asyncio.fixture`（strict mode 下必需）**
4. **MockLLMProvider 保持双版本过渡：**
```python
class MockLLMProvider:
    """Async mock satisfying the async LLMProvider Protocol."""
    async def chat(self, messages, tools=None):
        return LLMResponse(content="mock response", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="hello", finish_reason="stop")
```

**Detection:** 在 CI 中用 `pytest --co -q | wc -l` 确认测试数量没有下降。用 `-W error::RuntimeWarning` 捕获 coroutine was never awaited。

**Confidence:** HIGH -- pytest-asyncio 文档和社区经验充分。

---

### Pitfall M2: MessageBus 从 queue.Queue 到 asyncio.Queue 的线程边界问题

**What goes wrong:** 当前 MessageBus 用 `queue.Queue`（线程安全同步队列）。如果改为 `asyncio.Queue`（非线程安全）：
- EventRouter 在独立线程中消费 -- 无法用 asyncio.Queue
- src/ service 层在 ThreadPoolExecutor 线程中创建 bus -- 跨线程 put 不安全

但如果保持 `queue.Queue` 不改，async kernel 中 `bus.emit()` 的 sync `queue.put()` 在 async context 中是阻塞调用（虽然通常很快，但 maxsize>0 时可能阻塞）。

**Why it happens:** queue.Queue 和 asyncio.Queue 服务于不同的并发模型，不能简单替换。matmaster 的 EventRouter 本身是基于线程的消费者模式，和 async kernel 的执行模型不匹配。

**Prevention:**
1. **短期方案（v2.0 推荐）：保持 queue.Queue 不变。** 理由：
   - bus.emit() 是 fire-and-forget，queue.put() 在无限队列下不阻塞
   - EventRouter 线程消费模式不需要改动
   - src/ 层兼容性不变
   - 阻塞时间极短（microsecond 级），对 event loop 影响可忽略
2. **中期方案（v2.1 多 agent 编排时）：引入 janus 双面队列：**
```python
import janus

class AsyncMessageBus:
    def __init__(self):
        self._queue = janus.Queue()

    async def emit(self, event):
        await self._queue.async_q.put(event)

    def emit_sync(self, event):
        self._queue.sync_q.put(event)

    async def get(self, timeout=None):
        return await asyncio.wait_for(self._queue.async_q.get(), timeout)

    def get_sync(self, timeout=None):
        return self._queue.sync_q.get(timeout=timeout)
```
3. **绝对不要做的事：** 直接把 queue.Queue 换成 asyncio.Queue 然后在 EventRouter 线程中调用 asyncio.Queue.get() -- 这会 RuntimeError。

**Detection:** 确保 MessageBus 的 emit() 调用不会在 async context 中阻塞。在 asyncio debug mode 下运行如果 queue.put 阻塞超过阈值会有警告。

**Confidence:** HIGH -- janus 是社区公认的跨线程/asyncio 通信方案。

---

### Pitfall M3: Exp 清理回调从 sync 变 async 的资源泄漏

**What goes wrong:** 当前 `Exp._cleanup_callbacks` 是 `list[Callable[[], None]]`（sync callables）。如果某些清理操作需要 async（如关闭 AsyncOpenAI client、关闭 async MCP 连接），sync 的 `_run_cleanup_callbacks()` 无法 await 它们。

直接在 sync 上下文中调用 async cleanup function 只会创建一个 coroutine 对象但不执行它，资源永远不被释放。

**Why it happens:** 清理回调的 sync/async 需要和注册侧保持一致。当 tool 和 provider 变成 async 后，它们的清理逻辑也可能需要 async。

当前的清理调用链：
```
agent_run_service.py finally: exp._run_cleanup_callbacks()  # sync context
DevShell repl.py finally: runtime.cleanup()  # sync context
```

**Consequences:**
- AsyncOpenAI httpx.AsyncClient 未正确关闭，连接泄漏
- MCP connector 的 async session 未关闭
- pytest 中出现 Event loop is closed 或 unclosed resource 警告

**Prevention:**
1. **_cleanup_callbacks 支持 async：**
```python
async def _run_cleanup_callbacks(self) -> None:
    for cb in self._cleanup_callbacks:
        try:
            result = cb()
            if inspect.isawaitable(result):
                await result
        except Exception:
            self.logger.warning(...)
    self._cleanup_callbacks.clear()
```
2. **src/ service 层的调用侧也需要适配：**
   - agent_run_service.py 在 worker 线程中可以 `asyncio.run(exp._run_cleanup_callbacks())`
   - 或者在 worker 线程的 event loop 结束前统一 cleanup
3. **注册时标记 callback 类型：** 不需要额外标记，用 `inspect.isawaitable()` 检查返回值即可兼容 sync 和 async callback。

**Detection:** 在测试中 mock cleanup callback 并验证它被调用。对 async callback 确认 coroutine 被 await 而非仅被创建。

**Confidence:** HIGH -- Python async/sync 混合的标准问题。

---

### Pitfall M4: OpenAI SDK 同步 Iterator 到 AsyncIterator 的断裂

**What goes wrong:** 当前 `OpenAIProvider.chat_stream()` 使用 sync `openai.OpenAI` client，返回 `Iterator[StreamChunk]`。改为 `openai.AsyncOpenAI` 后，SDK 返回 `AsyncStream`（实现 `__aiter__` + `__anext__`），但 kernel 的消费代码如果还用 `for chunk in ...` 而非 `async for chunk in ...`，会得到 `TypeError: 'async_generator' object is not iterable`。

**Why it happens:** 这是 sync-to-async migration 中最常见的遗漏。开发者改了 provider 但忘了改所有消费侧。matmaster 中消费 chat_stream 的位置有：
1. `AgentKernel._do_stream_llm()` -- 主消费者，L266
2. `ContextCompactor._summarize()` -- 使用 chat()（非 stream），但如果改为 stream 也需要
3. 测试中的 `list(provider.chat_stream(...))` 模式 -- 需要改为 `[chunk async for chunk in ...]`

**Prevention:**
1. 改 Protocol 返回类型时，在 IDE 中全局搜索 `chat_stream` 的所有消费点
2. 使用 `AsyncIterator[StreamChunk]` 类型标注，mypy 会在 `for` (而非 `async for`) 处报错
3. 测试中用 helper：
```python
async def collect_stream(ait):
    return [chunk async for chunk in ait]
```

**Detection:** mypy strict mode + `for xxx in async_gen` 会有 type error。`grep -rn 'for chunk in.*chat_stream'` 找遗漏。

**Confidence:** HIGH -- 直接的类型系统问题。

---

### Pitfall M5: spawn_fn 从同步闭包变 async 的传播链断裂

**What goes wrong:** 当前 `Exp._make_spawn_fn()` 返回一个同步闭包，SpawnTool._execute() 同步调用它。改为 async 后整个链条需要一致：

```
SpawnTool._execute()  -> 需要变 async
  -> self._spawn_fn(exp_name, task, stop_event)  -> 需要 await
    -> child_exp.build_runtime(...)  -> 如果 build_runtime 变 async，需要 await
      -> child_runtime.kernel.run(...)  -> 需要 await
```

如果链条中有一环没改，就会出现：
- `spawn_fn()` 返回 coroutine 但 SpawnTool 没 await -- 子 agent 不执行
- SpawnTool._execute 是 async 但 BuiltinTool.execute 调用侧没 await -- 同上

**Why it happens:** async 的传染性 -- async 函数要求其所有调用者也是 async，一直向上传播到 event loop 入口。漏掉链条中的任一环节就会断裂。

**Prevention:**
1. 先画出 spawn 的完整调用链，从 kernel -> tool_registry.execute -> SpawnTool._execute -> spawn_fn -> child Exp.run -> child kernel.run
2. 从底层（child kernel.run）开始向上逐层改 async
3. SpawnTool 和其他 BuiltinTool 的 _execute 统一改为 async
4. ToolRegistry.execute 改为 `async def execute`
5. AgentKernel 中 tool execution 改为 `tool_result = await spec.tool_registry.execute(...)`

**Detection:** 搜索所有 `_execute(` 调用点和 `spawn_fn(` 调用点，确保每个都有 await。

**Confidence:** HIGH -- async 传染性是基础知识，但在多层嵌套系统中极易遗漏。

---

### Pitfall M6: time.sleep() 在 retry 逻辑中冻结 event loop

**What goes wrong:** 当前 retry 逻辑中直接使用 `time.sleep(backoff)`：
- `AgentKernel._call_llm()` L233: `time.sleep(backoff)`
- `OpenAIProvider.chat_with_retry()` L181/L204: `time.sleep(backoff)`

改为 async 后如果遗漏这些 sleep 调用，event loop 被冻结。LLM retry backoff 一般是 1-8 秒，这会导致其他所有 coroutine 停滞 1-8 秒。

**Why it happens:** `time.sleep()` 是 OS 级阻塞调用。在 async context 中必须用 `await asyncio.sleep()`。开发者容易在改 def -> async def 时遗漏函数体内的 `time.sleep`。

**Prevention:**
1. 全局搜索 `time.sleep` 并替换为 `await asyncio.sleep`
2. 在 CI 中加 lint 规则检测 async 函数中使用 time.sleep（使用 ruff 自定义规则或 flake8-async 插件）
3. retry 逻辑集中到一个 async retry decorator 中，避免分散在多处

**Detection:** `grep -rn 'time.sleep' matmaster/` -- 改完后应该为 0 个结果。`flake8-async` 或 ruff 的 `ASYNC` 规则可以自动检测。

**Confidence:** HIGH -- 最常见的 async migration 遗漏之一。

---

## Minor Pitfalls

### Pitfall L1: async for 的 break 不触发 async generator aclose

**What goes wrong:** 在 `async for chunk in stream: ... break` 的场景下，Python 会在 `break` 后自动对 async generator 调用 `aclose()`。但如果 consumer 代码捕获异常后没有正确退出 `async for` block，async generator 的 finally 可能不执行。

**Prevention:** 统一使用 `contextlib.aclosing` wrapper。在 _do_stream_llm 中确保异常路径正确退出。当前代码的 try/finally 模式是正确的，改为 async 时保持这个结构。

---

### Pitfall L2: pytest-asyncio event_loop fixture 已在 1.0 中移除

**What goes wrong:** 如果测试代码（或 conftest.py）依赖了 `event_loop` fixture，升级到 pytest-asyncio 1.0+ 后会报 DeprecationWarning 或直接失败。

**Prevention:** 检查所有 conftest.py 和测试文件中是否有 `event_loop` fixture 的引用。使用 `asyncio_default_fixture_loop_scope` 配置替代自定义 event_loop fixture。当前 matmaster 测试中没有发现 event_loop fixture 使用（好消息），但迁移时要确保新引入的 async fixture 不依赖它。

---

### Pitfall L3: asyncio.create_subprocess_exec 在 Windows 上需要 ProactorEventLoop

**What goes wrong:** 如果未来有 Windows 开发环境需求，asyncio subprocess 只在 ProactorEventLoop 上工作（Python 3.8+ 的 Windows 默认是 ProactorEventLoop，但某些测试框架可能强制 SelectorEventLoop）。

**Prevention:** 在 BashTool 测试中加 platform skip marker。matmaster 当前部署在 Linux，这个风险较低。

---

### Pitfall L4: async context manager 泄漏

**What goes wrong:** 如果 OpenAIProvider 使用 `async with httpx.AsyncClient() as client:` 模式，但 provider 的生命周期比单次调用长（在 Exp.build_runtime 时创建，整个 run 过程共用），需要在 cleanup 时显式 `await client.aclose()`。

**Prevention:** OpenAIProvider 的 httpx.AsyncClient 在 `__init__` 中创建，在 cleanup callback 中 `await client.aclose()`。或者改为 lazy initialization（首次调用时创建）。

---

### Pitfall L5: 忘记 await 的隐蔽表现

**What goes wrong:** 忘记 await 一个 coroutine 不会立即报错 -- Python 只会在 GC 时产生 `RuntimeWarning: coroutine 'xxx' was never awaited`。在测试中这个警告容易被淹没。在生产中这意味着某个操作（如 hook 通知、event emit、cleanup）被静默跳过。

具体的高风险点：
- `run_pre_llm_call(hooks, messages, turn)` -- 如果 hook 方法变 async，run_* helpers 需要 await 每个 hook
- `run_post_tool_call(hooks, tc, tool_result)` -- 同上
- `bus.emit(event)` -- 如果 emit 变 async
- `spec.compactor.compact_if_needed(...)` -- 如果变 async

**Prevention:**
1. 在 pyproject.toml 中将 RuntimeWarning 升级为 error：
```toml
[tool.pytest.ini_options]
filterwarnings = ["error::RuntimeWarning"]
```
2. 使用 mypy strict mode -- missing await 会被 type checker 捕获
3. 在 CI 中运行 `python -W error::RuntimeWarning` 确保无遗漏
4. run_* hook helpers 改为 async，内部 await 每个 hook 调用

**Detection:** `python -W error` 模式下运行测试。mypy 会在 `async def` 函数中对未 await 的 coroutine 报错。

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation | Priority |
|-------------|---------------|------------|----------|
| Protocol 定义改造 (Hook/Guard/LLMProvider) | C1: isinstance 不检查 async 签名 | 加 runtime coroutine validation helper | MUST |
| AgentKernel async 化 | C3: 遗留阻塞调用, C4: stop_event 线程模型 | asyncio debug mode, 保持 threading.Event | MUST |
| LLMProvider async 化 (AsyncOpenAI) | C5: async generator cleanup, M4: Iterator/AsyncIterator 断裂 | aclosing wrapper, 全量搜索消费点 | MUST |
| BuiltinTool async 化 | C3: BashTool subprocess 阻塞, M5: spawn chain 断裂 | create_subprocess_exec, 画调用链逐层改 | HIGH |
| Hook/Guard Protocol async 化 | L5: 忘记 await hook 调用 | run_* helpers 统一 async, mypy strict | HIGH |
| MessageBus | M2: queue.Queue 跨线程 | v2.0 保持 queue.Queue 不变 | LOW (defer) |
| 测试迁移 | M1: 批量 async 测试配置, L2: event_loop fixture | 分层迁移, asyncio_mode=auto | HIGH |
| Exp 生命周期 async 化 | M3: cleanup callback async 兼容 | inspect.isawaitable 兼容层 | MEDIUM |
| Integration boundary (src/ <-> matmaster/) | C2: asyncio.run 嵌套 | worker 线程用 asyncio.run, 不在 main loop 线程调用 | MUST |
| Integration boundary (DevShell <-> matmaster/) | C2: asyncio.run 嵌套 | detect running loop, 拒绝嵌套 | MEDIUM |

---

## Recommended Migration Order (Based on Pitfall Dependencies)

基于 pitfall 依赖关系推导的安全迁移顺序：

1. **Protocol 定义 + runtime validation** -- C1 必须最先解决，否则后续所有测试的 mock 都有隐患
2. **LLMProvider async 化 (AsyncOpenAI)** -- C5, M4 高影响，且是 kernel 的核心依赖
3. **AgentKernel async 化** -- C3, C4，依赖 Protocol 和 LLM 已完成
4. **BuiltinTool async 化** -- M5, C3 (BashTool)，依赖 kernel async
5. **Hook/Guard async 化 + run_* helpers** -- L5，依赖 kernel 调用侧已 async
6. **Exp 生命周期** -- M3，依赖上述全部
7. **测试分层迁移** -- M1，贯穿所有阶段，每阶段迁移对应测试
8. **Integration boundary 适配** -- C2, M2，最后处理，因为 src/ 层不在 scope 内

---

## Sources

- [Python asyncio 官方文档 -- Developing with asyncio](https://docs.python.org/3/library/asyncio-dev.html)
- [Python asyncio sync primitives -- not thread safe](https://docs.python.org/3/library/asyncio-sync.html)
- [PEP 525 -- Asynchronous Generators](https://peps.python.org/pep-0525/)
- [PEP 544 -- Protocols: Structural subtyping](https://peps.python.org/pep-0544/)
- [BBC Cloudfit -- Mixing Sync and Async](https://bbc.github.io/cloudfit-public-docs/asyncio/asyncio-part-5.html)
- [Erich Grunewald -- Gradually Migrating to asyncio](https://www.erichgrunewald.com/posts/gradually-migrating-python-code-to-asyncio/)
- [pytest-asyncio 1.0 migration](https://thinhdanggroup.github.io/pytest-asyncio-v1-migrate/)
- [janus -- thread-safe asyncio queue](https://github.com/aio-libs/janus)
- [OpenAI Python SDK -- AsyncOpenAI](https://github.com/openai/openai-python)
- [Python asyncio subprocess](https://docs.python.org/3/library/asyncio-subprocess.html)
- [Nil Monfort -- The Two Bridges: Async and Sync in Python](https://nilmonfort.com/writing/2025/12/02/the-two-bridges-async-sync-python/)
