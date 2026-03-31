# Phase 13: LLM Provider 异步实现 - Research

**Researched:** 2026-03-27
**Domain:** OpenAI Python SDK async API / httpx.AsyncClient / asyncio bridging
**Confidence:** HIGH

## Summary

Phase 13 将 OpenAIProvider 从同步实现改造为 async，使用 `openai.AsyncOpenAI` 客户端。核心改动集中在三个文件：`openai_provider.py`（主体改造）、`context_compactor.py`（顺带改造 `_summarize` 和 `compact_if_needed` 调用链为 async）、以及 Kernel 层的临时同步桥接。所有变更都已通过对 openai SDK 2.20.0 和 httpx 0.28.1 的实际 API 验证确认可行。

openai SDK 的异常类（APITimeoutError、APIConnectionError 等）在同步和异步客户端之间完全共享同一套类，异常映射逻辑无需修改。AsyncOpenAI 的构造函数是同步的（不涉及 I/O），但按 D-02 决策延迟到 `__aenter__` 创建以建立一致的生命周期语义。httpx.AsyncClient 的 Timeout 配置与 httpx.Client 完全对称，超时参数映射逻辑可直接复用。

**Primary recommendation:** OpenAIProvider 实现 async context manager（`__aenter__`/`__aexit__`），`__init__` 只存参数不创建 client，`chat()` 改为 `async def`，`chat_stream()` 改为 `async def` + `yield`（async generator）。Kernel 通过 `asyncio.new_event_loop()` + `run_until_complete()` 做临时同步桥接。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** OpenAIProvider 实现完整 async context manager。`__init__` 只存储参数不创建 client，`__aenter__` 创建 AsyncOpenAI + httpx.AsyncClient，`__aexit__` 关闭连接池。
- **D-02:** AsyncOpenAI 构造函数本身是同步的，但将创建延迟到 `__aenter__` 以保持语义一致性。
- **D-03:** ContextCompactor._summarize() 在 Phase 13 一并改为 async（加 await），compact_if_needed() 等调用链相应改为 async。
- **D-04:** AgentKernel._call_llm() 使用临时同步桥接过渡，Phase 17 正式改造时移除。
- **D-05:** build_provider() 保持同步函数，返回未初始化的 OpenAIProvider 实例。调用者通过 `async with provider` 管理生命周期。
- **D-06:** 只迁移 provider 层单元测试（test_openai_provider.py 和 test_llm_factory.py）改为 async 测试。
- **D-07:** 新增 async context manager 生命周期测试。
- **D-08:** 6 个集成测试留到 Phase 17-18 随 Kernel/Exp 一起迁移。

### Claude's Discretion
- httpx.AsyncClient 的超时配置细节
- chat_stream async generator 的具体实现方式
- 异常映射是否需要调整
- Kernel 临时同步桥接的具体实现方式
- ContextCompactor 调用链中需要改 async 的具体方法范围

### Deferred Ideas (OUT OF SCOPE)
None
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| LLMP-01 | OpenAIProvider 使用 AsyncOpenAI client，chat() 和 chat_stream() 实现为 async | AsyncOpenAI API 已验证：构造函数同步，chat.completions.create 为 coroutine，close() 为 coroutine。异常类共享。详见 Architecture Patterns 和 Code Examples。 |
| LLMP-02 | chat_stream() 使用 AsyncStream 作为 async iterator，正确处理 async context manager 生命周期 | AsyncStream 支持 `__aiter__`/`__anext__`/`__aenter__`/`__aexit__`。SDK 返回 AsyncStream 对象，可直接 `async for` 迭代。详见 Code Examples chat_stream 部分。 |
| LLMP-03 | provider 实例的创建和清理支持 async（__aenter__/__aexit__ 或显式 close） | AsyncOpenAI 自身支持 `__aenter__`/`__aexit__`，且 close() 是 async。Provider 层再包一层 context manager 语义清晰。详见 Architecture Patterns D-01 实现。 |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| openai | 2.20.0 | AsyncOpenAI client + AsyncStream | 项目已安装，AsyncOpenAI 与 OpenAI 共享同一包 |
| httpx | 0.28.1 | httpx.AsyncClient 作为 AsyncOpenAI 底层传输 | openai SDK 内部使用，显式传入 AsyncClient 可控制超时 |
| pytest-asyncio | 1.3.0 | async 测试运行 | Phase 12 已建立，asyncio_mode=auto |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| unittest.mock (AsyncMock) | stdlib | Mock async methods in tests | 替代 MagicMock 用于 async 方法 mock |

**Installation:** 无需额外安装，所有依赖已在项目中。

## Architecture Patterns

### Provider 生命周期模式
```
build_provider(config)     # 同步，返回未初始化的 OpenAIProvider
        |
        v
async with provider:       # __aenter__: 创建 AsyncOpenAI + httpx.AsyncClient
    |                       # provider 现在可用
    |-- await provider.chat(messages)
    |-- async for chunk in provider.chat_stream(messages):
    |       yield chunk
    |
    v                       # __aexit__: await client.close()
```

### Kernel 临时桥接模式（Phase 13-16 过渡期）
```
AgentKernel._call_llm()           # 仍然是 sync
    |
    v
_do_stream_llm()                  # 仍然是 sync
    |
    v
_sync_bridge_async_stream()       # 新增：sync -> async 桥接
    |-- loop = asyncio.new_event_loop()
    |-- async for chunk in provider.chat_stream(...):
    |       yield chunk  (via loop.run_until_complete)
    |-- loop.close()
```

### ContextCompactor async 调用链
```
compact_if_needed()  ->  async def compact_if_needed()   # 改 async
    |
    _summarize()     ->  async def _summarize()           # 改 async (await provider.chat())
```

Kernel 中 `spec.compactor.compact_if_needed(messages, last_usage, turn)` 也需要通过桥接调用。

### Anti-Patterns to Avoid
- **在 __init__ 中创建 AsyncOpenAI:** D-02 明确要求延迟到 `__aenter__`。虽然 AsyncOpenAI 构造函数是同步的，但语义上 provider 应在进入 context 后才可用。
- **使用 asyncio.run() 做桥接:** 如果调用方已在事件循环中会抛 RuntimeError。使用 `asyncio.new_event_loop()` 更安全。
- **忘记在 async generator 中处理异常:** `chat_stream` 的 try/except 必须包裹 `async for` 循环，否则异常会在 generator 内部未被捕获。
- **在未进入 context 时调用 chat/chat_stream:** 应在 chat()/chat_stream() 开头检查 `self._client is not None`，否则抛出明确的 RuntimeError。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async HTTP client | 自己用 aiohttp 调 API | openai.AsyncOpenAI | SDK 处理认证、序列化、流式解析、异常映射 |
| Async iterator 到 sync iterator 桥接 | 复杂的线程+queue 方案 | asyncio.new_event_loop() + run_until_complete(__anext__) | 简单、可靠、Phase 17 后直接删除 |
| 连接池管理 | 手动管理连接数和超时 | httpx.AsyncClient 传入 AsyncOpenAI | httpx 内置连接池和超时管理 |

## Common Pitfalls

### Pitfall 1: async generator 不能直接作为 AsyncIterator 类型标注
**What goes wrong:** `async def chat_stream(...) -> AsyncIterator[StreamChunk]` 作为类型标注是正确的，但实际返回的是 async generator 对象。`isinstance` 检查时，async generator 和标注为 `async def ... -> AsyncIterator` 的 Protocol stub 行为不同。
**Why it happens:** Protocol stub 中 `async def chat_stream() -> AsyncIterator[StreamChunk]: ...` 被 inspect 识别为 `iscoroutinefunction=True`，而实际的 async generator 实现被识别为 `isasyncgenfunction=True`。
**How to avoid:** Phase 12 的 `_is_async_callable()` 已处理此问题，同时检查两种情况。Provider 实现使用 `async def chat_stream(...) -> AsyncIterator[StreamChunk]` 加 `yield` 即可。
**Warning signs:** `validate_async_protocol(provider, LLMProvider)` 返回非空错误列表。

### Pitfall 2: 桥接时 event loop 冲突
**What goes wrong:** 在已有运行中 event loop 的线程中调用 `asyncio.run()` 会抛 RuntimeError("cannot be called from a running event loop")。
**Why it happens:** FastAPI/uvicorn 自带事件循环，如果 Kernel 在同一线程执行则冲突。
**How to avoid:** 使用 `asyncio.new_event_loop()` + `loop.run_until_complete()` + `loop.close()`。Kernel 当前在 Worker 进程或 API 线程中运行，无 event loop 冲突，但用 new_event_loop 更健壮。
**Warning signs:** RuntimeError("cannot be called from a running event loop")。

### Pitfall 3: 忘记关闭 httpx.AsyncClient
**What goes wrong:** 未调用 `await client.close()` 导致连接泄漏，表现为 ResourceWarning 或连接池耗尽。
**Why it happens:** 没有用 async context manager 模式，或 `__aexit__` 中忘记 close。
**How to avoid:** `__aexit__` 中 `await self._client.close()`，且测试验证 close 被调用。
**Warning signs:** pytest 运行时出现 ResourceWarning。

### Pitfall 4: Mock AsyncOpenAI 时遗漏 async 层
**What goes wrong:** 用 `MagicMock` mock `AsyncOpenAI` 时，`client.chat.completions.create` 不会自动返回 coroutine/async iterator，导致 `await` 失败。
**Why it happens:** MagicMock 不理解 async 方法。
**How to avoid:** 使用 `AsyncMock` 替代 `MagicMock`，或手写 async mock factory（Phase 12 conftest 已建立模式）。对于 chat_stream，mock 需要返回 async iterator。
**Warning signs:** `TypeError: object MagicMock can't be used in 'await' expression`。

### Pitfall 5: ContextCompactor 测试中的 MockSummaryProvider
**What goes wrong:** 现有 `MockSummaryProvider` 和 `FailingSummaryProvider` 在 test_context_compactor.py 中是 sync 的，compact_if_needed 改 async 后测试会失败。
**Why it happens:** MockSummaryProvider.chat() 是 sync def，但 ContextCompactor._summarize() 改为 `await self._summary_provider.chat()`。
**How to avoid:** 将 MockSummaryProvider/FailingSummaryProvider 的 chat/chat_stream 改为 async def，测试方法改为 async def。
**Warning signs:** `TypeError: object LLMResponse can't be used in 'await' expression`。

### Pitfall 6: E2E 测试中 CompactionTestProvider 也需改 async
**What goes wrong:** test_context_compactor.py 中的 `TestEndToEndCompaction` 使用内联 `CompactionTestProvider`，其 chat/chat_stream 是 sync。
**Why it happens:** E2E 测试通过 AgentKernel.run() 调用完整链路，Kernel 仍是 sync，但 ContextCompactor 内部会 await provider.chat()。
**How to avoid:** 这个测试属于 D-08 中延后到 Phase 17-18 的集成测试。Phase 13 中此测试预期会 break，应标记为跳过（D-08 明确说集成测试留到后续阶段）。
**Warning signs:** TestEndToEndCompaction 测试失败。

## Code Examples

### 1. OpenAIProvider async context manager + chat()
```python
# Source: 基于 openai 2.20.0 AsyncOpenAI API 验证
class OpenAIProvider:
    def __init__(self, *, model: str, api_key: str, base_url: str | None = None,
                 temperature: float = 0.7, max_tokens: int | None = None,
                 timeout: float = 300.0, stream_timeout: float | None = None,
                 stream_idle_timeout: float | None = None,
                 max_retries: int = 3, retry_delay: float = 1.0,
                 extra_kwargs: dict[str, Any] | None = None) -> None:
        # 只存参数，不创建 client
        self._model = model
        self._api_key = api_key  # 临时存储，__aenter__ 后传给 AsyncOpenAI
        self._base_url = base_url
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._stream_timeout = stream_timeout
        self._stream_idle_timeout = stream_idle_timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._extra_kwargs = extra_kwargs or {}
        self._client: openai.AsyncOpenAI | None = None  # 延迟创建

    async def __aenter__(self) -> OpenAIProvider:
        import httpx
        _first_token_t = self._stream_timeout if self._stream_timeout is not None else self._timeout
        _idle_t = self._stream_idle_timeout if self._stream_idle_timeout is not None else self._timeout
        _read_t = float(max(_idle_t, _first_token_t) + 10)

        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=_read_t, write=30.0, pool=15.0)
        )
        self._client = openai.AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            http_client=http_client,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _ensure_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            raise RuntimeError(
                "OpenAIProvider must be used as async context manager: "
                "'async with provider:'"
            )
        return self._client

    async def chat(self, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        client = self._ensure_client()
        kwargs = {"model": self._model, "messages": messages, "temperature": self._temperature}
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if tools:
            kwargs["tools"] = tools
        if self._extra_kwargs:
            kwargs.update(self._extra_kwargs)

        response = await client.chat.completions.create(**kwargs)
        # ... response mapping 逻辑不变 ...
```

### 2. chat_stream async generator
```python
# Source: 基于 openai 2.20.0 AsyncStream API 验证
    async def chat_stream(self, messages: list[dict[str, Any]],
                          tools: list[dict[str, Any]] | None = None,
                          *, timeout: float | None = None) -> AsyncIterator[StreamChunk]:
        from matmaster.types.errors import LLMError
        import httpx as _httpx

        client = self._ensure_client()
        kwargs = { ... }  # 同现有逻辑

        try:
            stream = await client.chat.completions.create(**kwargs)
            last_chunk_usage: dict[str, int] | None = None

            async for chunk in stream:    # AsyncStream 支持 async for
                # ... chunk 处理逻辑完全不变 ...
                yield StreamChunk(...)

            if last_chunk_usage is not None:
                yield StreamChunk(usage=last_chunk_usage)

        except (openai.APITimeoutError, openai.APIConnectionError,
                openai.RateLimitError, openai.InternalServerError) as exc:
            raise LLMError(str(exc), retryable=True) from exc
        except _httpx.ReadTimeout as exc:
            raise LLMError(str(exc), retryable=True) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LLMError(str(exc), retryable=False) from exc
        except openai.BadRequestError as exc:
            err_str = str(exc).lower()
            if "context" in err_str and ("length" in err_str or "token" in err_str):
                raise LLMError(str(exc), retryable=False) from exc
            raise LLMError(str(exc), retryable=True) from exc
```

### 3. Kernel 临时同步桥接
```python
# Source: asyncio stdlib + 验证通过的桥接模式
import asyncio
from typing import Any, AsyncIterator, Iterator

def _sync_iterate_async(async_iter: AsyncIterator[Any]) -> Iterator[Any]:
    """Bridge async iterator to sync iterator (temporary Phase 13-16)."""
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                yield loop.run_until_complete(async_iter.__anext__())
            except StopAsyncIteration:
                break
    finally:
        loop.close()

def _sync_call_async(coro):
    """Bridge async coroutine to sync call (temporary Phase 13-16)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# 在 AgentKernel._do_stream_llm 中使用：
# 原: for chunk in spec.llm_provider.chat_stream(api_messages, tool_defs, timeout=timeout):
# 改: for chunk in _sync_iterate_async(spec.llm_provider.chat_stream(api_messages, tool_defs, timeout=timeout)):
```

### 4. Async context manager 在 Kernel 桥接中的管理
```python
# Kernel._call_llm 中需要管理 provider 的 async context manager
# 选项 A: 在 _do_stream_llm 中每次调用都 enter/exit (低效但安全)
# 选项 B: 在 Kernel.run() 开始时 enter，结束时 exit
# 选项 C: provider 添加容错 -- chat/chat_stream 在 _client is None 时自动创建
#
# 推荐选项 B 的桥接版：
def run(self, spec, task, ...):
    # 桥接 provider 生命周期
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(spec.llm_provider.__aenter__())
        try:
            return self._run_loop(spec, task, ..., loop=loop)
        finally:
            loop.run_until_complete(spec.llm_provider.__aexit__(None, None, None))
    finally:
        loop.close()
```

### 5. Async 测试模式（Provider 测试）
```python
# Source: Phase 12 建立的 pytest-asyncio 基础设施
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

class TestAsyncChat:
    async def test_chat_content(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(content="Hello")

        # Patch __aenter__ to inject mock client
        with patch.object(provider, '_client', mock_client):
            result = await provider.chat([{"role": "user", "content": "Hi"}])

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello"
```

### 6. ContextCompactor async 改造
```python
# _summarize: 唯一改动是加 async + await
async def _summarize(self, old_messages: list[Message]) -> str:
    conversation_text = "\n".join(
        f"[{msg.role.value}]: {msg.content or ''}" for msg in old_messages
    )
    api_messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": f"Summarize this conversation:\n\n{conversation_text}"},
    ]
    response = await self._summary_provider.chat(api_messages)  # 唯一改动
    if not response.content:
        raise ValueError("Summary LLM returned empty content")
    return response.content

# compact_if_needed: 改为 async，内部 await self._summarize()
async def compact_if_needed(self, messages, last_usage, turn):
    # ... 前面的检查逻辑不变 ...
    try:
        summary = await self._summarize(old_messages)  # await
        # ... 后续逻辑不变 ...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| openai.OpenAI (sync) | openai.AsyncOpenAI (async) | SDK 1.0+ (2023-11) | 同一个 openai 包，两个 client 类 |
| httpx.Client | httpx.AsyncClient | httpx 0.24+ | Timeout 配置完全对称 |
| Iterator[StreamChunk] | AsyncIterator[StreamChunk] via async generator | Phase 12 Protocol | Protocol 已定义，Phase 13 实现 |

**SDK 版本说明：**
- openai 2.20.0: AsyncOpenAI 完全稳定。构造函数同步，`close()` 是 async coroutine。AsyncStream 同时支持 async iterator 和 async context manager。
- httpx 0.28.1: AsyncClient.aclose() 是 coroutine。Timeout 构造参数与同步版完全一致。

## Open Questions

1. **Kernel 桥接中 provider context manager 的管理位置**
   - What we know: Provider 需要 `async with` 管理生命周期。Kernel.run() 是 sync。
   - What's unclear: 桥接代码放在 Kernel.run() 入口（每次 run 创建/关闭 loop），还是放在更上层的 Exp/Service 层。
   - Recommendation: 放在 Kernel.run() 入口最简单，Phase 17 改造时直接移除桥接。如果 loop 创建/关闭开销可忽略（实测可忽略），这是最佳方案。

2. **ContextCompactor 桥接调用方式**
   - What we know: Kernel 的 `spec.compactor.compact_if_needed()` 调用现在也是 async。
   - What's unclear: 是用同一个 loop 桥接，还是让 compact_if_needed 在桥接内共享 loop。
   - Recommendation: 在 Kernel._call_llm / _do_stream_llm 中复用同一个 loop 实例，同时桥接 provider 和 compactor 的 async 调用。或者更简洁地，在 Kernel.run() 入口创建 loop 并传递到所有内部方法。

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 1.3.0 |
| Config file | pytest.ini (asyncio_mode=auto) |
| Quick run command | `uv run pytest tests/matmaster/providers/ -x` |
| Full suite command | `uv run pytest tests/matmaster/providers/ tests/matmaster/core/test_context_compactor.py -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| LLMP-01 | chat() 通过 AsyncOpenAI 调用，返回 LLMResponse | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatContent -x` | Wave 0 改造 |
| LLMP-01 | chat_stream() 返回 AsyncIterator[StreamChunk] | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamContent -x` | Wave 0 改造 |
| LLMP-02 | chat_stream async generator 可通过 async for 消费 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamContent::test_chat_stream_returns_iterator -x` | Wave 0 改造 |
| LLMP-02 | stream 异常正确映射为 LLMError | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamExceptionTranslation -x` | Wave 0 改造 |
| LLMP-03 | __aenter__ 创建 client | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestAsyncContextManager -x` | Wave 0 新增 |
| LLMP-03 | __aexit__ 关闭 client | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestAsyncContextManager -x` | Wave 0 新增 |
| LLMP-03 | 未进入 context 时调用 chat 报错 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestAsyncContextManager -x` | Wave 0 新增 |
| LLMP-03 | validate_async_protocol 检测通过 | unit | `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestProtocolConformance -x` | Wave 0 改造 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/providers/ tests/matmaster/core/test_context_compactor.py -x`
- **Per wave merge:** `uv run pytest tests/ -x --timeout=60`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [x] `tests/matmaster/providers/test_openai_provider.py` -- 需改造为 async 测试（现有 28 个测试）
- [x] `tests/matmaster/providers/test_llm_factory.py` -- 需适配新的 __init__ 不创建 client（现有 7 个测试）
- [x] `tests/matmaster/core/test_context_compactor.py` -- MockSummaryProvider/FailingSummaryProvider 改 async（现有 20 个测试）
- [ ] `tests/matmaster/providers/test_openai_provider.py::TestAsyncContextManager` -- 新增生命周期测试类
- [ ] 框架已就绪：pytest-asyncio 1.3.0 with asyncio_mode=auto

## Sources

### Primary (HIGH confidence)
- **openai SDK 2.20.0 (installed)** -- 直接在项目 venv 中 `import openai; inspect` 验证 AsyncOpenAI 构造函数同步、close() async、chat.completions.create coroutine、AsyncStream protocol
- **httpx 0.28.1 (installed)** -- 直接验证 AsyncClient.aclose() 是 coroutine、Timeout 配置参数与 Client 一致
- **asyncio stdlib** -- 验证 new_event_loop + run_until_complete 桥接模式可行

### Secondary (MEDIUM confidence)
- **Phase 12 established patterns** -- conftest.py async mock factories、validate_async_protocol helper、pytest-asyncio auto mode

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 所有库版本直接在项目 venv 中验证
- Architecture: HIGH - async context manager 模式和桥接方案都通过实际代码验证
- Pitfalls: HIGH - 基于 openai SDK 实际行为和 asyncio 已知限制

**Research date:** 2026-03-27
**Valid until:** 2026-04-27 (稳定技术栈，30 天有效)
