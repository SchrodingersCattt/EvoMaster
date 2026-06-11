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
from matmaster.response_text import normalize_visible_response_text
from matmaster.utils.event_source import normalize_event_source

# 历史回放时一定会被丢弃、不推送给前端的事件类型。
# 其中 history_checkpoint（整段模型上下文快照 base_messages）与 assistant_state
# （完整 tool_calls）通常是单表里体积最大的行，回放时读出再丢弃纯属浪费 DB IO/CPU。
# 该集合是单一来源：既用于回放后的最终守卫过滤，也用于 get_session_events 的 SQL 层裁剪。
REPLAY_DISCARDED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        'log_line',
        'assistant_state',
        'skill_hit',
        'user_turn_context',
        'compact_boundary',
        'history_checkpoint',
    }
)


def _should_emit_event_to_sse(event: dict) -> bool:
    """Filter persisted events for history replay SSE.

    NOTE: This filter is intentionally simpler than
    matmaster.integration.sse_handler.SSEHandler._should_skip().
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
    return event.get('type') not in REPLAY_DISCARDED_EVENT_TYPES


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


def _replayed_response_text(event: dict) -> str | None:
    """Visible text of a persisted replayed response, or None when absent.

    Persisted response content is either a dict with a ``content`` key (usage /
    model identity present) or the raw text itself.
    """
    content = event.get('content')
    raw = content.get('content') if isinstance(content, dict) else content
    if raw is None or isinstance(raw, (dict, list)):
        return None
    return normalize_visible_response_text(str(raw))


def _replayed_run_result_text(event: dict) -> str | None:
    """Final text of a persisted replayed run_result, or None when absent.

    Cancelled / max_turns terminals persist ``content: ''``; the empty string
    normalizes to None so such terminals never match any response.
    """
    content = event.get('content')
    if not isinstance(content, dict):
        return None
    raw = content.get('content')
    if raw is None or isinstance(raw, (dict, list)):
        return None
    return normalize_visible_response_text(str(raw))


def _dedupe_replayed_terminal_events(events: list[dict]) -> list[dict]:
    """Hide the replayed response that duplicates a run_result's final answer.

    `run_result` is the canonical business terminal event, so replay uses it as
    the final answer carrier. Within the same `(task_id, spawn_id)` stream, the
    `response` whose visible text equals the terminal's final text is the
    duplicated final-answer copy and is hidden; earlier intermediate responses
    (e.g. text emitted alongside tool calls) are kept.

    Matching is by normalized text only — not turn index — because the rescued
    natural-finish path carries `final_content` from an earlier turn. Each
    terminal removes at most the last matching response so anomalous duplicate
    writes are not over-deleted.

    `response_figures` is replayable answer metadata. It may appear before the
    hidden response, between response chunks, or before the terminal event. It is
    kept in replay output because it does not duplicate answer text.

    Dedupe is keyed by (task_id, spawn_id) so a sub-agent `response` does not
    get suppressed by the parent stream's `run_result`.
    """
    final_texts: dict[tuple[str, str | None], list[str]] = {}
    for event in events:
        dedupe_key = _replay_terminal_dedupe_key(event)
        if (
            dedupe_key is None
            or str(event.get('type') or '') != 'run_result'
            or not _should_emit_event_to_sse(event)
        ):
            continue
        final_text = _replayed_run_result_text(event)
        if final_text is not None:
            final_texts.setdefault(dedupe_key, []).append(final_text)

    response_candidates: dict[tuple[str, str | None], list[tuple[int, str | None]]] = {}
    for index, event in enumerate(events):
        dedupe_key = _replay_terminal_dedupe_key(event)
        if (
            dedupe_key is None
            or dedupe_key not in final_texts
            or str(event.get('type') or '') != 'response'
            or not _should_emit_event_to_sse(event)
        ):
            continue
        response_candidates.setdefault(dedupe_key, []).append(
            (index, _replayed_response_text(event))
        )

    removed: set[int] = set()
    for dedupe_key, texts in final_texts.items():
        candidates = response_candidates.get(dedupe_key, [])
        for final_text in texts:
            for index, response_text in reversed(candidates):
                if index in removed or response_text != final_text:
                    continue
                removed.add(index)
                break

    return [event for index, event in enumerate(events) if index not in removed]


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
