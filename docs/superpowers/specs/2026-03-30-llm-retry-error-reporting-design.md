# LLM Retry Error Reporting Enhancement

## Problem

`AgentKernel._call_llm` 的重试循环在最终失败时只抛出一句 `"LLM stream failed after N attempts"` 的 `RuntimeError`，中间每次重试的具体错误信息（错误类型、超时值、耗时等）仅存在于 warning 日志中且格式宽泛。运维排障时难以快速定位根因；上层展示时也缺乏结构化信息。

此外，`_is_incomplete_response`（reasoning-only 响应）重试耗尽后静默返回不完整 response，调用方无法区分正常响应与降级结果。

## Design Decisions

- **方案选择**: 增强现有 LLMError + 结构化日志（方案 A），不引入新异常类
- **日志侧**: 每次重试记录结构化字段，便于运维 grep 分析
- **异常侧**: 最终异常从 RuntimeError 改为 LLMError，携带完整重试历史
- **incomplete response**: 保持返回但标记 degraded，不改为抛异常
- **向后兼容**: 所有新字段有默认值，现有调用方零改动
- **Breaking change**: 最终异常从 `RuntimeError` 改为 `LLMError`。由于 `LLMError` 不是 `RuntimeError` 子类，任何用 `except RuntimeError` 捕获的代码需要改为 `except LLMError`。当前 service 层使用 `except Exception` 兜底，不受影响。受影响的 catch 点见 Tests 节
- **Scope**: `chat_with_retry` 是 legacy sync 方法（注释标注待删除），本次不改，异步化完成时统一清理
- **计时**: 使用 `time.monotonic()` 而非 `time.time()`，不受系统时钟调整影响

## Changes

### 1. LLMError Extension (`matmaster/types/errors.py`)

扩展 `LLMError`，新增两个可选属性：

```python
class LLMError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        error_category: str | None = None,
        attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.error_category = error_category
        self.attempts = attempts
```

- `error_category`: 错误分类标签，由 provider 层设置。枚举值：`"timeout"` | `"rate_limit"` | `"connection"` | `"server"` | `"auth"` | `"context_overflow"` | `"bad_request"` | `"incomplete_response"`
- `attempts`: 仅在重试耗尽的最终异常中填充，记录每次尝试的结构化信息

### 2. Provider Layer — error_category (`matmaster/providers/openai_provider.py`)

在 `chat_stream` 的各 except 分支中补上 `error_category` 参数：

| 异常类型 | error_category |
|----------|---------------|
| `APITimeoutError` | `"timeout"` |
| `APIConnectionError` | `"connection"` |
| `RateLimitError` | `"rate_limit"` |
| `InternalServerError` | `"server"` |
| `httpx.ReadTimeout` | `"timeout"` |
| `AuthenticationError` / `PermissionDeniedError` | `"auth"` |
| `BadRequestError` (context length) | `"context_overflow"` |
| `BadRequestError` (other) | `"bad_request"` |

改动仅在已有 except 分支中多传一个关键字参数，retryable 语义不变。

### 3. Kernel Layer — `_call_llm` Enhancement (`matmaster/core/agent.py`)

#### 3.1 每次尝试记录结构化信息

引入 `attempt_records: list[dict]`，每次重试（无论 LLMError 还是 incomplete response）都追加一条记录，包含以下字段：

- `attempt` — 第几次尝试（如 1）
- `error_type` — 异常类名（如 APITimeoutError）；incomplete response 路径填 `"IncompleteResponse"`
- `error_category` — 分类标签
- `error_message` — 原始错误信息
- `timeout_used` — 本次使用的 timeout 值
- `elapsed_seconds` — 本次 LLM 调用耗时
- `retryable` — 是否可重试
- `next_timeout` — 下次 timeout（所有 retryable 错误都翻倍 timeout，保持现有行为）
- `backoff_seconds` — 等待时间

#### 3.2 结构化日志

每次重试的 warning 日志增强为：

```
LLM call failed (attempt 1/3) [timeout]: Connection timed out (timeout=300s, elapsed=300.1s, backoff=1.0s, next_timeout=600s)
```

#### 3.3 最终异常

重试耗尽时抛出 `LLMError`（而非 RuntimeError），携带完整历史。需要处理 `last_error` 为 None 的情况（所有重试都是 incomplete response 时不会产生异常）：

```python
if last_error is not None:
    msg = (
        f"LLM stream failed after {max_retries} attempts: "
        f"last error [{last_error.error_category or 'unknown'}] {last_error}"
    )
    category = last_error.error_category
else:
    msg = f"LLM stream failed after {max_retries} attempts: all attempts returned incomplete responses"
    category = "incomplete_response"

raise LLMError(
    msg,
    retryable=False,
    error_category=category,
    attempts=attempt_records,
) from last_error
```

#### 3.4 incomplete response 降级标记

- 重试期间记录到 `attempt_records`，分类为 `"incomplete_response"`
- 最后一次仍然 incomplete 时，在 response 上标记 `degraded = True` 并返回
- 日志记录降级事实

#### 3.5 新增 import

`agent.py` 顶部加 `import time`（用于 `time.monotonic()` 计时）。

### 4. LLMResponse degraded field (`matmaster/types/messages.py`)

```python
class LLMResponse(BaseModel):
    # ... existing fields ...
    degraded: bool = False  # incomplete response 降级标记
```

### 5. Test Updates (`tests/matmaster/core/test_agent.py`)

- **更新** `test_all_retries_exhausted`: catch `LLMError` 而非 `RuntimeError`，验证 `error_category` 和 `attempts` 长度
- **新增** `test_retry_exhausted_carries_attempt_records`: 验证最终异常的 `.attempts` 包含每次尝试的完整结构化字段
- **新增** `test_incomplete_response_marked_degraded`: 验证 incomplete response 耗尽后返回的 `response.degraded is True`

## Non-goals

- `chat_with_retry` legacy sync 方法不在本次范围内，异步化完成时统一清理
- `finish_reason == "length"` 截断不纳入 incomplete 检测范围，与本次 degraded 标记无关
- `attempts` 字段使用 `list[dict[str, Any]]` 而非 TypedDict/dataclass — 作为异常属性，轻量 dict 足够，后续如需序列化再收紧类型

## Known Limitations

- `_do_stream_llm` 的 `finally` 块会调用 hooks，如果 hook 抛异常会覆盖原始 `LLMError`。这是已有问题，不在本次修复范围

## Files Changed

| File | Change |
|------|--------|
| `matmaster/types/errors.py` | LLMError 增加 2 个可选字段 |
| `matmaster/types/messages.py` | LLMResponse 增加 `degraded` 字段 |
| `matmaster/providers/openai_provider.py` | 各 except 分支加 `error_category` |
| `matmaster/core/agent.py` | `_call_llm` 重试循环增强 + `import time` |
| `tests/matmaster/core/test_agent.py` | 更新 1 个 + 新增 2 个测试 |
