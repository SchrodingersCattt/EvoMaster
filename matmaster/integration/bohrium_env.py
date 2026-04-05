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

Credential resolution is delegated to the runtime_bridge adapter so that
the precedence chain (explicit > session > env > none) is defined in one
place.
"""

from __future__ import annotations

import copy
import os
from typing import Any, NamedTuple

from matmaster.integration.runtime_bridge.adapters.bohrium import (
    build_bohrium_env,
    resolve_bohrium_credentials,
)

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

    Delegates to the runtime bridge for credential resolution, then maps
    the result back to the legacy dict format.

    Args:
        access_key: Optional access_key; takes precedence over env.
        project_id: Optional project_id; takes precedence over env.
        user_id: Optional user_id; takes precedence over env.

    Returns:
        Dict with ``access_key``, ``project_id``, ``user_id``.
    """
    # Build explicit dict from non-None params
    explicit: dict[str, Any] | None = None
    params: dict[str, Any] = {}
    if access_key is not None:
        params["access_key"] = str(access_key).strip()
    if project_id is not None:
        params["project_id"] = project_id
    if user_id is not None:
        params["user_id"] = user_id
    if params:
        explicit = params

    cred = resolve_bohrium_credentials(session=None, explicit=explicit)

    # Map back to legacy format with type coercion
    if cred.source == "none":
        # Legacy behavior: return empty/default values
        ak = str(access_key).strip() if access_key is not None else ""
        pid = _safe_int(project_id, -1)
        uid = _safe_int(user_id, -1)
        return {"access_key": ak, "project_id": pid, "user_id": uid}

    vals = cred.values
    return {
        "access_key": str(vals.get("access_key", "")).strip(),
        "project_id": _safe_int(vals.get("project_id"), -1),
        "user_id": _safe_int(vals.get("user_id"), -1),
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

    Delegates to the runtime bridge adapter for credential resolution
    and env projection.

    Used by MCP calculation and MonitorJob tool to forward credentials to
    remote skill scripts on Bohrium nodes.

    Returns:
        Non-empty dict with ``BOHRIUM_ACCESS_KEY``, ``BOHRIUM_PROJECT_ID``,
        ``BOHRIUM_BASE_URL`` (and optionally ``BOHRIUM_USER_ID`` /
        ``BOHRIUM_USER_NO``); empty dict if credentials missing or invalid.
    """
    return build_bohrium_env(session=session)


# ── Internal helpers ────────────────────────────────────────────────


def _safe_int(value: Any, default: int) -> int:
    """Convert *value* to int, returning *default* on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
