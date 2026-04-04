"""Bohrium environment constants, credentials, and executor helpers.

Pure-function module for Bohrium authentication and configuration.
No imports from evomaster, src, or playground -- self-contained within matmaster.

Provides:
- BOHRIUM_OPENAPI_HOST: resolved API host constant
- BohriumSetupResult: NamedTuple for setup outcome
- get_bohrium_credentials: read credentials from env or params
- get_bohrium_storage_config: build HTTPS storage config dict
- inject_bohrium_executor: deep-copy executor template and inject auth
- build_bohrium_skill_remote_env: extract session credentials into env dict
"""

from __future__ import annotations

import copy
import os
from typing import Any, NamedTuple

# ── Constants ────────────────────────────────────────────────────────

BOHRIUM_OPENAPI_HOST: str = os.getenv(
    "BOHRIUM_BASE_URL", "https://open.bohrium.com"
).rstrip("/")


# ── Types ────────────────────────────────────────────────────────────


class BohriumSetupResult(NamedTuple):
    """Result of Bohrium setup for a run.

    Mirrors src.services.agent_run_bohrium.BohriumSetupResult to break
    the reverse dependency from matmaster -> src.
    """

    ssh_attached: bool
    abort_result: tuple[Any, int] | None
    execution_session: Any | None
    execution_workdir: str | None
    session_type: str | None


# ── Credential helpers ───────────────────────────────────────────────


def get_bohrium_credentials(
    access_key: str | None = None,
    project_id: int | str | None = None,
    user_id: int | str | None = None,
) -> dict[str, Any]:
    """Read Bohrium auth from env vars (.env / os.environ) or use provided params.

    Args:
        access_key: Optional access_key; takes precedence over env.
        project_id: Optional project_id; takes precedence over env.
        user_id: Optional user_id; takes precedence over env.

    Returns:
        Dict with ``access_key``, ``project_id``, ``user_id``.
    """
    if access_key is None:
        access_key = os.getenv("BOHRIUM_ACCESS_KEY", "").strip()
    else:
        access_key = str(access_key).strip()

    if project_id is None:
        try:
            project_id = int(os.getenv("BOHRIUM_PROJECT_ID", "-1"))
        except (TypeError, ValueError):
            project_id = -1
    else:
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            project_id = -1

    if user_id is None:
        try:
            user_id = int(os.getenv("BOHRIUM_USER_ID", "-1"))
        except (TypeError, ValueError):
            user_id = -1
    else:
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            user_id = -1

    return {
        "access_key": access_key,
        "project_id": project_id,
        "user_id": user_id,
    }


def get_bohrium_storage_config(
    access_key: str | None = None,
    project_id: int | str | None = None,
    user_id: int | str | None = None,
) -> dict[str, Any]:
    """Build MCP calculation HTTPS storage config (type https + Bohrium plugin).

    Args:
        access_key: Optional access_key; takes precedence over env.
        project_id: Optional project_id; takes precedence over env.
        user_id: Optional user_id (unused, kept for API consistency).

    Returns:
        Storage config dict with ``type`` and ``plugin`` keys.
    """
    cred = get_bohrium_credentials(
        access_key=access_key, project_id=project_id, user_id=user_id
    )
    return {
        "type": "https",
        "plugin": {
            "type": "bohrium",
            "access_key": cred["access_key"],
            "project_id": cred["project_id"],
            "app_key": "agent",
        },
    }


def inject_bohrium_executor(
    executor_template: dict[str, Any],
    access_key: str | None = None,
    project_id: int | str | None = None,
    user_id: int | str | None = None,
    user_no: str | None = None,
) -> dict[str, Any]:
    """Deep-copy executor template and inject BOHRIUM_* auth fields.

    Aligns with MatMaster private_callback contract.

    Args:
        executor_template: Executor template dict (not mutated).
        access_key: Optional access_key; takes precedence over env.
        project_id: Optional project_id; takes precedence over env.
        user_id: Optional user_id; takes precedence over env.
        user_no: Optional academic code (account_api userNo) for BOHRIUM_USER_NO.

    Returns:
        New executor dict with auth injected.
    """
    executor = copy.deepcopy(executor_template)
    cred = get_bohrium_credentials(
        access_key=access_key, project_id=project_id, user_id=user_id
    )
    uid_val = cred.get("user_id", -1)
    uid_env = str(uid_val) if uid_val != -1 else ""
    user_no_s = (user_no or "").strip()

    def _inject_user_env(envs: dict[str, Any]) -> None:
        if uid_env:
            envs["BOHRIUM_USER_ID"] = uid_env
        if user_no_s:
            envs["BOHRIUM_USER_NO"] = user_no_s

    if executor.get("type") == "dispatcher":
        rp = executor.setdefault("machine", {}).setdefault("remote_profile", {})
        rp["access_key"] = cred["access_key"]
        rp["project_id"] = cred["project_id"]
        rp["real_user_id"] = cred["user_id"]
        resources = executor.setdefault("resources", {})
        envs = resources.setdefault("envs", {})
        envs["BOHRIUM_PROJECT_ID"] = cred["project_id"]
        _inject_user_env(envs)
    elif executor.get("type") == "local":
        env = executor.setdefault("env", {})
        env["BOHRIUM_PROJECT_ID"] = str(cred["project_id"])
        if cred.get("access_key"):
            env["BOHRIUM_ACCESS_KEY"] = str(cred["access_key"])
        _inject_user_env(env)
    return executor


# ── Session env builder ──────────────────────────────────────────────


def build_bohrium_skill_remote_env(session: Any) -> dict[str, str]:
    """Extract ``BOHRIUM_*`` env vars from ``session._bohrium_credentials``.

    Used by MCP calculation and MonitorJob tool to forward credentials to
    remote skill scripts on Bohrium nodes.

    ``BOHRIUM_BASE_URL`` is set from this module's ``BOHRIUM_OPENAPI_HOST``.

    Returns:
        Non-empty dict with ``BOHRIUM_ACCESS_KEY``, ``BOHRIUM_PROJECT_ID``,
        ``BOHRIUM_BASE_URL`` (and optionally ``BOHRIUM_USER_ID`` /
        ``BOHRIUM_USER_NO``); empty dict if credentials missing or invalid.
    """
    creds = getattr(session, "_bohrium_credentials", None)
    if not isinstance(creds, dict):
        return {}
    access_key = (creds.get("access_key") or "").strip()
    if not access_key:
        return {}
    pid = creds.get("project_id")
    if pid is None:
        return {}
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return {}
    out: dict[str, str] = {
        "BOHRIUM_ACCESS_KEY": access_key,
        "BOHRIUM_PROJECT_ID": str(pid_int),
        "BOHRIUM_BASE_URL": BOHRIUM_OPENAPI_HOST,
    }
    uid = creds.get("user_id")
    if uid is not None and str(uid).strip() and str(uid).strip() != "-1":
        out["BOHRIUM_USER_ID"] = str(uid).strip()
    user_no = creds.get("user_no")
    if isinstance(user_no, str) and user_no.strip():
        out["BOHRIUM_USER_NO"] = user_no.strip()
    return out
