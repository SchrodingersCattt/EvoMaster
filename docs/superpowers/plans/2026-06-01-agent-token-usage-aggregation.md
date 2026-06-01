# Agent Token Usage Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `RunResultEvent.usage` represent known run-level scalar LLM usage for the root run, including root accepted LLM turns, `Agent` subagent usage, and compaction summary LLM usage.

**Architecture:** Keep `state.total_usage` as the single accumulator. Convert subagent `DrainResult.usage` and compaction summary `LLMResponse.usage` into validated scalar deltas, add them to `state.total_usage` exactly once before emitting the corresponding public event, and keep `state.turn_usage` root-turn-only. Move provider cache and reasoning projection into scalar `LLMResponse.usage` / `StreamChunk.usage` so service-level summaries no longer mix root-only vendor details into aggregate usage.

**Tech Stack:** Python 3.11+ via `uv run`, Pydantic event/message models, async AgentKernel stream, existing provider adapters, pytest.

---

## Source Spec

- `docs/superpowers/specs/2026-06-01-agent-token-usage-aggregation-design.md`

## Current Code Facts

- `RunResultEvent.usage` is emitted from `dict(state.total_usage)` in `matmaster/core/agent.py`.
- Root accepted turns already call `accumulate_usage(state.total_usage, response.usage)`.
- `ToolResultEvent` currently receives `turn_usage=state.turn_usage` and `total_usage=state.total_usage` by reference in `matmaster/core/agent_tool_dispatch.py`.
- `ResponseEvent` and `AssistantStateEvent` currently receive `state.turn_usage` / `state.total_usage` by reference in `matmaster/core/agent.py`.
- `DrainResult` currently lives in `matmaster/core/stream_drain.py`, which prevents `matmaster/tools/builtin/agent_tool.py` from depending on it without importing from `matmaster.core`.
- `SubagentOrchestrator.spawn()` currently returns final content text, not the drained child result.
- `call_summary_llm()` currently returns `str` and discards `LLMResponse.usage`.
- `CompactionEvent` has no usage fields.
- `_build_run_usage_summary()` currently documents root-only usage and uses `usage_vendor_by_turn` as fallback for cache/reasoning fields.

## File Map

- Create `matmaster/types/stream_drain.py`: owns the shared `DrainResult` dataclass.
- Modify `matmaster/core/stream_drain.py`: keep only `drain_run_stream()` and import `DrainResult` from types.
- Modify `matmaster/core/subagent_orchestrator.py`: return `DrainResult` from `make_spawn_fn()` and `spawn()`, while continuing to forward child events and emit hooks.
- Modify `matmaster/tools/builtin/agent_tool.py`: migrate `SpawnFn` to `Awaitable[DrainResult]` and map `DrainResult` into `ToolResult.payload["subagent_usage"]`.
- Modify `matmaster/devshell/runner.py`, `evaluation/skill_trigger/runner.py`, `tests/matmaster/devshell/test_runner.py`, `tests/matmaster/devshell/test_repl.py`: repoint their `DrainResult` imports from `matmaster.core.stream_drain` to `matmaster.types.stream_drain`. These are the only other `DrainResult` import sites in the repo; pure `drain_run_stream` importers (`matmaster/core/subagent_orchestrator.py`, `evaluation/core/mat_runner.py`, and the separate `drain_run_stream` import in `evaluation/skill_trigger/runner.py`) stay on `matmaster.core.stream_drain` and need no change.
- Modify `matmaster/core/agent_tool_dispatch.py`: add `extract_tool_usage_delta()`, validate `Agent` usage payloads, accumulate deltas before event emission, and snapshot usage dicts.
- Modify `matmaster/core/agent.py`: snapshot usage dicts for `ResponseEvent` and `AssistantStateEvent`; catch malformed tool usage deltas at the dispatch boundary and emit a failed terminal result with `reason="internal_error"`.
- Modify `matmaster/context/compaction.py`: replace `call_summary_llm()` with `call_summary_llm_response()` and `validate_summary_response()`.
- Modify `matmaster/core/agent_compaction.py`: accumulate compaction summary usage and emit usage-bearing complete compaction events.
- Modify `matmaster/types/events.py`: add optional usage fields to `CompactionEvent` using `None` as the absent value.
- Modify `matmaster/integration/event_payloads.py`: project compaction `turn_usage` and `total_usage` into public content.
- Modify `matmaster/providers/openai_provider.py`: project cache write and reasoning fields into scalar usage when provider returns them.
- Modify `matmaster/providers/bedrock_provider.py`: project Bedrock cache and reasoning fields into scalar usage when provider returns them.
- Modify `src/services/agent_run_service.py`: make Feishu usage summary use aggregate scalar fields only.
- Modify `docs/superpowers/specs/2026-05-08-token-usage-events-design.md`: update the old root-only usage wording to the new aggregate semantics.
- Modify tests listed in each task.

## Constraints

- Use `uv run pytest` for verification commands, not system Python.
- Do not add new dataclasses, Pydantic models, DB tables, or event types except moving the existing `DrainResult` dataclass to `matmaster/types/stream_drain.py`.
- Do not keep compatibility branches for old `SpawnFn -> str` or `call_summary_llm() -> str`.
- Do not put subagent or compaction usage into `state.turn_usage`.
- Do not expand `usage_vendor_by_turn`; it remains root accepted turns only.
- Do not aggregate from forwarded child events, DB persistence, or `CompactionEvent` replay.
- Event usage dicts must be snapshots created with `dict(...)`.
- `DrainResult` is the canonical run-stream drain / subagent boundary result after the move to `matmaster/types/stream_drain.py`; `KernelResult` remains an existing internal/legacy terminal summary type and is not returned from `SpawnFn`.
- Runtime compaction validation failures count returned usage before fallback. Preflight validation failures still abort through the existing exception path in this plan and do not produce a public terminal usage carrier.

---

### Task 1: Move DrainResult To Types And Migrate AgentTool Spawn Contract

**Files:**
- Create: `matmaster/types/stream_drain.py`
- Modify: `matmaster/core/stream_drain.py`
- Modify: `matmaster/core/subagent_orchestrator.py`
- Modify: `matmaster/tools/builtin/agent_tool.py`
- Modify: `matmaster/devshell/runner.py`
- Modify: `evaluation/skill_trigger/runner.py`
- Modify: `tests/matmaster/tools/builtin/test_agent_tool.py`
- Modify: `tests/matmaster/core/test_hook_wiring.py`
- Modify: `tests/matmaster/devshell/test_runner.py`
- Modify: `tests/matmaster/devshell/test_repl.py`

- [ ] **Step 1: Write failing AgentTool tests for DrainResult mapping**

Add these imports to `tests/matmaster/tools/builtin/test_agent_tool.py`:

```python
from matmaster.types.stream_drain import DrainResult
```

Add these tests after `test_execute_returns_tool_result_payload`:

```python
    def test_execute_maps_completed_drain_result_to_tool_result_payload(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return DrainResult(
                status="completed",
                reason="natural",
                final_content="child answer",
                num_turns=2,
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "cache_read_tokens": 40,
                },
                messages=[],
            )

        tool = AgentTool(spawn_fn=fake_spawn, available_exps=[_meta()])
        result = asyncio.run(
            tool.execute(
                {
                    "exp_name": "explore",
                    "task_summary": "trace parser flow",
                    "prompt": "Inspect the parser stack and summarize the path.",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.content == "child answer"
        assert result.payload["exp_name"] == "explore"
        assert result.payload["task_summary"] == "trace parser flow"
        assert result.payload["prompt"] == (
            "Inspect the parser stack and summarize the path."
        )
        assert result.payload["subagent_usage"] == {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cache_read_tokens": 40,
        }
        assert result.payload["subagent_status"] == "completed"
        assert result.payload["subagent_reason"] == "natural"
        assert result.payload["subagent_num_turns"] == 2

    def test_execute_maps_noncompleted_drain_result_to_status_content(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return DrainResult(
                status="cancelled",
                reason="user_stop",
                final_content=None,
                num_turns=1,
                usage={"prompt_tokens": 10, "total_tokens": 10},
                messages=[],
            )

        tool = AgentTool(spawn_fn=fake_spawn, available_exps=[_meta()])
        result = asyncio.run(
            tool.execute(
                {
                    "exp_name": "explore",
                    "task_summary": "trace parser flow",
                    "prompt": "Inspect the parser stack.",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.content == (
            "SubAgent finished with status=cancelled, reason=user_stop"
        )
        assert result.payload["subagent_usage"] == {
            "prompt_tokens": 10,
            "total_tokens": 10,
        }
        assert result.payload["subagent_status"] == "cancelled"
        assert result.payload["subagent_reason"] == "user_stop"
        assert result.payload["subagent_num_turns"] == 1
```

- [ ] **Step 2: Run the AgentTool tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_agent_tool.py -q
```

Expected: FAIL. The new `from matmaster.types.stream_drain import DrainResult` import errors at collection time because that module does not exist yet, and the `SpawnFn` contract still returns `str`.

- [ ] **Step 3: Move DrainResult to the types layer**

Create `matmaster/types/stream_drain.py`:

```python
"""Shared run-stream drain result type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from matmaster.types.events import FinishDetail


@dataclass
class DrainResult:
    """Structured terminal result from draining a run_stream() to completion."""

    status: str
    reason: str
    final_content: str | None
    num_turns: int
    usage: dict[str, int]
    messages: list[Any]
    usage_vendor_by_turn: tuple[dict[str, Any], ...] = ()
    finish_detail: FinishDetail | None = None
    events: list[Any] = field(default_factory=list)
```

Modify the imports at the top of `matmaster/core/stream_drain.py`:

```python
import inspect
from collections.abc import AsyncIterator, Callable
from typing import Any

from matmaster.types.stream_drain import DrainResult
```

Delete the in-file `DrainResult` dataclass and the now-unused imports:

```python
from dataclasses import dataclass, field
from matmaster.types.events import FinishDetail
```

Keep the `drain_run_stream()` body unchanged except that it now constructs `DrainResult` from the imported type.

Then repoint every remaining `DrainResult` importer off the deleted core symbol — the move is repo-wide, not just the tool layer:

- `matmaster/devshell/runner.py`: the `TYPE_CHECKING` import and the in-function import. The in-function line imports both symbols, so split it — `DrainResult` from `matmaster.types.stream_drain`, `drain_run_stream` from `matmaster.core.stream_drain`.
- `evaluation/skill_trigger/runner.py`: the module-level `from matmaster.core.stream_drain import DrainResult` (its separate `drain_run_stream` import is left untouched).
- `tests/matmaster/devshell/test_runner.py` and `tests/matmaster/devshell/test_repl.py`: their `DrainResult` imports.

Do not touch pure `drain_run_stream` importers (`matmaster/core/subagent_orchestrator.py`, `evaluation/core/mat_runner.py`); that symbol stays in `matmaster.core.stream_drain`.

- [ ] **Step 4: Migrate AgentTool to return ToolResult from DrainResult**

Modify `matmaster/tools/builtin/agent_tool.py` imports:

```python
from matmaster.types.stream_drain import DrainResult
```

Change the `SpawnFn` alias:

```python
SpawnFn = Callable[[str, str, CancellationToken | None], Awaitable[DrainResult]]
```

Replace the `result = await self._spawn_fn(...)` block in `AgentTool.execute()` with:

```python
        drain = await self._spawn_fn(
            normalized["exp_name"],
            normalized["prompt"],
            self._cancel_token_for_exec(),
        )
        content = (
            drain.final_content
            if drain.status == "completed" and drain.final_content
            else f"SubAgent finished with status={drain.status}, reason={drain.reason}"
        )
        return ToolResult(
            status="success",
            content=content,
            payload={
                "exp_name": normalized["exp_name"],
                "task_summary": normalized["task_summary"],
                "prompt": normalized["prompt"],
                "subagent_usage": dict(drain.usage or {}),
                "subagent_status": drain.status,
                "subagent_reason": drain.reason,
                "subagent_num_turns": drain.num_turns,
            },
        )
```

- [ ] **Step 5: Migrate SubagentOrchestrator to return DrainResult**

Modify `matmaster/core/subagent_orchestrator.py` imports:

```python
from matmaster.types.stream_drain import DrainResult
```

Change `make_spawn_fn()`:

```python
    def make_spawn_fn(self) -> Callable[..., Awaitable[DrainResult]]:
        """Return the ``spawn_fn`` closure AgentTool forwards LLM calls to."""

        async def spawn_fn(
            exp_name: str,
            task: str,
            cancel_token: CancellationToken | None = None,
        ) -> DrainResult:
            return await self.spawn(exp_name, task, cancel_token=cancel_token)

        return spawn_fn
```

Change `spawn()`:

```python
    async def spawn(
        self,
        exp_name: str,
        task: str,
        *,
        cancel_token: CancellationToken | None = None,
    ) -> DrainResult:
        """Run one child agent and return its drained terminal result."""
        from matmaster.core.stream_drain import drain_run_stream

        child_source = f"{self._source_prefix}:{exp_name}"
        spawn_id = uuid.uuid4().hex[:16]

        async def _forward_child_event(event: Any) -> None:
            sink = self._child_event_sink
            if sink is None:
                return
            try:
                forwarded = event.model_copy(
                    update={"source": child_source, "spawn_id": spawn_id}
                )
                result = sink(forwarded)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.warning(
                    "subagent event forwarding failed type=%s spawn_id=%s",
                    getattr(event, "type", "?"),
                    spawn_id,
                    exc_info=True,
                )

        await self._emit(HookEvent.SUBAGENT_START, spawn_id, exp_name, task)
        try:
            return await drain_run_stream(
                self._child_run_factory(
                    exp_name, task, cancel_token=cancel_token, spawn_id=spawn_id
                ),
                on_event=_forward_child_event,
            )
        finally:
            await self._emit(HookEvent.SUBAGENT_STOP, spawn_id, exp_name, task)
```

- [ ] **Step 6: Update SubagentOrchestrator tests to assert DrainResult**

In `tests/matmaster/core/test_hook_wiring.py`, update the local fake drain objects in the `SubagentOrchestrator` tests so they include the complete `DrainResult` shape:

```python
            return SimpleNamespace(
                status="completed",
                final_content="child done",
                reason="natural",
                usage={"prompt_tokens": 3},
                num_turns=1,
                messages=[],
            )
```

For every assertion currently shaped like:

```python
assert result == "child done"
```

replace it with:

```python
assert result.final_content == "child done"
assert result.usage == {"prompt_tokens": 3}
```

For the integration-style child Exp test near the bottom of the same file, keep the real `drain_run_stream()` call and replace:

```python
assert result == "child done"
```

with:

```python
assert result.final_content == "child done"
assert result.status == "completed"
```

- [ ] **Step 7: Verify imports and behavior**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_agent_tool.py \
  tests/matmaster/core/test_hook_wiring.py::TestSubagentHookWiring \
  tests/matmaster/devshell/test_runner.py \
  tests/matmaster/devshell/test_repl.py -q
uv run python -c "import evaluation.skill_trigger.runner"
rg -n "Awaitable\\[str\\]|from matmaster.core.stream_drain import DrainResult|class DrainResult" matmaster evaluation tests
```

Expected: pytest PASS and the `evaluation.skill_trigger.runner` import succeeds. `rg` should show no `Awaitable[str]` spawn contract, **no `from matmaster.core.stream_drain import DrainResult` anywhere** (every `DrainResult` importer now points at `matmaster.types.stream_drain`), and exactly one `class DrainResult`, in `matmaster/types/stream_drain.py`.

- [ ] **Step 8: Commit**

```bash
git add matmaster/types/stream_drain.py matmaster/core/stream_drain.py \
  matmaster/core/subagent_orchestrator.py matmaster/tools/builtin/agent_tool.py \
  matmaster/devshell/runner.py evaluation/skill_trigger/runner.py \
  tests/matmaster/tools/builtin/test_agent_tool.py \
  tests/matmaster/core/test_hook_wiring.py \
  tests/matmaster/devshell/test_runner.py \
  tests/matmaster/devshell/test_repl.py
git commit -m "refactor: return drained subagent results"
```

---

### Task 2: Add Tool Usage Delta Extraction And Snapshot Usage Events

**Files:**
- Create: `tests/matmaster/core/test_agent_tool_dispatch.py`
- Modify: `matmaster/core/agent_tool_dispatch.py`
- Modify: `matmaster/core/agent.py`
- Test: `tests/matmaster/core/test_agent_tool_dispatch.py`
- Test: `tests/matmaster/core/test_agent_kernel_usage_events.py`

- [ ] **Step 1: Write failing tests for extraction, accumulation, and snapshots**

Create `tests/matmaster/core/test_agent_tool_dispatch.py`:

```python
from __future__ import annotations

import pytest

from matmaster.core.agent_tool_dispatch import (
    InvalidToolUsageDelta,
    extract_tool_usage_delta,
)
from matmaster.core.agent_tool_dispatch import dispatch_tool_calls
from matmaster.core.kernel_items import _KernelState
from matmaster.tools.tool_result import ToolResult
from matmaster.types.events import ToolResultEvent
from matmaster.types.messages import SystemMessage, ToolCallData


def test_extract_tool_usage_delta_ignores_non_agent_tools() -> None:
    result = ToolResult(
        status="success",
        content="ok",
        payload={"subagent_usage": {"prompt_tokens": 10}},
    )

    assert extract_tool_usage_delta("Read", result) == {}


def test_extract_tool_usage_delta_missing_subagent_usage_returns_empty() -> None:
    result = ToolResult(status="success", content="ok", payload={})

    assert extract_tool_usage_delta("Agent", result) == {}


@pytest.mark.parametrize(
    "usage",
    [
        "not-a-dict",
        {"prompt_tokens": "10"},
        {"prompt_tokens": True},
        {"prompt_tokens": -1},
    ],
)
def test_extract_tool_usage_delta_rejects_malformed_agent_usage(usage) -> None:
    result = ToolResult(
        status="success",
        content="ok",
        payload={"subagent_usage": usage},
    )

    with pytest.raises(InvalidToolUsageDelta):
        extract_tool_usage_delta("Agent", result)


def test_extract_tool_usage_delta_allows_cancelled_agent_without_usage() -> None:
    result = ToolResult(status="cancelled", content="Run cancelled.", payload={})

    assert extract_tool_usage_delta("Agent", result) == {}


class StaticRunner:
    def __init__(self, results):
        self.results = results

    async def execute_batch(self, tool_calls, ctx, *, on_result=None):
        del ctx, on_result
        return list(zip(tool_calls, self.results))


@pytest.mark.asyncio
async def test_dispatch_tool_calls_accumulates_agent_usage_before_event() -> None:
    state = _KernelState(
        messages=[SystemMessage(content="sys")],
        turn=1,
        turn_usage={"prompt_tokens": 5},
        total_usage={"prompt_tokens": 5},
    )
    tool_call = ToolCallData(
        id="call-agent",
        name="Agent",
        arguments={"prompt": "child task"},
    )
    runner = StaticRunner(
        [
            ToolResult(
                status="success",
                content="child answer",
                payload={
                    "subagent_usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    }
                },
            )
        ]
    )

    items = [
        item
        async for item in dispatch_tool_calls(
            tool_calls=[tool_call],
            tool_runner=runner,
            max_turns=10,
            state=state,
            cancel_token=None,
        )
    ]

    event = items[0].event
    assert isinstance(event, ToolResultEvent)
    assert event.turn_usage == {"prompt_tokens": 5}
    assert event.total_usage == {
        "prompt_tokens": 15,
        "completion_tokens": 2,
        "total_tokens": 12,
    }
    assert state.total_usage == event.total_usage


@pytest.mark.asyncio
async def test_dispatch_tool_calls_usage_fields_are_snapshots() -> None:
    state = _KernelState(
        messages=[SystemMessage(content="sys")],
        turn=1,
        turn_usage={"prompt_tokens": 5},
        total_usage={"prompt_tokens": 5},
    )
    tool_calls = [
        ToolCallData(id="call-1", name="Agent", arguments={}),
        ToolCallData(id="call-2", name="Agent", arguments={}),
    ]
    runner = StaticRunner(
        [
            ToolResult(
                status="success",
                content="one",
                payload={"subagent_usage": {"prompt_tokens": 10}},
            ),
            ToolResult(
                status="success",
                content="two",
                payload={"subagent_usage": {"prompt_tokens": 20}},
            ),
        ]
    )

    events = [
        item.event
        async for item in dispatch_tool_calls(
            tool_calls=tool_calls,
            tool_runner=runner,
            max_turns=10,
            state=state,
            cancel_token=None,
        )
        if isinstance(item.event, ToolResultEvent)
    ]

    assert events[0].total_usage == {"prompt_tokens": 15}
    assert events[1].total_usage == {"prompt_tokens": 35}
    state.total_usage["prompt_tokens"] = 999
    state.turn_usage["prompt_tokens"] = 888
    assert events[0].total_usage == {"prompt_tokens": 15}
    assert events[0].turn_usage == {"prompt_tokens": 5}
```

- [ ] **Step 2: Run the dispatch tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_tool_dispatch.py -q
```

Expected: FAIL because `InvalidToolUsageDelta` and `extract_tool_usage_delta()` do not exist, and `dispatch_tool_calls()` does not accumulate subagent usage.

- [ ] **Step 3: Implement extraction helper and accumulation**

Modify `matmaster/core/agent_tool_dispatch.py`.

Add imports:

```python
import logging
```

Add module logger, constant, and exception after the `TYPE_CHECKING` block:

```python
logger = logging.getLogger(__name__)

AGENT_TOOL_NAME = "Agent"


class InvalidToolUsageDelta(RuntimeError):
    """Raised when an internal tool usage payload violates scalar usage shape."""
```

Add this helper after `accumulate_usage()`:

```python
def extract_tool_usage_delta(tool_name: str, tool_result: Any) -> dict[str, int]:
    """Extract a validated usage delta from a structured tool result."""
    if tool_name != AGENT_TOOL_NAME:
        return {}

    payload = getattr(tool_result, "payload", {}) or {}
    if "subagent_usage" not in payload:
        return {}

    usage = payload["subagent_usage"]
    if not isinstance(usage, dict):
        logger.warning(
            "malformed Agent subagent_usage: expected dict, got %s",
            type(usage).__name__,
        )
        raise InvalidToolUsageDelta("Agent subagent_usage must be a dict")

    out: dict[str, int] = {}
    for key, value in usage.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            logger.warning(
                "malformed Agent subagent_usage field=%r value_type=%s",
                key,
                type(value).__name__,
            )
            raise InvalidToolUsageDelta(
                "Agent subagent_usage values must be non-negative ints"
            )
        out[key] = value
    return out
```

In `dispatch_tool_calls()`, insert usage accumulation after appending `ToolMessage` and before constructing `ToolResultEvent`:

```python
        usage_delta = extract_tool_usage_delta(tc.name, tool_result)
        if usage_delta:
            accumulate_usage(state.total_usage, usage_delta)
```

Do not catch `InvalidToolUsageDelta` inside `dispatch_tool_calls()`. A malformed `subagent_usage` on a successful `Agent` result is an internal contract violation, so the helper raises and the kernel dispatch boundary handles it. The boundary handling is added in the next step so the run produces a failed `RunResultEvent` instead of bubbling to the service's generic `ErrorEvent + StreamClosedEvent` path, which would lose the terminal usage carrier.

Change `ToolResultEvent` construction to snapshot dicts:

```python
                turn_usage=dict(state.turn_usage),
                total_usage=dict(state.total_usage),
```

- [ ] **Step 4: Snapshot usage dicts and terminalize malformed usage in AgentKernel**

Modify `matmaster/core/agent.py`.

Add `InvalidToolUsageDelta` to the existing `agent_tool_dispatch` import:

```python
from matmaster.core.agent_tool_dispatch import (
    InvalidToolUsageDelta,
    accumulate_usage,
    dispatch_tool_calls,
    validate_tool_call_ids,
)
```

Add the failed status mapping:

```python
_TERMINAL_REASON_TO_STATUS: dict[str, str] = {
    "cancelled": "cancelled",
    "interrupted": "completed",
    "internal_error": "failed",
    "invalid_finish": "failed",
    "natural": "completed",
    "max_turns": "completed",
}
```

For `ResponseEvent`, change:

```python
                        turn_usage=state.turn_usage,
                        total_usage=state.total_usage,
```

to:

```python
                        turn_usage=dict(state.turn_usage),
                        total_usage=dict(state.total_usage),
```

Wrap the `dispatch_tool_calls()` loop at the bottom of `_run_items()`:

```python
            try:
                async for item in dispatch_tool_calls(
                    tool_calls=response.tool_calls,
                    tool_runner=kernel_resources.tool_runner,
                    max_turns=kernel_spec.max_turns,
                    state=state,
                    cancel_token=cancel_token,
                ):
                    yield item
            except InvalidToolUsageDelta:
                logger.exception("malformed tool usage delta; ending run as failed")
                yield self._terminal(state, "internal_error")
                return
```

Do not add a new event type. The failed `RunResultEvent` is enough for service/fanout to carry the current `state.total_usage` snapshot and close the stream with failure semantics.

For `AssistantStateEvent`, change:

```python
                        turn_usage=state.turn_usage,
                        total_usage=state.total_usage,
```

to:

```python
                        turn_usage=dict(state.turn_usage),
                        total_usage=dict(state.total_usage),
```

- [ ] **Step 5: Add a kernel-level regression for subagent usage aggregation**

Append to `tests/matmaster/core/test_agent_kernel_usage_events.py`:

```python
@pytest.mark.asyncio
async def test_agent_tool_usage_delta_reaches_parent_run_result() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.events import ToolResultEvent
    from matmaster.types.messages import StreamChunk, ToolCallData

    from .agent_kernel_test_helpers import ToolCallingProvider, make_kernel_runtime

    class AgentUsageRunner:
        async def execute_batch(self, tool_calls, ctx, *, on_result=None):
            del ctx, on_result
            return [
                (
                    tool_calls[0],
                    ToolResult(
                        status="success",
                        content="child answer",
                        payload={
                            "subagent_usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                                "total_tokens": 12,
                            }
                        },
                    ),
                )
            ]

    class UsageProvider(ToolCallingProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            self._call_count += 1
            if self._call_count == 1:
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": "call-agent",
                            "name": "Agent",
                            "arguments": '{"prompt": "child"}',
                        }
                    ],
                )
                yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})
                return
            yield StreamChunk(content="done", finish_reason="stop")
            yield StreamChunk(
                usage={
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                }
            )

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(
            provider=UsageProvider(
                [ToolCallData(id="unused", name="Agent", arguments={})],
                max_tool_turns=1,
            ),
            tool_runner=AgentUsageRunner(),
        ),
        "test task",
    ):
        events.append(event)

    tool_event = next(e for e in events if isinstance(e, ToolResultEvent))
    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert tool_event.turn_usage == {"prompt_tokens": 5}
    assert tool_event.total_usage == {
        "prompt_tokens": 15,
        "completion_tokens": 2,
        "total_tokens": 12,
    }
    assert run_result.usage == {
        "prompt_tokens": 22,
        "completion_tokens": 5,
        "total_tokens": 22,
    }
    assert run_result.usage_vendor_by_turn == [{}, {}]


@pytest.mark.asyncio
async def test_malformed_agent_subagent_usage_aborts_run_via_error_path() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.events import RunResultEvent
    from matmaster.types.messages import StreamChunk, ToolCallData

    from .agent_kernel_test_helpers import ToolCallingProvider, make_kernel_runtime

    class MalformedUsageRunner:
        async def execute_batch(self, tool_calls, ctx, *, on_result=None):
            del ctx, on_result
            return [
                (
                    tool_calls[0],
                    ToolResult(
                        status="success",
                        content="child answer",
                        payload={"subagent_usage": {"prompt_tokens": -1}},
                    ),
                )
            ]

    class UsageProvider(ToolCallingProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "call-agent",
                        "name": "Agent",
                        "arguments": '{"prompt": "child"}',
                    }
                ],
            )
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(
            provider=UsageProvider(
                [ToolCallData(id="unused", name="Agent", arguments={})],
                max_tool_turns=1,
            ),
            tool_runner=MalformedUsageRunner(),
        ),
        "test task",
    ):
        events.append(event)

    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert run_result.status == "failed"
    assert run_result.reason == "internal_error"
    assert run_result.usage == {"prompt_tokens": 5}
```

- [ ] **Step 6: Update the root-only usage test name and assertion**

In `tests/matmaster/core/test_agent_kernel_usage_events.py`, rename:

```python
async def test_completed_run_result_usage_matches_distinct_response_turn_usage()
```

to:

```python
async def test_root_only_run_result_usage_matches_distinct_response_turn_usage()
```

Keep its body unchanged. The new subagent test proves the aggregate case where response turns are only a subset of final run usage.

- [ ] **Step 7: Verify dispatch and kernel usage behavior**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_tool_dispatch.py \
  tests/matmaster/core/test_agent_kernel_usage_events.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add matmaster/core/agent_tool_dispatch.py matmaster/core/agent.py \
  tests/matmaster/core/test_agent_tool_dispatch.py \
  tests/matmaster/core/test_agent_kernel_usage_events.py
git commit -m "feat: aggregate agent tool usage deltas"
```

---

### Task 3: Return LLMResponse From Summary Calls And Validate Separately

**Files:**
- Modify: `matmaster/context/compaction.py`
- Modify: `tests/matmaster/context/test_summary_caller.py`
- Modify: `tests/matmaster/core/test_agent_compaction.py`

- [ ] **Step 1: Update summary caller tests to target LLMResponse helper**

In `tests/matmaster/context/test_summary_caller.py`, replace the import:

```python
from matmaster.context.compaction import call_summary_llm
```

with:

```python
from matmaster.context.compaction import (
    call_summary_llm_response,
    validate_summary_response,
)
```

For tests that currently call `call_summary_llm(...)` and assert the returned summary text, change the call to:

```python
response = await call_summary_llm_response(
    llm_provider=provider,
    system_prompt="sys",
    full_messages=messages,
    phase="runtime",
    turn_input=None,
    tool_definitions=None,
    context_limit=20_000,
    reserved_summary_tokens=1_000,
)
assert validate_summary_response(response) == "summary text"
```

Replace the empty-response test with:

```python
async def test_validate_summary_response_raises_on_empty_response() -> None:
    from matmaster.types.messages import LLMResponse

    with pytest.raises(ValueError, match="Summary LLM returned empty content"):
        validate_summary_response(LLMResponse(content="   ", finish_reason="stop"))
```

Replace the tool-call rejection test with:

```python
async def test_validate_summary_response_rejects_tool_calls() -> None:
    from matmaster.types.messages import LLMResponse, ToolCallData

    response = LLMResponse(
        content="summary",
        finish_reason="tool_calls",
        tool_calls=[ToolCallData(id="tc-1", name="Read", arguments={})],
    )

    with pytest.raises(ValueError, match="Summary LLM attempted tool calls"):
        validate_summary_response(response)
```

Add one usage-preservation test:

```python
async def test_call_summary_llm_response_preserves_usage() -> None:
    provider = RecordingProvider(
        content="summary text",
        usage={"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45},
    )

    response = await call_summary_llm_response(
        llm_provider=provider,
        system_prompt="sys",
        full_messages=[SystemMessage(content="sys"), UserMessage(content="old")],
        phase="runtime",
        turn_input=None,
        tool_definitions=None,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert response.usage == {
        "prompt_tokens": 40,
        "completion_tokens": 5,
        "total_tokens": 45,
    }
    assert validate_summary_response(response) == "summary text"
```

Modify `RecordingProvider` in the same file so it stores and returns usage:

```python
class RecordingProvider:
    def __init__(
        self,
        content: str | None = "summary",
        *,
        tool_calls: list[ToolCallData] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.usage = usage or {}
        self.calls: list[dict[str, object]] = []

    async def chat(self, messages, tools=None, *, tool_choice=None):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        return LLMResponse(
            content=self.content,
            finish_reason="stop",
            tool_calls=self.tool_calls,
            usage=dict(self.usage),
        )
```

- [ ] **Step 2: Update monkeypatch names in compaction runner tests**

In `tests/matmaster/core/test_agent_compaction.py`, change `fake_call_summary_llm()` to return `LLMResponse`:

```python
    async def fake_call_summary_llm_response(**kwargs):
        captured.update(kwargs)
        from matmaster.types.messages import LLMResponse

        return LLMResponse(content="summary text", finish_reason="stop")
```

Change the monkeypatch target:

```python
    monkeypatch.setattr(
        "matmaster.context.compaction.call_summary_llm_response",
        fake_call_summary_llm_response,
    )
```

- [ ] **Step 3: Run the summary tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/context/test_summary_caller.py \
  tests/matmaster/core/test_agent_compaction.py::test_compaction_plan_runner_passes_configured_summary_safety_margin -q
```

Expected: FAIL because `call_summary_llm_response()` and `validate_summary_response()` are not implemented and `call_summary_llm()` still returns `str`.

- [ ] **Step 4: Implement call_summary_llm_response and validate_summary_response**

In `matmaster/context/compaction.py`, rename `call_summary_llm()` to `call_summary_llm_response()` and change its return type from `str` to `LLMResponse`:

```python
async def call_summary_llm_response(
    *,
    llm_provider: LLMProvider,
    system_prompt: str,
    full_messages: list[Message],
    phase: Literal["preflight", "runtime"],
    turn_input: TurnInput | None,
    tool_definitions: list[dict] | None,
    context_limit: int,
    reserved_summary_tokens: int,
    safety_margin_tokens: int = 5_000,
) -> LLMResponse:
    """Call the main LLM to summarize conversation history."""
    if not full_messages:
        raise ValueError("Cannot summarize empty message list")
    if not isinstance(full_messages[0], SystemMessage):
        raise TypeError(
            f"full_messages[0] must be SystemMessage, got {type(full_messages[0])}"
        )
    if full_messages[0].content != system_prompt:
        logger.debug("Summary call system prompt differs from supplied system_prompt")

    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase=phase,
        turn_input=turn_input,
        compact_request=compact_request,
        context_limit=context_limit,
        reserved_summary_tokens=reserved_summary_tokens,
        safety_margin_tokens=safety_margin_tokens,
    )
    summary_messages = [*prep.messages, compact_request]
    api_messages = normalize_and_validate_openai_messages(
        canonicalize_messages_for_provider(summary_messages)
    )
    return await llm_provider.chat(
        api_messages,
        tools=tool_definitions,
        tool_choice="none",
    )
```

Add below it:

```python
def validate_summary_response(response: LLMResponse) -> str:
    """Validate a summary LLM response and return stripped summary content."""
    if response.tool_calls:
        raise ValueError("Summary LLM attempted tool calls")
    if not response.content or not response.content.strip():
        raise ValueError("Summary LLM returned empty content")
    return response.content
```

Ensure `LLMResponse` is imported at the top of the file from `matmaster.types.messages`.

Delete the old `call_summary_llm()` symbol. Do not add a compatibility wrapper.

- [ ] **Step 5: Update all call sites and imports**

Run this search:

```bash
rg -n "call_summary_llm" matmaster tests
```

The only production call site is `run_compaction_plan()` in `matmaster/core/agent_compaction.py`. Migrate it **minimally** here so the existing `test_agent_compaction.py` suite stays green — call the new helper and validate, with no usage accumulation yet:

```python
        response = await call_summary_llm_response(
            llm_provider=kernel_resources.llm_provider,
            system_prompt=kernel_spec.system_prompt,
            full_messages=state.messages,
            phase=plan.phase,
            turn_input=turn_input,
            tool_definitions=tool_definitions,
            context_limit=kernel_spec.compaction.context_limit,
            reserved_summary_tokens=kernel_spec.compaction.reserved_summary_tokens,
            safety_margin_tokens=(kernel_spec.compaction.summary_safety_margin_tokens),
        )
        summary = validate_summary_response(response)
```

Task 4 (Step 5) replaces this exact block with the usage-accumulating version, so keep it minimal here — do not add `accumulate_usage`, a `summary_usage` variable, or `CompactionEvent` usage fields yet. Update every other hit to use `call_summary_llm_response` / `validate_summary_response`. After this step there should be no remaining `call_summary_llm` import or function definition.

- [ ] **Step 6: Verify summary helper migration**

Run:

```bash
uv run pytest tests/matmaster/context/test_summary_caller.py \
  tests/matmaster/core/test_agent_compaction.py -q
rg -n "call_summary_llm\\b" matmaster tests
```

Expected: pytest PASS and `rg` returns no matches.

- [ ] **Step 7: Commit**

```bash
git add matmaster/context/compaction.py tests/matmaster/context/test_summary_caller.py \
  tests/matmaster/core/test_agent_compaction.py
git commit -m "refactor: return summary llm responses"
```

---

### Task 4: Aggregate Compaction Summary Usage And Publish It

**Files:**
- Modify: `matmaster/types/events.py`
- Modify: `matmaster/core/agent_compaction.py`
- Modify: `matmaster/integration/event_payloads.py`
- Modify: `tests/matmaster/types/test_events.py`
- Modify: `tests/matmaster/core/test_agent_compaction.py`
- Modify: `tests/matmaster/integration/test_event_payloads.py`

- [ ] **Step 1: Write failing event model and public payload tests**

Append to `tests/matmaster/types/test_events.py`:

```python
def test_compaction_event_usage_fields_default_to_none() -> None:
    evt = CompactionEvent(
        source="context_compactor",
        compaction_id="root:1",
        status="complete",
        phase="runtime",
    )

    assert evt.turn_usage is None
    assert evt.total_usage is None


def test_compaction_event_accepts_usage_fields() -> None:
    evt = CompactionEvent(
        source="context_compactor",
        compaction_id="root:1",
        status="complete",
        phase="runtime",
        turn_usage={"prompt_tokens": 40},
        total_usage={"prompt_tokens": 55},
    )

    assert evt.turn_usage == {"prompt_tokens": 40}
    assert evt.total_usage == {"prompt_tokens": 55}
```

Add this test to `tests/matmaster/integration/test_event_payloads.py` near the compaction tests:

```python
def test_compaction_public_content_includes_usage_fields() -> None:
    content = _public_content_for_event(
        "compaction",
        {
            "compaction_id": "root:1",
            "status": "complete",
            "phase": "runtime",
            "turn_usage": {"prompt_tokens": 40},
            "total_usage": {"prompt_tokens": 55},
        },
    )

    assert content["turn_usage"] == {"prompt_tokens": 40}
    assert content["total_usage"] == {"prompt_tokens": 55}

    running = _public_content_for_event(
        "compaction",
        {
            "compaction_id": "root:1",
            "status": "running",
            "phase": "runtime",
            "turn_usage": None,
            "total_usage": None,
        },
    )
    assert "turn_usage" not in running
    assert "total_usage" not in running
```

- [ ] **Step 2: Write failing compaction aggregation tests**

Modify `Provider` in `tests/matmaster/core/test_agent_compaction.py` to accept usage:

```python
class Provider:
    def __init__(
        self,
        content: str | Exception = "summary",
        *,
        tool_calls: list[ToolCallData] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.usage = usage or {}
        self.calls = []
```

Change its `LLMResponse` return:

```python
        return LLMResponse(
            content=self.content,
            finish_reason="stop",
            tool_calls=self.tool_calls,
            usage=dict(self.usage),
        )
```

Add these tests:

```python
@pytest.mark.asyncio
async def test_compaction_plan_runner_accumulates_summary_usage_on_success() -> None:
    provider = Provider(
        "summary text",
        usage={"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45},
    )
    compactor = Compactor()
    state = _KernelState(
        messages=[SystemMessage(content="sys"), UserMessage(content="old")],
        total_usage={"prompt_tokens": 10},
    )

    events = [
        item.event
        async for item in run_compaction_plan(
            kernel_spec=_kernel_spec(),
            kernel_resources=_kernel_resources(provider, compactor),
            state=state,
            plan=_plan("runtime"),
            checkpoint_sink=None,
            tool_definitions=None,
        )
    ]

    complete = events[-1]
    assert isinstance(complete, CompactionEvent)
    assert complete.turn_usage == {
        "prompt_tokens": 40,
        "completion_tokens": 5,
        "total_tokens": 45,
    }
    assert complete.total_usage == {
        "prompt_tokens": 50,
        "completion_tokens": 5,
        "total_tokens": 45,
    }
    assert state.turn_usage == {}
    assert state.total_usage == complete.total_usage


@pytest.mark.asyncio
async def test_compaction_plan_runner_accumulates_usage_before_validation_fallback() -> None:
    provider = Provider(
        "summary text",
        tool_calls=[ToolCallData(id="tc-1", name="tool", arguments={})],
        usage={"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45},
    )
    compactor = Compactor()
    state = _KernelState(
        messages=[SystemMessage(content="sys"), UserMessage(content="old")],
        total_usage={"prompt_tokens": 10},
    )

    events = [
        item.event
        async for item in run_compaction_plan(
            kernel_spec=_kernel_spec(),
            kernel_resources=_kernel_resources(provider, compactor),
            state=state,
            plan=_plan("runtime"),
            checkpoint_sink=None,
            tool_definitions=[{"type": "function", "function": {"name": "tool"}}],
        )
    ]

    complete = events[-1]
    assert compactor.summary_calls == []
    assert compactor.fallback_calls[0][1] == "Summary LLM attempted tool calls"
    assert complete.strategy == "sliding_window"
    assert complete.turn_usage == {
        "prompt_tokens": 40,
        "completion_tokens": 5,
        "total_tokens": 45,
    }
    assert complete.total_usage == {
        "prompt_tokens": 50,
        "completion_tokens": 5,
        "total_tokens": 45,
    }
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/types/test_events.py::test_compaction_event_usage_fields_default_to_none \
  tests/matmaster/types/test_events.py::test_compaction_event_accepts_usage_fields \
  tests/matmaster/integration/test_event_payloads.py::test_compaction_public_content_includes_usage_fields \
  tests/matmaster/core/test_agent_compaction.py -q
```

Expected: FAIL because `CompactionEvent` has no usage fields and `run_compaction_plan()` does not accumulate summary usage.

- [ ] **Step 4: Add CompactionEvent usage fields**

Modify `matmaster/types/events.py`:

```python
class CompactionEvent(EventBase):
    """Public compaction lifecycle event."""

    type: Literal["compaction"] = "compaction"
    compaction_id: str
    status: Literal["running", "complete", "interrupted"]
    phase: Literal["preflight", "runtime"]
    strategy: Literal["summary", "sliding_window", "tool_truncation"] | None = None
    durability: Literal["durable", "ephemeral"] | None = None
    trigger_tokens: int | None = None
    retained_turns: int | None = None
    checkpoint_written: bool | None = None
    failure_reason: str | None = None
    covered_until_event_id: int | None = None
    turn_usage: dict[str, int] | None = None
    total_usage: dict[str, int] | None = None
```

- [ ] **Step 5: Accumulate compaction summary usage in run_compaction_plan**

Modify `matmaster/core/agent_compaction.py` imports:

```python
from matmaster.core.agent_tool_dispatch import accumulate_usage
```

Replace the summary call block in `run_compaction_plan()` with this shape:

```python
    summary_usage: dict[str, int] = {}
    try:
        from matmaster.context.compaction import (
            call_summary_llm_response,
            validate_summary_response,
        )

        response = await call_summary_llm_response(
            llm_provider=kernel_resources.llm_provider,
            system_prompt=kernel_spec.system_prompt,
            full_messages=state.messages,
            phase=plan.phase,
            turn_input=turn_input,
            tool_definitions=tool_definitions,
            context_limit=kernel_spec.compaction.context_limit,
            reserved_summary_tokens=kernel_spec.compaction.reserved_summary_tokens,
            safety_margin_tokens=(kernel_spec.compaction.summary_safety_margin_tokens),
        )
        summary_usage = dict(response.usage or {})
        if summary_usage:
            accumulate_usage(state.total_usage, summary_usage)
        summary = validate_summary_response(response)
        result = await kernel_resources.compactor.apply_summary(
            plan,
            state.messages,
            summary,
            turn_input=turn_input,
        )
    except Exception as exc:
        if plan.phase == "preflight":
            logger.warning(
                "Preflight compaction summary failed; aborting", exc_info=True
            )
            raise
        logger.warning(
            "Compaction #%d summary failed; falling back",
            plan.compaction_count,
            exc_info=True,
        )
        result = await kernel_resources.compactor.apply_fallback(
            plan,
            state.messages,
            failure_reason=str(exc),
        )
```

In the final `CompactionEvent(status="complete", ...)`, add:

```python
            turn_usage=dict(summary_usage) if summary_usage else None,
            total_usage=dict(state.total_usage) if summary_usage else None,
```

Do not set usage fields on the initial `status="running"` event. If a runtime summary response is returned but fails validation, `summary_usage` has already been accumulated and the fallback `complete` event carries it. If a preflight summary response fails validation, the existing preflight abort path still raises before any public terminal event; this plan deliberately does not introduce a preflight failure terminal carrier.

- [ ] **Step 6: Project compaction usage into public payload**

Modify the key loop in `matmaster/integration/event_payloads.py` for `event_type == 'compaction'`. Keep the existing `is not None` loop for the scalar/flag fields, and project the two usage dicts separately with a **truthy** check so running events, missing usage, and empty usage dicts do not leak into public content:

```python
        for key in (
            'strategy',
            'durability',
            'trigger_tokens',
            'retained_turns',
            'checkpoint_written',
            'failure_reason',
            'covered_until_event_id',
        ):
            if key in payload and payload.get(key) is not None:
                content[key] = payload[key]
        for key in ('turn_usage', 'total_usage'):
            if payload.get(key):
                content[key] = payload[key]
```

The split is deliberate: the scalar/flag fields include legitimate falsy values (`trigger_tokens=0`, `checkpoint_written=False`), so they must stay on `is not None`; the usage dicts must use a truthy check so `None` and empty `{}` are both omitted.

- [ ] **Step 7: Verify compaction aggregation**

Run:

```bash
uv run pytest tests/matmaster/types/test_events.py \
  tests/matmaster/core/test_agent_compaction.py \
  tests/matmaster/integration/test_event_payloads.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add matmaster/types/events.py matmaster/core/agent_compaction.py \
  matmaster/integration/event_payloads.py tests/matmaster/types/test_events.py \
  tests/matmaster/core/test_agent_compaction.py \
  tests/matmaster/integration/test_event_payloads.py
git commit -m "feat: aggregate compaction summary usage"
```

---

### Task 5: Normalize Provider Scalar Usage For Cache And Reasoning

**Files:**
- Modify: `matmaster/providers/openai_provider.py`
- Modify: `matmaster/providers/bedrock_provider.py`
- Modify: `tests/matmaster/providers/test_openai_provider.py`
- Modify: `tests/matmaster/providers/test_openai_provider_prompt_cache.py`
- Modify: `tests/matmaster/providers/test_bedrock_provider.py`

- [ ] **Step 1: Write failing OpenAI scalar projection tests**

In `tests/matmaster/providers/test_openai_provider.py`, update `test_chat_usage_vendor_preserves_anthropic_cache_fields` expected scalar usage to include cache write:

```python
        assert result.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_read_tokens": 45,
            "cache_write_tokens": 123,
        }
```

Add this test near `test_chat_usage`:

```python
    async def test_chat_usage_projects_reasoning_tokens(self) -> None:
        details = MagicMock()
        details.reasoning_tokens = 7
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 15
        usage.completion_tokens_details = details

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            usage=usage,
        )
        provider._client = mock_client
        result = await provider.chat([{"role": "user", "content": "Hi"}])

        assert result.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "reasoning_tokens": 7,
        }
```

In `tests/matmaster/providers/test_openai_provider_prompt_cache.py`, add a streaming projection test:

```python
    async def test_usage_final_chunk_projects_cache_write_and_reasoning(self) -> None:
        details = MagicMock()
        details.reasoning_tokens = 7
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 15
        usage.cache_creation_input_tokens = 3
        usage.completion_tokens_details = details

        usage_only_chunk = MagicMock()
        usage_only_chunk.choices = []
        usage_only_chunk.usage = usage

        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _async_iter(
            [
                _make_stream_chunk(content="answer", finish_reason="stop"),
                usage_only_chunk,
            ]
        )
        provider._client = mock_client
        chunks = [
            chunk
            async for chunk in provider.chat_stream([{"role": "user", "content": "Hi"}])
        ]

        assert chunks[1].usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_write_tokens": 3,
            "reasoning_tokens": 7,
        }
```

- [ ] **Step 2: Write failing Bedrock scalar projection tests**

Append to `tests/matmaster/providers/test_bedrock_provider.py`:

```python
async def test_bedrock_chat_projects_cache_and_reasoning_usage() -> None:
    provider = BedrockProvider(model_id="m1", region="us-west-2")

    def fake_converse(**kwargs):
        del kwargs
        return {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
                "cacheReadInputTokens": 4,
                "cacheWriteInputTokens": 3,
                "reasoningTokens": 2,
            },
        }

    client = MagicMock()
    client.converse.side_effect = fake_converse
    provider._client = client

    result = await provider.chat([{"role": "user", "content": "hi"}])

    assert result.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cache_read_tokens": 4,
        "cache_write_tokens": 3,
        "reasoning_tokens": 2,
    }


def test_bedrock_usage_from_metadata_projects_cache_and_reasoning() -> None:
    from matmaster.providers.bedrock_provider import _usage_from_metadata

    flat, vendor = _usage_from_metadata(
        {
            "usage": {
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
                "cacheReadInputTokens": 4,
                "cacheWriteInputTokens": 3,
                "reasoningTokens": 2,
            }
        }
    )

    assert flat == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cache_read_tokens": 4,
        "cache_write_tokens": 3,
        "reasoning_tokens": 2,
    }
    assert vendor["cacheReadInputTokens"] == 4
```

- [ ] **Step 3: Run provider tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/providers/test_openai_provider.py \
  tests/matmaster/providers/test_openai_provider_prompt_cache.py \
  tests/matmaster/providers/test_bedrock_provider.py -q
```

Expected: FAIL because cache write and reasoning fields are not projected into scalar usage.

- [ ] **Step 4: Add OpenAI scalar projection helpers**

In `matmaster/providers/openai_provider.py`, add helpers near `_extract_cached_tokens()`:

```python
def _extract_cache_write_tokens(usage: Any) -> int:
    val = getattr(usage, "cache_creation_input_tokens", None)
    if isinstance(val, int) and val > 0:
        return val
    cache_creation = getattr(usage, "cache_creation", None)
    if isinstance(cache_creation, dict):
        total = 0
        for value in cache_creation.values():
            if isinstance(value, int) and value > 0:
                total += value
        return total
    return 0


def _extract_reasoning_tokens(usage: Any) -> int:
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        val = getattr(details, "reasoning_tokens", None)
        if isinstance(val, int) and val > 0:
            return val
    val = getattr(usage, "reasoning_tokens", None)
    if isinstance(val, int) and val > 0:
        return val
    return 0


def _openai_usage_to_scalar_dict(usage: Any) -> dict[str, int]:
    out = {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }
    cache_read = _extract_cached_tokens(usage)
    if cache_read:
        out["cache_read_tokens"] = cache_read
    cache_write = _extract_cache_write_tokens(usage)
    if cache_write:
        out["cache_write_tokens"] = cache_write
    reasoning = _extract_reasoning_tokens(usage)
    if reasoning:
        out["reasoning_tokens"] = reasoning
    return out
```

Replace both OpenAI scalar usage construction sites with:

```python
usage = _openai_usage_to_scalar_dict(response.usage)
```

and:

```python
last_chunk_usage = _openai_usage_to_scalar_dict(usage)
```

Keep `usage_vendor = _openai_usage_to_vendor_dict(...)` unchanged.

- [ ] **Step 5: Add Bedrock scalar projection helper**

In `matmaster/providers/bedrock_provider.py`, replace `_usage_from_metadata()` with:

```python
def _get_positive_int(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def _usage_from_metadata(meta: dict[str, Any]) -> tuple[dict[str, int], dict[str, Any]]:
    usage = meta.get("usage") or {}
    inp = int(usage.get("inputTokens") or usage.get("input_tokens") or 0)
    out = int(usage.get("outputTokens") or usage.get("output_tokens") or 0)
    tot = int(usage.get("totalTokens") or usage.get("total_tokens") or (inp + out))
    flat = {
        "prompt_tokens": inp,
        "completion_tokens": out,
        "total_tokens": tot,
    }
    cache_read = _get_positive_int(
        usage,
        "cacheReadInputTokens",
        "cache_read_input_tokens",
        "cache_read_tokens",
    )
    if cache_read:
        flat["cache_read_tokens"] = cache_read
    cache_write = _get_positive_int(
        usage,
        "cacheWriteInputTokens",
        "cacheCreationInputTokens",
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
        "cache_write_tokens",
    )
    if cache_write:
        flat["cache_write_tokens"] = cache_write
    reasoning = _get_positive_int(
        usage,
        "reasoningTokens",
        "reasoning_tokens",
    )
    if reasoning:
        flat["reasoning_tokens"] = reasoning
    return flat, dict(usage)
```

In `BedrockProvider.chat()`, replace the local scalar construction inside `if u:` with:

```python
            usage, usage_vendor = _usage_from_metadata({"usage": u})
```

The streaming path already calls `_usage_from_metadata({"usage": usage})`; it should pick up the new fields automatically.

- [ ] **Step 6: Verify provider normalization**

Run:

```bash
uv run pytest tests/matmaster/providers/test_openai_provider.py \
  tests/matmaster/providers/test_openai_provider_prompt_cache.py \
  tests/matmaster/providers/test_bedrock_provider.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add matmaster/providers/openai_provider.py matmaster/providers/bedrock_provider.py \
  tests/matmaster/providers/test_openai_provider.py \
  tests/matmaster/providers/test_openai_provider_prompt_cache.py \
  tests/matmaster/providers/test_bedrock_provider.py
git commit -m "feat: normalize provider cache usage scalars"
```

---

### Task 6: Stop Feishu Summary From Mixing Root-Only Vendor Detail Into Aggregate Usage

> **Spec-atomicity note.** The design doc requires the provider scalar normalization (Task 5) and this Feishu vendor-fallback removal to land "在同一变更集中原子完成" so `cache_write_tokens` / `reasoning_tokens` never zero out in an intermediate state. This plan keeps them as two ordered commits rather than one, which is safe because: (1) the order is provider-first, so the failure mode the spec warns about — removing the vendor fallback before the scalar source exists — cannot happen; and (2) the `_build_run_usage_summary` unit tests inject `usage` directly instead of going through a provider, so Task 5 leaves them green and Task 6 changes the assertions in the same commit that changes the behavior. For strict spec compliance, squash Task 5 and Task 6 into one commit — but do not reorder them.

**Files:**
- Modify: `src/services/agent_run_service.py`
- Modify: `tests/matmaster/services/test_run_usage_summary.py`
- Modify: `src/utils/feishu_notifier.py`
- Modify: `tests/test_feishu_notifier_usage.py`

- [ ] **Step 1: Replace vendor fallback tests with scalar-only tests**

In `tests/matmaster/services/test_run_usage_summary.py`, delete these old tests:

- `test_vendor_fallback_openai_nested_cache`
- `test_vendor_fallback_anthropic_top_level_cache`
- `test_scalar_cache_read_takes_precedence_over_vendor`
- `test_vendor_reasoning_and_cache_write`
- `test_vendor_sum_across_turns`

Add these tests in their place:

```python
def test_vendor_by_turn_does_not_backfill_aggregate_cache() -> None:
    s = _build_run_usage_summary(
        _event(
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            usage_vendor_by_turn=[{"prompt_tokens_details": {"cached_tokens": 40}}],
        )
    )

    assert s is not None
    assert "cache_read_tokens" not in s


def test_scalar_cache_and_reasoning_fields_are_used_directly() -> None:
    s = _build_run_usage_summary(
        _event(
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cache_read_tokens": 10,
                "cache_write_tokens": 5,
                "reasoning_tokens": 3,
            },
            usage_vendor_by_turn=[
                {
                    "cache_read_input_tokens": 99,
                    "cache_creation_input_tokens": 88,
                    "completion_tokens_details": {"reasoning_tokens": 77},
                }
            ],
        )
    )

    assert s is not None
    assert s["cache_read_tokens"] == 10
    assert s["cache_write_tokens"] == 5
    assert s["reasoning_tokens"] == 3
```

- [ ] **Step 2: Update Feishu notifier wording tests**

In `tests/test_feishu_notifier_usage.py`, if a docstring or assertion describes root-only usage, update it to aggregate usage. Add this test:

```python
def test_aggregate_usage_rows_include_cache_write_and_reasoning_from_scalar() -> None:
    rows = format_usage_rows(
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "cache_write_tokens": 30,
            "reasoning_tokens": 7,
        }
    )

    text = "\n".join(row["text"]["content"] for row in rows)
    assert "cache_write_tokens" in text
    assert "reasoning_tokens" in text
```

- [ ] **Step 3: Run summary tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/services/test_run_usage_summary.py \
  tests/test_feishu_notifier_usage.py -q
```

Expected: FAIL because `_build_run_usage_summary()` still backfills cache/reasoning from `usage_vendor_by_turn`.

- [ ] **Step 4: Update _build_run_usage_summary scalar semantics**

Modify the `_build_run_usage_summary()` docstring in `src/services/agent_run_service.py`:

```python
    """从 ``RunResultEvent`` 提取 token 消耗摘要，供飞书通知等审计展示。

    ``event.usage`` 是 run-level aggregate scalar usage，包含 root accepted
    LLM turns、Agent subagent usage 和 compaction summary usage。``usage_vendor_by_turn``
    仍只表示 root accepted turns 的 provider-native 快照，不参与 aggregate cache /
    reasoning 补账。无任何 usage 信息时返回 ``None``。
    """
```

Replace the cache/reasoning calculation block with:

```python
    prompt = int(usage.get('prompt_tokens') or 0)
    completion = int(usage.get('completion_tokens') or 0)
    total = int(usage.get('total_tokens') or 0) or (prompt + completion)
    cache_read = int(usage.get('cache_read_tokens') or 0)
    cache_write = int(usage.get('cache_write_tokens') or 0)
    reasoning = int(usage.get('reasoning_tokens') or 0)
```

Remove the `vendor_by_turn` list and `_sum_vendor_field(...)` calls from `_build_run_usage_summary()`. Leave `_sum_vendor_field()` in place only if another function still uses it; otherwise delete `_sum_vendor_field()` as dead code and rerun tests.

Change the no-usage guard to:

```python
    if not usage and not last_turn_usage:
        return None
```

- [ ] **Step 5: Update Feishu notifier wording**

Modify the module docstring or inline wording in `src/utils/feishu_notifier.py` if it still says root kernel accepted turns only. Use this wording:

```python
"""Render run-level aggregate token usage rows for Feishu notifications."""
```

Do not change formatting behavior except where tests require scalar cache write and reasoning rows to render.

- [ ] **Step 6: Verify summary behavior**

Run:

```bash
uv run pytest tests/matmaster/services/test_run_usage_summary.py \
  tests/test_feishu_notifier_usage.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/services/agent_run_service.py src/utils/feishu_notifier.py \
  tests/matmaster/services/test_run_usage_summary.py tests/test_feishu_notifier_usage.py
git commit -m "fix: summarize aggregate scalar usage"
```

---

### Task 7: Update Token Usage Design Documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-05-08-token-usage-events-design.md`
- Test: documentation grep checks

- [ ] **Step 1: Find old root-only wording**

Run:

```bash
rg -n "root-only|root kernel accepted|compaction summary LLM|sub-agent|subagent 内部|RunResultEvent\\.usage|run_result\\.usage" docs/superpowers/specs/2026-05-08-token-usage-events-design.md
```

Expected: matches in sections that previously declared `run_result.usage` root-only and explicitly excluded subagent / compaction usage.

- [ ] **Step 2: Replace run_result usage semantics**

In `docs/superpowers/specs/2026-05-08-token-usage-events-design.md`, replace the old root-only paragraphs with this text:

```markdown
`run_result` 是业务终态。它携带的 `usage` 是 root run 下已知的 run-level aggregate
scalar usage，包含：

- root agent 通过 retry gate 后被接受的主循环 LLM turns。
- `Agent` 工具触发的 subagent run usage。
- context compaction summary LLM usage。

`response.complete.turn_usage` 仍只表示当前 root accepted LLM turn。`ToolResultEvent.turn_usage`
仍只表示父 agent 当前 LLM turn。subagent 与 compaction usage 只进入
`state.total_usage` / `RunResultEvent.usage`，不覆盖 `turn_usage`。
```

- [ ] **Step 3: Replace vendor-by-turn wording**

In the same doc, replace any sentence that implies `usage_vendor_by_turn` can reconstruct aggregate usage with:

```markdown
`usage_vendor_by_turn` 只保存 root accepted LLM turns 的 provider-native usage 快照，
用于诊断和 root-turn 对齐。它不包含 subagent 或 compaction 调用，也不再作为
run-level aggregate cache / reasoning 字段的补账来源。
```

- [ ] **Step 4: Add cross-reference to the new aggregation spec**

Near the first section that discusses future subagent/compaction usage, add:

```markdown
后续聚合语义见
`docs/superpowers/specs/2026-06-01-agent-token-usage-aggregation-design.md`。
```

- [ ] **Step 5: Verify docs no longer contradict the new plan**

Run:

```bash
rg -n "不含 retry 丢弃的 attempt、context compaction|root `run_result\\.usage` 只表示|context compaction summary LLM 的 usage 不并入 response.complete 或 run_result.usage|不在本阶段实现 sub-agent 全量 usage 聚合" docs/superpowers/specs/2026-05-08-token-usage-events-design.md
```

Expected: no matches.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-05-08-token-usage-events-design.md
git commit -m "docs: update token usage aggregation semantics"
```

---

### Task 8: End-To-End Verification And Cleanup

**Files:**
- Verify only, unless a preceding task surfaced a small fix.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_agent_tool.py \
  tests/matmaster/core/test_hook_wiring.py::TestSubagentHookWiring \
  tests/matmaster/core/test_agent_tool_dispatch.py \
  tests/matmaster/core/test_agent_kernel_usage_events.py \
  tests/matmaster/context/test_summary_caller.py \
  tests/matmaster/core/test_agent_compaction.py \
  tests/matmaster/types/test_events.py \
  tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/providers/test_openai_provider.py \
  tests/matmaster/providers/test_openai_provider_prompt_cache.py \
  tests/matmaster/providers/test_bedrock_provider.py \
  tests/matmaster/services/test_run_usage_summary.py \
  tests/test_feishu_notifier_usage.py -q
```

Expected: PASS.

- [ ] **Step 2: Run contract searches**

Run:

```bash
rg -n "Awaitable\\[str\\]|call_summary_llm\\b|from matmaster.core.stream_drain import DrainResult|root-only.*usage|root kernel accepted" matmaster src evaluation tests docs/superpowers/specs/2026-05-08-token-usage-events-design.md
```

Expected: no matches for old `SpawnFn` string return, old summary helper, tool-layer core import, or stale root-only usage summary wording.

Run:

```bash
rg -n "turn_usage=state\\.turn_usage|total_usage=state\\.total_usage" matmaster/core
```

Expected: no matches. Usage-bearing events should pass `dict(state.turn_usage)` and `dict(state.total_usage)`.

- [ ] **Step 3: Run line-count guard for touched Python files**

Run:

```bash
wc -l matmaster/core/agent.py matmaster/core/agent_tool_dispatch.py \
  matmaster/core/agent_compaction.py matmaster/context/compaction.py \
  matmaster/providers/openai_provider.py matmaster/providers/bedrock_provider.py \
  src/services/agent_run_service.py tests/matmaster/core/test_agent_tool_dispatch.py \
  tests/matmaster/core/test_agent_kernel_usage_events.py \
  tests/matmaster/core/test_agent_compaction.py
```

Expected: each Python file remains below 1000 lines.

- [ ] **Step 4: Run broader smoke tests for affected areas**

Run:

```bash
uv run pytest tests/matmaster/core tests/matmaster/context tests/matmaster/providers \
  tests/matmaster/devshell \
  tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/services/test_run_usage_summary.py \
  tests/test_feishu_notifier_usage.py -q
uv run python -c "import evaluation.skill_trigger.runner"
```

Expected: PASS and the `evaluation.skill_trigger.runner` import succeeds (guards the DrainResult move from Task 1).

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git diff --stat
git diff --check
git status --short
```

Expected: `git diff --check` reports no whitespace errors. `git status --short` only shows files intentionally changed by this plan.

- [ ] **Step 6: Confirm no generic cleanup commit is needed**

If Step 5 required a fix, return to the task that owns the affected file, apply the fix there, rerun that task's verification command, and amend that task's commit before continuing. If Step 5 only confirms a clean verification pass, do not create an empty commit.

---

## Acceptance Checklist

- [ ] `RunResultEvent.usage` contains root accepted LLM usage plus subagent usage plus compaction summary usage.
- [ ] `ResponseEvent.turn_usage`, `AssistantStateEvent.turn_usage`, and `ToolResultEvent.turn_usage` remain root-turn-only.
- [ ] `ToolResultEvent.total_usage` is a parent run cumulative snapshot and includes each `Agent` usage delta before the event is emitted.
- [ ] `ToolResult.payload["subagent_usage"]` is present for `Agent` tool results and represents only that child run delta.
- [ ] A malformed `Agent` `subagent_usage` on a successful tool result raises `InvalidToolUsageDelta` inside dispatch, is caught by `AgentKernel` at the dispatch boundary, and produces `RunResultEvent(status="failed", reason="internal_error")` with the current usage snapshot rather than bubbling to the service generic error path.
- [ ] Every `DrainResult` importer in the repo (core, devshell, evaluation, tests) points at `matmaster.types.stream_drain`; no module imports `DrainResult` from `matmaster.core.stream_drain`.
- [ ] `CompactionEvent(status="complete")` can carry summary-call `turn_usage` and cumulative `total_usage`.
- [ ] Running compaction events do not carry usage.
- [ ] Runtime summary LLM validation failures still count returned usage before fallback; preflight validation failures remain on the existing abort path and are not public usage acceptance cases in this plan.
- [ ] Provider exceptions before an `LLMResponse` do not invent usage.
- [ ] `usage_vendor_by_turn` remains root accepted turns only.
- [ ] Feishu usage summary reads aggregate scalar `event.usage` fields and does not use root-only vendor details for aggregate cache/reasoning fallback.
- [ ] All usage-bearing events hold independent dict snapshots.

## Self-Review

- Spec coverage: subagent contract, payload, and the repo-wide `DrainResult` import move (core/devshell/evaluation/tests, guarded by the Task 1 Step 7 and Task 8 greps + import smoke) are covered by Task 1; tool delta validation and parent accumulation are covered by Task 2; compaction response migration and usage aggregation are covered by Tasks 3 and 4; provider scalar normalization and Feishu summary semantics are covered by Tasks 5 and 6; old token usage design docs are updated in Task 7; verification gates are in Task 8.
- Placeholder scan: no unresolved placeholder tokens, no open-ended edge-case instruction, and every code-changing task includes concrete test code, implementation snippets, commands, and expected outcomes.
- Type consistency: `DrainResult` is defined once in `matmaster/types/stream_drain.py`; `SpawnFn` returns `Awaitable[DrainResult]`; `call_summary_llm_response()` returns `LLMResponse`; `validate_summary_response()` returns `str`; `extract_tool_usage_delta()` returns `dict[str, int]`; `CompactionEvent.turn_usage` and `CompactionEvent.total_usage` are `dict[str, int] | None`.

Plan complete and saved to `docs/superpowers/plans/2026-06-01-agent-token-usage-aggregation.md`.

Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
