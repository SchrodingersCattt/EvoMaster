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

Provider 层负责将 SDK 异常翻译为 `LLMError`：

| SDK Exception | retryable | Rationale |
|---------------|-----------|-----------|
| `openai.APITimeoutError`, `httpx.ReadTimeout` | True | Transient timeout |
| `openai.APIConnectionError` | True | Transient connection failure |
| `openai.RateLimitError` | True | Transient rate limit |
| `openai.BadRequestError` (generic) | True | May succeed on retry |
| `openai.AuthenticationError`, `PermissionDeniedError` | False | Config error |
| `openai.BadRequestError` (context length) | False | Input too large |

### 2. OpenAIProvider Changes

**File**: `matmaster/providers/openai_provider.py`

#### 2a. `__init__` accepts stream timeout params

New parameters:
- `stream_timeout: float | None = None`
- `stream_idle_timeout: float | None = None`

#### 2b. Custom httpx.Client with granular timeout

```python
import httpx

_first_token_t = stream_timeout if stream_timeout is not None else timeout
_read_t = float(max(stream_idle_timeout or timeout, _first_token_t) + 10)

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

New optional parameter `timeout: float | None = None` for per-request timeout override (used by kernel retry with doubled timeout).

#### 2d. `chat_stream()` translates exceptions to `LLMError`

Wrap the generator body with try/except, catch `openai.*` and `httpx.*` exceptions, raise `LLMError(message, retryable=...)`.

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

```
_call_llm(spec, messages):
    current_timeout = spec.llm_provider stream_timeout or timeout
    for attempt in range(max_retries):
        try:
            return _do_stream_llm(spec, messages, timeout=current_timeout)
        except LLMError as e:
            if not e.retryable:
                raise
            current_timeout *= 2  # double on each retry
            backoff = retry_delay * (2 ** attempt)
            sleep(backoff)
    raise RuntimeError("LLM stream failed after N attempts") from last_error
```

On each retry, `_do_stream_llm` reinitializes all accumulators (content_parts, reasoning_parts, tool_calls_acc, etc.), so no partial data leaks.

### 5. Config Value Updates

**File**: `matmaster_config/llm_config.yaml`

All profiles:

| Parameter | opus/sonnet | haiku | gemini/compaction |
|-----------|------------|-------|-------------------|
| `timeout` | 1200 | 1200 | 1200 |
| `stream_timeout` | 120 | 60 | 60 |
| `stream_idle_timeout` | 60 | 30 | 30 |

With timeout-doubling retry (3 attempts), worst-case effective wait for opus first-token: 120 + 240 + 480 = 840s (~14min).

## Files Changed

| File | Change |
|------|--------|
| `matmaster/types/errors.py` | **New**: `LLMError(message, retryable)` |
| `matmaster/providers/openai_provider.py` | Accept stream timeout params; build custom httpx.Client; chat_stream() timeout override + exception translation |
| `matmaster/providers/llm_factory.py` | Pass `stream_timeout`, `stream_idle_timeout` to `build_provider()` |
| `matmaster/core/agent.py` | Extract `_do_stream_llm()`; `_call_llm()` retry wrapper catching `LLMError` |
| `matmaster_config/llm_config.yaml` | Update timeout values |

## Not Changed

- `LLMProvider` Protocol interface
- `AgentKernel.run()` loop logic
- Hook system
- Old architecture code (`evomaster/`)
