"""Shared helpers for Bohrium run setup and cleanup orchestration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from matmaster.integration.workspace_resolver import (
    get_remote_session_workspace_root,
    load_workspace_config_dict,
)
from src.services.user_service import BohriumAccessKeyFetchResult, UserService

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_run_credentials(
    sessions_service: Any, session_id: str
) -> tuple[dict[str, Any], str | None, str]:
    """Load run credentials from the session store."""
    row = sessions_service.get_session(session_id)
    run_creds: dict[str, Any] = {}
    if row:
        uid = row.get('user_id')
        if uid is not None:
            run_creds['user_id'] = str(uid)
        oid = row.get('org_id')
        if oid is not None and str(oid).strip():
            run_creds['org_id'] = str(oid).strip()
        pid = row.get('project_id')
        if pid is not None:
            run_creds['project_id'] = int(pid)
    user_id_for_ak = run_creds.get('user_id')
    org_id = (run_creds.get('org_id') or '').strip()
    if user_id_for_ak:
        user_no = UserService.get_user_no_by_user_id(user_id_for_ak)
        if user_no:
            run_creds['user_no'] = user_no
    return run_creds, user_id_for_ak, org_id


def _build_access_key_failure_reason(result: BohriumAccessKeyFetchResult) -> str:
    """Convert structured Bohrium AK fetch results to user-facing abort reasons."""
    if result.status == 'timeout':
        return 'Bohrium access_key 获取失败：请求 Bohrium Core 超时'
    if result.status in {'no_items', 'no_valid_ak'}:
        return 'Bohrium access_key 获取失败：当前用户在该组织下没有可用 AK'
    return 'Bohrium access_key 获取失败：Bohrium Core 返回异常状态'


def _remote_session_workspace_root() -> str:
    """Return Bohrium SSH shared workspace root."""
    return str(
        get_remote_session_workspace_root(
            load_workspace_config_dict(_PROJECT_ROOT), project_root=_PROJECT_ROOT
        )
    )


def _creator_id_from_user(user_id: str | None) -> int:
    """Convert user id to creator id."""
    if user_id is None:
        return 0
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return 0


def _emit_node_status(
    event_callback: Callable[..., None],
    node_id: int | None,
    status: str,
    message: str,
    ip: str | None = None,
) -> None:
    """Emit a Bohrium node status event."""
    payload: dict[str, Any] = {
        'status': status,
        'message': message,
    }
    if node_id is not None:
        payload['node_id'] = node_id
    if ip:
        payload['ip'] = ip
    event_callback('System', 'bohrium_node', payload)
