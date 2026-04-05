"""Bohrium-specific credential adapter.

Maps Bohrium credential fields to env vars, extracts session credentials,
and provides Bohrium-aware resolution, env projection, and MCP arg injection.
"""

from __future__ import annotations

import copy
import os
from typing import Any

from matmaster.integration.runtime_bridge.env_projector import project_to_env
from matmaster.integration.runtime_bridge.models import ResolvedCredential
from matmaster.integration.runtime_bridge.resolver import resolve_credentials

# ── Field / env-var mappings ────────────────────────────────────────

# Credential fields that are resolved via the precedence chain.
_BOHRIUM_ENV_KEYS: dict[str, str] = {
    "access_key": "BOHRIUM_ACCESS_KEY",
    "project_id": "BOHRIUM_PROJECT_ID",
    "user_id": "BOHRIUM_USER_ID",
    "user_no": "BOHRIUM_USER_NO",
}

# base_url is always read from env (with a default), not from session.
_BOHRIUM_BASE_URL_DEFAULT = "https://open.bohrium.com"

# The minimum set of fields required for a valid credential.
_BOHRIUM_REQUIRED_KEYS = ["access_key"]

# Full env projection mapping (includes base_url).
_BOHRIUM_FIELD_TO_ENV: dict[str, str] = {
    "access_key": "BOHRIUM_ACCESS_KEY",
    "project_id": "BOHRIUM_PROJECT_ID",
    "user_id": "BOHRIUM_USER_ID",
    "user_no": "BOHRIUM_USER_NO",
    "base_url": "BOHRIUM_BASE_URL",
}


# ── Helpers ─────────────────────────────────────────────────────────


def _read_base_url() -> str:
    """Read BOHRIUM_BASE_URL from env, strip trailing slash, apply default."""
    raw = os.environ.get("BOHRIUM_BASE_URL", _BOHRIUM_BASE_URL_DEFAULT)
    return raw.rstrip("/")


def _session_extractor(session: Any | None):
    """Return a callable that extracts Bohrium credentials from a session."""
    if session is None:
        return None

    def _extract() -> dict[str, Any] | None:
        creds = getattr(session, "_bohrium_credentials", None)
        if not isinstance(creds, dict):
            return None
        access_key = (creds.get("access_key") or "").strip()
        if not access_key:
            return None
        return dict(creds)

    return _extract


def _coerce_values(values: dict[str, Any]) -> dict[str, Any]:
    """Coerce known Bohrium fields to expected types.

    - ``access_key`` -> stripped str
    - ``project_id`` -> int (or original if non-numeric)
    - ``user_id`` -> int (or original if non-numeric)
    """
    out = dict(values)
    if "access_key" in out:
        out["access_key"] = str(out["access_key"]).strip()
    for int_field in ("project_id", "user_id"):
        if int_field in out:
            try:
                out[int_field] = int(out[int_field])
            except (TypeError, ValueError):
                pass
    return out


# ── Public API ──────────────────────────────────────────────────────


def resolve_bohrium_credentials(
    *, session: Any | None = None, explicit: dict[str, Any] | None = None
) -> ResolvedCredential:
    """Resolve Bohrium credentials through the precedence chain.

    ``base_url`` is always read from ``BOHRIUM_BASE_URL`` env var (with
    default fallback) and attached to the result values.

    Args:
        session: Optional session with ``_bohrium_credentials`` attribute.
        explicit: Optional caller-provided credential dict.

    Returns:
        A ``ResolvedCredential`` with Bohrium-specific values.
    """
    cred = resolve_credentials(
        "bohrium",
        explicit=explicit,
        session_extractor=_session_extractor(session),
        env_keys=_BOHRIUM_ENV_KEYS,
        required_keys=_BOHRIUM_REQUIRED_KEYS,
    )

    # Attach base_url to resolved values (always from env).
    if cred.source != "none":
        enriched = _coerce_values(cred.values)
        enriched["base_url"] = _read_base_url()
        return ResolvedCredential(
            service=cred.service,
            source=cred.source,
            values=enriched,
        )

    return cred


def build_bohrium_env(
    *, session: Any | None = None, explicit: dict[str, Any] | None = None
) -> dict[str, str]:
    """Build a ``BOHRIUM_*`` env dict from resolved credentials.

    Args:
        session: Optional session with ``_bohrium_credentials`` attribute.
        explicit: Optional caller-provided credential dict.

    Returns:
        Dict of ``{BOHRIUM_*: value}`` strings. Empty if no credentials.
    """
    cred = resolve_bohrium_credentials(session=session, explicit=explicit)
    if cred.source == "none":
        return {}
    return project_to_env(cred.values, _BOHRIUM_FIELD_TO_ENV)


def inject_bohrium_mcp_args(
    *,
    session: Any | None = None,
    explicit: dict[str, Any] | None = None,
    executor_template: dict[str, Any] | None = None,
    user_no: str | None = None,
) -> dict[str, Any]:
    """Deep-copy an executor template and inject Bohrium credentials.

    Mirrors the logic of ``bohrium_env.inject_bohrium_executor`` but uses
    the bridge resolver for credential sourcing.

    Args:
        session: Optional session with ``_bohrium_credentials`` attribute.
        explicit: Optional caller-provided credential dict.
        executor_template: Executor template dict (not mutated).
        user_no: Optional academic code for ``BOHRIUM_USER_NO``.

    Returns:
        New executor dict with auth injected, or empty dict if no
        template provided.
    """
    if executor_template is None:
        return {}

    cred = resolve_bohrium_credentials(session=session, explicit=explicit)
    if cred.source == "none":
        return copy.deepcopy(executor_template)

    vals = cred.values
    executor = copy.deepcopy(executor_template)

    uid_val = vals.get("user_id", -1)
    uid_env = str(uid_val) if uid_val != -1 else ""
    user_no_s = (user_no or vals.get("user_no", "") or "").strip()

    def _inject_user_env(envs: dict[str, Any]) -> None:
        if uid_env:
            envs["BOHRIUM_USER_ID"] = uid_env
        if user_no_s:
            envs["BOHRIUM_USER_NO"] = user_no_s

    if executor.get("type") == "dispatcher":
        rp = executor.setdefault("machine", {}).setdefault("remote_profile", {})
        rp["access_key"] = vals["access_key"]
        rp["project_id"] = vals.get("project_id", -1)
        rp["real_user_id"] = vals.get("user_id", -1)
        resources = executor.setdefault("resources", {})
        envs = resources.setdefault("envs", {})
        envs["BOHRIUM_PROJECT_ID"] = vals.get("project_id", -1)
        _inject_user_env(envs)
    elif executor.get("type") == "local":
        env = executor.setdefault("env", {})
        env["BOHRIUM_PROJECT_ID"] = str(vals.get("project_id", -1))
        if vals.get("access_key"):
            env["BOHRIUM_ACCESS_KEY"] = str(vals["access_key"])
        _inject_user_env(env)

    return executor
