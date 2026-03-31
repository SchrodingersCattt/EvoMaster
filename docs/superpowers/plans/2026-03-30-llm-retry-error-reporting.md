# LLM Retry Error Reporting Enhancement — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance `_call_llm` retry error reporting with structured logging per attempt, richer `LLMError` with `error_category` + `attempts` history, and `degraded` flag on `LLMResponse` for incomplete responses.

**Architecture:** Extend `LLMError` with two optional fields (`error_category`, `attempts`). Provider layer tags each exception with a category. Kernel layer records per-attempt structured info, emits enhanced logs, and raises `LLMError` (not `RuntimeError`) on exhaustion. `LLMResponse` gains a `degraded: bool` field for incomplete response marking.

**Tech Stack:** Python, Pydantic, pytest, asyncio

**Spec:** `docs/superpowers/specs/2026-03-30-llm-retry-error-reporting-design.md`

---

## Chunk 1: Foundation — LLMError + LLMResponse + Provider

### Task 1: Extend LLMError with error_category and attempts

**Files:**
- Modify: `matmaster/types/errors.py:6-11`
- Test: `tests/matmaster/core/test_agent.py`

- [ ] **Step 1: Write the failing test**

In `tests/matmaster/core/test_agent.py`, add a test at the end of the `TestCallLlmRetry` class:

```python
async def test_llm_error_carries_category_and_attempts(self) -> None:
    """LLMError can carry error_category and attempts fields."""
    attempts = [{"attempt": 1, "error_type": "APITimeoutError"}]
    err = LLMError(
        "test",
        retryable=False,
        error_category="timeout",
        attempts=attempts,
    )
    assert err.error_category == "timeout"
    assert err.attempts == attempts
    assert not err.retryable

    # Backward compat: omitting new fields still works
    basic = LLMError("basic", retryable=True)
    assert basic.error_category is None
    assert basic.attempts is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry::test_llm_error_carries_category_and_attempts -xvs`
Expected: FAIL — `LLMError.__init__() got an unexpected keyword argument 'error_category'`

- [ ] **Step 3: Write minimal implementation**

Replace the entire `LLMError` class in `matmaster/types/errors.py`:

```python
"""Custom exceptions for the matmaster LLM layer."""

from __future__ import annotations

from typing import Any


class LLMError(Exception):
    """LLM call exception. retryable indicates whether caller should retry."""

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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry::test_llm_error_carries_category_and_attempts -xvs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/types/errors.py tests/matmaster/core/test_agent.py
git commit -m "feat(errors): extend LLMError with error_category and attempts fields"
```

### Task 2: Add degraded field to LLMResponse

**Files:**
- Modify: `matmaster/types/messages.py:120-128`

- [ ] **Step 1: Write the failing test**

Add to `tests/matmaster/core/test_agent.py` at the end of the file (standalone test, not inside a class):

```python
class TestLLMResponseDegraded:
    def test_degraded_defaults_false(self) -> None:
        """LLMResponse.degraded defaults to False."""
        resp = LLMResponse(content="hello", finish_reason="stop")
        assert resp.degraded is False

    def test_degraded_can_be_set(self) -> None:
        """LLMResponse.degraded can be explicitly set."""
        resp = LLMResponse(content="hello", finish_reason="stop", degraded=True)
        assert resp.degraded is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent.py::TestLLMResponseDegraded -xvs`
Expected: FAIL — `unexpected keyword argument 'degraded'`

- [ ] **Step 3: Write minimal implementation**

In `matmaster/types/messages.py`, add `degraded` field to `LLMResponse` (line 127, after `usage`):

```python
class LLMResponse(BaseModel):
    """Non-streaming LLM response from LLMProvider.chat()."""

    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCallData] | None = None
    finish_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    degraded: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent.py::TestLLMResponseDegraded -xvs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/types/messages.py tests/matmaster/core/test_agent.py
git commit -m "feat(messages): add degraded field to LLMResponse"
```

### Task 3: Add error_category to provider except branches

**Files:**
- Modify: `matmaster/providers/openai_provider.py:333-348`
- Test: `tests/matmaster/providers/test_openai_provider.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/matmaster/providers/test_openai_provider.py` as a new class. Follow the existing `TestChatStreamExceptionTranslation` pattern: use `_make_provider()` helper that directly sets `provider._client` (do NOT use `async with provider:`).

```python
class TestChatStreamErrorCategory:
    """Verify chat_stream raises LLMError with correct error_category."""

    def _make_provider(self) -> tuple[OpenAIProvider, AsyncMock]:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        provider._client = mock_client
        return provider, mock_client

    async def test_timeout_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APITimeoutError(request=MagicMock())
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}]
            )]
        assert exc_info.value.error_category == "timeout"
        assert exc_info.value.retryable is True

    async def test_connection_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}]
            )]
        assert exc_info.value.error_category == "connection"

    async def test_rate_limit_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.RateLimitError(
                response=MagicMock(status_code=429, headers={}),
                body=None, message="rate limited",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}]
            )]
        assert exc_info.value.error_category == "rate_limit"

    async def test_server_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.InternalServerError(
                response=MagicMock(status_code=500, headers={}),
                body=None, message="server error",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}]
            )]
        assert exc_info.value.error_category == "server"

    async def test_auth_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.AuthenticationError(
                response=MagicMock(status_code=401, headers={}),
                body=None, message="invalid key",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}]
            )]
        assert exc_info.value.error_category == "auth"
        assert exc_info.value.retryable is False

    async def test_context_overflow_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.BadRequestError(
                response=MagicMock(status_code=400, headers={}),
                body=None,
                message="This model's maximum context length is 8192 tokens",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}]
            )]
        assert exc_info.value.error_category == "context_overflow"
        assert exc_info.value.retryable is False

    async def test_bad_request_category(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.BadRequestError(
                response=MagicMock(status_code=400, headers={}),
                body=None,
                message="invalid parameter",
            )
        )
        with pytest.raises(LLMError) as exc_info:
            _ = [c async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}]
            )]
        assert exc_info.value.error_category == "bad_request"
        assert exc_info.value.retryable is True
```

All imports (`MagicMock`, `AsyncMock`, `openai`, `LLMError`, `pytest`) already exist in this test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamErrorCategory -xvs`
Expected: FAIL — `assert exc_info.value.error_category == "timeout"` fails because `error_category` is None

- [ ] **Step 3: Write implementation**

In `matmaster/providers/openai_provider.py`, the current code at lines 333-348 has a **combined 4-type except block** that must be **split into 4 separate except blocks** (each needs its own `error_category`).

Replace the old except blocks (lines 333-348):

Old code:
```python
        except (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
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

New code:
```python
        except openai.APITimeoutError as exc:
            raise LLMError(str(exc), retryable=True, error_category="timeout") from exc
        except openai.APIConnectionError as exc:
            raise LLMError(str(exc), retryable=True, error_category="connection") from exc
        except openai.RateLimitError as exc:
            raise LLMError(str(exc), retryable=True, error_category="rate_limit") from exc
        except openai.InternalServerError as exc:
            raise LLMError(str(exc), retryable=True, error_category="server") from exc
        except _httpx.ReadTimeout as exc:
            raise LLMError(str(exc), retryable=True, error_category="timeout") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LLMError(str(exc), retryable=False, error_category="auth") from exc
        except openai.BadRequestError as exc:
            err_str = str(exc).lower()
            if "context" in err_str and ("length" in err_str or "token" in err_str):
                raise LLMError(str(exc), retryable=False, error_category="context_overflow") from exc
            raise LLMError(str(exc), retryable=True, error_category="bad_request") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamErrorCategory -xvs`
Expected: PASS

- [ ] **Step 5: Run all provider tests to ensure no regression**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/providers/ -x`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/providers/openai_provider.py tests/matmaster/providers/test_openai_provider.py
git commit -m "feat(provider): add error_category to all chat_stream exception branches"
```

---

## Chunk 2: Kernel _call_llm Enhancement

### Task 4: Enhance _call_llm with structured attempt records and logging

**Files:**
- Modify: `matmaster/core/agent.py:14,255-306`
- Test: `tests/matmaster/core/test_agent.py`

- [ ] **Step 1: Write the failing tests**

**IMPORTANT:** `test_all_retries_exhausted` already exists at line 1188-1202. This is a **replacement** of the existing test (change `RuntimeError` to `LLMError` and add assertions). Also add one new test method after it.

Replace existing `test_all_retries_exhausted` (lines 1188-1202) and add `test_retry_exhausted_carries_attempt_records` after it:

```python
    async def test_all_retries_exhausted(self) -> None:
        """_call_llm raises LLMError (not RuntimeError) after all retries exhausted."""
        provider = ErrorThenSuccessProvider(
            fail_count=99,
            error=LLMError("timeout", retryable=True),
        )
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        with pytest.raises(LLMError, match="LLM stream failed") as exc_info:
            await kernel._call_llm(spec, [UserMessage(content="hi")])
        assert provider._call_count == 3  # max_retries default
        assert exc_info.value.retryable is False
        assert exc_info.value.attempts is not None
        assert len(exc_info.value.attempts) == 3

    async def test_retry_exhausted_carries_attempt_records(self) -> None:
        """Each attempt record has the required structured fields."""
        provider = ErrorThenSuccessProvider(
            fail_count=99,
            error=LLMError("conn refused", retryable=True, error_category="connection"),
        )
        provider.stream_timeout = 10.0
        provider.max_retries = 2
        provider.retry_delay = 0.0  # no wait in tests
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        with pytest.raises(LLMError) as exc_info:
            await kernel._call_llm(spec, [UserMessage(content="hi")])

        err = exc_info.value
        assert err.error_category == "connection"
        assert len(err.attempts) == 2
        for i, rec in enumerate(err.attempts):
            assert rec["attempt"] == i + 1
            assert rec["error_category"] == "connection"
            assert rec["error_type"] == "LLMError"
            assert "conn refused" in rec["error_message"]
            assert "timeout_used" in rec
            assert "elapsed_seconds" in rec
            assert rec["retryable"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry::test_all_retries_exhausted tests/matmaster/core/test_agent.py::TestCallLlmRetry::test_retry_exhausted_carries_attempt_records -xvs`
Expected: FAIL — `test_all_retries_exhausted` catches `RuntimeError` not `LLMError`; `test_retry_exhausted_carries_attempt_records` doesn't exist yet

- [ ] **Step 3: Write implementation**

In `matmaster/core/agent.py`:

First, add `import time` at line 19 (after `import threading`):

```python
import time
```

Then replace the `_call_llm` method (lines 255-306) with:

```python
    async def _call_llm(
        self,
        spec: AgentRuntimeSpec,
        messages: list[Message],
    ) -> LLMResponse:
        """Call LLM with timeout-doubling retry on transient errors."""
        provider = spec.llm_provider
        current_timeout = getattr(provider, "stream_timeout", None) or getattr(
            provider, "_timeout", 300.0
        )
        max_retries = getattr(provider, "max_retries", 3)
        retry_delay = getattr(provider, "retry_delay", 1.0)

        attempt_records: list[dict[str, Any]] = []
        last_error: LLMError | None = None
        for attempt in range(max_retries):
            t0 = time.monotonic()
            try:
                response = await self._do_stream_llm(spec, messages, timeout=current_timeout)
                elapsed = time.monotonic() - t0

                if (
                    self._is_incomplete_response(response)
                    and attempt < max_retries - 1
                ):
                    backoff = retry_delay * (2**attempt)
                    attempt_records.append({
                        "attempt": attempt + 1,
                        "error_type": "IncompleteResponse",
                        "error_category": "incomplete_response",
                        "error_message": "reasoning-only response without content",
                        "timeout_used": current_timeout,
                        "elapsed_seconds": round(elapsed, 2),
                        "retryable": True,
                        "backoff_seconds": backoff,
                    })
                    logger.warning(
                        "LLM returned reasoning without content "
                        "(attempt %d/%d, elapsed=%.1fs), retrying.",
                        attempt + 1,
                        max_retries,
                        elapsed,
                    )
                    await asyncio.sleep(backoff)
                    continue

                # Last attempt still incomplete — return degraded
                if self._is_incomplete_response(response):
                    logger.warning(
                        "LLM returned incomplete response after %d attempts, "
                        "returning degraded result.",
                        max_retries,
                    )
                    response.degraded = True
                return response
            except LLMError as e:
                elapsed = time.monotonic() - t0
                if not e.retryable:
                    raise
                last_error = e
                next_timeout = current_timeout * 2
                backoff = retry_delay * (2**attempt) if attempt < max_retries - 1 else 0.0
                attempt_records.append({
                    "attempt": attempt + 1,
                    "error_type": type(e.__cause__).__name__ if e.__cause__ else type(e).__name__,
                    "error_category": getattr(e, "error_category", None),
                    "error_message": str(e),
                    "timeout_used": current_timeout,
                    "elapsed_seconds": round(elapsed, 2),
                    "retryable": e.retryable,
                    "next_timeout": next_timeout,
                    "backoff_seconds": backoff,
                })
                logger.warning(
                    "LLM call failed (attempt %d/%d) [%s]: %s "
                    "(timeout=%.0fs, elapsed=%.1fs, backoff=%.1fs, next_timeout=%.0fs)",
                    attempt + 1,
                    max_retries,
                    getattr(e, "error_category", None) or "unknown",
                    e,
                    current_timeout,
                    elapsed,
                    backoff,
                    next_timeout,
                )
                current_timeout = next_timeout
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff)

        # Retries exhausted
        if last_error is not None:
            msg = (
                f"LLM stream failed after {max_retries} attempts: "
                f"last error [{last_error.error_category or 'unknown'}] {last_error}"
            )
            category = last_error.error_category
        else:
            msg = (
                f"LLM stream failed after {max_retries} attempts: "
                f"all attempts returned incomplete responses"
            )
            category = "incomplete_response"

        raise LLMError(
            msg,
            retryable=False,
            error_category=category,
            attempts=attempt_records,
        ) from last_error
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry -xvs`
Expected: All PASS

- [ ] **Step 5: Run full agent test suite**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent.py -x`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent.py
git commit -m "feat(kernel): enhance _call_llm with structured retry records and LLMError on exhaustion"
```

### Task 5: Test incomplete response degraded marking

**Files:**
- Test: `tests/matmaster/core/test_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `TestCallLlmRetry` in `tests/matmaster/core/test_agent.py`:

```python
    async def test_incomplete_response_marked_degraded(self) -> None:
        """When all attempts return incomplete response, last one is returned with degraded=True."""
        call_count = 0

        class AlwaysIncompleteProvider:
            stream_timeout = 10.0
            max_retries = 2
            retry_delay = 0.0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages, tools=None):
                return LLMResponse(content="not used", finish_reason="stop")

            async def chat_stream(self, messages, tools=None, *, timeout=None):
                nonlocal call_count
                call_count += 1
                # reasoning only, no content, no tool_calls
                yield StreamChunk(reasoning_content="thinking...", finish_reason="stop")

        spec = AgentRuntimeSpec(
            llm_provider=AlwaysIncompleteProvider(),
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        response = await kernel._call_llm(spec, [UserMessage(content="hi")])
        assert response.degraded is True
        assert response.reasoning_content is not None
        assert response.content is None
        assert call_count == 2  # tried max_retries times
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry::test_incomplete_response_marked_degraded -xvs`
Expected: PASS (implementation was done in Task 4)

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/ -x`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/matmaster/core/test_agent.py
git commit -m "test(kernel): add incomplete response degraded marking test"
```
