"""HTTP REST routes for the MatMaster web service."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, Response

from . import remote_workspace, state
from .models import ChatRequest, RenameRequest
from .paths import (
    _get_run_id_web,
    _get_run_workspace_path,
    _list_state_ids,
    _list_workspace_ids,
    _resolve_session_workspace,
    _runs_dir,
)

router = APIRouter()


@router.get('/')
def root():
    """API only. Use http://localhost:3000 for the dashboard."""
    return {
        'service': 'MatMaster Local Web (dev)',
        'role': 'local_dev',
        'not_production_api': True,
        'message': (
            'Local MatMaster Web backend for debugging only. '
            'Dashboard at http://localhost:3000. '
            'Production platform API: repo root app.py + src/.'
        ),
        'docs': '/docs',
        'openapi': '/openapi.json',
        'ws_chat': '/ws/chat',
    }


@router.get('/info')
def info():
    """Service info and links (for API users)."""
    return {
        'service': 'MatMaster Local Web (dev)',
        'role': 'local_dev',
        'not_production_api': True,
        'dashboard': 'http://localhost:3000',
        'docs': '/docs',
        'openapi': '/openapi.json',
        'ws_chat': 'ws://localhost:50001/ws/chat',
        'api_start': '/api/start',
        'api_share': '/api/share/{session_id}',
    }


@router.post('/api/start')
async def start_task(req: ChatRequest):
    """Optional: signal ready and return session id."""
    if state.SESSION_ID_DEMO not in state.SESSIONS:
        state.SESSIONS[state.SESSION_ID_DEMO] = {
            'history': [],
            'last_task_id': None,
        }
    return {'status': 'ready', 'session_id': state.SESSION_ID_DEMO}


@router.get('/api/sessions')
def list_sessions():
    """List session ids (in-memory + disk workspaces)."""
    disk_ids = _list_workspace_ids()
    state_ids = _list_state_ids()
    all_disk_ids = list(dict.fromkeys(disk_ids + state_ids))
    in_memory = list(state.SESSIONS.keys())
    disk_only = [wid for wid in all_disk_ids if wid not in state.SESSIONS]
    all_ids = in_memory + disk_only
    sessions = []
    for sid in all_ids:
        data = state.SESSIONS.get(sid)
        history_length = len(data.get('history', [])) if data else 0
        sessions.append({'id': sid, 'history_length': history_length})
    return {'sessions': sessions}


@router.get('/api/sessions/{session_id}/history')
def get_session_history(session_id: str):
    """Return session history (for loading when switching session)."""
    data = state.SESSIONS.get(session_id)
    if not data:
        return []
    return data.get('history', [])


@router.get('/api/sessions/{session_id}/run_info')
def get_session_run_info(session_id: str):
    """Return run_id and task_ids for this session."""
    data = state.SESSIONS.get(session_id)
    if data:
        last_task_id = data.get('last_task_id')
        return {
            'run_id': _get_run_id_web(),
            'last_task_id': last_task_id,
            'task_ids': [last_task_id] if last_task_id else [],
        }
    base = _get_run_workspace_path(
        _get_run_id_web(), task_id=session_id, session_id=session_id
    )
    if base and base.is_dir():
        return {
            'run_id': _get_run_id_web(),
            'last_task_id': session_id,
            'task_ids': [session_id],
        }
    return {'run_id': _get_run_id_web(), 'last_task_id': None, 'task_ids': []}


@router.get('/api/sessions/{session_id}/files')
def list_session_files(session_id: str, path: str = ''):
    """List files under this session's workspace."""
    if remote_workspace._is_remote_session():
        ws = remote_workspace._remote_workspace()
        target = f"{ws.rstrip('/')}/{path}" if path else ws
        if not remote_workspace._remote_is_dir(target):
            raise HTTPException(status_code=404, detail='Path not found')
        entries = remote_workspace._remote_list_dir(target)
        if path:
            for e in entries:
                e['path'] = f"{path.rstrip('/')}/{e['name']}"
        return {
            'run_id': _get_run_id_web(),
            'path': path or '.',
            'entries': entries,
            'workspace_root': ws,
            'task_id': session_id,
        }

    try:
        base, task_id = _resolve_session_workspace(session_id, create=True)
    except HTTPException:
        return {
            'run_id': _get_run_id_web(),
            'path': path or '.',
            'entries': [],
            'workspace_root': None,
            'task_id': None,
        }
    target = (base / path).resolve() if path else base
    if not target.is_dir():
        raise HTTPException(status_code=404, detail='Path not found')
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail='Path outside workspace')
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        rel = p.relative_to(base)
        entries.append(
            {
                'name': p.name,
                'path': str(rel).replace('\\', '/'),
                'dir': p.is_dir(),
            }
        )
    return {
        'run_id': _get_run_id_web(),
        'path': path or '.',
        'entries': entries,
        'workspace_root': str(base) if base else None,
        'task_id': task_id,
    }


@router.get('/api/sessions/{session_id}/files/content')
def get_session_file_content(session_id: str, path: str):
    """Serve file content for display or download."""
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail='path is required')

    if remote_workspace._is_remote_session():
        ws = remote_workspace._remote_workspace()
        remote_path = f"{ws.rstrip('/')}/{path.strip()}"
        if not remote_workspace._remote_path_exists(remote_path):
            raise HTTPException(status_code=404, detail='File not found')
        if remote_workspace._remote_is_dir(remote_path):
            raise HTTPException(status_code=400, detail='Path is a directory')
        data = remote_workspace._remote_read_file(remote_path)
        media_type, _ = mimetypes.guess_type(path.strip(), strict=False)
        filename = path.strip().rsplit('/', 1)[-1]
        return Response(
            content=data,
            media_type=media_type or 'application/octet-stream',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )

    base, _ = _resolve_session_workspace(session_id, create=False)
    target = (base / path.strip()).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail='Path outside workspace')
    if not target.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if target.is_dir():
        raise HTTPException(status_code=400, detail='Path is a directory')
    media_type, _ = mimetypes.guess_type(str(target), strict=False)
    return FileResponse(
        path=str(target),
        media_type=media_type or 'application/octet-stream',
        filename=target.name,
    )


@router.post('/api/sessions/{session_id}/files/upload')
async def upload_session_file(
    session_id: str, file: UploadFile = File(...), path: str = Form('')
):
    """Upload a file into the session workspace."""
    if not file.filename:
        raise HTTPException(status_code=400, detail='Filename is required')

    if remote_workspace._is_remote_session():
        ws = remote_workspace._remote_workspace()
        target_dir = f"{ws.rstrip('/')}/{path}" if path else ws
        remote_dest = f"{target_dir.rstrip('/')}/{file.filename}"
        if remote_workspace._remote_path_exists(remote_dest):
            raise HTTPException(status_code=409, detail='File already exists')
        data = await file.read()
        remote_workspace._remote_write_file(remote_dest, data)
        rel = f"{path.rstrip('/')}/{file.filename}" if path else file.filename
        return {'status': 'ok', 'path': rel}

    base, _ = _resolve_session_workspace(session_id, create=True)
    target_dir = (base / path).resolve() if path else base
    try:
        target_dir.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail='Path outside workspace')
    if not target_dir.is_dir():
        raise HTTPException(status_code=404, detail='Target directory not found')
    dest = (target_dir / file.filename).resolve()
    try:
        dest.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail='Path outside workspace')
    if dest.exists():
        raise HTTPException(status_code=409, detail='File already exists')
    with dest.open('wb') as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return {'status': 'ok', 'path': str(dest.relative_to(base)).replace('\\', '/')}


@router.put('/api/sessions/{session_id}/files/rename')
def rename_session_file(session_id: str, req: RenameRequest):
    """Rename a file or directory within the session workspace."""
    if not req.path or not req.path.strip():
        raise HTTPException(status_code=400, detail='path is required')
    if not req.new_name or not req.new_name.strip():
        raise HTTPException(status_code=400, detail='new_name is required')

    if remote_workspace._is_remote_session():
        ws = remote_workspace._remote_workspace()
        old_path = f"{ws.rstrip('/')}/{req.path.strip()}"
        if not remote_workspace._remote_path_exists(old_path):
            raise HTTPException(status_code=404, detail='Path not found')
        new_name = req.new_name.strip().rsplit('/', 1)[-1]
        if not new_name or new_name in {'.', '..'}:
            raise HTTPException(status_code=400, detail='Invalid new_name')
        parent = old_path.rsplit('/', 1)[0]
        new_path = f"{parent}/{new_name}"
        if remote_workspace._remote_path_exists(new_path):
            raise HTTPException(status_code=409, detail='Target already exists')
        remote_workspace._remote_rename(old_path, new_path)
        rel = (
            f"{req.path.strip().rsplit('/', 1)[0]}/{new_name}"
            if '/' in req.path.strip()
            else new_name
        )
        return {'status': 'ok', 'path': rel}

    base, _ = _resolve_session_workspace(session_id, create=False)
    target = (base / req.path.strip()).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail='Path outside workspace')
    if not target.exists():
        raise HTTPException(status_code=404, detail='Path not found')
    new_name = Path(req.new_name.strip()).name
    if not new_name or new_name in {'.', '..'}:
        raise HTTPException(status_code=400, detail='Invalid new_name')
    dest = target.with_name(new_name).resolve()
    try:
        dest.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail='Path outside workspace')
    if dest.exists():
        raise HTTPException(status_code=409, detail='Target already exists')
    target.rename(dest)
    return {'status': 'ok', 'path': str(dest.relative_to(base)).replace('\\', '/')}


@router.get('/api/share/{session_id}')
def get_share_data(session_id: str):
    """Return session history for read-only share view."""
    data = state.SESSIONS.get(session_id)
    if not data:
        raise HTTPException(status_code=404, detail='Session not found')
    return data['history']


@router.get('/api/runs')
def list_runs():
    """List run directories (mat_master_* under runs/)."""
    runs_list: list[dict] = []
    rd = _runs_dir()
    if rd.is_dir():
        for p in sorted(rd.iterdir(), key=lambda x: x.name, reverse=True):
            if p.is_dir() and p.name.startswith('mat_master_'):
                runs_list.append({'id': p.name, 'label': p.name})
    if not runs_list:
        runs_list.append(
            {'id': 'mat_master_web', 'label': 'mat_master_web (created on first run)'}
        )
    return {'runs': runs_list}


@router.get('/api/runs/{run_id}/files')
def list_run_files(run_id: str, path: str = '', task_id: str | None = None):
    """List files under a run's workspace."""
    base = _get_run_workspace_path(run_id, task_id=task_id)
    if not base or not base.is_dir():
        raise HTTPException(status_code=404, detail='Run not found')
    target = (base / path).resolve() if path else base
    if not target.is_dir():
        raise HTTPException(status_code=404, detail='Path not found')
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail='Path outside workspace')
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        rel = p.relative_to(base)
        entries.append(
            {
                'name': p.name,
                'path': str(rel).replace('\\', '/'),
                'dir': p.is_dir(),
            }
        )
    return {'run_id': run_id, 'path': path or '.', 'entries': entries}
