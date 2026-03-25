"""Workspace resolver for local task workspaces and remote SSH roots."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_REMOTE_SESSION_WORKSPACE_ROOT = '/share/workspace'


def _default_project_root() -> Path:
    """Return repository root."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _mat_master_config_path(project_root: Path | None = None) -> Path:
    """Return the main MatMaster config path."""
    root = project_root or _default_project_root()
    return root / 'configs' / 'mat_master' / 'config.yaml'


@lru_cache
def _load_workspace_config_from_file(config_path: str) -> dict[str, Any]:
    """Load config.yaml once for workspace settings."""
    path = Path(config_path)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception as exc:
        logger.debug('workspace resolver: load config failed path=%s err=%s', path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def load_workspace_config_dict(project_root: Path | None = None) -> dict[str, Any]:
    """Load workspace-related config from config.yaml."""
    return _load_workspace_config_from_file(str(_mat_master_config_path(project_root)))


def _mat_master_value(config_dict: dict[str, Any] | None, key: str) -> Any:
    """Read a key from the top-level mat_master config."""
    if not isinstance(config_dict, dict):
        return None
    section = config_dict.get('mat_master')
    if not isinstance(section, dict):
        return None
    return section.get(key)


def _resolve_optional_path(
    raw: str | os.PathLike[str] | None,
    *,
    project_root: Path | None = None,
) -> Path | None:
    """Resolve a possibly-relative path to absolute."""
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ((project_root or _default_project_root()) / path).resolve()
    return path


def get_remote_session_workspace_root(
    config_dict: dict[str, Any] | None = None,
    *,
    project_root: Path | None = None,
) -> Path:
    """Return the remote SSH root for session-scoped workspaces."""
    raw = (
        _mat_master_value(config_dict, 'remote_session_workspace_root')
        or _DEFAULT_REMOTE_SESSION_WORKSPACE_ROOT
    )
    resolved = _resolve_optional_path(raw, project_root=project_root)
    assert resolved is not None
    return resolved


@dataclass(frozen=True)
class WorkspaceResolution:
    """Resolved workspace path plus metadata."""

    mode: str
    path: Path
    source: str
    session_id: str | None
    task_id: str | None
    override_path: Path | None = None


def resolve_workspace_path(
    run_dir: str | Path,
    *,
    task_id: str | None = None,
    session_id: str | None = None,
    config_dict: dict[str, Any] | None = None,
    project_root: Path | None = None,
    create: bool = False,
) -> WorkspaceResolution:
    """Resolve the effective local workspace path."""
    run_path = Path(run_dir).expanduser().resolve()
    session_key = (session_id or '').strip() or None
    task_key = (task_id or '').strip() or None

    if task_key:
        path = run_path / 'workspaces' / task_key
        source = 'task'
    else:
        path = run_path / 'workspace'
        source = 'default'

    if create:
        path.mkdir(parents=True, exist_ok=True)

    return WorkspaceResolution(
        mode='task',
        path=path,
        source=source,
        session_id=session_key,
        task_id=task_key,
        override_path=None,
    )
