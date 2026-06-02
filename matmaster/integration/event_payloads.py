"""Public-facing event payload transformations.

Shared by PersistenceHandler and SSEHandler to normalize internal
bus events into the frontend SSE / persistence contract.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_public_source(source: object) -> str:
    """Collapse internal source labels to the public SSE set."""
    raw = str(source or '').strip()
    if raw in {'User', 'System'}:
        return raw
    # Preserve MatMaster:subtype prefix for sub-agent events
    if raw.startswith('MatMaster:'):
        return raw
    return 'MatMaster'


def _flatten_bohrium_content(raw_payload: object) -> object:
    """Unwrap Bohrium callback payloads into the frontend-facing content shape."""
    if not isinstance(raw_payload, dict):
        return raw_payload

    nested = raw_payload.get('content')
    extras = {
        key: value
        for key, value in raw_payload.items()
        if key not in {'content', 'type'}
    }

    if isinstance(nested, dict):
        content: dict[str, Any] = {**nested, **extras}
    elif nested is None:
        content = extras
    else:
        content = {'message': nested, **extras}

    event_type = raw_payload.get('type')
    if event_type is not None and 'event_type' not in content:
        content['event_type'] = event_type

    return content


_CONTENT_META_KEYS = frozenset({'type', 'source', 'timestamp'})
_RESPONSE_USAGE_KEYS = (
    'turn_index',
    'stream_id',
    'turn_usage',
    'total_usage',
    'usage_vendor',
)
_MODEL_IDENTITY_KEYS = (
    'model',
    'model_profile',
    'model_route',
)
# Envelope metadata lifted to the top level for structured events. stream_state
# / stream_id only exist on response & thought (which take the full-passthrough
# branch), so timestamp is the only relevant key for structured payloads.
_ENVELOPE_TOP_LEVEL_KEYS = ('timestamp',)
# Fields that must never be lifted to the SSE top level. usage_vendor_by_turn is
# per-turn vendor detail consumed only by the in-process drain (eval / devshell)
# via the event object; the public payload carries the aggregated ``usage`` only.
_TOP_LEVEL_DENYLIST = frozenset({'usage_vendor_by_turn'})
# Event types whose raw fields are lifted to the SSE top level instead of being
# confined to ``content``. ``normalize_response_sse_payload`` collapses response
# / thought content to a string, so their usage / model / stream fields must
# live at the top level. Must stay in sync with the set gating that normalizer.
_STRUCTURAL_PASSTHROUGH_EVENT_TYPES = frozenset({'response', 'thought'})
# ``finish`` is retained only for the #4 frontend migration hold. Backend event
# models and active consumers no longer produce or accept it as a live terminal
# event type.
_TERMINAL_EVENT_TYPES = frozenset({'run_result', 'finish'})


def _copy_nonempty_keys(
    out: dict[str, Any],
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != {} and value != "":
            out[key] = value


def _response_public_content(payload: dict[str, Any]) -> object | None:
    content = payload.get('content')
    has_usage = bool(payload.get('turn_usage') or payload.get('total_usage'))
    has_model_identity = any(payload.get(key) for key in _MODEL_IDENTITY_KEYS)
    if not has_usage and not has_model_identity:
        return content

    out: dict[str, Any] = {'content': content or ''}
    _copy_nonempty_keys(out, payload, _RESPONSE_USAGE_KEYS)
    _copy_nonempty_keys(out, payload, _MODEL_IDENTITY_KEYS)
    return out


def _thought_public_content(payload: dict[str, Any]) -> object | None:
    content = payload.get('content')
    if not any(payload.get(key) for key in _MODEL_IDENTITY_KEYS):
        return content
    out: dict[str, Any] = {'content': content or ''}
    _copy_nonempty_keys(out, payload, _MODEL_IDENTITY_KEYS)
    return out


def normalize_response_sse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event_type = payload.get('type')
    if event_type not in _STRUCTURAL_PASSTHROUGH_EVENT_TYPES:
        return payload

    content = payload.get('content')
    if not isinstance(content, dict) or 'content' not in content:
        return payload

    normalized = dict(payload)
    normalized['content'] = str(content.get('content') or '')
    keys = (
        (*_RESPONSE_USAGE_KEYS, *_MODEL_IDENTITY_KEYS)
        if event_type == 'response'
        else _MODEL_IDENTITY_KEYS
    )
    for key in keys:
        if key in content and content.get(key) is not None:
            normalized[key] = content[key]
    return normalized


def normalize_replayed_terminal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Lift persisted run_result/finish content fields back to the live SSE shape.

    The live path lifts these fields to the top level via
    ``_carry_top_level_fields``, but persistence stores only the ``content`` dict
    produced by ``_public_content_for_event``, so replay reconstructs the
    top-level shape here. Counterpart of ``normalize_response_sse_payload`` for
    terminal event payloads.
    """
    if payload.get('type') not in _TERMINAL_EVENT_TYPES:
        return payload

    content = payload.get('content')
    if not isinstance(content, dict):
        return payload

    normalized = dict(payload)
    normalized['final_content'] = content.get('content') or ''
    for key in ('status', 'reason', 'finish_detail', 'num_turns', 'usage', *_MODEL_IDENTITY_KEYS):
        if content.get(key) is not None:
            normalized[key] = content[key]
    return normalized


def _carry_top_level_fields(
    out: dict[str, Any],
    raw: dict[str, Any],
    event_type: str,
    content: object,
) -> None:
    """Lift remaining raw fields to the top level without duplicating content.

    Structured events keep their payload in ``content`` only; the top level
    carries envelope metadata. The full-passthrough branch instead lifts every
    raw field for response / thought plus any event whose ``content`` is
    non-dict (tool_progress, stream_closed, unknown passthrough), which keeps
    its structured identifiers at the top level.
    """
    if (
        event_type in _STRUCTURAL_PASSTHROUGH_EVENT_TYPES
        or not isinstance(content, dict)
    ):
        for key, value in raw.items():
            if key in _TOP_LEVEL_DENYLIST:
                continue
            if key in _MODEL_IDENTITY_KEYS and value is None:
                continue
            if key not in out:
                out[key] = value
        return
    for key in _ENVELOPE_TOP_LEVEL_KEYS:
        value = raw.get(key)
        if value is not None and key not in out:
            out[key] = value


def build_public_sse_payload_from_bus_dump(
    raw: dict[str, Any],
    *,
    session_id: str,
    task_id: str,
    invocation_id: str | None,
    spawn_id: Any,
) -> dict[str, Any]:
    """从 BusEvent.model_dump 组装对前端的 SSE payload。

    顶层键顺序与 ``chat_events_table.get_session_events`` 回放一致：
    source, type, content, session_id, task_id, invocation_id（若有）, spawn_id。
    结构化事件（tool_result / run_result / error / mcp_* 等）的业务字段只放在
    ``content`` 内，顶层仅捎带信封元数据（timestamp）；response / thought 以及
    content 非 dict 的事件（tool_progress 等）保持全量上提，详见
    ``_carry_top_level_fields``。
    """
    event_type = str(raw.get('type', ''))
    content = _public_content_for_event(event_type, raw)
    source = _normalize_public_source(raw.get('source'))

    out: dict[str, Any] = {
        'source': source,
        'type': raw.get('type'),
    }
    if content is not None:
        out['content'] = content
    elif 'content' in raw:
        out['content'] = raw['content']

    out['session_id'] = session_id
    out['task_id'] = task_id
    if invocation_id is not None:
        out['invocation_id'] = invocation_id
    out['spawn_id'] = spawn_id

    _carry_top_level_fields(out, raw, event_type, content)
    return normalize_response_sse_payload(out)


def _public_content_for_event(
    event_type: str, payload: dict[str, Any]
) -> object | None:
    """Adapt internal event payloads to the frontend SSE contract."""
    if event_type == 'response':
        return _response_public_content(payload)

    if event_type == 'thought':
        return _thought_public_content(payload)

    if event_type == 'tool_call':
        call_id = payload.get('call_id')
        return {
            'id': call_id,
            'call_id': call_id,
            'name': payload.get('tool_name'),
            'args': payload.get('arguments') or {},
        }

    if event_type == 'tool_result':
        call_id = payload.get('call_id')
        out: dict[str, Any] = {
            'id': call_id,
            'call_id': call_id,
            'name': payload.get('tool_name'),
            'result': payload.get('result'),
            'status': payload.get('status', 'success'),
            'info': payload.get('info') or payload.get('payload') or {},
        }
        if payload.get('turn_usage'):
            if payload.get('turn_index') is not None:
                out['turn_index'] = payload['turn_index']
            out['turn_usage'] = payload['turn_usage']
            out['total_usage'] = payload.get('total_usage', {})
        return out

    if event_type == 'error':
        return {
            'message': payload.get('message'),
            'traceback': payload.get('traceback'),
        }

    if event_type == 'workspace_upload_error':
        return {'message': payload.get('message')}

    if event_type == 'bohrium_node':
        return _flatten_bohrium_content(payload.get('payload'))

    if event_type == 'mcp_server_status':
        detail = payload.get('detail')
        content = {
            'server_name': payload.get('server_name'),
            'transport': payload.get('transport'),
            'phase': payload.get('phase'),
        }
        if isinstance(detail, dict):
            content.update(detail)
        return content

    if event_type == 'mcp_connect':
        return {
            'phase': payload.get('phase'),
            'message': payload.get('message'),
            'elapsed_ms': payload.get('elapsed_ms'),
            'error': payload.get('error'),
        }

    if event_type == 'compaction':
        content = {
            'compaction_id': payload.get('compaction_id'),
            'status': payload.get('status'),
            'phase': payload.get('phase'),
        }
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
        return content

    if event_type == 'response_figures':
        return {'figures': payload.get('figures') or []}

    if event_type in _TERMINAL_EVENT_TYPES:
        content = {
            'content': payload.get('final_content') or '',
            'status': payload.get('status'),
            'reason': payload.get('reason'),
        }
        if payload.get('finish_detail') is not None:
            content['finish_detail'] = payload['finish_detail']
        if payload.get('num_turns') is not None:
            content['num_turns'] = payload['num_turns']
        if payload.get('usage'):
            content['usage'] = payload['usage']
        # usage_vendor_by_turn (per-turn vendor detail) is intentionally NOT
        # projected into the public payload: it is consumed only by the
        # in-process drain (eval / devshell) via the event object, never by the
        # frontend, so keeping it here would bloat SSE / persistence.
        return content

    if event_type == 'assistant_state':
        content: dict[str, Any] = {'state': payload.get('state')}
        if payload.get('finish_detail') is not None:
            content['finish_detail'] = payload['finish_detail']
        if payload.get('turn_usage'):
            if payload.get('turn_index') is not None:
                content['turn_index'] = payload['turn_index']
            content['turn_usage'] = payload['turn_usage']
            content['total_usage'] = payload.get('total_usage', {})
        _copy_nonempty_keys(content, payload, _MODEL_IDENTITY_KEYS)
        return content

    if event_type == 'checkpoint':
        return {'turn_index': payload.get('turn_index')}

    if event_type == 'skill_hit':
        return {'skill_name': payload.get('skill_name')}

    if event_type == 'cancelled':
        return {'reason': payload.get('reason', '')}

    if event_type == 'ask_question':
        return {
            'request_id': payload.get('request_id'),
            'questions': payload.get('questions') or [],
            'metadata': payload.get('metadata') or {},
            'origin': payload.get('origin'),
            'preview_format': payload.get('preview_format', 'markdown'),
        }

    if event_type == 'ask_question_reply':
        return {
            'request_id': payload.get('request_id'),
            'answers': payload.get('answers') or {},
            'annotations': payload.get('annotations') or {},
        }

    if event_type == 'ask_question_timeout':
        return {
            'request_id': payload.get('request_id'),
            'questions': payload.get('questions') or [],
            'reason': payload.get('reason', 'timeout'),
        }

    if event_type == 'exp_run':
        return {'exp_name': payload.get('exp_name')}

    raw_content = payload.get('content')
    if raw_content is not None:
        return raw_content

    extracted = {
        key: value for key, value in payload.items() if key not in _CONTENT_META_KEYS
    }
    if extracted:
        logger.warning(
            'No explicit content mapping for event type=%s, using extracted fields',
            event_type,
        )
        return extracted

    return None
