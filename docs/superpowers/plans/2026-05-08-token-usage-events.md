# Token Usage Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist token usage on accepted complete `response` events and on `run_result` public content without breaking live SSE, replay, or chat-history text consumption.

**Architecture:** Keep provider/kernel usage accumulation as the source of truth. Rename retry-before response segment markers to `stream_state="segment_end"`, and reserve `stream_state="complete"` for retry-accepted audit responses. DB persistence stores structured response content; live/replay SSE normalize it back to string `content` plus top-level usage metadata.

**Tech Stack:** Python 3.11+ via `uv run`, Pydantic event models, async generator kernel, pytest.

---

## Source Spec

- `docs/superpowers/specs/2026-05-08-token-usage-events-design.md`

## File Map

- Create `matmaster/core/kernel_items.py`: move `_TerminalItem`, `_KernelItem`, `_KernelState`, `_KernelStopRequested` out of `agent.py` so the 998-line file stays under the 1000-line gate.
- Modify `matmaster/core/agent.py`: emit `segment_end` before retry gate, emit root-only usage-bearing `response.complete` after accepted response, add `turn_index` to assistant/tool result events.
- Modify `matmaster/core/exp.py`: write `spawn_id` into `spec.meta`.
- Modify `matmaster/types/events.py`: add response usage fields and usage-event `turn_index`.
- Modify `matmaster/integration/event_payloads.py`: map response/run_result usage and provide `normalize_response_sse_payload`.
- Modify `matmaster/integration/persistence_handler.py`, `matmaster/integration/sse_handler.py`, `src/services/stream_service.py`: skip `segment_end` and unpack replay response content.
- Modify tests listed in each task. If `tests/matmaster/core/test_agent_kernel_stream.py` grows past 1000 lines, move the new token-usage tests into `tests/matmaster/core/test_agent_kernel_usage_events.py`.

## Constraints

- Use `uv run pytest ...`, not system Python.
- Do not change DB schema.
- Do not aggregate sub-agent token usage in this phase.
- Do not touch unrelated files.

---

### Task 1: Event Models And Line-Count Guard

**Files:**
- Create: `matmaster/core/kernel_items.py`
- Modify: `matmaster/core/agent.py`
- Modify: `matmaster/types/events.py`
- Test: `tests/matmaster/types/test_events.py`

- [ ] **Step 1: Write failing event model tests**

Add to `tests/matmaster/types/test_events.py`:

```python
class TestResponseEventUsage:
    def test_response_usage_fields(self) -> None:
        evt = ResponseEvent(
            source="agent",
            content="answer",
            stream_state="complete",
            turn_index=2,
            turn_usage={"prompt_tokens": 10, "completion_tokens": 4},
            total_usage={"prompt_tokens": 30, "completion_tokens": 9},
            usage_vendor={"inputTokens": 10, "outputTokens": 4},
        )

        assert evt.turn_index == 2
        assert evt.turn_usage == {"prompt_tokens": 10, "completion_tokens": 4}
        assert evt.total_usage == {"prompt_tokens": 30, "completion_tokens": 9}
        assert evt.usage_vendor == {"inputTokens": 10, "outputTokens": 4}

    def test_response_usage_defaults(self) -> None:
        evt = ResponseEvent(source="agent")
        assert evt.turn_index is None
        assert evt.turn_usage == {}
        assert evt.total_usage == {}
        assert evt.usage_vendor is None


def test_tool_result_turn_index_defaults_to_none() -> None:
    evt = ToolResultEvent(
        source="agent",
        call_id="c1",
        tool_name="bash",
        result="output",
    )
    assert evt.turn_index is None


def test_assistant_state_turn_index_defaults_to_none() -> None:
    evt = AssistantStateEvent(source="agent", state={"content": None})
    assert evt.turn_index is None
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/types/test_events.py::TestResponseEventUsage \
  tests/matmaster/types/test_events.py::test_tool_result_turn_index_defaults_to_none \
  tests/matmaster/types/test_events.py::test_assistant_state_turn_index_defaults_to_none -q
```

Expected: FAIL because the fields are not defined.

- [ ] **Step 3: Extract kernel item dataclasses**

Create `matmaster/core/kernel_items.py`:

```python
"""Internal kernel item dataclasses used by AgentKernel."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from matmaster.types.events import FinishDetail
from matmaster.types.messages import LLMResponse


@dataclass
class _TerminalItem:
    reason: str
    final_content: str | None = None
    num_turns: int = 0
    usage: dict[str, int] = dc_field(default_factory=dict)
    usage_vendor_by_turn: list[dict[str, Any]] = dc_field(default_factory=list)
    messages: list[Any] = dc_field(default_factory=list)
    finish_detail: FinishDetail | None = None


@dataclass
class _KernelItem:
    event: Any = None
    llm_response: LLMResponse | None = None
    messages_delta: list[Any] | None = None
    terminal: _TerminalItem | None = None


@dataclass
class _KernelState:
    messages: list[Any]
    turn: int = 0
    total_usage: dict[str, int] = dc_field(default_factory=dict)
    usage_vendor_by_turn: list[dict[str, Any]] = dc_field(default_factory=list)
    cached_tool_definitions: list[dict[str, Any]] | None = None
    last_catalog_version: int = -1


class _KernelStopRequested(Exception):
    pass
```

Modify `matmaster/core/agent.py`:

```python
from matmaster.core.kernel_items import (
    _KernelItem,
    _KernelState,
    _KernelStopRequested,
    _TerminalItem,
)
```

Remove the matching in-file dataclass/exception definitions and remove now-unused dataclass imports.

- [ ] **Step 4: Add event fields**

Modify `matmaster/types/events.py`:

```python
class ResponseEvent(EventBase):
    type: Literal["response"] = "response"
    content: str = ""
    stream_state: str | None = None
    stream_id: str | None = None
    turn_index: int | None = None
    turn_usage: dict[str, int] = Field(default_factory=dict)
    total_usage: dict[str, int] = Field(default_factory=dict)
    usage_vendor: dict[str, Any] | None = None


class ToolResultEvent(EventBase):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    tool_name: str
    result: Any
    status: str = "success"
    payload: dict[str, Any] = Field(default_factory=dict)
    turn_index: int | None = None
    turn_usage: dict[str, int] = Field(default_factory=dict)
    total_usage: dict[str, int] = Field(default_factory=dict)


class AssistantStateEvent(EventBase):
    type: Literal["assistant_state"] = "assistant_state"
    state: dict[str, Any]
    turn_index: int | None = None
    turn_usage: dict[str, int] = Field(default_factory=dict)
    total_usage: dict[str, int] = Field(default_factory=dict)
    finish_detail: FinishDetail | None = None
```

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/matmaster/types/test_events.py \
  tests/matmaster/core/test_agent_kernel_stream.py::TestGap2RunStreamYieldsBusEvent -q
wc -l matmaster/core/agent.py matmaster/core/kernel_items.py
```

Expected: PASS, and `agent.py` below 1000 lines.

Commit:

```bash
git add matmaster/core/kernel_items.py matmaster/core/agent.py \
  matmaster/types/events.py tests/matmaster/types/test_events.py
git commit -m "feat: add usage fields to agent events"
```

---

### Task 2: Public Payload Mapping And SSE Unpack

**Files:**
- Modify: `matmaster/integration/event_payloads.py`
- Test: `tests/matmaster/integration/test_event_payloads.py`

- [ ] **Step 1: Write failing payload tests**

Add `normalize_response_sse_payload` to the imports in `tests/matmaster/integration/test_event_payloads.py`, then add:

```python
def test_response_with_usage_returns_structured_content(self) -> None:
    payload = {
        "type": "response",
        "source": "Agent",
        "content": "answer",
        "stream_state": "complete",
        "stream_id": "s1",
        "turn_index": 2,
        "turn_usage": {"prompt_tokens": 10, "completion_tokens": 4},
        "total_usage": {"prompt_tokens": 30, "completion_tokens": 9},
        "usage_vendor": {"inputTokens": 10, "outputTokens": 4},
    }
    assert _public_content_for_event("response", payload) == {
        "content": "answer",
        "turn_index": 2,
        "stream_id": "s1",
        "turn_usage": {"prompt_tokens": 10, "completion_tokens": 4},
        "total_usage": {"prompt_tokens": 30, "completion_tokens": 9},
        "usage_vendor": {"inputTokens": 10, "outputTokens": 4},
    }


def test_run_result_public_content_includes_usage(self) -> None:
    payload = {
        "type": "run_result",
        "source": "Agent",
        "status": "completed",
        "reason": "natural",
        "final_content": "done",
        "num_turns": 2,
        "usage": {"prompt_tokens": 20, "completion_tokens": 6},
        "usage_vendor_by_turn": [{"inputTokens": 20, "outputTokens": 6}],
    }
    assert _public_content_for_event("run_result", payload)["usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 6,
    }


def test_usage_event_mappings_preserve_turn_index(self) -> None:
    state = {"role": "assistant", "content": None, "tool_calls": []}
    assistant = _public_content_for_event(
        "assistant_state",
        {
            "state": state,
            "turn_index": 1,
            "turn_usage": {"prompt_tokens": 10},
            "total_usage": {"prompt_tokens": 10},
        },
    )
    tool = _public_content_for_event(
        "tool_result",
        {
            "call_id": "call-6",
            "tool_name": "bash",
            "result": "output",
            "status": "success",
            "turn_index": 1,
            "turn_usage": {"prompt_tokens": 10},
            "total_usage": {"prompt_tokens": 10},
        },
    )
    assert assistant["turn_index"] == 1
    assert tool["turn_index"] == 1


def test_structured_response_content_is_unpacked_for_sse(self) -> None:
    payload = {
        "source": "MatMaster",
        "type": "response",
        "content": {
            "content": "answer",
            "turn_index": 3,
            "turn_usage": {"total_tokens": 12},
            "total_usage": {"total_tokens": 30},
            "usage_vendor": {"inputTokens": 10, "outputTokens": 2},
        },
        "session_id": "sess",
        "task_id": "task",
        "spawn_id": None,
    }
    normalized = normalize_response_sse_payload(payload)
    assert normalized["content"] == "answer"
    assert normalized["turn_index"] == 3
    assert normalized["turn_usage"] == {"total_tokens": 12}
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/integration/test_event_payloads.py -q
```

Expected: FAIL because response/run_result mappings and `normalize_response_sse_payload` are missing.

- [ ] **Step 3: Implement mappings**

Modify `matmaster/integration/event_payloads.py`:

```python
_RESPONSE_USAGE_KEYS = (
    "turn_index",
    "stream_id",
    "turn_usage",
    "total_usage",
    "usage_vendor",
)


def _response_public_content(payload: dict[str, Any]) -> object | None:
    content = payload.get("content")
    if not (payload.get("turn_usage") or payload.get("total_usage")):
        return content
    out: dict[str, Any] = {"content": content or ""}
    for key in _RESPONSE_USAGE_KEYS:
        value = payload.get(key)
        if value is not None and value != {}:
            out[key] = value
    return out


def normalize_response_sse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("type") != "response":
        return payload
    content = payload.get("content")
    if not isinstance(content, dict) or "content" not in content:
        return payload
    normalized = dict(payload)
    normalized["content"] = str(content.get("content") or "")
    for key in _RESPONSE_USAGE_KEYS:
        if key in content and content.get(key) is not None:
            normalized[key] = content[key]
    return normalized
```

Use it in `build_public_sse_payload_from_bus_dump()`:

```python
    for key, value in raw.items():
        if key not in out:
            out[key] = value
    return normalize_response_sse_payload(out)
```

Add response mapping before `tool_call`:

```python
    if event_type == "response":
        return _response_public_content(payload)
```

Update `run_result` mapping to add `num_turns`, `usage`, and non-empty `usage_vendor_by_turn`. Update `assistant_state` and `tool_result` mappings to copy `turn_index` when present.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/matmaster/integration/test_event_payloads.py -q
```

Expected: PASS.

Commit:

```bash
git add matmaster/integration/event_payloads.py \
  tests/matmaster/integration/test_event_payloads.py
git commit -m "feat: map usage in public event payloads"
```

---

### Task 3: Persistence, SSE, And Replay Filters

**Files:**
- Modify: `matmaster/integration/persistence_handler.py`
- Modify: `matmaster/integration/sse_handler.py`
- Modify: `src/services/stream_service.py`
- Test: `tests/matmaster/integration/test_sse_handler_mode_filter.py`
- Test: `tests/matmaster/integration/test_upstream_scenarios.py`
- Test: `tests/test_stream_replay_skill_hit.py`
- Test: `tests/matmaster/integration/test_events_to_messages.py`

- [ ] **Step 1: Write failing filter/replay tests**

Add:

```python
def test_segment_end_response_filtered_like_complete() -> None:
    handler = _make_handler("planner")
    ev = ResponseEvent(
        source="MatMaster",
        content="segment text",
        stream_state="segment_end",
    )
    assert handler._should_skip(ev) is True
```

Add to `TestEventHandlerPersistence`:

```python
async def test_persistence_skips_response_segment_end(self) -> None:
    mock_events_table = MagicMock()
    handler = PersistenceHandler(mock_events_table, "sess-1", "task-1")
    await handler.handle(
        ResponseEvent(
            source="agent",
            content="segment text",
            stream_state="segment_end",
            stream_id="s1",
        )
    )
    mock_events_table.add_event.assert_not_called()
```

Add the replay guard to `tests/test_stream_replay_skill_hit.py`, and add the
history guard to `TestEventsToMessagesPreservesOrder` in
`tests/matmaster/integration/test_events_to_messages.py`:

```python
def test_normalize_replayed_event_unpacks_structured_response_content() -> None:
    from src.services.stream_service import _normalize_replayed_event

    out = _normalize_replayed_event(
        {
            "source": "agent",
            "type": "response",
            "content": {
                "content": "answer",
                "turn_index": 1,
                "turn_usage": {"total_tokens": 12},
                "total_usage": {"total_tokens": 20},
            },
            "session_id": "sess",
            "task_id": "task",
            "spawn_id": None,
        }
    )
    assert out["source"] == "MatMaster"
    assert out["content"] == "answer"
    assert out["turn_index"] == 1


def test_structured_response_content_discards_usage_metadata(self):
    events = [
        _user_event("q"),
        {
            "source": "MatMaster",
            "type": "response",
            "content": {
                "content": "answer",
                "turn_usage": {"total_tokens": 12},
            },
        },
    ]
    result = ChatHistoryConverter.events_to_messages(events)
    assert isinstance(result[-1], AssistantMessage)
    assert result[-1].content == "answer"
```

- [ ] **Step 2: Run targeted tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/integration/test_sse_handler_mode_filter.py \
  tests/matmaster/integration/test_upstream_scenarios.py::TestEventHandlerPersistence \
  tests/test_stream_replay_skill_hit.py::test_normalize_replayed_event_unpacks_structured_response_content \
  tests/matmaster/integration/test_events_to_messages.py::TestEventsToMessagesPreservesOrder::test_structured_response_content_discards_usage_metadata -q
```

Expected: FAIL for filters/replay; history may already pass.

- [ ] **Step 3: Implement filters and replay unpack**

Use these exact changes:

```python
# matmaster/integration/persistence_handler.py
_STREAMING_STATES = frozenset({"start", "streaming", "segment_end", "end"})
```

```python
# matmaster/integration/sse_handler.py
if (
    isinstance(event, (ThoughtEvent, ResponseEvent))
    and event.stream_state in {"complete", "segment_end"}
):
    return True
```

```python
# src/services/stream_service.py
from matmaster.integration.event_payloads import normalize_response_sse_payload


def _normalize_replayed_event(event: dict) -> dict:
    replay_event = dict(event)
    replay_event["source"] = normalize_event_source(replay_event.get("source"))
    return normalize_response_sse_payload(replay_event)
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/matmaster/integration/test_sse_handler_mode_filter.py \
  tests/matmaster/integration/test_upstream_scenarios.py::TestEventHandlerPersistence \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_events_to_messages.py -q
```

Expected: PASS.

Commit:

```bash
git add matmaster/integration/persistence_handler.py \
  matmaster/integration/sse_handler.py src/services/stream_service.py \
  tests/matmaster/integration/test_sse_handler_mode_filter.py \
  tests/matmaster/integration/test_upstream_scenarios.py \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_events_to_messages.py
git commit -m "feat: normalize usage response replay"
```

---

### Task 4: Kernel Accepted Response Usage Events

**Files:**
- Modify: `matmaster/core/agent.py`
- Modify: `matmaster/core/exp.py`
- Test: `tests/matmaster/core/test_agent_kernel_stream.py`

- [ ] **Step 1: Write failing kernel tests**

Add or update these tests:

```python
@pytest.mark.asyncio
async def test_response_segment_end_at_stream_end(self) -> None:
    from matmaster.core.agent import AgentKernel, _KernelItem

    provider = ReasoningThenContentProvider()
    spec = _make_spec(provider=provider)
    kernel = AgentKernel()
    items: list[_KernelItem] = []
    async for item in kernel._stream_llm_items(
        spec, [{"role": "user", "content": "test"}], None
    ):
        items.append(item)

    completes = [
        i for i in items
        if i.event and isinstance(i.event, ResponseEvent)
        and i.event.stream_state == "complete"
    ]
    segment_ends = [
        i for i in items
        if i.event and isinstance(i.event, ResponseEvent)
        and i.event.stream_state == "segment_end"
    ]
    assert completes == []
    assert "visible part 1" in segment_ends[0].event.content


@pytest.mark.asyncio
async def test_run_stream_emits_usage_bearing_response_complete(self) -> None:
    from matmaster.core.agent import AgentKernel

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        _make_spec(provider=ContentOnlyProvider()), "test task"
    ):
        events.append(event)

    completes = [
        e for e in events
        if isinstance(e, ResponseEvent) and e.stream_state == "complete"
    ]
    assert len(completes) == 1
    assert completes[0].content == "hello world"
    assert completes[0].turn_index == 0
    assert completes[0].turn_usage == {"prompt_tokens": 5}
    assert completes[0].total_usage == {"prompt_tokens": 5}


@pytest.mark.asyncio
async def test_retry_discarded_attempt_does_not_emit_usage_response_complete(self) -> None:
    from matmaster.core.agent import AgentKernel

    provider = EmptyThenContentProvider()
    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        _make_spec(provider=provider), "test task"
    ):
        events.append(event)

    completes = [
        e for e in events
        if isinstance(e, ResponseEvent) and e.stream_state == "complete"
    ]
    assert provider.call_count == 2
    assert [e.content for e in completes] == ["recovered"]


@pytest.mark.asyncio
async def test_child_runtime_does_not_emit_usage_response_complete(self) -> None:
    from matmaster.core.agent import AgentKernel

    spec = _make_spec(provider=ContentOnlyProvider()).model_copy(
        update={"meta": {"spawn_id": "child-1"}}
    )
    events: list[Any] = []
    async for event in AgentKernel().run_stream(spec, "child task"):
        events.append(event)

    assert not [
        e for e in events
        if isinstance(e, ResponseEvent) and e.stream_state == "complete"
    ]
```

Also extend `test_assistant_state_drops_trivial_tool_call_preamble_content`:

```python
assert assistant_state_events[0].turn_index == 0
assert assistant_state_events[0].turn_usage != {}
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestStreamLlmItems \
  tests/matmaster/core/test_agent_kernel_stream.py::TestGap2RunStreamYieldsBusEvent \
  tests/matmaster/core/test_agent_kernel_stream.py::TestRunItemsAssistantState -q
```

Expected: FAIL because `segment_end`, accepted `response.complete`, and `turn_index` propagation are not implemented.

- [ ] **Step 3: Implement child metadata**

Modify `matmaster/core/exp.py` in the `spec.meta` update:

```python
"meta": {
    **spec.meta,
    "task_id": run_meta.get("task_id", ""),
    "session_id": run_meta.get("session_id", ""),
    "spawn_id": spawn_id,
    "checkpoint_sink_factory": checkpoint_sink_factory,
    "checkpoint_sink": checkpoint_sink,
},
```

- [ ] **Step 4: Implement kernel event changes**

In `_stream_llm_items()`, replace response segment `_response_item(..., "complete")` calls with:

```python
yield self._response_item(visible_snapshot, stream_id, "segment_end")
```

After `_accumulate_usage(...)` and `usage_vendor_by_turn.append(...)` in `_run_items()`, add:

```python
is_root_run = spec.meta.get("spawn_id") is None
if (
    is_root_run
    and response.content
    and not is_trivial_response_text(response.content)
):
    yield _KernelItem(
        event=ResponseEvent(
            source="agent",
            content=response.content,
            stream_state="complete",
            turn_index=state.turn,
            turn_usage=dict(turn_usage),
            total_usage=dict(state.total_usage),
            usage_vendor=dict(response.usage_vendor)
            if response.usage_vendor
            else None,
        )
    )
```

Add `turn_index=state.turn` to `AssistantStateEvent(...)` and `ToolResultEvent(...)` constructors.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_stream.py -q
wc -l matmaster/core/agent.py tests/matmaster/core/test_agent_kernel_stream.py
```

Expected: PASS and both files below 1000 lines, or split new kernel usage tests before committing.

Commit:

```bash
git add matmaster/core/agent.py matmaster/core/exp.py \
  tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "feat: emit accepted response usage events"
```

---

### Task 5: Audit Invariants And Final Verification

**Files:**
- Modify: `tests/matmaster/core/test_agent_kernel_stream.py`
- Modify: `tests/matmaster/integration/test_event_payloads.py`

- [ ] **Step 1: Add invariant tests**

Add:

```python
@pytest.mark.asyncio
async def test_completed_run_result_usage_matches_distinct_response_turn_usage(self) -> None:
    from matmaster.core.agent import AgentKernel

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        _make_spec(provider=ContentOnlyProvider()), "test task"
    ):
        events.append(event)

    usage: dict[str, int] = {}
    seen: set[int] = set()
    for event in events:
        if not isinstance(event, ResponseEvent):
            continue
        if event.stream_state != "complete" or event.turn_index is None:
            continue
        if event.turn_index in seen:
            continue
        seen.add(event.turn_index)
        for key, value in event.turn_usage.items():
            usage[key] = usage.get(key, 0) + value

    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert usage == run_result.usage


def test_failed_run_result_preserves_usage_and_finish_detail(self) -> None:
    detail = {
        "kind": "reasoning_only",
        "message": "Model produced reasoning but no visible content.",
        "last_turn_usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    content = _public_content_for_event(
        "run_result",
        {
            "status": "failed",
            "reason": "invalid_finish",
            "final_content": None,
            "num_turns": 1,
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            "finish_detail": detail,
        },
    )
    assert content["usage"] == {"prompt_tokens": 10, "completion_tokens": 2}
    assert content["finish_detail"] == detail
```

- [ ] **Step 2: Run protocol regression suite**

Run:

```bash
uv run pytest tests/matmaster/types/test_events.py \
  tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/integration/test_sse_handler_mode_filter.py \
  tests/matmaster/integration/test_upstream_scenarios.py::TestEventHandlerPersistence \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_events_to_messages.py \
  tests/matmaster/core/test_agent_kernel_stream.py -q
git diff --check
wc -l matmaster/core/agent.py matmaster/core/kernel_items.py \
  tests/matmaster/core/test_agent_kernel_stream.py
```

Expected: pytest PASS, diff check exits 0, no touched file exceeds 1000 lines.

- [ ] **Step 3: Commit Task 5**

```bash
git add tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "test: cover token usage audit invariants"
```

- [ ] **Step 4: Manual contract inspection**

Run:

```bash
git diff HEAD~5 -- matmaster/types/events.py matmaster/core/agent.py \
  matmaster/integration/event_payloads.py src/services/stream_service.py
```

Confirm:

- `response.segment_end` only appears before retry gate.
- `response.complete` with usage is emitted after `state.total_usage` accumulation.
- DB response content is structured only when usage exists.
- SSE/replay response content is string after normalization.
- `run_result` public content includes usage fields when present.
- `AssistantStateEvent` and `ToolResultEvent` include `turn_index`.

---

## Self-Review

- Spec coverage: response usage Task 4; run_result usage Task 2/5; live/replay SSE string content Task 2/3; ChatHistoryConverter guard Task 3; retry guard Task 4; `segment_end` Task 3/4; root-only sub-agent boundary Task 4; usage authority and `turn_index` Task 1/2/4/5.
- Placeholder scan: use the no-placeholder pattern set from `superpowers:writing-plans`; expected no output.
- Type consistency: field names are `turn_index`, `turn_usage`, `total_usage`, `usage_vendor`; helper is `normalize_response_sse_payload`; root boundary key is `spec.meta["spawn_id"]`.
