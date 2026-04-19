# Response Figures Early Render Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit `response_figures` as soon as parent tool results produce uploaded images, so the frontend can render images immediately after a complete `[[fig:<figure_id>]]` anchor appears in the streaming answer.

**Architecture:** Convert `ResponseFiguresAccumulator` from a one-shot collector into an incremental complete-snapshot state machine. `AgentRunService` will absorb parent `ToolResultEvent.payload.figures`, dispatch the original tool result, then dispatch a derived `response_figures` snapshot with safe failure handling and final-flush fallback. SSE/persistence continue to inject run-level `session_id`, `task_id`, `invocation_id`, and `spawn_id`; replay remains append-only and frontend state converges by `invocation_id` upsert.

**Tech Stack:** Python 3.11+, Pydantic events, FastAPI/SSE service layer, pytest, uv-managed environment.

---

## Scope And Boundaries

This plan modifies only the `matmaster-evo` repository. The sibling frontend repository `../scimaster-bohr-chat` is reference material only because project rules forbid editing sibling repositories from this workspace.

Existing unrelated local changes must not be staged or reverted. At plan-writing time, the worktree contains unrelated modifications in:

- `matmaster/core/agent.py`
- `tests/matmaster/core/test_agent_kernel_stream.py`

Every commit command below stages explicit paths only.

## File Structure

- Modify: `src/services/response_figures_service.py`
  - Owns the answer-level figure accumulator and snapshot state.
- Modify: `src/services/agent_run_service.py`
  - Owns event-loop timing for dispatching derived `response_figures` snapshots.
- Modify: `src/services/stream_service.py`
  - Updates replay dedupe documentation for new legal event orders.
- Modify: `src/models/chat.py`
  - Updates public ag-ui protocol text for early and repeated `response_figures`.
- Create: `tests/matmaster/services/test_response_figures_service.py`
  - Unit tests for accumulator state, duplicate handling, child filtering, and invalid payloads.
- Modify: `tests/matmaster/services/test_agent_run_stream_response_figures.py`
  - Integration tests for event ordering, multi-snapshot behavior, final flush, and dispatch-failure recovery.
- Create: `tests/matmaster/integration/test_sse_response_figures.py`
  - SSE payload test proving `invocation_id` and run context are injected for `response_figures`.
- Modify: `tests/test_stream_replay_skill_hit.py`
  - Replay dedupe tests for `response_figures -> response -> run_result` and interleaved snapshots.
- Modify: `docs/superpowers/specs/2026-04-14-chat-response-figures-design.md`
  - Historical design update documenting the newer incremental snapshot behavior.
- Modify: `docs/superpowers/plans/2026-04-14-chat-response-figures.md`
  - Historical plan note documenting that the original one-shot implementation has been superseded.

## Task 1: Incremental ResponseFiguresAccumulator

**Files:**
- Create: `tests/matmaster/services/test_response_figures_service.py`
- Modify: `src/services/response_figures_service.py`

- [ ] **Step 1: Write failing accumulator tests**

Create `tests/matmaster/services/test_response_figures_service.py` with:

```python
from __future__ import annotations

import logging

from matmaster.types.events import ToolResultEvent
from src.services.response_figures_service import ResponseFiguresAccumulator


def _figure(
    figure_id: str,
    *,
    asset_url: str | None = None,
    caption: str | None = None,
    source_tool_call_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        'figure_id': figure_id,
        'asset_url': asset_url or f'https://oss.example/{figure_id}.png',
        'caption': caption or figure_id,
        'importance': 'primary',
        'placement_hint': 'sidebar_only',
    }
    if source_tool_call_id is not None:
        payload['source_tool_call_id'] = source_tool_call_id
    return payload


def _tool_result(
    call_id: str,
    figures: list[dict[str, object]] | object,
    *,
    spawn_id: str | None = None,
) -> ToolResultEvent:
    return ToolResultEvent(
        source='MatMaster',
        spawn_id=spawn_id,
        call_id=call_id,
        tool_name='Bash',
        result='done',
        payload={'figures': figures},
    )


def test_snapshot_requires_mark_emitted_before_repeats_are_suppressed() -> None:
    acc = ResponseFiguresAccumulator()

    changed = acc.add_tool_result(_tool_result('call-band', [_figure('band')]))
    assert changed is True

    first = acc.build_snapshot_event_if_dirty()
    assert first is not None
    assert [fig.figure_id for fig in first.figures] == ['band']

    repeated_before_commit = acc.build_snapshot_event_if_dirty()
    assert repeated_before_commit is not None
    assert [fig.figure_id for fig in repeated_before_commit.figures] == ['band']

    acc.mark_snapshot_emitted()
    assert acc.build_snapshot_event_if_dirty() is None


def test_later_tool_result_emits_complete_snapshot_with_previous_figures() -> None:
    acc = ResponseFiguresAccumulator()

    assert acc.add_tool_result(_tool_result('call-band', [_figure('band')])) is True
    first = acc.build_snapshot_event_if_dirty()
    assert first is not None
    acc.mark_snapshot_emitted()

    assert acc.add_tool_result(_tool_result('call-dos', [_figure('dos')])) is True
    second = acc.build_snapshot_event_if_dirty()

    assert second is not None
    assert [fig.figure_id for fig in second.figures] == ['band', 'dos']
    assert [fig.source_tool_call_id for fig in second.figures] == [
        'call-band',
        'call-dos',
    ]


def test_duplicate_figure_id_keeps_first_and_logs_warning(caplog) -> None:
    acc = ResponseFiguresAccumulator()
    caplog.set_level(logging.WARNING)

    assert acc.add_tool_result(_tool_result('call-band', [_figure('band')])) is True
    assert acc.add_tool_result(_tool_result('call-band-new', [_figure('band')])) is False

    snapshot = acc.build_snapshot_event_if_dirty()
    assert snapshot is not None
    assert [fig.source_tool_call_id for fig in snapshot.figures] == ['call-band']
    assert 'Ignoring duplicate response figure_id=band' in caplog.text
    assert 'first_tool_call=call-band' in caplog.text
    assert 'duplicate_tool_call=call-band-new' in caplog.text


def test_ignores_child_spawn_invalid_payload_and_non_list_figures() -> None:
    acc = ResponseFiguresAccumulator()

    assert (
        acc.add_tool_result(
            _tool_result('call-child', [_figure('child')], spawn_id='sub-1')
        )
        is False
    )
    assert (
        acc.add_tool_result(
            _tool_result(
                'call-invalid',
                [{'figure_id': 'broken', 'asset_url': 'https://oss.example/broken.png'}],
            )
        )
        is False
    )
    assert acc.add_tool_result(_tool_result('call-non-list', {'bad': 'shape'})) is False
    assert acc.build_snapshot_event_if_dirty() is None
```

- [ ] **Step 2: Run accumulator tests and verify they fail**

Run:

```bash
uv run pytest tests/matmaster/services/test_response_figures_service.py -v
```

Expected: FAIL because `add_tool_result()` currently returns `None`, and `build_snapshot_event_if_dirty()` / `mark_snapshot_emitted()` do not exist.

- [ ] **Step 3: Implement incremental accumulator**

Replace the contents of `src/services/response_figures_service.py` with:

```python
"""回答级图片聚合服务。"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from matmaster.types.events import ResponseFiguresEvent, ToolResultEvent
from matmaster.types.figures import FigureDescriptor

logger = logging.getLogger(__name__)


class ResponseFiguresAccumulator:
    """把多个 tool_result.payload.figures 汇总成可增量发出的回答级快照。"""

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        self._source_by_id: dict[str, str | None] = {}
        self._ordered: list[FigureDescriptor] = []
        self._last_emitted_count = 0

    def add_tool_result(self, event: ToolResultEvent) -> bool:
        """吸收父级 tool result 中的图片，保持到达顺序与 first-writer-wins。"""
        if event.spawn_id is not None:
            return False

        raw_items = (event.payload or {}).get('figures') or []
        if not isinstance(raw_items, list):
            return False

        added = False
        for raw in raw_items:
            try:
                figure = FigureDescriptor.model_validate(raw)
            except ValidationError:
                logger.warning(
                    'Ignoring invalid response figure payload for tool_call=%s',
                    event.call_id,
                    exc_info=True,
                )
                continue

            if figure.source_tool_call_id is None:
                figure = figure.model_copy(
                    update={'source_tool_call_id': event.call_id}
                )

            if figure.figure_id in self._seen_ids:
                logger.warning(
                    'Ignoring duplicate response figure_id=%s '
                    'first_tool_call=%s duplicate_tool_call=%s',
                    figure.figure_id,
                    self._source_by_id.get(figure.figure_id),
                    event.call_id,
                )
                continue

            self._seen_ids.add(figure.figure_id)
            self._source_by_id[figure.figure_id] = figure.source_tool_call_id
            self._ordered.append(figure)
            added = True

        return added

    def build_snapshot_event_if_dirty(self) -> ResponseFiguresEvent | None:
        """构造完整 response_figures 快照；不提交 emitted 状态。"""
        if len(self._ordered) <= self._last_emitted_count:
            return None
        return ResponseFiguresEvent(source='System', figures=list(self._ordered))

    def mark_snapshot_emitted(self) -> None:
        """在快照 dispatch 成功返回后提交 emitted 状态。"""
        self._last_emitted_count = len(self._ordered)
```

- [ ] **Step 4: Run accumulator tests and verify they pass**

Run:

```bash
uv run pytest tests/matmaster/services/test_response_figures_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit accumulator change**

Run:

```bash
git add src/services/response_figures_service.py tests/matmaster/services/test_response_figures_service.py
git commit -m "feat: make response figures accumulator incremental"
```

Expected: commit includes exactly the accumulator service and its new tests.

## Task 2: Early Dispatch From AgentRunService

**Files:**
- Modify: `src/services/agent_run_service.py`
- Modify: `tests/matmaster/services/test_agent_run_stream_response_figures.py`

- [ ] **Step 1: Write failing AgentRunService tests**

Modify imports in `tests/matmaster/services/test_agent_run_stream_response_figures.py` to include `ResponseEvent` and `patch`:

```python
from unittest.mock import AsyncMock, patch

from matmaster.types.events import ResponseEvent, RunResultEvent, ToolResultEvent
```

Append these tests to `tests/matmaster/services/test_agent_run_stream_response_figures.py`:

```python
@pytest.mark.asyncio
async def test_run_agent_emits_response_figures_immediately_after_parent_tool_result():
    tool_result = ToolResultEvent(
        source='MatMaster',
        call_id='call-band',
        tool_name='Bash',
        result='done',
        payload={
            'figures': [
                {
                    'figure_id': 'band',
                    'asset_url': 'https://oss.example/band.png',
                    'caption': 'band',
                    'importance': 'primary',
                    'placement_hint': 'sidebar_only',
                }
            ]
        },
    )
    response = ResponseEvent(
        source='MatMaster',
        content='见 [[fig:band]]',
        stream_state='streaming',
        stream_id='resp-1',
    )
    run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='answer',
    )

    async with _patched_service([tool_result, response, run_result]) as (
        svc,
        sse_events,
        _,
    ):
        controller = CancellationController()
        await svc.run_agent(
            session_id='sess-1',
            user_prompt='show band structure',
            send_cb=AsyncMock(),
            cancel_token=controller.token,
            mode='direct',
            task_id='task-1',
            invocation_id='inv-1',
        )

    sse_types = [getattr(evt, 'type', None) for evt in sse_events]
    assert sse_types.index('tool_result') < sse_types.index('response_figures')
    assert sse_types.index('response_figures') < sse_types.index('response')
    assert sse_types.index('response_figures') < sse_types.index('run_result')


@pytest.mark.asyncio
async def test_run_agent_emits_complete_response_figure_snapshots_after_each_tool_result():
    first_tool_result = ToolResultEvent(
        source='MatMaster',
        call_id='call-band',
        tool_name='Bash',
        result='done',
        payload={
            'figures': [
                {
                    'figure_id': 'band',
                    'asset_url': 'https://oss.example/band.png',
                    'caption': 'band',
                    'importance': 'primary',
                    'placement_hint': 'sidebar_only',
                }
            ]
        },
    )
    second_tool_result = ToolResultEvent(
        source='MatMaster',
        call_id='call-dos',
        tool_name='Bash',
        result='done',
        payload={
            'figures': [
                {
                    'figure_id': 'dos',
                    'asset_url': 'https://oss.example/dos.png',
                    'caption': 'dos',
                    'importance': 'secondary',
                    'placement_hint': 'sidebar_only',
                }
            ]
        },
    )
    run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='answer',
    )

    async with _patched_service(
        [first_tool_result, second_tool_result, run_result]
    ) as (svc, sse_events, _):
        controller = CancellationController()
        await svc.run_agent(
            session_id='sess-1',
            user_prompt='show band and dos',
            send_cb=AsyncMock(),
            cancel_token=controller.token,
            mode='direct',
            task_id='task-1',
            invocation_id='inv-1',
        )

    figure_events = [
        event for event in sse_events if getattr(event, 'type', None) == 'response_figures'
    ]
    assert len(figure_events) == 2
    assert [fig.figure_id for fig in figure_events[0].figures] == ['band']
    assert [fig.figure_id for fig in figure_events[1].figures] == ['band', 'dos']


@pytest.mark.asyncio
async def test_run_agent_final_flush_retries_uncommitted_response_figures_snapshot():
    tool_result = ToolResultEvent(
        source='MatMaster',
        call_id='call-band',
        tool_name='Bash',
        result='done',
        payload={
            'figures': [
                {
                    'figure_id': 'band',
                    'asset_url': 'https://oss.example/band.png',
                    'caption': 'band',
                    'importance': 'primary',
                    'placement_hint': 'sidebar_only',
                }
            ]
        },
    )
    run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='answer',
    )

    from matmaster.integration.fanout import RunEventFanout

    real_dispatch = RunEventFanout.dispatch
    failed_once = False

    async def flaky_dispatch(self, event):
        nonlocal failed_once
        if getattr(event, 'type', None) == 'response_figures' and not failed_once:
            failed_once = True
            raise RuntimeError('synthetic response_figures dispatch failure')
        return await real_dispatch(self, event)

    async with _patched_service([tool_result, run_result]) as (svc, sse_events, _):
        controller = CancellationController()
        with patch.object(RunEventFanout, 'dispatch', flaky_dispatch):
            await svc.run_agent(
                session_id='sess-1',
                user_prompt='show band structure',
                send_cb=AsyncMock(),
                cancel_token=controller.token,
                mode='direct',
                task_id='task-1',
                invocation_id='inv-1',
            )

    sse_types = [getattr(evt, 'type', None) for evt in sse_events]
    assert failed_once is True
    assert sse_types.count('response_figures') == 1
    assert sse_types.index('response_figures') < sse_types.index('run_result')
```

Update the existing `test_run_agent_emits_response_figures_before_run_result()` expectation so it also requires `tool_result` to appear before `response_figures`:

```python
    assert sse_types.index('tool_result') < sse_types.index('response_figures')
    assert sse_types.index('response_figures') < sse_types.index('run_result')
```

- [ ] **Step 2: Run AgentRunService response figure tests and verify they fail**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream_response_figures.py -v
```

Expected: FAIL because current code emits one `response_figures` only at root `run_result`, and `ResponseFiguresAccumulator` no longer has `build_event()`.

- [ ] **Step 3: Implement safe early dispatch in AgentRunService**

In `src/services/agent_run_service.py`, immediately after `figure_upload_config` is created, add a local helper before the existing `_child_event_sink` helper:

```python
            async def _dispatch_response_figures_if_dirty(reason: str) -> None:
                response_figures_event = (
                    figure_accumulator.build_snapshot_event_if_dirty()
                )
                if response_figures_event is None:
                    return
                try:
                    await fanout.dispatch(response_figures_event)
                except Exception:
                    logger.warning(
                        'response_figures dispatch failed reason=%s',
                        reason,
                        exc_info=True,
                    )
                else:
                    figure_accumulator.mark_snapshot_emitted()
```

Then replace the event loop block around `ToolResultEvent` and `RunResultEvent` with:

```python
                    if isinstance(event, ToolResultEvent):
                        figure_accumulator.add_tool_result(event)

                    if isinstance(event, RunResultEvent) and event.spawn_id is None:
                        await _dispatch_response_figures_if_dirty('final_flush')

                    await fanout.dispatch(event)

                    if isinstance(event, ToolResultEvent):
                        await _dispatch_response_figures_if_dirty('tool_result')

                    # Detect terminal event
                    if isinstance(event, RunResultEvent):
                        run_result_event = event
```

This preserves causal ordering:

```text
tool_result -> response_figures
response chunks or additional tool results
response_figures final flush -> run_result
```

- [ ] **Step 4: Run AgentRunService response figure tests and verify they pass**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream_response_figures.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit AgentRunService early dispatch**

Run:

```bash
git add src/services/agent_run_service.py tests/matmaster/services/test_agent_run_stream_response_figures.py
git commit -m "feat: emit response figure snapshots during tool results"
```

Expected: commit includes exactly AgentRunService and its response figure integration tests.

## Task 3: Invocation ID Payload And Replay Ordering

**Files:**
- Create: `tests/matmaster/integration/test_sse_response_figures.py`
- Modify: `tests/test_stream_replay_skill_hit.py`
- Modify: `src/services/stream_service.py`

- [ ] **Step 1: Write SSE invocation_id test**

Create `tests/matmaster/integration/test_sse_response_figures.py` with:

```python
from __future__ import annotations

import pytest

from matmaster.integration.sse_handler import SSEHandler
from matmaster.types.events import ResponseFiguresEvent
from matmaster.types.figures import FigureDescriptor


@pytest.mark.asyncio
async def test_response_figures_sse_payload_includes_run_context() -> None:
    sent: list[dict] = []
    handler = SSEHandler(
        send_cb=lambda payload: sent.append(payload),
        session_id='sess-1',
        task_id='task-1',
        invocation_id='inv-1',
        mode='direct',
    )

    await handler.handle(
        ResponseFiguresEvent(
            source='System',
            figures=[
                FigureDescriptor(
                    figure_id='band',
                    asset_url='https://oss.example/band.png',
                    caption='band',
                    importance='primary',
                    placement_hint='sidebar_only',
                    source_tool_call_id='call-band',
                )
            ],
        )
    )

    assert len(sent) == 1
    payload = sent[0]
    assert payload['type'] == 'response_figures'
    assert payload['session_id'] == 'sess-1'
    assert payload['task_id'] == 'task-1'
    assert payload['invocation_id'] == 'inv-1'
    assert payload['spawn_id'] is None
    assert payload['content']['figures'][0]['figure_id'] == 'band'
```

- [ ] **Step 2: Write replay ordering tests**

Append these tests to `TestReplayDedupeSpawnId` in `tests/test_stream_replay_skill_hit.py`:

```python
    def test_response_figures_before_response_still_dedupes_later_run_result(
        self,
    ) -> None:
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response_figures",
                "source": "System",
                "content": {
                    "figures": [
                        {
                            "figure_id": "band",
                            "asset_url": "https://oss.example/band.png",
                            "caption": "band",
                        }
                    ]
                },
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": "answer with [[fig:band]]",
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": "answer with [[fig:band]]",
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == ["response_figures", "response"]

    def test_interleaved_response_and_multiple_response_figures_keep_order(
        self,
    ) -> None:
        from src.services.stream_service import _dedupe_replayed_terminal_events

        events = [
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response_figures",
                "source": "System",
                "content": {
                    "figures": [
                        {
                            "figure_id": "band",
                            "asset_url": "https://oss.example/band.png",
                            "caption": "band",
                        }
                    ]
                },
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response",
                "source": "MatMaster",
                "content": "first answer chunk",
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "response_figures",
                "source": "System",
                "content": {
                    "figures": [
                        {
                            "figure_id": "band",
                            "asset_url": "https://oss.example/band.png",
                            "caption": "band",
                        },
                        {
                            "figure_id": "dos",
                            "asset_url": "https://oss.example/dos.png",
                            "caption": "dos",
                        },
                    ]
                },
            },
            {
                "task_id": "t1",
                "spawn_id": None,
                "type": "run_result",
                "source": "MatMaster",
                "content": "duplicate final answer",
            },
        ]

        out = _dedupe_replayed_terminal_events(events)
        assert [e["type"] for e in out] == [
            "response_figures",
            "response",
            "response_figures",
        ]
```

- [ ] **Step 3: Run SSE and replay tests**

Run:

```bash
uv run pytest tests/matmaster/integration/test_sse_response_figures.py tests/test_stream_replay_skill_hit.py -v
```

Expected: tests should PASS before code changes except for any docstring-only expectation, because payload injection and replay dedupe behavior already mostly support the new order. If a failure appears, inspect it before changing production code.

- [ ] **Step 4: Update stream_service replay dedupe docstring**

In `src/services/stream_service.py`, replace `_dedupe_replayed_terminal_events()` docstring with:

```python
    """Hide replayed run_result when the same task already has a replayable response.

    Live SSE already streamed the final `response` content. After persisted
    complete response segments were added, replaying the trailing `run_result`
    would duplicate the final answer after reconnect. We suppress terminal
    events once a replayable `response` has been seen for the same
    `(task_id, spawn_id)` stream.

    `response_figures` is replayable answer metadata. It may appear before the
    first response, between response chunks, or after a response. It is kept in
    replay output and does not reset or suppress the response-seen state.

    Dedupe is keyed by (task_id, spawn_id) so a sub-agent `response` does not
    suppress the parent stream's `run_result`.
    """
```

- [ ] **Step 5: Run SSE and replay tests again**

Run:

```bash
uv run pytest tests/matmaster/integration/test_sse_response_figures.py tests/test_stream_replay_skill_hit.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit invocation_id and replay ordering coverage**

Run:

```bash
git add src/services/stream_service.py tests/matmaster/integration/test_sse_response_figures.py tests/test_stream_replay_skill_hit.py
git commit -m "test: cover response figure run context and replay order"
```

Expected: commit includes exactly SSE/replay tests and the replay docstring update.

## Task 4: Protocol Documentation Updates

**Files:**
- Modify: `src/models/chat.py`
- Modify: `docs/superpowers/specs/2026-04-14-chat-response-figures-design.md`
- Modify: `docs/superpowers/plans/2026-04-14-chat-response-figures.md`

- [ ] **Step 1: Update public ag-ui protocol text**

In `src/models/chat.py`, replace the `response_figures` paragraph in the module docstring with:

```text
  response_figures：回答级图片绑定事件；content.figures 为已上传图片列表，顶层仍带 session_id、task_id、invocation_id、spawn_id。该事件用于侧边栏等图像展示，不会把图片写回正文文本。该事件可以在同一 invocation_id 下出现多次，每次都是当前已知完整图片组快照；合法顺序包括早于第一段 response、位于多个 response chunk 之间、或位于 run_result 之前的 final flush。前端应按 invocation_id eager upsert，且不从 tool_result.payload.figures 反推正式回答级图片。
```

- [ ] **Step 2: Update older response figures design**

In `docs/superpowers/specs/2026-04-14-chat-response-figures-design.md`, find the section that says:

```text
一次 assistant 回答最多发出一次 `response_figures`。
```

Replace that subsection with:

```markdown
本设计的第一版实现曾约定一次 assistant 回答最多发出一次 `response_figures`，并固定在 `run_result` 之前发出。

后续增量渲染设计已 supersede 该限制：同一 `invocation_id` 下允许发出多次完整 `response_figures` 快照。每次快照包含当前已知的完整图片组，前端按 `invocation_id` upsert，最终状态由最后一次快照收敛。`response_figures` 仍应早于对应最终 `run_result`，但可以早于第一段 `response` 或位于多个 `response` chunk 之间。
```

- [ ] **Step 3: Update older implementation plan**

In `docs/superpowers/plans/2026-04-14-chat-response-figures.md`, add this note near the top after `**Architecture:**`:

```markdown
**Superseded behavior note:** This original plan implemented a one-shot `response_figures` event before `run_result`. The approved 2026-04-16 early-render design changes that behavior to incremental complete snapshots: the backend may emit `response_figures` immediately after parent tool results produce uploaded figures, and may emit multiple snapshots for the same `invocation_id`.
```

- [ ] **Step 4: Run documentation diff checks**

Run:

```bash
git diff --check -- src/models/chat.py docs/superpowers/specs/2026-04-14-chat-response-figures-design.md docs/superpowers/plans/2026-04-14-chat-response-figures.md
```

Expected: no output.

- [ ] **Step 5: Commit protocol documentation updates**

Because `docs/` is ignored by `.gitignore`, use `git add -f` for the two docs files:

```bash
git add src/models/chat.py
git add -f docs/superpowers/specs/2026-04-14-chat-response-figures-design.md docs/superpowers/plans/2026-04-14-chat-response-figures.md
git commit -m "docs: document incremental response figure snapshots"
```

Expected: commit includes the protocol docstring and two docs files.

## Task 5: Targeted Regression Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run response figure focused tests**

Run:

```bash
uv run pytest \
  tests/matmaster/services/test_response_figures_service.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  tests/matmaster/integration/test_sse_response_figures.py \
  tests/test_stream_replay_skill_hit.py \
  tests/test_chat_stream_direct_response_figures.py \
  tests/matmaster/integration/test_event_payloads.py \
  -v
```

Expected: PASS.

- [ ] **Step 2: Run nearby AgentRunService stream tests**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream.py -v
```

Expected: PASS. If unrelated tests fail because of pre-existing local changes in `matmaster/core/agent.py` or `tests/matmaster/core/test_agent_kernel_stream.py`, record the exact failure and inspect whether the touched files in this plan are involved before changing anything.

- [ ] **Step 3: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 4: Leave verification-only runs uncommitted**

If all tests pass and no files changed during verification, do not create a commit.

If verification reveals a real issue, stop and create a new focused task before editing code. Do not hide implementation changes inside a verification step.

Expected: no commit when verification only ran commands.

## Task 6: Frontend Coordination Checklist

**Files:**
- No files in `matmaster-evo`.
- Do not edit `../scimaster-bohr-chat` from this workspace.

- [ ] **Step 1: Manually verify frontend assumptions from reference repo**

Read these files:

```bash
sed -n '1,120p' ../scimaster-bohr-chat/src/pages/matmaster/chat-evo/hooks/useEvoSSEHandler.event-handler.dispatch.response-figures.ts
sed -n '1,120p' ../scimaster-bohr-chat/src/pages/matmaster/chat-evo/utils/response-figures.ts
sed -n '1,80p' ../scimaster-bohr-chat/src/pages/matmaster/chat-evo/utils/response-figures-store.ts
sed -n '40,70p' ../scimaster-bohr-chat/src/pages/matmaster/chat-evo/agent-message.tsx
```

Expected observations:

- `normalizeResponseFiguresEvent()` ignores payloads without non-empty `invocation_id`.
- `upsertResponseFigureGroup()` creates or replaces the group by `invocationId`.
- `agent-message.tsx` reads `evoResponseFiguresByInvocation[invocationId]`.
- Anchor replacement only happens after complete `[[fig:<figure_id>]]` text exists.

- [ ] **Step 2: Record frontend follow-up outside this repo**

If the reference repo lacks tests for early `response_figures`, create a follow-up note or ticket outside this repository with this exact checklist:

```text
Frontend follow-up for response_figures early snapshots:
1. response_figures may arrive before the assistant message exists.
2. Store must eager upsert by invocation_id.
3. Later assistant message with the same invocation_id must resolve existing figures.
4. Multiple response_figures events for one invocation_id are complete snapshots; the latest replaces the previous group.
5. tool_result.payload.figures remains an intermediate backend detail, not the formal frontend source.
```

Expected: no files changed in `matmaster-evo`.

## Final Verification And Handoff

- [ ] **Step 1: Inspect changed files**

Run:

```bash
git status --short --untracked-files=all
git log --oneline -6
```

Expected:

- Only unrelated pre-existing local changes may remain unstaged.
- New commits should correspond to the task commits above.

- [ ] **Step 2: Summarize implementation**

Prepare a final summary containing:

```text
Implemented:
- Incremental complete-snapshot ResponseFiguresAccumulator.
- Early response_figures dispatch after parent tool_result, with final flush fallback.
- invocation_id SSE payload coverage.
- Replay ordering coverage for early and interleaved response_figures.
- Protocol docs updated.

Verification:
- uv run pytest tests/matmaster/services/test_response_figures_service.py tests/matmaster/services/test_agent_run_stream_response_figures.py tests/matmaster/integration/test_sse_response_figures.py tests/test_stream_replay_skill_hit.py tests/test_chat_stream_direct_response_figures.py tests/matmaster/integration/test_event_payloads.py -v
- uv run pytest tests/matmaster/services/test_agent_run_stream.py -v
- git diff --check

Notes:
- Did not modify ../scimaster-bohr-chat.
- Unrelated pre-existing dirty files at plan start were matmaster/core/agent.py and tests/matmaster/core/test_agent_kernel_stream.py; report whether they are still present.
```

Expected: final response is grounded in actual command output from this session.
