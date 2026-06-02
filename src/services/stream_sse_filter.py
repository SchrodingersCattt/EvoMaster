"""SSE event filter + normalization helpers.

The helpers were named with a leading underscore in the original
module; we keep the public API underscored too to avoid disturbing
callers that import these internals from ``stream_service``.
"""

from __future__ import annotations

from matmaster.integration.event_payloads import (
    normalize_replayed_terminal_payload,
    normalize_response_sse_payload,
)
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
    replay_event = normalize_replayed_terminal_payload(replay_event)
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
    """Hide replayed response when the same task has a replayable terminal event.

    `run_result` is the canonical business terminal event, so replay should use
    it as the final answer carrier when it exists. Persisted complete
    `response` segments are hidden for the same `(task_id, spawn_id)` stream to
    avoid duplicating the final answer after reconnect.

    `response_figures` is replayable answer metadata. It may appear before the
    hidden response, between response chunks, or before the terminal event. It is
    kept in replay output because it does not duplicate answer text.

    Dedupe is keyed by (task_id, spawn_id) so a sub-agent `response` does not
    get suppressed by the parent stream's `run_result`.
    """
    terminal_keys: set[tuple[str, str | None]] = set()
    for event in events:
        dedupe_key = _replay_terminal_dedupe_key(event)
        event_type = str(event.get('type') or '')
        if (
            dedupe_key is not None
            and event_type in {'run_result', 'finish'}
            and _should_emit_event_to_sse(event)
        ):
            terminal_keys.add(dedupe_key)

    deduped: list[dict] = []

    for event in events:
        dedupe_key = _replay_terminal_dedupe_key(event)
        event_type = str(event.get('type') or '')
        if (
            dedupe_key is not None
            and event_type == 'response'
            and dedupe_key in terminal_keys
            and _should_emit_event_to_sse(event)
        ):
            continue

        deduped.append(event)

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
