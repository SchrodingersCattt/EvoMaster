"""
MatMaster web service: FastAPI + WebSocket for streaming agent runs.
Run from project root or playground/mat_master/service with PYTHONPATH including project root.

Tools (MCP, skills, etc.) are loaded once at startup so the first user message does not wait.
"""

import asyncio
import importlib
import logging
import mimetypes
import os
import queue
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

# Ensure project root is on path (service is at playground/mat_master/service)
_project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Register mat_master playground (same as run.py auto_import_playgrounds), so get_playground_class returns MatMasterPlayground
importlib.import_module('playground.mat_master.core.playground')


logger = logging.getLogger(__name__)

# Pre-initialized playground (tools loaded at startup). Reused per run with set_run_dir(task_id).
# Single worker so only one run at a time and run_dir is correct.
_cached_pg = None
_playground_init_done = threading.Event()
_executor = ThreadPoolExecutor(max_workers=1)


def _init_playground_sync() -> None:
    """Load playground once: config, LLM, session, MCP tools, skills, agent. Run at startup."""
    global _cached_pg
    try:
        from evomaster.core import get_playground_class

        config_path = _project_root / 'configs' / 'mat_master' / 'config.yaml'
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        pg = get_playground_class('mat_master', config_path=config_path)
        run_dir = _runs_dir() / _get_run_id_web()
        run_dir.mkdir(parents=True, exist_ok=True)
        pg.set_run_dir(run_dir)
        pg.setup()
        _cached_pg = pg
        logger.info('Playground (tools, MCP, agent) initialized at startup.')
    except Exception as e:
        logger.exception('Playground init at startup failed: %s', e)
        _cached_pg = None
    finally:
        _playground_init_done.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load tools in a thread so server is ready only after tools are loaded."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init_playground_sync)
    yield
    if _cached_pg is not None:
        s = _get_session()
        if s is not None and hasattr(s, 'close'):
            try:
                s.close()
                logger.info('SSH session closed on shutdown.')
            except Exception:
                pass


app = FastAPI(title='MatMaster Web Service', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

SESSIONS: dict[str, dict] = {}
SESSION_ID_DEMO = 'demo_session'
RUN_ID_WEB = 'mat_master_web'
# Per-session cancel: session_id -> Event, read by agent thread
_run_stop_events: dict[str, threading.Event] = {}
# Pending cancel: session_ids that requested cancel before run registered (race fix)
_pending_cancel: set[str] = set()


class ChatRequest(BaseModel):
    prompt: str
    workspace: str = './workspace'


class RenameRequest(BaseModel):
    path: str
    new_name: str


@app.get('/')
def root():
    """API only. Use http://localhost:3000 for the dashboard."""
    return {
        'service': 'MatMaster Web Service',
        'message': 'API only. Dashboard at http://localhost:3000',
        'docs': '/docs',
        'openapi': '/openapi.json',
        'ws_chat': '/ws/chat',
    }


@app.get('/info')
def info():
    """Service info and links (for API users)."""
    return {
        'service': 'MatMaster Web Service',
        'dashboard': 'http://localhost:3000',
        'docs': '/docs',
        'openapi': '/openapi.json',
        'ws_chat': 'ws://localhost:50001/ws/chat',
        'api_start': '/api/start',
        'api_share': '/api/share/{session_id}',
    }


@app.post('/api/start')
async def start_task(req: ChatRequest):
    """Optional: signal ready and return session id."""
    if SESSION_ID_DEMO not in SESSIONS:
        SESSIONS[SESSION_ID_DEMO] = {
            'history': [],
            'task_ids': [],
            'last_task_id': None,
        }
    return {'status': 'ready', 'session_id': SESSION_ID_DEMO}


@app.get('/api/sessions')
def list_sessions():
    """List session ids (in-memory + 本地 workspaces 目录下的所有文件夹，重启后仍可回溯历史)."""
    disk_ids = _list_workspace_ids()
    in_memory = list(SESSIONS.keys())
    disk_only = [wid for wid in disk_ids if wid not in SESSIONS]
    all_ids = in_memory + disk_only
    sessions = []
    for sid in all_ids:
        data = SESSIONS.get(sid)
        history_length = len(data.get('history', [])) if data else 0
        sessions.append({'id': sid, 'history_length': history_length})
    return {'sessions': sessions}


@app.get('/api/sessions/{session_id}/history')
def get_session_history(session_id: str):
    """Return session history (for loading when switching session)."""
    data = SESSIONS.get(session_id)
    if not data:
        return []
    return data.get('history', [])


@app.get('/api/sessions/{session_id}/run_info')
def get_session_run_info(session_id: str):
    """Return run_id and task_ids for this session (内存无则用磁盘 workspace 目录对应 task_id，便于回溯历史)."""
    data = SESSIONS.get(session_id)
    if data:
        task_ids = data.get('task_ids') or []
        last_task_id = data.get('last_task_id')
        return {
            'run_id': _get_run_id_web(),
            'last_task_id': last_task_id,
            'task_ids': task_ids,
        }
    # 重启后仅存在磁盘的 workspace：用 session_id 作为 task_id 指向 workspaces/<session_id>
    base = _get_run_workspace_path(_get_run_id_web(), task_id=session_id)
    if base and base.is_dir():
        return {
            'run_id': _get_run_id_web(),
            'last_task_id': session_id,
            'task_ids': [session_id],
        }
    return {'run_id': _get_run_id_web(), 'last_task_id': None, 'task_ids': []}


@app.get('/api/sessions/{session_id}/files')
def list_session_files(session_id: str, path: str = ''):
    """List files under this session's workspace.

    For remote sessions (SSH/Docker), lists files on the remote host.
    For local sessions, uses runs/mat_master_web/workspaces/<key>/.
    """
    if _is_remote_session():
        ws = _remote_workspace()
        target = f"{ws.rstrip('/')}/{path}" if path else ws
        if not _remote_is_dir(target):
            raise HTTPException(status_code=404, detail='Path not found')
        entries = _remote_list_dir(target)
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

    # --- local fallback ---
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


@app.get('/api/sessions/{session_id}/files/content')
def get_session_file_content(session_id: str, path: str):
    """Serve file content for display or download.

    For remote sessions, downloads the file via session and returns bytes.
    """
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail='path is required')

    if _is_remote_session():
        ws = _remote_workspace()
        remote_path = f"{ws.rstrip('/')}/{path.strip()}"
        if not _remote_path_exists(remote_path):
            raise HTTPException(status_code=404, detail='File not found')
        if _remote_is_dir(remote_path):
            raise HTTPException(status_code=400, detail='Path is a directory')
        data = _remote_read_file(remote_path)
        media_type, _ = mimetypes.guess_type(path.strip(), strict=False)
        filename = path.strip().rsplit('/', 1)[-1]
        return Response(
            content=data,
            media_type=media_type or 'application/octet-stream',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )

    # --- local fallback ---
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


@app.post('/api/sessions/{session_id}/files/upload')
async def upload_session_file(
    session_id: str, file: UploadFile = File(...), path: str = Form('')
):
    """Upload a file into the session workspace.

    For remote sessions, uploads via session.upload to the remote host.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail='Filename is required')

    if _is_remote_session():
        ws = _remote_workspace()
        target_dir = f"{ws.rstrip('/')}/{path}" if path else ws
        remote_dest = f"{target_dir.rstrip('/')}/{file.filename}"
        if _remote_path_exists(remote_dest):
            raise HTTPException(status_code=409, detail='File already exists')
        data = await file.read()
        _remote_write_file(remote_dest, data)
        rel = f"{path.rstrip('/')}/{file.filename}" if path else file.filename
        return {'status': 'ok', 'path': rel}

    # --- local fallback ---
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


@app.put('/api/sessions/{session_id}/files/rename')
def rename_session_file(session_id: str, req: RenameRequest):
    """Rename a file or directory within the session workspace."""
    if not req.path or not req.path.strip():
        raise HTTPException(status_code=400, detail='path is required')
    if not req.new_name or not req.new_name.strip():
        raise HTTPException(status_code=400, detail='new_name is required')

    if _is_remote_session():
        ws = _remote_workspace()
        old_path = f"{ws.rstrip('/')}/{req.path.strip()}"
        if not _remote_path_exists(old_path):
            raise HTTPException(status_code=404, detail='Path not found')
        new_name = req.new_name.strip().rsplit('/', 1)[-1]
        if not new_name or new_name in {'.', '..'}:
            raise HTTPException(status_code=400, detail='Invalid new_name')
        parent = old_path.rsplit('/', 1)[0]
        new_path = f"{parent}/{new_name}"
        if _remote_path_exists(new_path):
            raise HTTPException(status_code=409, detail='Target already exists')
        _remote_rename(old_path, new_path)
        rel = (
            f"{req.path.strip().rsplit('/', 1)[0]}/{new_name}"
            if '/' in req.path.strip()
            else new_name
        )
        return {'status': 'ok', 'path': rel}

    # --- local fallback ---
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


@app.get('/api/share/{session_id}')
def get_share_data(session_id: str):
    """Return session history for read-only share view."""
    data = SESSIONS.get(session_id)
    if not data:
        raise HTTPException(status_code=404, detail='Session not found')
    return data['history']


def _runs_dir() -> Path:
    """Root directory for run dirs. When MAT_MASTER_RUN_DIR is set, returns its parent."""
    override = os.environ.get('MAT_MASTER_RUN_DIR', '').strip()
    if override:
        return Path(override).expanduser().resolve().parent
    return _project_root / 'runs'


def _get_run_id_web() -> str:
    """Run id used for web mode (path segment and API). When MAT_MASTER_RUN_DIR is set, returns its basename."""
    override = os.environ.get('MAT_MASTER_RUN_DIR', '').strip()
    if override:
        return Path(override).expanduser().resolve().name
    return RUN_ID_WEB


def _workspace_root_override() -> Path | None:
    raw = (os.environ.get('MAT_MASTER_WORKSPACE_ROOT') or '').strip()
    if not raw:
        try:
            config_path = _project_root / 'configs' / 'mat_master' / 'config.yaml'
            if config_path.is_file():
                data = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
                raw = (data.get('mat_master') or {}).get('workspace_root') or ''
        except Exception:
            raw = ''
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (_project_root / p).resolve()
    return p


def _list_workspace_ids() -> list[str]:
    """List all workspace folder names under runs/mat_master_web/workspaces/ (disk-only, so restart后也能回溯历史)."""
    run_path = _runs_dir() / _get_run_id_web()
    workspaces_dir = run_path / 'workspaces'
    if not workspaces_dir.is_dir():
        return []
    pairs = []
    for p in workspaces_dir.iterdir():
        if p.is_dir():
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0
            pairs.append((p.name, mtime))
    pairs.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in pairs]


def _get_run_workspace_path(run_id: str, task_id: str | None = None) -> Path | None:
    """Resolve run_id (and optional task_id) to workspace directory."""
    runs = _runs_dir()
    run_path = runs / run_id
    if not run_path.is_dir():
        return None
    if task_id:
        ws = run_path / 'workspaces' / task_id
        if ws.is_dir():
            return ws
        return None
    ws = run_path / 'workspace'
    if ws.is_dir():
        return ws
    workspaces = run_path / 'workspaces'
    if workspaces.is_dir():
        subs = [p for p in workspaces.iterdir() if p.is_dir()]
        if subs:
            return max(subs, key=lambda p: p.stat().st_mtime)
    return run_path


def _resolve_session_workspace(
    session_id: str, create: bool = True
) -> tuple[Path, str]:
    """Resolve session workspace dir and task_id, optionally creating it."""
    override = _workspace_root_override()
    if override is not None:
        if create:
            override.mkdir(parents=True, exist_ok=True)
        if not override.is_dir():
            raise HTTPException(status_code=404, detail='Workspace root not found')
        return override, 'external'
    run_path = _runs_dir() / _get_run_id_web()
    if not run_path.is_dir():
        raise HTTPException(status_code=404, detail='Run not found')
    data = SESSIONS.get(session_id)
    task_id = (data or {}).get('last_task_id') if data else None
    if task_id is None:
        task_id = session_id
        if create:
            (run_path / 'workspaces' / task_id).mkdir(parents=True, exist_ok=True)
    base = _get_run_workspace_path(_get_run_id_web(), task_id=task_id)
    if not base or not base.is_dir():
        raise HTTPException(status_code=404, detail='Workspace not found')
    return base, task_id


# ---------------------------------------------------------------------------
# Remote-session aware file helpers
# ---------------------------------------------------------------------------


def _get_session():
    """Return the current session from the cached playground (or None)."""
    if _cached_pg is not None and hasattr(_cached_pg, 'session'):
        return _cached_pg.session
    return None


def _is_remote_session() -> bool:
    """True when the playground uses a remote session (SSH / Docker)."""
    s = _get_session()
    if s is None:
        return False
    return 'Local' not in type(s).__name__


def _remote_workspace() -> str:
    """Return the remote workspace root (e.g. ``/workspace``)."""
    s = _get_session()
    if s is None:
        return '/workspace'
    return (
        getattr(getattr(s, 'config', None), 'workspace_path', '/workspace')
        or '/workspace'
    )


def _remote_list_dir(dir_path: str) -> list[dict]:
    """List entries in *dir_path* on the remote session."""
    s = _get_session()
    if s is None:
        return []
    cmd = (
        f"find '{dir_path}' -maxdepth 1 -mindepth 1 "
        f"-printf '%y %f\\n' 2>/dev/null | sort -k2"
    )
    try:
        result = s.exec_bash(cmd)
    except (RuntimeError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Remote session error: {exc}")
    entries = []
    for line in (result.get('stdout') or '').strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        ftype, name = parts
        entries.append({'name': name, 'path': name, 'dir': ftype == 'd'})
    entries.sort(key=lambda e: (not e['dir'], e['name'].lower()))
    return entries


def _remote_read_file(remote_path: str) -> bytes:
    """Download a remote file as bytes."""
    s = _get_session()
    if s is None:
        raise HTTPException(status_code=500, detail='No session available')
    try:
        return s.download(remote_path)
    except (RuntimeError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Remote session error: {exc}")


def _remote_write_file(remote_path: str, data: bytes) -> None:
    """Write bytes to a remote file via upload (binary-safe)."""
    s = _get_session()
    if s is None:
        raise HTTPException(status_code=500, detail='No session available')
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        s.upload(tmp_path, remote_path)
    except (RuntimeError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Remote session error: {exc}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _remote_path_exists(remote_path: str) -> bool:
    s = _get_session()
    if s is None:
        return False
    try:
        return s.path_exists(remote_path)
    except (RuntimeError, TimeoutError, OSError):
        return False


def _remote_is_dir(remote_path: str) -> bool:
    s = _get_session()
    if s is None:
        return False
    try:
        return s.is_directory(remote_path)
    except (RuntimeError, TimeoutError, OSError):
        return False


def _remote_is_file(remote_path: str) -> bool:
    s = _get_session()
    if s is None:
        return False
    try:
        return s.is_file(remote_path)
    except (RuntimeError, TimeoutError, OSError):
        return False


def _remote_rename(old_path: str, new_path: str) -> None:
    s = _get_session()
    if s is None:
        raise HTTPException(status_code=500, detail='No session available')
    try:
        result = s.exec_bash(f"mv '{old_path}' '{new_path}'")
    except (RuntimeError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Remote session error: {exc}")
    if result.get('exit_code', -1) != 0:
        raise HTTPException(
            status_code=500, detail=f"Rename failed: {result.get('stdout', '')}"
        )


@app.get('/api/runs')
def list_runs():
    """List run directories (mat_master_* under runs/, same as run.py)."""
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


@app.get('/api/runs/{run_id}/files')
def list_run_files(run_id: str, path: str = '', task_id: str | None = None):
    """List files under a run's workspace. path is optional subdir; task_id pins to that workspace."""
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


def _planner_ask_and_wait(
    prompt: str,
    send_cb,
    loop: asyncio.AbstractEventLoop,
    reply_queue: queue.Queue,
) -> str:
    """Send planner_ask to client and block indefinitely until a planner_reply arrives.

    This is the fallback input_fn path used when ConfirmationManager is unavailable.
    We never time-out and return 'abort' here; the plan confirmation gate must stay
    open until the human explicitly replies (go / abort / revise).
    """
    payload = {'source': 'Planner', 'type': 'planner_ask', 'content': prompt}
    future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
    try:
        future.result(timeout=5)
    except Exception:
        pass
    # Block indefinitely — no timeout, no implicit abort.
    return reply_queue.get()


def _run_agent_sync(
    session_id: str,
    user_prompt: str,
    send_cb,
    loop: asyncio.AbstractEventLoop,
    stop_event: threading.Event,
    mode: str = 'direct',
    planner_reply_queue: queue.Queue | None = None,
    task_id: str | None = None,
    ask_human_queue: queue.Queue | None = None,
    bohrium_access_key: str | None = None,
    bohrium_project_id: int | None = None,
):
    """Run MatMaster in a thread (direct or planner exp); send_cb streams events. task_id is set by caller."""
    import logging

    logging.basicConfig(level=logging.INFO)
    run_done: threading.Event | None = None
    _msg_seq = 0  # auto-incrementing message id per run

    def event_callback(source: str, event_type: str, content) -> None:
        nonlocal _msg_seq
        _msg_seq += 1
        payload = {
            'msg_id': _msg_seq,
            'source': source,
            'type': event_type,
            'content': content,
            'session_id': session_id,
        }
        if session_id not in SESSIONS:
            SESSIONS[session_id] = {'history': [], 'task_ids': [], 'last_task_id': None}
        if event_type != 'log_line':
            SESSIONS[session_id]['history'].append(payload)
        future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
        try:
            future.result(timeout=5)
        except Exception:
            pass

    try:
        _playground_init_done.wait(timeout=300)
        run_dir = _runs_dir() / _get_run_id_web()
        task_id = task_id or ('ws_' + uuid.uuid4().hex[:8])

        if _cached_pg is not None:
            pg = _cached_pg
            pg.set_run_dir(run_dir, task_id=task_id)
            pg._setup_trajectory_file()
        else:
            from evomaster.core import get_playground_class

            config_path = _project_root / 'configs' / 'mat_master' / 'config.yaml'
            if not config_path.exists():
                raise FileNotFoundError(f"Config not found: {config_path}")
            pg = get_playground_class('mat_master', config_path=config_path)
            pg.set_run_dir(run_dir, task_id=task_id)
            pg.setup()
            pg._setup_trajectory_file()

        run_done = threading.Event()

        mode = (mode or 'direct').strip().lower() or 'direct'
        pg.set_mode(mode)

        if mode == 'planner' and planner_reply_queue is not None:
            pg._planner_input_fn = lambda prompt: _planner_ask_and_wait(
                prompt, send_cb, loop, planner_reply_queue
            )
        pg._planner_output_callback = event_callback

        # Bohrium node lifecycle: create -> wait -> attach SSH -> run -> destroy
        bohrium_node_id = None
        _ssh_attached = False
        access_key = (bohrium_access_key or '').strip()
        if access_key and bohrium_project_id is not None:
            try:
                from src.services.bohrium_node_service import get_bohrium_node_service

                node_svc = get_bohrium_node_service()
                node_info = node_svc.create_node(access_key, int(bohrium_project_id))
                bohrium_node_id = node_info.get('node_id')
                if bohrium_node_id is not None:
                    event_callback(
                        'System',
                        'bohrium_node',
                        {
                            'node_id': bohrium_node_id,
                            'status': 'created',
                            'message': '节点已创建，正在等待就绪...',
                        },
                    )
                    node_info = node_svc.wait_until_ready(access_key, bohrium_node_id)
                    node_ip = node_info.get('ip')
                    node_domain = node_info.get('domain') or ''
                    node_pwd = node_info.get('password')
                    node_user = node_info.get('node_user') or 'root'
                    # Bohrium container nodes expose SSH via domainName, not raw IP
                    ssh_host = node_domain or node_ip
                    event_callback(
                        'System',
                        'bohrium_node',
                        {
                            'node_id': bohrium_node_id,
                            'status': 'ready',
                            'ip': node_ip,
                            'domain': node_domain,
                            'message': 'Bohrium 节点已就绪',
                        },
                    )
                    if ssh_host:
                        pg.attach_ssh_session(
                            host=ssh_host,
                            username=node_user,
                            password=node_pwd,
                            working_dir='/personal/workspace',
                        )
                        _ssh_attached = True
                        logger.info(
                            'SSH session attached to Bohrium node host=%s', ssh_host
                        )
                        event_callback(
                            'System', 'status', f"已连接到 Bohrium 节点 {ssh_host}"
                        )
                        try:
                            pg.sync_skills_to_remote()
                            event_callback(
                                'System', 'status', 'Skills 已同步到远程节点'
                            )
                        except Exception as e:
                            logger.warning(
                                'sync_skills_to_remote failed: %s', e, exc_info=True
                            )
            except Exception as e:
                logger.warning('Auto create Bohrium node failed: %s', e, exc_info=True)
                event_callback(
                    'System',
                    'status',
                    f"自动创建 Bohrium 节点失败: {e}，继续使用当前环境运行",
                )

        base = pg.agent
        config_dict = pg.config.model_dump()
        agents_block = config_dict.get('agents')
        if isinstance(agents_block, dict) and agents_block:
            agent_config = next(iter(agents_block.values()))
        else:
            agent_config = config_dict.get('agent') or {}
        if not isinstance(agent_config, dict):
            agent_config = {}
        system_prompt_file = agent_config.get('system_prompt_file')
        user_prompt_file = agent_config.get('user_prompt_file')
        playground_base = Path(str(pg.config_dir).replace('configs', 'playground'))
        if system_prompt_file:
            p = Path(system_prompt_file)
            if not p.is_absolute():
                system_prompt_file = str((playground_base / p).resolve())
        if user_prompt_file:
            p = Path(user_prompt_file)
            if not p.is_absolute():
                user_prompt_file = str((playground_base / p).resolve())
        prompt_format_kwargs = agent_config.get('prompt_format_kwargs', {})

        from playground.mat_master.service.stream_agent import StreamingMatMasterAgent

        agent = StreamingMatMasterAgent(
            event_callback=event_callback,
            llm=base.llm,
            session=base.session,
            tools=base.tools,
            system_prompt_file=system_prompt_file,
            user_prompt_file=user_prompt_file,
            prompt_format_kwargs=prompt_format_kwargs,
            config=base.config,
            skill_registry=base.skill_registry,
            output_config=base.output_config,
            config_dir=pg.config_dir,
            enable_tools=base.enable_tools,
            enabled_tool_names=getattr(base, 'enabled_tool_names', None),
            direct_max_workers=getattr(base, '_direct_max_workers', 4),
            rate_limit=getattr(base, '_rate_limit', None),
            config_dict=config_dict,
        )
        agent.set_agent_name(getattr(base, '_agent_name', 'default'))
        agent._stop_event = stop_event
        if ask_human_queue is not None:
            agent._ask_human_queue = ask_human_queue
            # Attach unified confirmation manager for planner/ask-human
            try:
                from playground.mat_master.service.confirm import ConfirmationManager

                ah_cfg = (
                    (getattr(pg, 'config', None) or {})
                    .get('mat_master', {})
                    .get('ask_human', {})
                )
                agent._ask_human_config = ah_cfg
                agent._confirm_manager = ConfirmationManager(
                    emitter=event_callback,
                    reply_queue=ask_human_queue,
                    default_timeout_sec=ah_cfg.get('timeout_seconds', 20),
                )
            except Exception:
                pass

        pg.agent = agent
        exp = pg._create_exp()
        exp.set_run_dir(run_dir)
        event_callback('MatMaster', 'exp_run', exp.__class__.__name__)

        exp.run(task_description=user_prompt, task_id=task_id)
        if stop_event.is_set():
            event_callback('System', 'cancelled', 'Task cancelled by user.')
        else:
            event_callback('System', 'finish', 'Done')
    except Exception as e:
        event_callback('System', 'error', str(e))
        raise
    finally:
        # Detach SSH session and restore default session
        if _ssh_attached:
            try:
                pg.detach_session()
                pg._setup_session()
                logger.info('SSH session detached, default session restored')
            except Exception as e:
                logger.warning('Session restore failed: %s', e)
        # Destroy Bohrium node
        if bohrium_node_id is not None:
            try:
                from src.services.bohrium_node_service import get_bohrium_node_service

                get_bohrium_node_service().destroy_node(
                    access_key,
                    int(bohrium_node_id),
                    int(bohrium_project_id or 0),
                )
                logger.info('Bohrium node destroyed node_id=%s', bohrium_node_id)
                event_callback(
                    'System',
                    'bohrium_node',
                    {
                        'node_id': bohrium_node_id,
                        'status': 'destroyed',
                        'message': '节点已销毁',
                    },
                )
            except Exception as e:
                logger.warning('Auto destroy Bohrium node failed: %s', e, exc_info=True)
        if run_done is not None:
            run_done.set()
        _run_stop_events.pop(session_id, None)
        _pending_cancel.discard(session_id)


@app.websocket('/ws/chat')
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_event_loop()
    command_queue: asyncio.Queue = asyncio.Queue()
    planner_reply_queue: queue.Queue = queue.Queue()
    ask_human_queue: queue.Queue = queue.Queue()

    async def send_json(payload: dict):
        await websocket.send_json(payload)

    async def reader_loop():
        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get('type')
                if msg_type == 'planner_reply':
                    content = data.get('content', '')
                    planner_reply_queue.put(content)
                    sid = data.get('session_id') or SESSION_ID_DEMO
                    if sid not in SESSIONS:
                        SESSIONS[sid] = {
                            'history': [],
                            'task_ids': [],
                            'last_task_id': None,
                        }
                    payload = {
                        'source': 'User',
                        'type': 'planner_reply',
                        'content': content,
                        'session_id': sid,
                    }
                    SESSIONS[sid]['history'].append(payload)
                    await send_json(payload)
                elif msg_type == 'ask_human_reply':
                    content = data.get('content', '')
                    ask_human_queue.put(content)
                    sid = data.get('session_id') or SESSION_ID_DEMO
                    if sid not in SESSIONS:
                        SESSIONS[sid] = {
                            'history': [],
                            'task_ids': [],
                            'last_task_id': None,
                        }
                    payload = {
                        'source': 'User',
                        'type': 'ask_human_reply',
                        'content': content,
                        'session_id': sid,
                    }
                    SESSIONS[sid]['history'].append(payload)
                    await send_json(payload)
                elif msg_type == 'confirmation_reply':
                    content = data.get('content', '')
                    # Use the same queue for unified confirmation
                    ask_human_queue.put(content)
                    sid = data.get('session_id') or SESSION_ID_DEMO
                    if sid not in SESSIONS:
                        SESSIONS[sid] = {
                            'history': [],
                            'task_ids': [],
                            'last_task_id': None,
                        }
                    payload = {
                        'source': 'User',
                        'type': 'confirmation_reply',
                        'content': content,
                        'session_id': sid,
                    }
                    SESSIONS[sid]['history'].append(payload)
                    await send_json(payload)
                else:
                    await command_queue.put(data)
        except Exception:
            pass

    reader_task: asyncio.Task | None = None

    try:
        reader_task = asyncio.create_task(reader_loop())

        while True:
            data = await command_queue.get()

            if data.get('type') == 'cancel':
                sid = data.get('session_id')
                if sid:
                    if sid in _run_stop_events:
                        _run_stop_events[sid].set()
                    else:
                        # Run not started yet (e.g. during "Initializing..."); record so run exits immediately
                        _pending_cancel.add(sid)
                await send_json(
                    {
                        'source': 'System',
                        'type': 'status',
                        'content': 'Cancelling...',
                        'session_id': sid,
                    }
                )
                continue

            user_prompt = (data.get('content') or '').strip()
            if not user_prompt:
                await send_json(
                    {
                        'source': 'System',
                        'type': 'status',
                        'content': 'Empty prompt ignored.',
                        'session_id': data.get('session_id'),
                    }
                )
                continue

            mode = (data.get('mode') or 'direct').strip().lower() or 'direct'
            if mode not in ('direct', 'planner'):
                mode = 'direct'

            session_id = data.get('session_id') or str(uuid.uuid4())
            if session_id not in SESSIONS:
                SESSIONS[session_id] = {
                    'history': [],
                    'task_ids': [],
                    'last_task_id': None,
                }
            # Keep a stable workspace per session so uploads are visible before first run.
            task_id = SESSIONS[session_id].get('last_task_id') or session_id
            if task_id not in SESSIONS[session_id].setdefault('task_ids', []):
                SESSIONS[session_id]['task_ids'].append(task_id)
            SESSIONS[session_id]['last_task_id'] = task_id
            user_msg = {
                'source': 'User',
                'type': 'query',
                'content': user_prompt,
                'mode': mode,
                'session_id': session_id,
            }
            SESSIONS[session_id]['history'].append(user_msg)
            await send_json(user_msg)

            await send_json(
                {
                    'source': 'System',
                    'type': 'status',
                    'content': f"Initializing ({mode})...",
                    'session_id': session_id,
                }
            )

            stop_ev = threading.Event()
            _run_stop_events[session_id] = stop_ev
            if session_id in _pending_cancel:
                stop_ev.set()
                _pending_cancel.discard(session_id)
            bak = (data.get('bohrium_access_key') or '').strip() or None
            bpid = data.get('bohrium_project_id')
            # Fallback: when frontend doesn't send creds, use env vars
            # if ENABLE_SSH_SANDBOX is set (opt-in for local testing).
            if not bak and os.environ.get('ENABLE_SSH_SANDBOX', '').lower() in (
                '1',
                'true',
                'yes',
            ):
                bak = os.environ.get('BOHRIUM_ACCESS_KEY', '').strip() or None
                bpid = bpid or os.environ.get('BOHRIUM_PROJECT_ID')
            if bpid is not None:
                try:
                    bpid = int(bpid)
                except (TypeError, ValueError):
                    bpid = None
            asyncio.get_event_loop().run_in_executor(
                _executor,
                _run_agent_sync,
                session_id,
                user_prompt,
                send_json,
                loop,
                stop_ev,
                mode,
                planner_reply_queue,
                task_id,
                ask_human_queue,
                bak,
                bpid,
            )
    except asyncio.CancelledError:
        pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await send_json({'source': 'System', 'type': 'error', 'content': str(e)})
        except Exception:
            pass
    finally:
        if reader_task is not None:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass


if __name__ == '__main__':
    import os

    import uvicorn

    # host="0.0.0.0" so backend is reachable from other machines (server deployment)
    # Windows 上 reload 会 spawn 子进程，易触发 DuplicateHandle PermissionError，默认关闭
    force_reload = os.environ.get('RELOAD', '').lower() in ('1', 'true', 'yes')
    use_reload = force_reload or (sys.platform != 'win32')
    backend_port = int(os.environ.get('BACKEND_PORT', '50001'))
    uvicorn.run(
        'server:app',
        host='0.0.0.0',
        port=backend_port,
        reload=use_reload,
    )
