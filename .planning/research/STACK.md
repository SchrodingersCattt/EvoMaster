# Technology Stack: matmaster v2.0 Async Transformation

**Project:** matmaster-evo 协程改造
**Researched:** 2026-03-26
**Overall confidence:** HIGH

## Executive Summary

matmaster v2.0 的 async 改造不需要引入重型新依赖。Python 3.13 标准库的 asyncio 模块已提供全部所需原语（TaskGroup, asyncio.Queue, asyncio.Event, create_subprocess_exec）。OpenAI SDK 已内置 AsyncOpenAI + AsyncStream（当前安装的 openai 2.20.0 即支持，无需升级）。唯一需要新增的 dev 依赖是 pytest-asyncio，用于测试 async 代码。

核心判断：
1. 所有 async 原语来自 Python 3.13 stdlib（asyncio.Queue, asyncio.Event, TaskGroup, create_subprocess_exec），零新运行时依赖
2. OpenAI AsyncOpenAI 已在当前安装版本中可用（verified: `from openai import AsyncOpenAI` 成功），chat.completions.create 的 async streaming 返回 AsyncStream（同时是 AsyncIterator 和 async context manager）
3. pytest-asyncio >= 1.0.0 是唯一新增的 dev 依赖，配置 `asyncio_mode = "auto"` 后所有 `async def test_*` 自动识别
4. MessageBus 从 `queue.Queue` 改为 `asyncio.Queue`，无需第三方库（janus 仅在需要 sync/async 桥接时使用，但 v2.0 全链路 async 不需要）
5. `threading.Event` 的 9 处使用全部替换为 `asyncio.Event`
6. `time.sleep` 的 3 处使用全部替换为 `asyncio.sleep`

## Recommended Stack

### Core Runtime (No Changes)

| Technology | Current Version | Purpose | Status |
|------------|----------------|---------|--------|
| Python | 3.13.2 | Runtime | Unchanged -- asyncio.TaskGroup (3.11+), full async subprocess support |
| Pydantic | 2.12.5 | Data models, frozen configs | Unchanged -- model_copy(), ConfigDict 与 async 无关 |
| FastAPI | 0.128.8 | Web service layer | Unchanged -- src/ 层不在 v2.0 scope |
| OpenAI SDK | 2.20.0 | LLM API client | Unchanged -- AsyncOpenAI 已可用，无需升级 |
| httpx | 0.28.1 | HTTP client (OpenAI SDK 底层) | Unchanged -- httpx.AsyncClient 已可用于 AsyncOpenAI |
| tiktoken | 0.7.0+ | Token estimation | Unchanged -- estimate_tokens() 是纯计算，不涉及 I/O |

### New Dev Dependencies

| Technology | Version | Purpose | Why This One |
|------------|---------|---------|--------------|
| pytest-asyncio | >=1.0.0 | Async test runner | 唯一成熟的 pytest async 插件；auto mode 自动识别 async def test；与 pytest >=9.0.2 兼容 |

### Explicitly NOT Adding

| Library | Why Not | Use Instead |
|---------|---------|-------------|
| anyio | 项目只用 asyncio，不需要 Trio/curio 兼容层，引入 anyio 增加不必要的抽象 | 直接用 asyncio stdlib |
| trio | Python stdlib asyncio 已是项目标准，Trio 的 structured concurrency 模型不兼容现有 FastAPI 栈 | asyncio.TaskGroup（Python 3.11+ 引入的 structured concurrency） |
| aiofiles | 文件 I/O 操作委托给 BaseSession（远程执行），本地文件操作（config 加载等）是一次性同步读取，不值得 async 化 | 同步 pathlib（config 加载）+ asyncio.to_thread（必要时） |
| janus | v2.0 全链路 async，MessageBus producer/consumer 都在 event loop 内，不需要 sync/async 桥接 | asyncio.Queue |
| aiomysql / aioredis | src/ 服务层不在 v2.0 scope，数据库/Redis 连接保持现状 | 现有 pymysql + redis |
| backoff / tenacity | 项目已有手动 retry 实现（3 处 time.sleep + exponential backoff），改为 asyncio.sleep 即可，不值得引入装饰器库 | 手动 async retry + asyncio.sleep |

## Async Primitive Mapping (stdlib)

每个需要替换的同步原语及其 async 对应物：

| Sync Primitive | Async Replacement | Location in Codebase | Notes |
|---------------|-------------------|---------------------|-------|
| `queue.Queue` | `asyncio.Queue` | `matmaster/core/bus.py` | emit() -> async emit(), get() -> async get(), get_nowait() 保留同名 |
| `threading.Event` | `asyncio.Event` | agent.py, exp.py, spawn_tool.py, event_router.py, repl.py, runner.py, playground.py (9 处) | stop_event.is_set() 签名不变，stop_event.wait() 变 await |
| `time.sleep(backoff)` | `await asyncio.sleep(backoff)` | openai_provider.py (2 处), agent.py (1 处) | retry backoff 从阻塞变非阻塞 |
| `Iterator[StreamChunk]` (sync generator) | `AsyncIterator[StreamChunk]` (async generator) | LLMProvider.chat_stream, OpenAIProvider.chat_stream | yield -> async for, 消费端从 for chunk in 变 async for chunk in |
| `for chunk in provider.chat_stream()` | `async for chunk in provider.chat_stream()` | agent.py _do_stream_llm() | OpenAI SDK stream 变 AsyncStream（已是 AsyncIterator） |
| `subprocess.run()` | `asyncio.create_subprocess_exec()` | BashTool (间接通过 session) | session-dependent: session 层改造；session-free (DevShell): 直接用 create_subprocess_exec |

## Detailed Component Changes

### 1. LLMProvider Protocol -- Async 化

**Current** (sync):
```python
@runtime_checkable
class LLMProvider(Protocol):
    def chat(self, messages, tools=None) -> LLMResponse: ...
    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0) -> LLMResponse: ...
    def chat_stream(self, messages, tools=None, *, timeout=None) -> Iterator[StreamChunk]: ...
```

**Target** (async):
```python
@runtime_checkable
class LLMProvider(Protocol):
    async def chat(self, messages, tools=None) -> LLMResponse: ...
    async def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0) -> LLMResponse: ...
    def chat_stream(self, messages, tools=None, *, timeout=None) -> AsyncIterator[StreamChunk]: ...
```

**Key point:** `chat_stream` 返回类型从 `Iterator[StreamChunk]` 变 `AsyncIterator[StreamChunk]`。方法本身可以是 `async def`（返回 async generator）或普通 `def`（返回 AsyncIterator 对象）。建议用 `async def` + `yield`（async generator），更自然。

**runtime_checkable 与 async 方法：** `@runtime_checkable` Protocol 只检查方法名是否存在，不检查是否是 coroutine。这意味着 isinstance() 检查仍然有效，但 mypy 会在编译时检查 async 签名匹配。当前 codebase 中 `LLMProvider` 的 runtime_checkable 主要用于 AgentRuntimeSpec 的 arbitrary_types_allowed 场景，async 化后行为不变。

### 2. OpenAIProvider -- AsyncOpenAI

**Current** (sync):
```python
self._client = openai.OpenAI(api_key=..., http_client=httpx.Client(...))
```

**Target** (async):
```python
self._client = openai.AsyncOpenAI(api_key=..., http_client=httpx.AsyncClient(...))
```

**Verified availability** (Python 3.13.2, openai 2.20.0):
- `from openai import AsyncOpenAI` -- available
- `AsyncOpenAI.__init__` accepts `http_client: httpx.AsyncClient | None`
- `openai.AsyncStream` -- is both AsyncIterator and async context manager

**chat_stream async pattern:**
```python
async def chat_stream(self, messages, tools=None, *, timeout=None) -> AsyncIterator[StreamChunk]:
    stream = await self._client.chat.completions.create(stream=True, **kwargs)
    async for chunk in stream:
        yield StreamChunk(...)
```

**Retry 替换:**
```python
# Before:
time.sleep(backoff)
# After:
await asyncio.sleep(backoff)
```

### 3. AgentKernel.run() -- Async Execution Loop

**Current:**
```python
def run(self, spec, task, history=None, stop_event: threading.Event | None = None) -> KernelRunResult:
    while turn < spec.max_turns:
        if stop_event and stop_event.is_set(): ...
        response = self._call_llm(spec, messages)
        tool_result = spec.tool_registry.execute(tc.name, tc.arguments)
```

**Target:**
```python
async def run(self, spec, task, history=None, stop_event: asyncio.Event | None = None) -> KernelRunResult:
    while turn < spec.max_turns:
        if stop_event and stop_event.is_set(): ...  # asyncio.Event.is_set() 签名相同
        response = await self._call_llm(spec, messages)
        tool_result = await spec.tool_registry.execute(tc.name, tc.arguments)
```

**Cancellation:** `asyncio.Event.is_set()` 是同步方法（不需要 await），与 `threading.Event.is_set()` 签名完全一致。差别仅在于 `event.wait()` 变成 `await event.wait()`。AgentKernel 的 cancel check 使用 `is_set()`，无需修改检查逻辑。

### 4. MessageBus -- asyncio.Queue

**Current** (`queue.Queue`, thread-safe):
```python
class MessageBus:
    def __init__(self):
        self._queue: queue.Queue[BusEvent] = queue.Queue()
    def emit(self, event: BusEvent) -> None:
        self._queue.put(event)
    def get(self, timeout=None) -> BusEvent:
        return self._queue.get(timeout=timeout)
```

**Target** (`asyncio.Queue`, coroutine-safe):
```python
class MessageBus:
    def __init__(self):
        self._queue: asyncio.Queue[BusEvent] = asyncio.Queue()
    async def emit(self, event: BusEvent) -> None:
        await self._queue.put(event)
    async def get(self, timeout=None) -> BusEvent:
        if timeout is not None:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        return await self._queue.get()
    def get_nowait(self) -> BusEvent:
        return self._queue.get_nowait()  # 同步方法，asyncio.Queue 也提供
```

**Note:** `asyncio.Queue.get_nowait()` 和 `asyncio.Queue.qsize()` 是同步方法，与 `queue.Queue` 签名一致。`empty` property 也保持不变。变化集中在 `put()` -> `await put()` 和 `get()` -> `await get()`。

### 5. Hook Protocol -- Async 化

**Current** (7 sync methods):
```python
class Hook(Protocol):
    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction: ...
    def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None: ...
    def on_stream_chunk(self, chunk: StreamChunk) -> None: ...
    # ... 4 more
```

**Target** (7 async methods):
```python
class Hook(Protocol):
    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction: ...
    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None: ...
    async def on_stream_chunk(self, chunk: StreamChunk) -> None: ...
    # ... 4 more
```

**run_* helpers 变化：**
```python
# Before:
def run_pre_tool_call(hooks, tool_call) -> HookAction:
    for hook in hooks:
        action = hook.pre_tool_call(tool_call)
        if action == HookAction.SKIP:
            return HookAction.SKIP
    return HookAction.CONTINUE

# After:
async def run_pre_tool_call(hooks, tool_call) -> HookAction:
    for hook in hooks:
        action = await hook.pre_tool_call(tool_call)
        if action == HookAction.SKIP:
            return HookAction.SKIP
    return HookAction.CONTINUE
```

**EventEmitterHook 影响：** emit 调用从同步变 async，内部 `self._bus.emit(event)` 变 `await self._bus.emit(event)`。所有 7 个 hook method 都需要变 async def。

### 6. BuiltinTool -- Async execute

**Current:**
```python
class BuiltinTool(ABC):
    def execute(self, arguments: dict[str, Any]) -> str:
        return self._execute(arguments)

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str: ...
```

**Target:**
```python
class BuiltinTool(ABC):
    async def execute(self, arguments: dict[str, Any]) -> str:
        return await self._execute(arguments)

    @abstractmethod
    async def _execute(self, arguments: dict[str, Any]) -> str: ...
```

**BashTool 特殊处理：** 当前通过 `session.exec_bash()` 同步执行。v2.0 有两个选项：
- session-dependent（生产）: 如果 BaseSession 不改为 async，用 `asyncio.to_thread(session.exec_bash, ...)` 包装
- session-free (DevShell): 用 `asyncio.create_subprocess_exec()` 或 `asyncio.create_subprocess_shell()` 直接 async 执行

**Subprocess async pattern:**
```python
async def _execute(self, arguments):
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(self._workdir),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "Error: command timed out"
    return stdout.decode()
```

### 7. Tool Protocol -- Async execute

**Current:**
```python
@runtime_checkable
class Tool(Protocol):
    def execute(self, arguments: dict[str, Any]) -> str | ToolResult | None: ...
```

**Target:**
```python
@runtime_checkable
class Tool(Protocol):
    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult | None: ...
```

**ToolRegistry.execute 变化:**
```python
# Before:
def execute(self, name, arguments) -> ToolResult:
    return normalize_tool_result(tool.execute(arguments))

# After:
async def execute(self, name, arguments) -> ToolResult:
    return normalize_tool_result(await tool.execute(arguments))
```

### 8. Guard Protocol -- 保持同步

Guard 的 `evaluate()` 是纯计算（fingerprint 比对、turn 计数），不涉及 I/O。保持同步。

```python
# 保持不变
class Guard(Protocol):
    def evaluate(self, ctx: GuardContext) -> GuardResult: ...

class GuardPipeline:
    def evaluate(self, tool_call, current_turn, max_turns) -> GuardResult: ...
```

**Rationale:** async 化所有东西是过度工程。Guard 是 CPU-bound 纯逻辑，async 化没有收益，反而增加 await 开销。

### 9. ContextCompactor -- Async 化

内部调用 `self._summary_provider.chat()`，这个 LLM 调用是 I/O-bound，必须 async。

```python
# Before:
def _summarize(self, old_messages) -> str:
    response = self._summary_provider.chat(api_messages)
    return response.content

# After:
async def _summarize(self, old_messages) -> str:
    response = await self._summary_provider.chat(api_messages)
    return response.content
```

`compact_if_needed()` 也需要变 async（因为内部调用 `_summarize`）。

### 10. SubAgent Spawn -- Async 化

**Current** (sync spawn_fn closure):
```python
def spawn_fn(exp_name, task, stop_event: threading.Event | None = None) -> str:
    child_runtime = child_exp.build_runtime(ctx, bus=bus, ...)
    run_result = child_runtime.kernel.run(child_runtime.spec, task, stop_event=stop_event)
    return result.final_content
```

**Target** (async spawn_fn):
```python
async def spawn_fn(exp_name, task, stop_event: asyncio.Event | None = None) -> str:
    child_runtime = await child_exp.build_runtime(ctx, bus=bus, ...)
    run_result = await child_runtime.kernel.run(child_runtime.spec, task, stop_event=stop_event)
    return result.final_content
```

**Structured concurrency (future v2.1):** 当前 SubAgent 是串行执行（一次只 spawn 一个），async 改造后可以用 `asyncio.TaskGroup` 实现并行 spawn，但这是 v2.0 out of scope。v2.0 只做 async 基础设施。

## Testing Configuration

### pytest-asyncio Setup

**pyproject.toml 新增：**
```toml
[project.optional-dependencies]
dev = [
    "pre-commit>=4.5.1",
    "pytest>=9.0.2",
    "pytest-asyncio>=1.0.0",   # 新增
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**为什么选 auto mode：**
- matmaster 只使用 asyncio（不用 Trio/curio），auto mode 是最简配置
- 所有 `async def test_*` 自动被识别为 asyncio 测试，无需手动加 `@pytest.mark.asyncio`
- 所有 async fixtures 自动被 pytest-asyncio 管理
- auto mode 从 pytest-asyncio 0.25 开始是推荐模式（1.3.0 默认 strict，但显式配 auto 更简洁）

**为什么不用 strict mode：**
- strict mode 要求每个 async test 手动加 `@pytest.mark.asyncio`
- 项目有 863 个测试，新增的 async test 会很多，手动标记增加样板代码
- 项目不混用多个 async 框架，auto mode 不会产生歧义

### Mock Pattern for Async Tests

**Current MockLLMProvider** (tests/matmaster/core/conftest.py):
```python
class MockLLMProvider:
    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="mock response", finish_reason="stop")
    def chat_stream(self, messages, tools=None, *, timeout=None) -> Iterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")
```

**Target MockLLMProvider:**
```python
class MockLLMProvider:
    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="mock response", finish_reason="stop")
    async def chat_stream(self, messages, tools=None, *, timeout=None) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")
```

async generator（`async def` + `yield`）是最自然的 mock 方式。调用端用 `async for chunk in provider.chat_stream(...)` 消费。

### Fixture Patterns

```python
import pytest
import asyncio

# asyncio_mode = "auto" 下，async fixture 自动被识别
@pytest.fixture
async def message_bus():
    return MessageBus()

@pytest.fixture
async def stop_event():
    return asyncio.Event()

# Sync fixtures 不受影响，继续正常工作
@pytest.fixture
def mock_tool_call():
    return ToolCallData(id="tc-1", name="test_tool", arguments={"key": "value"})
```

## Version Compatibility Matrix

| Package | Current | Required for v2.0 | Action |
|---------|---------|-------------------|--------|
| Python | 3.13.2 | >=3.11 (TaskGroup) | No change |
| openai | 2.20.0 | >=1.0.0 (AsyncOpenAI) | No change -- already satisfied |
| httpx | 0.28.1 | >=0.23.0 (AsyncClient) | No change -- already satisfied |
| pydantic | 2.12.5 | >=2.0 | No change |
| pytest | 9.0.2 | >=8.0 (pytest-asyncio 1.x compat) | No change |
| pytest-asyncio | (not installed) | >=1.0.0 | **NEW: add to dev deps** |
| FastAPI | 0.128.8 | N/A (src/ not in scope) | No change |
| redis | >=5.0.0 | N/A (src/ not in scope) | No change |

## Installation

```bash
# Only one new dev dependency
uv sync --extra dev

# After adding pytest-asyncio to pyproject.toml:
# [project.optional-dependencies]
# dev = [
#     "pre-commit>=4.5.1",
#     "pytest>=9.0.2",
#     "pytest-asyncio>=1.0.0",
# ]
```

## Integration with src/ Service Layer

v2.0 scope 声明 src/ 不改造，但需要考虑接口兼容：

**Current:** `src/services/agent_run_service.py` 通过 `Exp.run()` 同步调用 agent。Worker 在线程中执行。

**v2.0 兼容策略:** Exp.run() 变 async 后，src/ 层调用入口需要 `asyncio.run()` 或 `loop.run_until_complete()` 包装。但这属于 src/ 层适配，不是 matmaster/ 层职责。

**DevShell 兼容策略:** 项目已声明 DevShell async 改造延后，用 `asyncio.run(exp.run(...))` 包装 async 入口即可。

## Sources

- **PyPI verified:** pytest-asyncio 1.3.0 (2025-11-10), supports Python 3.10-3.14 -- [pytest-asyncio PyPI](https://pypi.org/project/pytest-asyncio/)
- **PyPI verified:** openai 2.20.0 (installed), AsyncOpenAI available -- `from openai import AsyncOpenAI` 本地验证通过
- **PyPI verified:** httpx 0.28.1 (installed), AsyncClient available -- 本地验证通过
- **Runtime verified:** `openai.AsyncStream` is both AsyncIterator and async context manager -- 本地代码检查确认
- **Runtime verified:** Python 3.13.2, asyncio.TaskGroup, asyncio.Queue, asyncio.Event, create_subprocess_exec -- 全部本地验证可用
- **Official docs:** [pytest-asyncio configuration](https://pytest-asyncio.readthedocs.io/en/stable/reference/configuration.html)
- **Official docs:** [pytest-asyncio auto mode concepts](https://pytest-asyncio.readthedocs.io/en/stable/concepts.html)
- **Official docs:** [Python 3.13 asyncio subprocess](https://docs.python.org/3/library/asyncio-subprocess.html)
- **Official docs:** [Python 3.13 asyncio.Queue](https://docs.python.org/3/library/asyncio-queue.html)
- **Official docs:** [Python 3.13 What's New -- TaskGroup improvements](https://docs.python.org/3/whatsnew/3.13.html)

---
*Stack research for: matmaster v2.0 协程改造*
*Researched: 2026-03-26*
