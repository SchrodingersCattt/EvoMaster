"""SSE event filter + normalization extracted from stream_service.

Phase 0 refactor (DESIGN.md §14 Phase 0a): move ``_should_emit_event_to_sse``
and friends out of ``stream_service.py`` so the file stays under the
800-line target. Phase 1 (EVT-02) will extend
``_should_emit_event_to_sse`` here to also hide ``user_turn_context``
events, mirroring the live ``SSEHandler._should_skip()`` policy.

The helpers were named with a leading underscore in the original
module; we keep the public API underscored too to avoid disturbing
callers that import these internals from ``stream_service``.
"""

from __future__ import annotations

from matmaster.integration.event_payloads import normalize_response_sse_payload
from matmaster.utils.event_source import normalize_event_source


def _should_emit_event_to_sse(event: dict) -> bool:
    """Filter persisted events for history replay SSE.

    NOTE: This filter is intentionally simpler than
    matmaster.integration.event_router.SSEHandler._should_skip().
    The live SSE path knows the run mode and stream_state; replay only sees
    persisted event rows.

    Practical consequences:
    - assistant_state is always hidden in replay
    - log_line is always hidden in replay
    - checkpoint bookkeeping events are always hidden in replay
    - direct-mode non-streaming thoughts may still appear in replay if they
      were persisted as completed events

    If exact parity is required in the future, the missing replay inputs
    (for example mode) must be persisted explicitly.
    """
    t = event.get('type')
    if t == 'log_line':
        return False
    if t in {'assistant_state', 'skill_hit', 'user_turn_context'}:
        return False
    if t in {'compact_boundary', 'history_checkpoint'}:
        return False
    return True


def _normalize_replayed_event(event: dict) -> dict:
    """Normalize source labels in replayed history events to the public set."""
    replay_event = dict(event)
    replay_event['source'] = normalize_event_source(replay_event.get('source'))
    return normalize_response_sse_payload(replay_event)


def _normalize_replayed_compaction_events(events: list[dict]) -> list[dict]:
    """Normalize replayed compaction lifecycle rows for frontend consumption."""
    terminal_ids: set[str] = set()
    for event in events:
        if event.get('type') != 'compaction':
            continue
        content = event.get('content') or {}
        if not isinstance(content, dict):
            continue
        if content.get('status') in {'complete', 'interrupted'}:
            compaction_id = str(content.get('compaction_id') or '')
            if compaction_id:
                terminal_ids.add(compaction_id)

    normalized: list[dict] = []
    for event in events:
        event_type = event.get('type')
        if event_type == 'context_compaction':
            continue
        if event_type != 'compaction':
            normalized.append(event)
            continue

        content = event.get('content') or {}
        if not isinstance(content, dict):
            normalized.append(event)
            continue

        compaction_id = str(content.get('compaction_id') or '')
        if content.get('status') == 'running' and compaction_id not in terminal_ids:
            normalized.append(
                {
                    **event,
                    'content': {
                        **content,
                        'status': 'interrupted',
                        'failure_reason': content.get('failure_reason')
                        or 'replay_inferred_interrupted',
                    },
                }
            )
            continue

        normalized.append(event)

    return normalized


def _replay_terminal_dedupe_key(event: dict) -> tuple[str, str | None] | None:
    """Key for replay dedupe: parent stream vs each sub-agent share task_id but differ by spawn_id."""
    task_id = event.get('task_id')
    if task_id is None:
        return None
    spawn_id = event.get('spawn_id')
    if spawn_id is not None:
        spawn_id = str(spawn_id)
    return (str(task_id), spawn_id)


def _dedupe_replayed_terminal_events(events: list[dict]) -> list[dict]:
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
    deduped: list[dict] = []
    saw_response_by_key: dict[tuple[str, str | None], bool] = {}

    for event in events:
        dedupe_key = _replay_terminal_dedupe_key(event)
        event_type = str(event.get('type') or '')
        if (
            dedupe_key is not None
            and event_type in {'run_result', 'finish'}
            and saw_response_by_key.get(dedupe_key, False)
        ):
            continue

        deduped.append(event)

        if dedupe_key is not None and _should_emit_event_to_sse(event):
            if event_type == 'response':
                saw_response_by_key[dedupe_key] = True
            elif event_type in {'run_result', 'finish'}:
                saw_response_by_key.setdefault(dedupe_key, False)

    return deduped


def _inject_elapsed_for_history(events: list[dict]) -> list[dict]:
    """为历史事件按 task_id 补全 stream_started_at、elapsed_ms，便于刷新后前端仍能展示耗时。"""
    task_start_ms: dict[str, int] = {}
    for ev in events:
        tid = ev.get('task_id')
        t_ms = ev.get('created_at_ms')
        if tid is not None and t_ms is not None:
            if tid not in task_start_ms or t_ms < task_start_ms[tid]:
                task_start_ms[tid] = t_ms
    out = []
    for ev in events:
        ev = dict(ev)
        tid = ev.get('task_id')
        t_ms = ev.get('created_at_ms')
        if tid and t_ms is not None and tid in task_start_ms:
            start = task_start_ms[tid]
            ev['stream_started_at'] = start
            ev['elapsed_ms'] = t_ms - start
        out.append(ev)
    return out
