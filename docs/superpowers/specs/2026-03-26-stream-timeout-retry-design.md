# Stream Timeout & Retry Design

## Problem

`AgentKernel._call_llm()` 通过 `OpenAIProvider.chat_stream()` 调用 LLM，在复杂任务中报错 "The read operation timed out"。

根因分析：

1. **stream_timeout / stream_idle_timeout 未传递**：`LLMProfileConfig` 定义了这两个字段，`llm_config.yaml` 也配置了值，但 `build_provider()` → `OpenAIProvider.__init__()` 链路上被丢弃。OpenAI SDK 只拿到一个通用 `timeout`。
2. **缺少自定义 httpx Client**：旧架构 `OpenAILLM._setup()` 构建了四维超时的 `httpx.Client`（connect/read/write/pool），新架构 `OpenAIProvider` 使用 SDK 默认 httpx client，无法区分连接超时和读取超时。
3. **流式调用无重试**：旧架构 `BaseLLM._call_with_retry()` 有超时翻倍重试（timeout * 2 per retry），新架构 `chat_stream()` 一次超时直接抛异常，`AgentKernel` 也无 catch。

## Design

### 1. Custom Exception: `LLMError`

**新增文件**：`matmaster/types/errors.py`

```python
class LLMError(Exception):
    """LLM call exception. retryable indicates whether caller should retry."""
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable
```

Provider 使用 `raise LLMError(...) from original_exc` 保留原始异常链。

Provider 层负责将 SDK 异常翻译为 `LLMError`：

| SDK Exception | retryable | Rationale |
|---------------|-----------|-----------|
| `openai.APITimeoutError`, `httpx.ReadTimeout` | True | Transient timeout |
| `openai.APIConnectionError` | True | Transient connection failure |
| `openai.RateLimitError` | True | Transient rate limit |
| `openai.InternalServerError` | True | Transient server 500 |
| `openai.BadRequestError` (generic) | True | May succeed on retry |
| `openai.AuthenticationError`, `PermissionDeniedError` | False | Config error |
| `openai.BadRequestError` (context length) | False | Input too large |

### 2. OpenAIProvider Changes

**File**: `matmaster/providers/openai_provider.py`

#### 2a. `__init__` accepts stream timeout params

New parameters:
- `stream_timeout: float | None = None`
- `stream_idle_timeout: float | None = None`

Stored as `self._stream_timeout` and `self._stream_idle_timeout`. Expose as public read-only properties: `stream_timeout`, `stream_idle_timeout`, `max_retries`, `retry_delay`. Kernel uses `getattr()` with defaults to access these without coupling to concrete type.

#### 2b. Custom httpx.Client with granular timeout

httpx `Timeout` 有四个独立维度，每个控制不同阶段的 socket 等待：

- `connect`: TCP 握手 + TLS 协商超时。固定 15s 足够。
- `read`: 单次 `socket.recv()` 等待数据的超时。流式场景下这个值必须覆盖 chunk 间最大间隔。
- `write`: 发送请求体超时。固定 30s。
- `pool`: 从连接池获取可用连接的等待超时。固定 15s。

`read` 超时的计算逻辑：取 `stream_idle_timeout` 和 `stream_timeout` 的较大值，再加 10s 安全边距。加 10s 是因为 httpx read timeout 必须大于 openai SDK 层的 stream timeout，避免 httpx 比 SDK 先触发超时导致异常类型不可预测。

```python
import httpx

_first_token_t = stream_timeout if stream_timeout is not None else timeout
_idle_t = stream_idle_timeout if stream_idle_timeout is not None else timeout
_read_t = float(max(_idle_t, _first_token_t) + 10)  # +10s safety margin over SDK timeout

http_client = httpx.Client(timeout=httpx.Timeout(
    connect=15.0,
    read=_read_t,
    write=30.0,
    pool=15.0,
))

self._client = openai.OpenAI(
    api_key=api_key,
    base_url=base_url,
    timeout=timeout,
    max_retries=0,
    http_client=http_client,
)
```

#### 2c. `chat_stream()` accepts optional timeout override

New optional keyword parameter `timeout: float | None = None` for per-request timeout override (used by kernel retry with doubled timeout). When provided, passed to openai SDK's per-request `timeout` kwarg.

`LLMProvider` Protocol 同步更新签名（见 Section 6）。

#### 2d. `chat_stream()` translates exceptions to `LLMError`

Wrap the generator body with try/except, catch `openai.*` and `httpx.*` exceptions, `raise LLMError(message, retryable=...) from exc`.

### 3. `build_provider()` passes stream timeout params

**File**: `matmaster/providers/llm_factory.py`

```python
return OpenAIProvider(
    ...
    timeout=profile.timeout,
    stream_timeout=profile.stream_timeout,
    stream_idle_timeout=profile.stream_idle_timeout,
    ...
)
```

### 4. `AgentKernel._call_llm()` retry logic

**File**: `matmaster/core/agent.py`

Refactor `_call_llm()`:
- Extract current streaming + accumulation logic into `_do_stream_llm(spec, messages, timeout)`.
- `_call_llm()` becomes a retry wrapper around `_do_stream_llm()`.

AgentKernel.run() runs in an independent thread (dispatched by agent_run_service via Worker or ThreadPoolExecutor). `time.sleep()` blocks only this thread, not the main event loop.

```
_call_llm(spec, messages):
    provider = spec.llm_provider
    current_timeout = getattr(provider, 'stream_timeout', None) or getattr(provider, '_timeout', 300)
    max_retries = getattr(provider, 'max_retries', 3)
    retry_delay = getattr(provider, 'retry_delay', 1.0)

    for attempt in range(max_retries):
        try:
            return _do_stream_llm(spec, messages, timeout=current_timeout)
        except LLMError as e:
            if not e.retryable:
                raise
            last_error = e
            current_timeout *= 2  # double on each retry
            if attempt < max_retries - 1:
                backoff = retry_delay * (2 ** attempt)
                time.sleep(backoff)
    raise RuntimeError("LLM stream failed after N attempts") from last_error
```

Retry params accessed via `getattr()` with sensible defaults, keeping kernel decoupled from concrete provider type.

On each retry, `_do_stream_llm` reinitializes all accumulators (content_parts, reasoning_parts, tool_calls_acc, etc.), so no partial data leaks.

### 5. Config Value Updates

**File**: `matmaster_config/llm_config.yaml`

Target values (replace current YAML values during implementation):

All 7 profiles:

| Profile | timeout | stream_timeout | stream_idle_timeout |
|---------|---------|----------------|---------------------|
| opus | 1200 | 120 | 60 |
| sonnet | 1200 | 120 | 60 |
| haiku | 1200 | 60 | 30 |
| gemini | 1200 | 60 | 30 |
| gemini-pro | 1200 | 120 | 60 |
| gpt54 | 1200 | 120 | 60 |
| compaction | 1200 | 60 | 30 |

With timeout-doubling retry (3 attempts), worst-case effective wait for opus first-token: 120 + backoff(1s) + 240 + backoff(2s) + 480 = ~843s (~14min).

### 6. LLMProvider Protocol Update

**File**: `matmaster/types/llm_provider.py`

`chat_stream()` 签名新增可选 `timeout` 参数：

```python
def chat_stream(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    timeout: float | None = None,
) -> Iterator[StreamChunk]: ...
```

This is backward-compatible: existing callers that don't pass `timeout` continue to work. The kernel retry loop passes `timeout=current_timeout` on each attempt.

## Files Changed

| File | Change |
|------|--------|
| `matmaster/types/errors.py` | **New**: `LLMError(message, retryable)` |
| `matmaster/types/llm_provider.py` | `chat_stream()` adds optional `timeout` kwarg |
| `matmaster/providers/openai_provider.py` | Accept stream timeout params; build custom httpx.Client; chat_stream() timeout override + exception translation |
| `matmaster/providers/llm_factory.py` | Pass `stream_timeout`, `stream_idle_timeout` to `build_provider()` |
| `matmaster/core/agent.py` | Extract `_do_stream_llm()`; `_call_llm()` retry wrapper catching `LLMError` |
| `matmaster_config/llm_config.yaml` | Update timeout values for all 7 profiles |

## Not Changed

- `AgentKernel.run()` loop logic
- Hook system
- Old architecture code (`evomaster/`)
