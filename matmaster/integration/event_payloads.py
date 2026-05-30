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
    if event_type not in {'response', 'thought'}:
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
    source, type, content, session_id, task_id, invocation_id（若有）, spawn_id，
    再追加 model_dump 中其余字段（如 timestamp、stream_state）。
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

    for key, value in raw.items():
        if key not in out:
            out[key] = value
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
        return content

    if event_type == 'response_figures':
        return {'figures': payload.get('figures') or []}

    if event_type in ('run_result', 'finish'):
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
        if payload.get('usage_vendor_by_turn'):
            content['usage_vendor_by_turn'] = payload['usage_vendor_by_turn']
        _copy_nonempty_keys(content, payload, _MODEL_IDENTITY_KEYS)
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
