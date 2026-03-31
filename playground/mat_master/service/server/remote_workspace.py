"""Remote-session (SSH) file helpers for the MatMaster web service."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import HTTPException

from . import state


def _get_session():
    """Return the current session from the cached playground (or None)."""
    if state._cached_pg is not None and hasattr(state._cached_pg, 'session'):
        return state._cached_pg.session
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
    except (RuntimeError, OSError) as exc:
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
    except (RuntimeError, OSError) as exc:
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
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Remote session error: {exc}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _remote_path_exists(remote_path: str) -> bool:
    s = _get_session()
    if s is None:
        return False
    try:
        return s.path_exists(remote_path)
    except (RuntimeError, OSError):
        return False


def _remote_is_dir(remote_path: str) -> bool:
    s = _get_session()
    if s is None:
        return False
    try:
        return s.is_directory(remote_path)
    except (RuntimeError, OSError):
        return False


def _remote_is_file(remote_path: str) -> bool:
    s = _get_session()
    if s is None:
        return False
    try:
        return s.is_file(remote_path)
    except (RuntimeError, OSError):
        return False


def _remote_rename(old_path: str, new_path: str) -> None:
    s = _get_session()
    if s is None:
        raise HTTPException(status_code=500, detail='No session available')
    try:
        result = s.exec_bash(f"mv '{old_path}' '{new_path}'")
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Remote session error: {exc}")
    if result.get('exit_code', -1) != 0:
        raise HTTPException(
            status_code=500, detail=f"Rename failed: {result.get('stdout', '')}"
        )
