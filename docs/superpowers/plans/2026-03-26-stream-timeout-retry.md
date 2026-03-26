# Stream Timeout & Retry Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix "The read operation timed out" by adding stream-specific timeouts, custom httpx client, and timeout-doubling retry to the LLM streaming call chain.

**Architecture:** Provider layer translates SDK exceptions to `LLMError(retryable=bool)`. Kernel layer retries with timeout doubling on retryable errors. Custom httpx client with granular (connect/read/write/pool) timeouts replaces SDK defaults.

**Tech Stack:** Python 3.10+, openai SDK, httpx, pytest

---

## Chunk 1: Foundation — LLMError + Provider Changes

### Task 1: Create `LLMError` exception class

**Files:**
- Create: `matmaster/types/errors.py`
- Test: `tests/matmaster/types/test_errors.py`

- [ ] **Step 1: Write tests for LLMError**

```python
# tests/matmaster/types/test_errors.py
"""Tests for LLMError custom exception."""

from matmaster.types.errors import LLMError


class TestLLMError:
    def test_retryable_default_true(self) -> None:
        err = LLMError("timeout")
        assert err.retryable is True
        assert str(err) == "timeout"

    def test_retryable_false(self) -> None:
        err = LLMError("auth failed", retryable=False)
        assert err.retryable is False

    def test_exception_chaining(self) -> None:
        original = ValueError("original")
        err = LLMError("wrapped", retryable=True)
        err.__cause__ = original
        assert err.__cause__ is original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/types/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'matmaster.types.errors'`

- [ ] **Step 3: Implement LLMError**

```python
# matmaster/types/errors.py
"""Custom exceptions for the matmaster LLM layer."""

from __future__ import annotations


class LLMError(Exception):
    """LLM call exception. retryable indicates whether caller should retry."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/types/test_errors.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add matmaster/types/errors.py tests/matmaster/types/test_errors.py
git commit -m "feat: add LLMError exception with retryable flag"
```

---

### Task 2: Update LLMProvider Protocol — add timeout kwarg to chat_stream

**Files:**
- Modify: `matmaster/types/llm_provider.py:42-46`
- Modify: `tests/matmaster/types/test_llm_provider.py` (if Protocol conformance tests exist)

- [ ] **Step 1: Write test for updated Protocol signature**

Add to `tests/matmaster/types/test_llm_provider.py` (or create if needed):

```python
def test_chat_stream_accepts_timeout_kwarg() -> None:
    """Protocol allows optional timeout keyword argument."""
    import inspect
    from matmaster.types.llm_provider import LLMProvider

    sig = inspect.signature(LLMProvider.chat_stream)
    assert "timeout" in sig.parameters
    param = sig.parameters["timeout"]
    assert param.default is None
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/types/test_llm_provider.py::test_chat_stream_accepts_timeout_kwarg -v`
Expected: FAIL — `timeout` not in parameters

- [ ] **Step 3: Update Protocol**

In `matmaster/types/llm_provider.py`, change `chat_stream` signature:

```python
    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> Iterator[StreamChunk]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/types/test_llm_provider.py -v`
Expected: all passed

- [ ] **Step 5: Update MockLLMProvider in conftest**

In `tests/matmaster/core/conftest.py`, update `MockLLMProvider.chat_stream` and `StreamingProvider.chat_stream` signatures to accept `timeout`:

```python
    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> Iterator[StreamChunk]:
```

Do the same for `StreamingProvider` and `ToolCallingProvider` in `tests/matmaster/core/test_agent.py`. Also update all inline `chat_stream` definitions in test classes throughout that file (search for `def chat_stream` — there are ~10 occurrences including inline provider classes in test methods). Add `*, timeout=None` or `**kwargs` to each.

Also update `CompleteLLMProvider` and `MissingRetryProvider` in `tests/matmaster/types/test_llm_provider.py` — add `*, timeout: float | None = None` to their `chat_stream` signatures.

- [ ] **Step 6: Run all existing tests to verify no regressions**

Run: `uv run pytest tests/matmaster/ -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add matmaster/types/llm_provider.py tests/
git commit -m "feat: add optional timeout kwarg to LLMProvider.chat_stream Protocol"
```

---

### Task 3: OpenAIProvider — stream timeout params + custom httpx client

**Files:**
- Modify: `matmaster/providers/openai_provider.py:31-55`
- Modify: `tests/matmaster/providers/test_openai_provider.py`

- [ ] **Step 1: Write tests for new constructor params and properties**

Add to `tests/matmaster/providers/test_openai_provider.py`:

```python
class TestStreamTimeoutConstruction:
    def test_stream_timeout_stored(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                stream_timeout=120.0,
                stream_idle_timeout=60.0,
            )
        assert provider.stream_timeout == 120.0
        assert provider.stream_idle_timeout == 60.0

    def test_stream_timeout_defaults_none(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        assert provider.stream_timeout is None
        assert provider.stream_idle_timeout is None

    def test_max_retries_property(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(
                model="gpt-4o-mini", api_key="sk-test", max_retries=5
            )
        assert provider.max_retries == 5

    def test_retry_delay_property(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI"):
            provider = OpenAIProvider(
                model="gpt-4o-mini", api_key="sk-test", retry_delay=2.0
            )
        assert provider.retry_delay == 2.0

    def test_custom_httpx_client_created(self) -> None:
        """When stream timeouts provided, custom httpx.Client is passed to OpenAI."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                timeout=1200.0,
                stream_timeout=120.0,
                stream_idle_timeout=60.0,
            )
            call_kwargs = mock_cls.call_args
            assert "http_client" in call_kwargs.kwargs
            http_client = call_kwargs.kwargs["http_client"]
            # read timeout = max(60, 120) + 10 = 130
            assert http_client.timeout.read == 130.0
            assert http_client.timeout.connect == 15.0
            assert http_client.timeout.write == 30.0
            assert http_client.timeout.pool == 15.0

    def test_httpx_client_fallback_without_stream_timeouts(self) -> None:
        """Without stream timeouts, httpx client uses general timeout for read."""
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            OpenAIProvider(
                model="gpt-4o-mini",
                api_key="sk-test",
                timeout=300.0,
            )
            call_kwargs = mock_cls.call_args
            http_client = call_kwargs.kwargs["http_client"]
            # read timeout = max(300, 300) + 10 = 310
            assert http_client.timeout.read == 310.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestStreamTimeoutConstruction -v`
Expected: FAIL — `stream_timeout` not recognized / no `http_client`

- [ ] **Step 3: Implement constructor changes**

Update `matmaster/providers/openai_provider.py` `__init__`:

```python
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 300.0,
        stream_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._stream_timeout = stream_timeout
        self._stream_idle_timeout = stream_idle_timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._extra_kwargs = extra_kwargs or {}

        # Build custom httpx client with granular timeout.
        # read timeout must exceed SDK stream timeout to avoid httpx
        # triggering ReadTimeout before the SDK's own timeout handling.
        import httpx

        _first_token_t = stream_timeout if stream_timeout is not None else timeout
        _idle_t = stream_idle_timeout if stream_idle_timeout is not None else timeout
        _read_t = float(max(_idle_t, _first_token_t) + 10)

        http_client = httpx.Client(
            timeout=httpx.Timeout(
                connect=15.0,
                read=_read_t,
                write=30.0,
                pool=15.0,
            )
        )

        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )

    @property
    def stream_timeout(self) -> float | None:
        return self._stream_timeout

    @property
    def stream_idle_timeout(self) -> float | None:
        return self._stream_idle_timeout

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def retry_delay(self) -> float:
        return self._retry_delay
```

Add `import httpx` to the top-level imports (remove inline import).

- [ ] **Step 4: Fix existing construction tests**

The existing `TestConstruction.test_construction` and `test_custom_base_url` assert exact `openai.OpenAI(...)` call kwargs. Now `http_client` is always present. Update those tests to expect `http_client` in the call:

```python
    def test_construction(self) -> None:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["api_key"] == "sk-test"
            assert call_kwargs["base_url"] is None
            assert call_kwargs["timeout"] == 300.0
            assert call_kwargs["max_retries"] == 0
            assert "http_client" in call_kwargs
```

Update `test_custom_base_url` the same way (assert individual kwargs instead of `assert_called_once_with`). Update `test_max_retries_stored` similarly — it currently uses `assert_called_once_with(...)`, change to asserting individual kwargs plus `"http_client" in call_kwargs`.

- [ ] **Step 5: Run all provider tests**

Run: `uv run pytest tests/matmaster/providers/test_openai_provider.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add matmaster/providers/openai_provider.py tests/matmaster/providers/test_openai_provider.py
git commit -m "feat: add stream timeout params and custom httpx client to OpenAIProvider"
```

---

### Task 4: OpenAIProvider — exception translation in chat_stream

**Files:**
- Modify: `matmaster/providers/openai_provider.py:173-240`
- Modify: `tests/matmaster/providers/test_openai_provider.py`

- [ ] **Step 1: Write tests for exception translation**

Add to `tests/matmaster/providers/test_openai_provider.py`:

```python
from matmaster.types.errors import LLMError


class TestChatStreamExceptionTranslation:
    def _make_provider(self) -> tuple[OpenAIProvider, MagicMock]:
        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        return provider, mock_client

    def test_timeout_raises_retryable_llm_error(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.return_value = iter([])
        mock_client.chat.completions.create.side_effect = (
            openai.APITimeoutError(request=MagicMock())
        )

        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True
        assert exc_info.value.__cause__ is not None

    def test_connection_error_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError(request=MagicMock())
        )

        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_rate_limit_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.RateLimitError(
                response=MagicMock(status_code=429, headers={}),
                body=None, message="rate limited",
            )
        )

        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_internal_server_error_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.InternalServerError(
                response=MagicMock(status_code=500, headers={}),
                body=None, message="server error",
            )
        )

        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_auth_error_raises_non_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.AuthenticationError(
                response=MagicMock(status_code=401, headers={}),
                body=None, message="invalid key",
            )
        )

        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is False

    def test_context_length_raises_non_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.BadRequestError(
                response=MagicMock(status_code=400, headers={}),
                body=None, message="context length exceeded",
            )
        )

        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is False

    def test_generic_bad_request_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = (
            openai.BadRequestError(
                response=MagicMock(status_code=400, headers={}),
                body=None, message="something went wrong",
            )
        )

        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_httpx_read_timeout_raises_retryable(self) -> None:
        provider, mock_client = self._make_provider()
        import httpx
        mock_client.chat.completions.create.side_effect = httpx.ReadTimeout(
            "read timed out"
        )

        with pytest.raises(LLMError) as exc_info:
            list(provider.chat_stream([{"role": "user", "content": "Hi"}]))
        assert exc_info.value.retryable is True

    def test_chat_stream_accepts_timeout_override(self) -> None:
        """timeout kwarg is forwarded to SDK create call."""
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.return_value = iter([
            _make_stream_chunk(content="ok", finish_reason="stop"),
        ])

        list(provider.chat_stream(
            [{"role": "user", "content": "Hi"}],
            timeout=600.0,
        ))

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("timeout") == 600.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/providers/test_openai_provider.py::TestChatStreamExceptionTranslation -v`
Expected: FAIL — no LLMError raised, raw openai exceptions propagate

- [ ] **Step 3: Implement exception translation and timeout override**

Update `chat_stream` in `matmaster/providers/openai_provider.py`:

```python
    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> Iterator[StreamChunk]:
        """Streaming chat completion, yields StreamChunk per delta.

        Translates SDK exceptions to LLMError for caller retry logic.
        """
        from matmaster.types.errors import LLMError
        import httpx

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "stream": True,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if tools:
            kwargs["tools"] = tools
        if self._extra_kwargs:
            kwargs.update(self._extra_kwargs)
        stream_options = kwargs.get("stream_options", {})
        if not isinstance(stream_options, dict):
            stream_options = {}
        kwargs["stream_options"] = {**stream_options, "include_usage": True}

        try:
            stream = self._client.chat.completions.create(**kwargs)
            last_chunk_usage: dict[str, int] | None = None

            for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if (
                    isinstance(getattr(usage, "prompt_tokens", None), int)
                    and isinstance(getattr(usage, "completion_tokens", None), int)
                    and isinstance(getattr(usage, "total_tokens", None), int)
                ):
                    last_chunk_usage = {
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                        "total_tokens": usage.total_tokens,
                    }
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason
                reasoning_content = getattr(delta, "reasoning_content", None)

                tool_call_deltas: list[dict[str, Any]] | None = None
                if delta.tool_calls:
                    tool_call_deltas = []
                    for tc_delta in delta.tool_calls:
                        d: dict[str, Any] = {"index": tc_delta.index}
                        if tc_delta.id:
                            d["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                d["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                d["arguments"] = tc_delta.function.arguments
                        tool_call_deltas.append(d)

                yield StreamChunk(
                    content=delta.content,
                    reasoning_content=reasoning_content,
                    tool_call_deltas=tool_call_deltas,
                    finish_reason=finish_reason,
                )

            if last_chunk_usage is not None:
                yield StreamChunk(usage=last_chunk_usage)

        except (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
            raise LLMError(str(exc), retryable=True) from exc
        except httpx.ReadTimeout as exc:
            raise LLMError(str(exc), retryable=True) from exc
        except (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
        ) as exc:
            raise LLMError(str(exc), retryable=False) from exc
        except openai.BadRequestError as exc:
            err_str = str(exc).lower()
            is_context = "context" in err_str and (
                "length" in err_str or "token" in err_str
            )
            raise LLMError(str(exc), retryable=not is_context) from exc
```

- [ ] **Step 4: Run all provider tests**

Run: `uv run pytest tests/matmaster/providers/test_openai_provider.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add matmaster/providers/openai_provider.py tests/matmaster/providers/test_openai_provider.py
git commit -m "feat: translate SDK exceptions to LLMError in chat_stream"
```

---

### Task 5: build_provider — pass stream timeout params

**Files:**
- Modify: `matmaster/providers/llm_factory.py:48-58`
- Modify: `tests/matmaster/providers/test_llm_factory.py`

- [ ] **Step 1: Write test for stream timeout passthrough**

Add to `tests/matmaster/providers/test_llm_factory.py`:

```python
    @patch("matmaster.providers.openai_provider.openai.OpenAI")
    def test_stream_timeout_passed(self, _mock_client) -> None:
        """stream_timeout and stream_idle_timeout from profile are passed to provider."""
        from matmaster.config.llm import LLMRouteConfig
        config = LLMConfig(
            profiles={
                "opus": LLMProfileConfig(
                    provider="openai",
                    model="claude-opus-4-6",
                    model_family="claude-4.6",
                    api_key="sk-test-opus",
                    base_url="http://litellm-proxy",
                    thinking_effort="high",
                    reasoning_protocol="anthropic_adaptive_thinking",
                    temperature_policy="force_one_when_reasoning",
                    temperature=0.7,
                    stream_timeout=120.0,
                    stream_idle_timeout=60.0,
                ),
            },
            routes={"claude-opus-4-6": LLMRouteConfig(profile="opus")},
            default="opus",
        )

        provider = build_provider(config)

        assert provider.stream_timeout == 120.0
        assert provider.stream_idle_timeout == 60.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/providers/test_llm_factory.py::TestBuildProvider::test_stream_timeout_passed -v`
Expected: FAIL — `stream_timeout` not passed / `AttributeError`

- [ ] **Step 3: Update build_provider**

In `matmaster/providers/llm_factory.py:48-58`, add the two params:

```python
    return OpenAIProvider(
        model=resolved.model,
        api_key=profile.api_key,
        base_url=profile.base_url,
        temperature=profile.effective_temperature(),
        max_tokens=profile.max_tokens,
        timeout=profile.timeout,
        stream_timeout=profile.stream_timeout,
        stream_idle_timeout=profile.stream_idle_timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
        extra_kwargs=profile.build_extra_kwargs(),
    )
```

- [ ] **Step 4: Run all factory tests**

Run: `uv run pytest tests/matmaster/providers/test_llm_factory.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add matmaster/providers/llm_factory.py tests/matmaster/providers/test_llm_factory.py
git commit -m "feat: pass stream_timeout and stream_idle_timeout through build_provider"
```

---

## Chunk 2: Kernel Retry + Config Updates

### Task 6: AgentKernel — extract _do_stream_llm and add retry in _call_llm

**Files:**
- Modify: `matmaster/core/agent.py:201-326`
- Modify: `tests/matmaster/core/test_agent.py`
- Modify: `tests/matmaster/core/conftest.py`

- [ ] **Step 1: Write test for retry on retryable LLMError**

Add to `tests/matmaster/core/test_agent.py`:

```python
from matmaster.types.errors import LLMError


class ErrorThenSuccessProvider:
    """Provider that raises LLMError N times, then succeeds."""

    def __init__(self, fail_count: int, error: LLMError) -> None:
        self._fail_count = fail_count
        self._error = error
        self._call_count = 0
        self.stream_timeout = 10.0
        self.max_retries = 3
        self.retry_delay = 0.0  # no sleep in tests

    def chat(self, messages, tools=None):
        return LLMResponse(content="not used", finish_reason="stop")

    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None, *, timeout=None):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise self._error
        yield StreamChunk(content="recovered", finish_reason="stop")


class TestCallLlmRetry:
    def test_retry_on_retryable_error(self) -> None:
        """_call_llm retries on retryable LLMError and succeeds."""
        provider = ErrorThenSuccessProvider(
            fail_count=1,
            error=LLMError("timeout", retryable=True),
        )
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        response = kernel._call_llm(spec, [UserMessage(content="hi")])
        assert response.content == "recovered"
        assert provider._call_count == 2

    def test_no_retry_on_non_retryable_error(self) -> None:
        """_call_llm raises immediately on non-retryable LLMError."""
        provider = ErrorThenSuccessProvider(
            fail_count=1,
            error=LLMError("auth failed", retryable=False),
        )
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt="test",
        )
        from matmaster.core.agent import AgentKernel
        kernel = AgentKernel()
        with pytest.raises(LLMError, match="auth failed"):
            kernel._call_llm(spec, [UserMessage(content="hi")])
        assert provider._call_count == 1

    def test_all_retries_exhausted(self) -> None:
        """_call_llm raises RuntimeError after all retries exhausted."""
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
        with pytest.raises(RuntimeError, match="LLM stream failed"):
            kernel._call_llm(spec, [UserMessage(content="hi")])
        assert provider._call_count == 3  # max_retries default

    def test_timeout_doubles_on_retry(self) -> None:
        """Each retry doubles the timeout passed to chat_stream."""
        timeouts_seen: list[float | None] = []

        class TimeoutTracker:
            stream_timeout = 10.0
            max_retries = 3
            retry_delay = 0.0

            def chat(self, messages, tools=None):
                return LLMResponse(content="", finish_reason="stop")
            def chat_with_retry(self, messages, tools=None, **kw):
                return self.chat(messages, tools)
            def chat_stream(self, messages, tools=None, *, timeout=None):
                timeouts_seen.append(timeout)
                if len(timeouts_seen) < 3:
                    raise LLMError("timeout", retryable=True)
                yield StreamChunk(content="ok", finish_reason="stop")

        spec = AgentRuntimeSpec(
            llm_provider=TimeoutTracker(),
            system_prompt="test",
        )
        kernel = AgentKernel()
        kernel._call_llm(spec, [UserMessage(content="hi")])
        assert timeouts_seen == [10.0, 20.0, 40.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry -v`
Expected: FAIL — no retry logic exists, LLMError propagates

- [ ] **Step 3: Refactor _call_llm into _call_llm + _do_stream_llm**

In `matmaster/core/agent.py`, add `import time` to imports, and add `from matmaster.types.errors import LLMError` import.

Rename current `_call_llm` to `_do_stream_llm`, adding `timeout` parameter. Then create new `_call_llm` as retry wrapper:

```python
    def _call_llm(
        self, spec: AgentRuntimeSpec, messages: list[Message]
    ) -> LLMResponse:
        """Call LLM with timeout-doubling retry on transient errors."""
        provider = spec.llm_provider
        current_timeout = getattr(provider, "stream_timeout", None) or getattr(
            provider, "_timeout", 300.0
        )
        max_retries = getattr(provider, "max_retries", 3)
        retry_delay = getattr(provider, "retry_delay", 1.0)

        last_error: LLMError | None = None
        for attempt in range(max_retries):
            try:
                return self._do_stream_llm(spec, messages, timeout=current_timeout)
            except LLMError as e:
                if not e.retryable:
                    raise
                last_error = e
                next_timeout = current_timeout * 2
                logger.warning(
                    "LLM stream timed out after %.0fs (attempt %d/%d). "
                    "Retrying with timeout=%.0fs.",
                    current_timeout,
                    attempt + 1,
                    max_retries,
                    next_timeout,
                )
                current_timeout = next_timeout
                if attempt < max_retries - 1:
                    backoff = retry_delay * (2**attempt)
                    time.sleep(backoff)

        raise RuntimeError(
            f"LLM stream failed after {max_retries} attempts"
        ) from last_error

    def _do_stream_llm(
        self,
        spec: AgentRuntimeSpec,
        messages: list[Message],
        timeout: float | None = None,
    ) -> LLMResponse:
        """Stream LLM response and accumulate into LLMResponse. Single attempt."""
```

The body of `_do_stream_llm` is the exact body of the current `_call_llm` method (the one being renamed). It is a whole-method move with one single-line change:

The line:
```python
for chunk in spec.llm_provider.chat_stream(api_messages, tool_defs):
```
becomes:
```python
for chunk in spec.llm_provider.chat_stream(api_messages, tool_defs, timeout=timeout):
```

No other lines change. The method signature adds the `timeout` parameter; the rest is an exact copy.

- [ ] **Step 4: Run retry tests**

Run: `uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry -v`
Expected: all passed

- [ ] **Step 5: Run all kernel tests for regressions**

Run: `uv run pytest tests/matmaster/core/test_agent.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent.py
git commit -m "feat: add timeout-doubling retry to AgentKernel._call_llm"
```

---

### Task 7: Update llm_config.yaml timeout values

**Files:**
- Modify: `matmaster_config/llm_config.yaml`

- [ ] **Step 1: Update all 7 profiles**

Target values per spec:

| Profile | timeout | stream_timeout | stream_idle_timeout |
|---------|---------|----------------|---------------------|
| opus | 1200 | 120 | 60 |
| sonnet | 1200 | 120 | 60 |
| haiku | 1200 | 60 | 30 |
| gemini | 1200 | 60 | 30 |
| gemini-pro | 1200 | 120 | 60 |
| gpt54 | 1200 | 120 | 60 |
| compaction | 1200 | 60 | 30 |

- [ ] **Step 2: Verify config loads without error**

Run: `uv run python -c "from matmaster.config.loader import load_llm_config; c = load_llm_config('matmaster_config/llm_config.yaml'); print(f'profiles: {list(c.profiles.keys())}'); print(f'opus timeout: {c.profiles[\"opus\"].timeout}')"`
Expected: `opus timeout: 1200.0`

- [ ] **Step 3: Commit**

```bash
git add matmaster_config/llm_config.yaml
git commit -m "config: increase timeout values for all LLM profiles"
```

---

### Task 8: Integration smoke test

**Files:**
- Test: `tests/matmaster/integration/test_stream_timeout_retry.py`

- [ ] **Step 1: Write end-to-end integration test**

```python
# tests/matmaster/integration/test_stream_timeout_retry.py
"""Integration test: full chain from build_provider through kernel retry."""

from __future__ import annotations

from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import openai
import pytest

from matmaster.config.llm import LLMConfig, LLMProfileConfig, LLMRouteConfig
from matmaster.core.agent import AgentKernel
from matmaster.providers.llm_factory import build_provider
from matmaster.types.errors import LLMError
from matmaster.types.messages import StreamChunk, UserMessage
from matmaster.types.runtime import AgentRuntimeSpec


def _make_stream_chunk(content=None, finish_reason=None):
    mock = MagicMock()
    choice = MagicMock()
    choice.delta.content = content
    choice.delta.reasoning_content = None
    choice.delta.tool_calls = None
    choice.finish_reason = finish_reason
    mock.choices = [choice]
    mock.usage = None
    return mock


class TestStreamTimeoutRetryIntegration:
    def test_provider_retries_through_kernel(self) -> None:
        """Timeout in chat_stream -> LLMError -> kernel retries -> success."""
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise openai.APITimeoutError(request=MagicMock())
            return iter([
                _make_stream_chunk(content="answer", finish_reason="stop"),
            ])

        config = LLMConfig(
            profiles={
                "test": LLMProfileConfig(
                    model="test-model",
                    api_key="sk-test",
                    timeout=1200.0,
                    stream_timeout=10.0,
                    stream_idle_timeout=5.0,
                    max_retries=3,
                    retry_delay=0.0,
                ),
            },
            routes={},
            default="test",
        )

        with patch("matmaster.providers.openai_provider.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.chat.completions.create.side_effect = fake_create

            provider = build_provider(config)
            assert provider.stream_timeout == 10.0

            spec = AgentRuntimeSpec(
                llm_provider=provider,
                system_prompt="test",
            )
            kernel = AgentKernel()
            response = kernel._call_llm(spec, [UserMessage(content="hi")])

            assert response.content == "answer"
            assert call_count == 2
```

- [ ] **Step 2: Run integration test**

Run: `uv run pytest tests/matmaster/integration/test_stream_timeout_retry.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/matmaster/ -v`
Expected: all passed, no regressions

- [ ] **Step 4: Commit**

```bash
git add tests/matmaster/integration/test_stream_timeout_retry.py
git commit -m "test: add integration test for stream timeout retry chain"
```
