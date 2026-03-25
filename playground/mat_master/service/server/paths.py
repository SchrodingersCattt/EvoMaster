"""Run/workspace path helpers for the MatMaster web service."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import HTTPException

from playground.mat_master.core.workspace_resolver import (
    resolve_workspace_path,
)

from . import state
from .bootstrap import PROJECT_ROOT

logger = logging.getLogger(__name__)


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


def _list_workspace_ids() -> list[str]:
    """List workspace folder names from the active workspace root."""
    workspace_root = _runs_dir() / _get_run_id_web() / 'workspaces'
    if not workspace_root.is_dir():
        return []
    pairs = []
    for p in workspace_root.iterdir():
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


def _get_run_workspace_path(
    run_id: str,
    task_id: str | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Resolve run_id (and optional task_id) to workspace directory."""
    runs = _runs_dir()
    run_path = runs / run_id
    if not run_path.is_dir():
        return None
    resolution = resolve_workspace_path(
        run_path,
        task_id=task_id,
        session_id=session_id,
        create=False,
    )
    if resolution.path.is_dir():
        return resolution.path
    if task_id or session_id:
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
    """Resolve session workspace dir and task_id, optionally creating it.

    Local web keeps task-scoped workspaces. When a session has no last_task_id
    yet, we use session_id as a stable placeholder so file APIs still have a
    deterministic local directory before the first run completes.
    """
    run_path = _runs_dir() / _get_run_id_web()
    if not run_path.is_dir():
        raise HTTPException(status_code=404, detail='Run not found')
    data = state.SESSIONS.get(session_id)
    task_id = ((data or {}).get('last_task_id') or '') if data else ''
    task_id = task_id or None
    if task_id is None:
        task_id = session_id
    resolution = resolve_workspace_path(
        run_path,
        task_id=task_id,
        session_id=session_id,
        create=create,
    )
    base = resolution.path
    if not base or not base.is_dir():
        raise HTTPException(status_code=404, detail='Workspace not found')
    logical_task_id = task_id or session_id
    logger.debug(
        'resolve_session_workspace: mode=%s source=%s session_id=%s task_id=%s path=%s',
        resolution.mode,
        resolution.source,
        session_id,
        logical_task_id,
        base,
    )
    return base, logical_task_id
