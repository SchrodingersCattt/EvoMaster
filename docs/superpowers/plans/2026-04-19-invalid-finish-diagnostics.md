# Invalid Finish Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the existing `invalid_finish` terminal contract while adding structured diagnostics that distinguish output-token truncation, content filtering, empty responses, reasoning-only responses, missing final response objects, and other non-stop provider finish reasons.

**Architecture:** Add a Pydantic `FinishDetail` event type and a focused `matmaster/core/finish_diagnostics.py` classifier so `agent.py` stays within the project line-count limit. The kernel imports that pure classifier, stores the detail on `_TerminalItem`, exposes it through `RunResultEvent`, preserves it in public `content`, lets `AgentRunService` choose the user-visible `ErrorEvent`, and copies it through devshell drain/runner/CLI for scripts and evaluation. Tool-call responses with `finish_reason == "length"` remain executable but get a `FinishDetail` on `AssistantStateEvent` plus structured warning logs.

**Tech Stack:** Python 3.11+, Pydantic v2, async generator kernel, existing MatMaster event bus, Redis/SSE fanout service tests, pytest, uv-managed environment.

---

## Source Spec

Approved spec: `docs/superpowers/specs/2026-04-19-invalid-finish-diagnostics-design.md`

The implementation must preserve these decisions:

- Keep `RunResultEvent.reason == "invalid_finish"` for all invalid final outputs.
- Keep quota behavior unchanged: failed invalid finishes do not deduct quota.
- Keep `StreamClosedEvent.end_reason == "invalid_finish"` and do not add `failure_kind` to `StreamClosedEvent` in this version.
- Treat `run_result.content.finish_detail` as the public frontend/history contract.
- Treat top-level `run_result.finish_detail` as the Pydantic/SSE mirror of the event field.
- Use a structured Pydantic `FinishDetail`, not `dict[str, Any]`.
- Lock `FinishDetail` with `ConfigDict(extra="forbid")`; callers use `model_dump(mode="json")` without `exclude_none` or `exclude_defaults` when they need the full diagnostic contract.
- Prefer `last_turn_usage.completion_tokens` and provider-native `last_turn_usage_vendor` over character counts when diagnosing token-limit truncation.
- Classify by `finish_reason` first; `length` takes priority over empty/reasoning-only shape.
- Preserve tool execution semantics for tool calls with `finish_reason == "length"`, but record truncation risk on `AssistantStateEvent.finish_detail` and in logs.
- Keep provider errors that raise exceptions on the existing exception path; `missing_llm_response` is a defensive classification for a stream that ends without a final `LLMResponse`.

## File Map

Modify:

- `matmaster/types/events.py`
- `matmaster/types/__init__.py`
- `matmaster/core/agent.py`
- `matmaster/integration/event_payloads.py`
- `src/services/agent_run_service.py`
- `matmaster/core/stream_drain.py`
- `matmaster/devshell/runner.py`
- `matmaster/devshell/cli.py`
- `matmaster/devshell/event_logger.py`

Create:

- `matmaster/core/finish_diagnostics.py`

Modify tests:

- `tests/matmaster/types/test_events.py`
- `tests/matmaster/core/test_agent_kernel_stream.py`
- `tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py`
- `tests/matmaster/core/test_stream_drain.py`
- `tests/matmaster/integration/test_event_payloads.py`
- `tests/matmaster/integration/test_quota_pipeline.py`
- `tests/matmaster/devshell/test_runner.py`
- `tests/matmaster/devshell/test_repl.py`
- `tests/matmaster/devshell/test_event_logger.py`

Line-count guard:

- `matmaster/core/agent.py` is already exactly 1000 lines before this feature.
- The classifier must be created in `matmaster/core/finish_diagnostics.py` in Task 2. Do not add classifier helper methods to `AgentKernel`.
- After edits, run `wc -l matmaster/core/agent.py src/services/agent_run_service.py matmaster/types/events.py matmaster/core/finish_diagnostics.py`.
- Expected: `matmaster/core/agent.py` remains at or below 1000 lines. If wiring still pushes it over the limit, extract another small kernel-adjacent helper instead of leaving the file above the guard.

## Runtime Contract

New event model in `matmaster/types/events.py`:

```python
class FinishDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "output_length_exceeded",
        "content_filtered",
        "empty_response",
        "reasoning_only",
        "missing_llm_response",
        "non_stop_finish",
        "unknown",
    ]
    provider_finish_reason: str | None = None
    message: str
    content_chars: int = 0
    reasoning_chars: int = 0
    has_visible_content: bool = False
    has_reasoning: bool = False
    has_tool_calls: bool = False
    tool_call_count: int = 0
    last_turn_usage: dict[str, int] = Field(default_factory=dict)
    last_turn_usage_vendor: dict[str, Any] = Field(default_factory=dict)
    attempts: int | None = None
    last_error_kind: str | None = None
    truncation_risk: bool = False
```

New optional event fields:

```python
class RunResultEvent(EventBase):
    finish_detail: FinishDetail | None = None


class AssistantStateEvent(EventBase):
    finish_detail: FinishDetail | None = None
```

New terminal/drain fields:

```python
@dataclass
class _TerminalItem:
    finish_detail: FinishDetail | None = None


@dataclass
class DrainResult:
    finish_detail: FinishDetail | None = None
```

Public run result content shape:

```json
{
  "content": "",
  "status": "failed",
  "reason": "invalid_finish",
  "finish_detail": {
    "kind": "output_length_exceeded",
    "provider_finish_reason": "length",
    "message": "Model output was truncated by the provider output-token limit."
  }
}
```

Public assistant state content shape when tool-call truncation risk exists:

```json
{
  "state": {
    "role": "assistant",
    "content": null,
    "tool_calls": []
  },
  "finish_detail": {
    "kind": "output_length_exceeded",
    "provider_finish_reason": "length",
    "has_tool_calls": true,
    "truncation_risk": true
  }
}
```

## Task 1: Event Types And Serialization

**Files:**

- Modify: `matmaster/types/events.py`
- Modify: `matmaster/types/__init__.py`
- Test: `tests/matmaster/types/test_events.py`

- [ ] **Step 1: Write failing event-model tests**

Add tests to `tests/matmaster/types/test_events.py` near `TestRunResultEvent` and `TestAssistantStateEvent`:

```python
from pydantic import ValidationError

from matmaster.types.events import FinishDetail


class TestFinishDetail:
    def test_finish_detail_serializes_structured_fields(self) -> None:
        detail = FinishDetail(
            kind="output_length_exceeded",
            provider_finish_reason="length",
            message="Model output was truncated by the provider output-token limit.",
            content_chars=12,
            reasoning_chars=34,
            has_visible_content=True,
            has_reasoning=True,
            last_turn_usage={"completion_tokens": 4096},
            last_turn_usage_vendor={"outputTokens": 4096},
            truncation_risk=True,
        )

        dumped = detail.model_dump(mode="json")
        assert dumped["kind"] == "output_length_exceeded"
        assert dumped["last_turn_usage"]["completion_tokens"] == 4096
        assert dumped["last_turn_usage_vendor"]["outputTokens"] == 4096
        assert dumped["truncation_risk"] is True

    def test_finish_detail_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            FinishDetail(kind="typo", message="bad")

        assert any(err["loc"] == ("kind",) for err in exc_info.value.errors())
```


Add this method to the existing `TestRunResultEvent` class:

```python
    def test_finish_detail_round_trips(self) -> None:
        evt = RunResultEvent(
            source="agent",
            status="failed",
            reason="invalid_finish",
            finish_detail=FinishDetail(
                kind="empty_response",
                message="Model stopped without a visible final answer.",
            ),
        )

        dumped = evt.model_dump(mode="json")
        assert dumped["finish_detail"]["kind"] == "empty_response"
        restored = _bus_event_adapter.validate_python(dumped)
        assert isinstance(restored, RunResultEvent)
        assert restored.finish_detail is not None
        assert restored.finish_detail.kind == "empty_response"
```

Add this method to the existing `TestAssistantStateEvent` class:

```python
    def test_finish_detail_round_trips(self) -> None:
        evt = AssistantStateEvent(
            source="agent",
            state={"role": "assistant", "tool_calls": []},
            finish_detail=FinishDetail(
                kind="output_length_exceeded",
                provider_finish_reason="length",
                message="Model output was truncated by the provider output-token limit.",
                has_tool_calls=True,
                truncation_risk=True,
            ),
        )

        dumped = evt.model_dump(mode="json")
        assert dumped["finish_detail"]["has_tool_calls"] is True
        restored = _bus_event_adapter.validate_python(dumped)
        assert isinstance(restored, AssistantStateEvent)
        assert restored.finish_detail is not None
        assert restored.finish_detail.truncation_risk is True
```

Also add one import/export assertion:

```python
def test_finish_detail_exported_from_types_package() -> None:
    import matmaster.types as types_pkg

    assert types_pkg.FinishDetail is FinishDetail
```

- [ ] **Step 2: Run the event tests and verify RED**

Run:

```bash
uv run pytest tests/matmaster/types/test_events.py -q
```

Expected: tests fail because `FinishDetail` does not exist and event classes have no `finish_detail`.

- [ ] **Step 3: Implement `FinishDetail` and event fields**

In `matmaster/types/events.py`, import `ConfigDict` and add `FinishDetail` before `RunResultEvent`. Keep the field order from the Runtime Contract above and do not use `exclude_none` or `exclude_defaults` in this model; downstream JSON summaries intentionally include default and `None` fields unless an individual caller explicitly chooses otherwise.

```python
from pydantic import BaseModel, ConfigDict, Field


class FinishDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "output_length_exceeded",
        "content_filtered",
        "empty_response",
        "reasoning_only",
        "missing_llm_response",
        "non_stop_finish",
        "unknown",
    ]
    provider_finish_reason: str | None = None
    message: str
    content_chars: int = 0
    reasoning_chars: int = 0
    has_visible_content: bool = False
    has_reasoning: bool = False
    has_tool_calls: bool = False
    tool_call_count: int = 0
    last_turn_usage: dict[str, int] = Field(default_factory=dict)
    last_turn_usage_vendor: dict[str, Any] = Field(default_factory=dict)
    attempts: int | None = None
    last_error_kind: str | None = None
    truncation_risk: bool = False
```

Update `RunResultEvent`:

```python
finish_detail: FinishDetail | None = None
```

Update `AssistantStateEvent`:

```python
finish_detail: FinishDetail | None = None
```

In `matmaster/types/__init__.py`, import and export `FinishDetail`:

```python
from .events import FinishDetail
```

Add `"FinishDetail"` to the existing `__all__` event export block next to
`"ErrorEvent"` and the other event contracts.

- [ ] **Step 4: Run the event tests and verify GREEN**

Run:

```bash
uv run pytest tests/matmaster/types/test_events.py -q
```

Expected: all event model and union serialization tests pass.

- [ ] **Step 5: Commit the type contract**

Run:

```bash
git add matmaster/types/events.py matmaster/types/__init__.py tests/matmaster/types/test_events.py
git commit -m "feat: add finish detail event contract"
```

## Task 2: Finish Diagnostics Classifier

**Files:**

- Create: `matmaster/core/finish_diagnostics.py`
- Test: `tests/matmaster/core/test_agent_kernel_stream.py`

- [ ] **Step 1: Write failing pure classifier tests**

Add a new test class near `TestEmptyFinalResponseInvalidFinish`:

```python
class TestInvalidFinishDetailClassifier:
    def test_length_takes_priority_over_response_shape(self) -> None:
        from matmaster.core.finish_diagnostics import build_finish_detail

        response = LLMResponse(
            content="partial",
            reasoning_content="thinking",
            finish_reason="length",
            usage={"completion_tokens": 4096},
            usage_vendor={"outputTokens": 4096},
        )

        detail = build_finish_detail(response)

        assert detail.kind == "output_length_exceeded"
        assert detail.provider_finish_reason == "length"
        assert detail.has_visible_content is True
        assert detail.has_reasoning is True
        assert detail.last_turn_usage["completion_tokens"] == 4096
        assert detail.last_turn_usage_vendor["outputTokens"] == 4096
        assert detail.truncation_risk is True

    @pytest.mark.parametrize(
        ("response", "expected_kind"),
        [
            (
                LLMResponse(content=None, finish_reason="content_filter"),
                "content_filtered",
            ),
            (
                LLMResponse(content=None, reasoning_content="thinking", finish_reason="stop"),
                "reasoning_only",
            ),
            (
                LLMResponse(content=None, finish_reason="stop"),
                "empty_response",
            ),
            (
                LLMResponse(content="visible", finish_reason="guardrail_intervened"),
                "non_stop_finish",
            ),
            (
                LLMResponse(content="visible", finish_reason=None),
                "non_stop_finish",
            ),
        ],
    )
    def test_classifies_invalid_finish_matrix(
        self, response: LLMResponse, expected_kind: str
    ) -> None:
        from matmaster.core.finish_diagnostics import build_finish_detail

        detail = build_finish_detail(response)

        assert detail.kind == expected_kind
        assert detail.provider_finish_reason == response.finish_reason

    def test_missing_llm_response_api_shape_accepts_retry_metadata(self) -> None:
        from matmaster.core.finish_diagnostics import build_finish_detail
        from matmaster.types.errors import LLMError

        detail = build_finish_detail(
            None,
            attempts=3,
            last_error=LLMError(
                "stream failed",
                retryable=True,
                error_category="incomplete_response",
            ),
        )

        assert detail.kind == "missing_llm_response"
        assert detail.attempts == 3
        assert detail.last_error_kind == "incomplete_response"

    def test_classifier_fallback_returns_unknown(self, monkeypatch, caplog) -> None:
        import logging

        from matmaster.core import finish_diagnostics
        from matmaster.core.finish_diagnostics import build_finish_detail

        def raise_visible(_response: LLMResponse) -> bool:
            raise RuntimeError("boom")

        monkeypatch.setattr(
            finish_diagnostics,
            "_has_visible_content",
            raise_visible,
        )
        caplog.set_level(logging.WARNING, logger="matmaster.core.finish_diagnostics")

        detail = build_finish_detail(LLMResponse(content="x", finish_reason="stop"))

        assert detail.kind == "unknown"
        assert "finish detail classification failed" in caplog.text
```

This `missing_llm_response` test is an API-shape test for future retry-state wiring. The current production path calls `build_finish_detail(None)` without `attempts` or `last_error` because `_call_llm_streaming()` does not expose retry state to `_run_items()` in this version.

- [ ] **Step 2: Run classifier tests and verify RED**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestInvalidFinishDetailClassifier -q
```

Expected: fail because `matmaster.core.finish_diagnostics` does not exist.

- [ ] **Step 3: Implement classifier with explicit fallback**

Create `matmaster/core/finish_diagnostics.py`:

```python
"""Classify non-natural LLM finish states into structured diagnostics."""

from __future__ import annotations

import logging

from matmaster.response_text import normalize_visible_response_text
from matmaster.types.errors import LLMError
from matmaster.types.events import FinishDetail
from matmaster.types.messages import LLMResponse

logger = logging.getLogger(__name__)


def build_finish_detail(
    response: LLMResponse | None,
    *,
    attempts: int | None = None,
    last_error: LLMError | None = None,
) -> FinishDetail:
    """Classify invalid LLM finish state.

    The input finish reason is already normalized by provider adapters where
    possible. OpenAI-style ``content_filter`` maps to ``content_filtered``;
    Bedrock-specific stop reasons such as ``guardrail_intervened`` remain
    provider finish reasons and fall through to ``non_stop_finish``.
    """
    try:
        return _build_finish_detail_inner(
            response,
            attempts=attempts,
            last_error=last_error,
        )
    except Exception:
        logger.warning("finish detail classification failed", exc_info=True)
        return FinishDetail(
            kind="unknown",
            message="Model finish state could not be classified.",
        )


def _build_finish_detail_inner(
    response: LLMResponse | None,
    *,
    attempts: int | None = None,
    last_error: LLMError | None = None,
) -> FinishDetail:
    if response is None:
        return FinishDetail(
            kind="missing_llm_response",
            message="LLM stream ended without a final response object.",
            attempts=attempts,
            last_error_kind=getattr(last_error, "error_category", None),
        )

    finish_reason = response.finish_reason
    has_visible = _has_visible_content(response)
    has_reasoning = bool(response.reasoning_content)
    tool_calls = response.tool_calls or []
    base = {
        "provider_finish_reason": finish_reason,
        "content_chars": len(response.content or ""),
        "reasoning_chars": len(response.reasoning_content or ""),
        "has_visible_content": has_visible,
        "has_reasoning": has_reasoning,
        "has_tool_calls": bool(tool_calls),
        "tool_call_count": len(tool_calls),
        "last_turn_usage": dict(response.usage or {}),
        "last_turn_usage_vendor": dict(response.usage_vendor or {}),
    }

    if finish_reason == "length":
        return FinishDetail(
            kind="output_length_exceeded",
            message="Model output was truncated by the provider output-token limit.",
            truncation_risk=True,
            **base,
        )
    if finish_reason == "content_filter":
        return FinishDetail(
            kind="content_filtered",
            message="Model output was blocked or truncated by provider content policy.",
            **base,
        )
    if finish_reason == "stop" and not has_visible and has_reasoning:
        return FinishDetail(
            kind="reasoning_only",
            message="Model returned reasoning content without a visible final answer.",
            **base,
        )
    if finish_reason == "stop" and not has_visible:
        return FinishDetail(
            kind="empty_response",
            message="Model stopped without a visible final answer.",
            **base,
        )
    return FinishDetail(
        kind="non_stop_finish",
        message="Model returned a finish reason that cannot be committed as natural.",
        **base,
    )


def _has_visible_content(response: LLMResponse) -> bool:
    return normalize_visible_response_text(response.content) is not None
```

- [ ] **Step 4: Run classifier tests and verify GREEN**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestInvalidFinishDetailClassifier -q
```

Expected: all classifier matrix tests pass.

- [ ] **Step 5: Commit the classifier**

Run:

```bash
git add matmaster/core/finish_diagnostics.py tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "feat: classify invalid finish details"
```

## Task 3: Terminal Detail Propagation

**Files:**

- Modify: `matmaster/core/agent.py`
- Test: `tests/matmaster/core/test_agent_kernel_stream.py`
- Test: `tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py`

- [ ] **Step 1: Write failing propagation tests for final invalid finishes**

In `tests/matmaster/core/test_agent_kernel_stream.py`, add providers near `EmptyStopProvider`:

```python
class LengthFinishProvider(ContentOnlyProvider):
    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="partial")
        yield StreamChunk(
            finish_reason="length",
            usage={"prompt_tokens": 10, "completion_tokens": 4096},
            usage_vendor={"outputTokens": 4096},
        )


class ContentFilterProvider(ContentOnlyProvider):
    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(finish_reason="content_filter")


class NonStopFinishProvider(ContentOnlyProvider):
    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="blocked by guardrail")
        yield StreamChunk(finish_reason="guardrail_intervened")
```

Add tests to `TestEmptyFinalResponseInvalidFinish`:

```python
    @pytest.mark.asyncio
    async def test_length_finish_sets_output_length_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=LengthFinishProvider()),
                "test task",
            )
        ]

        result = events[-1]
        assert isinstance(result, RunResultEvent)
        assert result.status == "failed"
        assert result.reason == "invalid_finish"
        assert result.finish_detail is not None
        assert result.finish_detail.kind == "output_length_exceeded"
        assert result.finish_detail.last_turn_usage["completion_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_content_filter_finish_sets_content_filtered_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=ContentFilterProvider()),
                "test task",
            )
        ]

        assert events[-1].finish_detail.kind == "content_filtered"

    @pytest.mark.asyncio
    async def test_empty_stop_sets_empty_response_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=EmptyStopProvider()),
                "test task",
            )
        ]

        assert events[-1].finish_detail.kind == "empty_response"

    @pytest.mark.asyncio
    async def test_reasoning_only_sets_reasoning_only_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=EmptyStopProvider(reasoning="thinking only")),
                "test task",
            )
        ]

        assert events[-1].finish_detail.kind == "reasoning_only"

    @pytest.mark.asyncio
    async def test_unknown_provider_finish_sets_non_stop_finish_detail(self) -> None:
        from matmaster.core.agent import AgentKernel

        events = [
            event
            async for event in AgentKernel().run_stream(
                _make_spec(provider=NonStopFinishProvider()),
                "test task",
            )
        ]

        assert events[-1].finish_detail.kind == "non_stop_finish"
        assert events[-1].finish_detail.provider_finish_reason == "guardrail_intervened"
```

In `tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py`, extend `test_empty_sentinel_stop_finishes_as_invalid_finish`. This assertion assumes the existing sentinel provider returns sentinel-only content with no reasoning chunks, so the normalized response has neither visible content nor reasoning:

```python
    assert events[-1].finish_detail is not None
    assert events[-1].finish_detail.kind == "empty_response"
```

- [ ] **Step 2: Write a focused test for the defensive missing response path**

In `tests/matmaster/core/test_agent_kernel_stream.py`, add:

```python
@pytest.mark.asyncio
async def test_missing_llm_response_terminal_sets_detail(monkeypatch) -> None:
    from matmaster.core.agent import AgentKernel, _KernelItem

    async def no_final_response(self, spec, api_messages, tool_defs, *, cancel_token=None):
        if False:
            yield _KernelItem()

    kernel = AgentKernel()
    monkeypatch.setattr(
        AgentKernel,
        "_call_llm_streaming",
        no_final_response,
    )

    events = [
        event
        async for event in kernel.run_stream(
            _make_spec(provider=ContentOnlyProvider()),
            "test task",
        )
    ]

    assert events[-1].reason == "invalid_finish"
    assert events[-1].finish_detail.kind == "missing_llm_response"
```

- [ ] **Step 3: Run propagation tests and verify RED**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_agent_kernel_stream.py::TestEmptyFinalResponseInvalidFinish \
  tests/matmaster/core/test_agent_kernel_stream.py::test_missing_llm_response_terminal_sets_detail \
  tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py::test_empty_sentinel_stop_finishes_as_invalid_finish \
  -q
```

Expected: fail because terminal events do not carry `finish_detail`.

- [ ] **Step 4: Implement terminal propagation**

In `matmaster/core/agent.py`, update `_TerminalItem`:

```python
finish_detail: FinishDetail | None = None
```

Update `_terminal()` signature:

```python
def _terminal(
    state: _KernelState,
    reason: str,
    *,
    final_content: str | None = None,
    turn_offset: int = 0,
    finish_detail: FinishDetail | None = None,
) -> _KernelItem:
```

Pass `finish_detail` into `_TerminalItem`.

Import the classifier near other kernel imports:

```python
from matmaster.core.finish_diagnostics import build_finish_detail
```

Also import `FinishDetail` from `matmaster.types.events` in the existing event import block so `_TerminalItem` can use the annotation:

```python
from matmaster.types.events import FinishDetail
```

Update `run_stream()` where it constructs `RunResultEvent`:

```python
finish_detail=item.terminal.finish_detail,
```

Update invalid finish branches in `_run_items()`:

```python
if llm_response is None:
    yield self._terminal(
        state,
        "invalid_finish",
        # attempts and last_error_kind are classifier-level fields. Wiring
        # retry state out of _call_llm_streaming() is intentionally out of
        # scope for this version.
        finish_detail=build_finish_detail(None),
    )
    return
```

```python
if not self._is_valid_natural_finish(response):
    yield self._terminal(
        state,
        "invalid_finish",
        finish_detail=build_finish_detail(response),
    )
    return
```

Do not pass finish detail for `natural`, `cancelled`, or `max_turns`.

- [ ] **Step 5: Run propagation tests and verify GREEN**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_agent_kernel_stream.py::TestEmptyFinalResponseInvalidFinish \
  tests/matmaster/core/test_agent_kernel_stream.py::test_missing_llm_response_terminal_sets_detail \
  tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py::test_empty_sentinel_stop_finishes_as_invalid_finish \
  -q
```

Expected: all targeted invalid finish propagation tests pass.

- [ ] **Step 6: Commit terminal propagation**

Run:

```bash
git add \
  matmaster/core/agent.py \
  tests/matmaster/core/test_agent_kernel_stream.py \
  tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py
git commit -m "feat: propagate invalid finish details"
```

## Task 4: Tool-Call Length Risk Observability

**Files:**

- Modify: `matmaster/core/agent.py`
- Test: `tests/matmaster/core/test_agent_kernel_stream.py`

- [ ] **Step 1: Write failing tool-call truncation risk test**

Add a provider near `SkillStreamProvider`:

```python
class ToolCallLengthProvider:
    def __init__(self) -> None:
        self.call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self.call_count += 1
        if self.call_count == 1:
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-length",
                        "name": "test_tool",
                        "arguments": '{"x": 1}',
                    }
                ]
            )
            yield StreamChunk(
                finish_reason="length",
                usage={"completion_tokens": 4096},
                usage_vendor={"outputTokens": 4096},
            )
        else:
            yield StreamChunk(content="done")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})
```

Add test:

```python
@pytest.mark.asyncio
async def test_tool_call_length_finish_adds_assistant_state_detail(caplog) -> None:
    import logging

    from matmaster.core.agent import AgentKernel

    caplog.set_level(logging.WARNING, logger="matmaster.core.agent")
    registry, tools = _make_tool_registry(tool_names=["test_tool"])
    events = [
        event
        async for event in AgentKernel().run_stream(
            _make_spec(
                provider=ToolCallLengthProvider(),
                tool_registry=registry,
            ),
            "test task",
        )
    ]

    assistant_state = next(
        event for event in events if isinstance(event, AssistantStateEvent)
    )
    assert assistant_state.finish_detail is not None
    assert assistant_state.finish_detail.kind == "output_length_exceeded"
    assert assistant_state.finish_detail.has_tool_calls is True
    assert assistant_state.finish_detail.tool_call_count == 1
    assert assistant_state.finish_detail.truncation_risk is True
    assert tools[0].calls == [("test_tool", {"x": 1})]
    assert events[-1].reason == "natural"
    warning_records = [
        record
        for record in caplog.records
        if record.name == "matmaster.core.agent"
        and record.levelno == logging.WARNING
    ]
    assert any(
        record.getMessage().startswith("tool call response ended")
        and record.tool_names == ["test_tool"]
        and record.finish_detail["kind"] == "output_length_exceeded"
        for record in warning_records
    )
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::test_tool_call_length_finish_adds_assistant_state_detail -q
```

Expected: fail because `AssistantStateEvent.finish_detail` is not set and no warning is logged.

- [ ] **Step 3: Implement tool-call length observability**

In `_run_items()`, place this computation immediately after the multi-line
`assistant_msg = AssistantMessage(` construction and before yielding
`AssistantStateEvent`:

```python
assistant_finish_detail = None
if response.finish_reason == "length":
    assistant_finish_detail = build_finish_detail(response)
    logger.warning(
        "tool call response ended with length finish reason",
        extra={
            "turn": state.turn,
            "tool_names": [tc.name for tc in response.tool_calls or []],
            "finish_detail": assistant_finish_detail.model_dump(mode="json"),
        },
    )
```

Then pass it to the event:

```python
finish_detail=assistant_finish_detail,
```

Do not change `_validate_tool_call_ids()`, `ToolCallEvent`, `ToolResultEvent`, or tool execution order.

Scope: only `finish_reason == "length"` is recorded on tool-call assistant state in this version. Other non-stop finish reasons co-occurring with tool calls are rarer and deferred so this change stays observability-only.

- [ ] **Step 4: Run the tool-call risk test and verify GREEN**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::test_tool_call_length_finish_adds_assistant_state_detail -q
```

Expected: the assistant state carries `output_length_exceeded`, the warning appears, and the tool still executes.

- [ ] **Step 5: Commit tool-call observability**

Run:

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "feat: record tool call truncation risk"
```

## Task 5: Public Payload Mapping

**Files:**

- Modify: `matmaster/integration/event_payloads.py`
- Test: `tests/matmaster/integration/test_event_payloads.py`

- [ ] **Step 1: Write failing content mapping tests**

Add tests to `tests/matmaster/integration/test_event_payloads.py`:

```python
    def test_run_result_preserves_finish_detail_in_public_content(self) -> None:
        detail = {
            "kind": "output_length_exceeded",
            "provider_finish_reason": "length",
            "message": "Model output was truncated by the provider output-token limit.",
        }
        payload = {
            "type": "run_result",
            "source": "Agent",
            "status": "failed",
            "reason": "invalid_finish",
            "final_content": None,
            "finish_detail": detail,
        }

        content = _public_content_for_event("run_result", payload)

        assert content == {
            "content": "",
            "status": "failed",
            "reason": "invalid_finish",
            "finish_detail": detail,
        }

    def test_assistant_state_preserves_finish_detail_in_public_content(self) -> None:
        state = {"role": "assistant", "content": None, "tool_calls": []}
        detail = {
            "kind": "output_length_exceeded",
            "provider_finish_reason": "length",
            "message": "Model output was truncated by the provider output-token limit.",
            "has_tool_calls": True,
            "truncation_risk": True,
        }
        payload = {
            "type": "assistant_state",
            "source": "Agent",
            "state": state,
            "finish_detail": detail,
        }

        assert _public_content_for_event("assistant_state", payload) == {
            "state": state,
            "finish_detail": detail,
        }
```

- [ ] **Step 2: Run payload tests and verify RED**

Run:

```bash
uv run pytest tests/matmaster/integration/test_event_payloads.py -q
```

Expected: new tests fail because `_public_content_for_event()` drops `finish_detail`.

- [ ] **Step 3: Preserve `finish_detail` in run result and assistant state content**

In `matmaster/integration/event_payloads.py`, update the run result branch:

```python
    if event_type in ('run_result', 'finish'):
        content = {
            'content': payload.get('final_content') or '',
            'status': payload.get('status'),
            'reason': payload.get('reason'),
        }
        if payload.get('finish_detail') is not None:
            content['finish_detail'] = payload['finish_detail']
        return content
```

Update the assistant state branch:

```python
    if event_type == 'assistant_state':
        content: dict[str, Any] = {'state': payload.get('state')}
        if payload.get('finish_detail') is not None:
            content['finish_detail'] = payload['finish_detail']
        if payload.get('turn_usage'):
            content['turn_usage'] = payload['turn_usage']
            content['total_usage'] = payload.get('total_usage', {})
        return content
```

- [ ] **Step 4: Run payload tests and verify GREEN**

Run:

```bash
uv run pytest tests/matmaster/integration/test_event_payloads.py -q
```

Expected: all payload mapping tests pass.

- [ ] **Step 5: Commit payload mapping**

Run:

```bash
git add matmaster/integration/event_payloads.py tests/matmaster/integration/test_event_payloads.py
git commit -m "feat: expose finish details in event payloads"
```

## Task 6: Service Error Messages

**Files:**

- Modify: `src/services/agent_run_service.py`
- Test: `tests/matmaster/integration/test_quota_pipeline.py`

- [ ] **Step 1: Write failing service-message assertions**

In `tests/matmaster/integration/test_quota_pipeline.py`, extend `test_invalid_finish_emits_error_and_stream_closed_event`:

```python
        assert run_result_payload['content']['finish_detail']['kind'] == (
            'output_length_exceeded'
        )
        assert run_result_payload['content']['finish_detail'][
            'provider_finish_reason'
        ] == 'length'
```

Then update its error assertion:

```python
        assert '输出 token 上限截断' in error_payload['message']
```

Extend `test_empty_stop_invalid_finish_emits_error_and_stream_closed_event`:

```python
        assert run_result_payload['content']['finish_detail']['kind'] == 'empty_response'
        assert '没有返回可见最终回答' in error_payload['message']
```

Keep the existing stream-closed assertions unchanged.

- [ ] **Step 2: Run quota invalid-finish tests and verify RED**

Run:

```bash
uv run pytest \
  tests/matmaster/integration/test_quota_pipeline.py::TestQuotaNotDeductedOnFailure::test_invalid_finish_emits_error_and_stream_closed_event \
  tests/matmaster/integration/test_quota_pipeline.py::TestQuotaNotDeductedOnFailure::test_empty_stop_invalid_finish_emits_error_and_stream_closed_event \
  -q
```

Expected: fail because the error message is generic or payload content lacks `finish_detail`.

- [ ] **Step 3: Implement a small invalid-finish message helper**

In `src/services/agent_run_service.py`, add a module-level helper near other private helpers:

```python
def _invalid_finish_error_message(finish_detail: Any) -> str:
    kind = None
    if finish_detail is not None:
        kind = getattr(finish_detail, "kind", None)
        if kind is None and isinstance(finish_detail, dict):
            kind = finish_detail.get("kind")

    if kind == "output_length_exceeded":
        return (
            "模型输出被 provider 的输出 token 上限截断，"
            "未形成可提交的最终回答。请缩短上下文或提高输出上限后重试。"
        )
    if kind == "content_filtered":
        return "模型输出被 provider 内容策略截断或拦截，未形成可提交的最终回答。"
    if kind == "reasoning_only":
        return "模型只返回了思考内容，没有生成可见最终回答。请重试。"
    if kind == "empty_response":
        return "模型本轮没有返回可见最终回答。请重试。"
    if kind == "missing_llm_response":
        return "模型流结束但没有返回可验证的响应对象。请重试。"
    return "模型没有返回有效最终回答。请重试。"
```

Replace the generic invalid finish `ErrorEvent` message:

```python
message=_invalid_finish_error_message(run_result_event.finish_detail),
```

Do not change the `StreamClosedEvent` branch.

- [ ] **Step 4: Run quota invalid-finish tests and verify GREEN**

Run:

```bash
uv run pytest \
  tests/matmaster/integration/test_quota_pipeline.py::TestQuotaNotDeductedOnFailure::test_invalid_finish_emits_error_and_stream_closed_event \
  tests/matmaster/integration/test_quota_pipeline.py::TestQuotaNotDeductedOnFailure::test_empty_stop_invalid_finish_emits_error_and_stream_closed_event \
  -q
```

Expected: run result content includes `finish_detail`, the output-length message is specific, and `stream_closed` still only carries `end_reason="invalid_finish"`.

- [ ] **Step 5: Commit service messages**

Run:

```bash
git add src/services/agent_run_service.py tests/matmaster/integration/test_quota_pipeline.py
git commit -m "feat: explain invalid finish causes"
```

## Task 7: DevShell Drain, Runner, CLI, And Event Log

**Files:**

- Modify: `matmaster/core/stream_drain.py`
- Modify: `matmaster/devshell/runner.py`
- Modify: `matmaster/devshell/cli.py`
- Modify: `matmaster/devshell/event_logger.py`
- Test: `tests/matmaster/core/test_stream_drain.py`
- Test: `tests/matmaster/devshell/test_runner.py`
- Test: `tests/matmaster/devshell/test_repl.py`
- Test: `tests/matmaster/devshell/test_event_logger.py`

- [ ] **Step 1: Write failing stream drain test**

In `tests/matmaster/core/test_stream_drain.py`, import `FinishDetail` and add:

```python
@pytest.mark.asyncio
async def test_drain_run_stream_copies_finish_detail() -> None:
    detail = FinishDetail(
        kind="output_length_exceeded",
        provider_finish_reason="length",
        message="Model output was truncated by the provider output-token limit.",
    )

    async def stream():
        yield RunResultEvent(
            source="agent",
            status="failed",
            reason="invalid_finish",
            finish_detail=detail,
        )

    result = await drain_run_stream(stream())

    assert result.finish_detail is detail
```

- [ ] **Step 2: Write failing runner observer test**

In `tests/matmaster/devshell/test_runner.py`, add:

```python
    def test_observer_run_result_preserves_finish_detail(self, tmp_path: Path) -> None:
        from matmaster.core.stream_drain import DrainResult
        from matmaster.devshell.event_observer import DevEventObserver
        from matmaster.devshell.runner import DevRunner
        from matmaster.types.events import FinishDetail, RunResultEvent

        runner = self._make_runner(tmp_path)
        detail = FinishDetail(
            kind="output_length_exceeded",
            provider_finish_reason="length",
            message="Model output was truncated by the provider output-token limit.",
        )
        fake_result = DrainResult(
            status="failed",
            reason="invalid_finish",
            final_content=None,
            num_turns=1,
            usage={},
            messages=[],
            finish_detail=detail,
        )
        runtime = MagicMock()
        runtime.spec = MagicMock(tool_catalog=None)
        runtime.kernel = MagicMock()
        runtime.kernel.run_stream.return_value = object()
        fake_exp = MagicMock()
        fake_exp.build_runtime = AsyncMock(return_value=runtime)
        fake_exp._run_cleanup_callbacks = AsyncMock()
        observer = DevEventObserver()

        with (
            patch("matmaster.devshell.runner.Exp", return_value=fake_exp),
            patch(
                "matmaster.core.stream_drain.drain_run_stream",
                new=AsyncMock(return_value=fake_result),
            ),
        ):
            runner.run("test", event_observer=observer)

        emitted = observer.drain()
        terminal = next(event for event in emitted if isinstance(event, RunResultEvent))
        assert terminal.finish_detail is detail
```

- [ ] **Step 3: Write failing CLI summary test**

In `tests/matmaster/devshell/test_repl.py`, keep the existing `test_run_single_uses_drain_result_fields` unchanged. Add a separate invalid-finish test:

```python
    def test_run_single_serializes_finish_detail_on_invalid_finish(
        self, capsys, tmp_path: Path
    ) -> None:
        from matmaster.core.stream_drain import DrainResult
        from matmaster.devshell.cli import _run_single, parse_args
        from matmaster.types.events import FinishDetail

        args = parse_args(
            [
                "run",
                "--workdir",
                str(tmp_path / "ws"),
                "--log-dir",
                str(tmp_path / "logs"),
                "-p",
                "hello",
            ]
        )
        drain_result = DrainResult(
            status="failed",
            reason="invalid_finish",
            final_content=None,
            num_turns=1,
            usage={},
            messages=[],
            finish_detail=FinishDetail(
                kind="output_length_exceeded",
                provider_finish_reason="length",
                message="Model output was truncated by the provider output-token limit.",
            ),
        )
        resolved = SimpleNamespace(model="m", profile_key="p", route_key="r")

        with patch(
            "matmaster.devshell.cli._run_with_event_log",
            return_value=(drain_result, tmp_path / "logs" / "events.jsonl"),
        ):
            rc = _run_single(args, runner=object(), resolved=resolved)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert rc == 1
        assert output["reason"] == "invalid_finish"
        assert output["finish_detail"]["kind"] == "output_length_exceeded"
        assert output["finish_detail"]["provider_finish_reason"] == "length"
        assert output["finish_detail"]["message"] == (
            "Model output was truncated by the provider output-token limit."
        )
```

- [ ] **Step 4: Write failing event logger test**

In `tests/matmaster/devshell/test_event_logger.py`, extend `test_run_result_event` or add:

```python
    def test_run_result_event_includes_finish_detail(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger
        from matmaster.types.events import FinishDetail

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-002")

        logger.log_event(
            RunResultEvent(
                source="test",
                status="failed",
                reason="invalid_finish",
                finish_detail=FinishDetail(
                    kind="output_length_exceeded",
                    provider_finish_reason="length",
                    message="Model output was truncated by the provider output-token limit.",
                ),
            )
        )
        logger.close()

        rec = json.loads(log_file.read_text().strip())
        assert rec["finish_detail"]["kind"] == "output_length_exceeded"
```

- [ ] **Step 5: Run devshell tests and verify RED**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_stream_drain.py \
  tests/matmaster/devshell/test_runner.py \
  tests/matmaster/devshell/test_repl.py::TestCliRunMode::test_run_single_serializes_finish_detail_on_invalid_finish \
  tests/matmaster/devshell/test_event_logger.py::TestEventLogger::test_run_result_event_includes_finish_detail \
  -q
```

Expected: new tests fail because drain/runner/CLI/event logger drop `finish_detail`.

- [ ] **Step 6: Implement drain and devshell propagation**

In `matmaster/core/stream_drain.py`:

```python
from matmaster.types.events import FinishDetail

@dataclass
class DrainResult:
    finish_detail: FinishDetail | None = None
```

When returning from `drain_run_stream()`:

```python
finish_detail=event.finish_detail,
```

In `matmaster/devshell/runner.py`, when re-emitting `RunResultEvent`, add:

```python
finish_detail=getattr(result, "finish_detail", None),
```

In `matmaster/devshell/cli.py`, add to summary only when present:

```python
finish_detail = getattr(result, "finish_detail", None)
if finish_detail is not None:
    if hasattr(finish_detail, "model_dump"):
        summary["finish_detail"] = finish_detail.model_dump(mode="json")
    else:
        summary["finish_detail"] = dict(finish_detail)
```

In `matmaster/devshell/event_logger.py`, include detail in run result records:

```python
record = {
    "type": "run_result",
    "status": event.status,
    "reason": event.reason,
}
if event.finish_detail is not None:
    record["finish_detail"] = event.finish_detail.model_dump(mode="json")
return record
```

- [ ] **Step 7: Run devshell tests and verify GREEN**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_stream_drain.py \
  tests/matmaster/devshell/test_runner.py \
  tests/matmaster/devshell/test_repl.py::TestCliRunMode::test_run_single_serializes_finish_detail_on_invalid_finish \
  tests/matmaster/devshell/test_event_logger.py::TestEventLogger::test_run_result_event_includes_finish_detail \
  -q
```

Expected: all devshell/drain tests pass.

- [ ] **Step 8: Commit devshell propagation**

Run:

```bash
git add \
  matmaster/core/stream_drain.py \
  matmaster/devshell/runner.py \
  matmaster/devshell/cli.py \
  matmaster/devshell/event_logger.py \
  tests/matmaster/core/test_stream_drain.py \
  tests/matmaster/devshell/test_runner.py \
  tests/matmaster/devshell/test_repl.py \
  tests/matmaster/devshell/test_event_logger.py
git commit -m "feat: surface finish details in devshell"
```

## Task 8: Regression Suite And Contract Checks

**Files:**

- No new production files unless a line-count extraction is required.
- Update only tests needed to keep existing assertions aligned with the new optional field.

- [ ] **Step 1: Run focused invalid-finish regression tests**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_agent_kernel_stream.py \
  tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py \
  tests/matmaster/core/test_stream_drain.py \
  tests/matmaster/types/test_events.py \
  tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/integration/test_quota_pipeline.py \
  tests/matmaster/devshell/test_runner.py \
  tests/matmaster/devshell/test_repl.py \
  tests/matmaster/devshell/test_event_logger.py \
  -q
```

Expected: all targeted tests pass.

- [ ] **Step 2: Add assistant-state history replay regression**

In `tests/matmaster/integration/test_events_to_messages.py`, add this test near the existing wrapped assistant-state tests:

```python
    def test_wrapped_assistant_state_finish_detail_does_not_enter_message(self):
        events = [
            _user_event("q"),
            {
                "source": "MatMaster",
                "type": "assistant_state",
                "content": {
                    "state": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "name": "bash",
                                "arguments": {"cmd": "pwd"},
                            }
                        ],
                    },
                    "finish_detail": {
                        "kind": "output_length_exceeded",
                        "provider_finish_reason": "length",
                        "message": (
                            "Model output was truncated by the provider "
                            "output-token limit."
                        ),
                        "has_tool_calls": True,
                        "truncation_risk": True,
                    },
                },
            },
            _tool_result_event("call-1", "bash", "/tmp"),
            _response_event("done"),
        ]

        result = ChatHistoryConverter.events_to_messages(events)

        assistant_with_tools = [
            m for m in result if isinstance(m, AssistantMessage) and m.tool_calls
        ]
        assert len(assistant_with_tools) == 1
        assert assistant_with_tools[0].tool_calls[0].id == "call-1"
        assert not hasattr(assistant_with_tools[0], "finish_detail")
```

- [ ] **Step 3: Run protocol guardrail tests because assistant state and tool-call behavior changed**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_agent_kernel_protocol_guardrails.py \
  tests/matmaster/integration/test_tool_protocol_guardrails.py \
  tests/matmaster/integration/test_events_to_messages.py \
  -q
```

Expected: all tests pass. These verify that adding `AssistantStateEvent.finish_detail` does not break persisted assistant message restoration or tool-call protocol constraints.

- [ ] **Step 4: Run service stream tests for terminal ordering**

Run:

```bash
uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  -q
```

Expected: all tests pass. `run_result -> error -> stream_closed` ordering for invalid finish remains unchanged where covered by quota tests, and normal terminal stream ordering remains unchanged here.

- [ ] **Step 5: Check line counts and formatting hygiene**

Run:

```bash
wc -l matmaster/core/agent.py src/services/agent_run_service.py matmaster/types/events.py matmaster/core/finish_diagnostics.py
git diff --check
```

Expected: no touched source file exceeds 1000 lines and `git diff --check` reports no whitespace errors.

- [ ] **Step 6: Commit remaining test alignment**

If Step 1-5 required any additional test-only fixes, commit them:

```bash
git add tests matmaster src
git commit -m "test: cover invalid finish diagnostics"
```

Skip this commit when there are no remaining changes after the previous task commits.

## Final Verification

- [ ] **Step 1: Run the complete targeted verification set**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_agent_kernel_stream.py \
  tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py \
  tests/matmaster/core/test_stream_drain.py \
  tests/matmaster/types/test_events.py \
  tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/integration/test_quota_pipeline.py \
  tests/matmaster/integration/test_events_to_messages.py \
  tests/matmaster/integration/test_tool_protocol_guardrails.py \
  tests/matmaster/core/test_agent_kernel_protocol_guardrails.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  tests/matmaster/devshell/test_runner.py \
  tests/matmaster/devshell/test_repl.py \
  tests/matmaster/devshell/test_event_logger.py \
  -q
```

Expected: all listed tests pass.

- [ ] **Step 2: Inspect the final diff**

Run:

```bash
git diff --stat HEAD
git diff --check
```

Expected: no unstaged whitespace errors. If all implementation tasks were committed, `git diff --stat HEAD` is empty.

- [ ] **Step 3: Summarize behavior for review**

Prepare a short reviewer note containing:

- `invalid_finish` top-level reason is unchanged.
- `RunResultEvent.finish_detail` and `run_result.content.finish_detail` now carry the diagnostic.
- `output_length_exceeded` maps provider `finish_reason="length"` and includes last-turn usage.
- Tool-call `length` finish is observable on `AssistantStateEvent.finish_detail` but still executes as before.
- `StreamClosedEvent` remains transport-only and does not include `failure_kind`.
- Devshell JSON summary and event logs include `finish_detail`.
