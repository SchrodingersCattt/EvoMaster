"""Session history/meta persistence (.state/) for the MatMaster web service."""

from __future__ import annotations

import json

from . import state


def _persist_history_event(session_id: str, payload: dict) -> None:
    """Append one event to .state/<session_id>/history.jsonl."""
    try:
        from .paths import _session_state_dir

        d = _session_state_dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        with (d / 'history.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(payload, ensure_ascii=False) + '\n')
    except Exception:
        pass


def _heal_orphaned_tool_calls(session_id: str) -> None:
    """Scan history for tool_call events with no matching tool_result; persist synthetic results."""
    session = state.SESSIONS.get(session_id)
    if not session:
        return
    history: list[dict] = session.get('history', [])

    result_ids: set[str] = set()
    for ev in history:
        if ev.get('type') == 'tool_result':
            c = ev.get('content')
            if isinstance(c, dict):
                rid = str(c.get('id') or '')
                if rid:
                    result_ids.add(rid)

    orphaned: list[dict] = []
    for ev in history:
        if ev.get('type') == 'tool_call':
            c = ev.get('content')
            if isinstance(c, dict):
                call_id = str(c.get('id') or '')
                name = str(c.get('name') or 'unknown')
                if call_id and call_id not in result_ids:
                    orphaned.append({'id': call_id, 'name': name})

    if not orphaned:
        return

    state.logger.warning(
        '_heal_orphaned_tool_calls: session_id=%s found %d orphaned tool_call(s); '
        'persisting placeholder tool_result(s): %s',
        session_id,
        len(orphaned),
        [o['id'] for o in orphaned],
    )

    for o in orphaned:
        payload = {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {
                'id': o['id'],
                'name': o['name'],
                'result': {
                    'status': 'interrupted',
                    'observation': (
                        'Tool call was interrupted before completion; '
                        'result is unavailable. Please retry if needed.'
                    ),
                },
            },
            'session_id': session_id,
        }
        history.append(payload)
        _persist_history_event(session_id, payload)


def _persist_meta(session_id: str, data: dict) -> None:
    """Write .state/<session_id>/meta.json (last_task_id etc)."""
    try:
        from .paths import _session_state_dir

        d = _session_state_dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        meta = {'last_task_id': data.get('last_task_id')}
        (d / 'meta.json').write_text(
            json.dumps(meta, ensure_ascii=False), encoding='utf-8'
        )
    except Exception:
        pass


def _load_persisted_sessions() -> None:
    """On startup: read .state/*/history.jsonl + meta.json into SESSIONS."""
    from .paths import _state_dir

    state_root = _state_dir()
    if not state_root.is_dir():
        return
    for sid_dir in state_root.iterdir():
        if not sid_dir.is_dir():
            continue
        sid = sid_dir.name
        history = []
        history_file = sid_dir / 'history.jsonl'
        if history_file.exists():
            for line in history_file.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except Exception:
                        pass
        meta = {}
        meta_file = sid_dir / 'meta.json'
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding='utf-8'))
            except Exception:
                pass
        state.SESSIONS[sid] = {
            'history': history,
            'last_task_id': meta.get('last_task_id'),
        }
