"""Run/workspace path helpers for the MatMaster web service."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from fastapi import HTTPException

from . import state
from .bootstrap import PROJECT_ROOT


def _runs_dir() -> Path:
    """Root directory for run dirs. When MAT_MASTER_RUN_DIR is set, returns its parent."""
    override = os.environ.get('MAT_MASTER_RUN_DIR', '').strip()
    if override:
        return Path(override).expanduser().resolve().parent
    return PROJECT_ROOT / 'runs'


def _get_run_id_web() -> str:
    """Run id used for web mode (path segment and API)."""
    override = os.environ.get('MAT_MASTER_RUN_DIR', '').strip()
    if override:
        return Path(override).expanduser().resolve().name
    return state.RUN_ID_WEB


def _workspace_root_override() -> Path | None:
    raw = (os.environ.get('MAT_MASTER_WORKSPACE_ROOT') or '').strip()
    if not raw:
        try:
            config_path = PROJECT_ROOT / 'configs' / 'mat_master' / 'config.yaml'
            if config_path.is_file():
                data = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
                raw = (data.get('mat_master') or {}).get('workspace_root') or ''
        except Exception:
            raw = ''
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def _list_workspace_ids() -> list[str]:
    """List workspace folder names under runs/.../workspaces/."""
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


def _state_dir() -> Path:
    """Root directory for session state (.state/)."""
    return _runs_dir() / _get_run_id_web() / '.state'


def _session_state_dir(session_id: str) -> Path:
    """Directory for a specific session state."""
    return _state_dir() / session_id


def _list_state_ids() -> list[str]:
    """List all session IDs that have persisted state in .state/."""
    state_root = _state_dir()
    if not state_root.is_dir():
        return []
    return [p.name for p in state_root.iterdir() if p.is_dir()]


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
    data = state.SESSIONS.get(session_id)
    task_id = (data or {}).get('last_task_id') if data else None
    if task_id is None:
        task_id = session_id
        if create:
            (run_path / 'workspaces' / task_id).mkdir(parents=True, exist_ok=True)
    base = _get_run_workspace_path(_get_run_id_web(), task_id=task_id)
    if not base or not base.is_dir():
        raise HTTPException(status_code=404, detail='Workspace not found')
    return base, task_id
