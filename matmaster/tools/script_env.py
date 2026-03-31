"""Session credential -> script environment bridge.

Declarative mapping from session credential attributes to POSIX env vars.
Injection strategy adapts to session transport capabilities.
"""

from __future__ import annotations

import logging
import shlex
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# -- declarative credential mapping ----------------------------------------

_CREDENTIAL_SOURCES: list[tuple[str, dict[str, str]]] = [
    (
        "_bohrium_credentials",
        {
            "access_key": "BOHRIUM_ACCESS_KEY",
            "project_id": "BOHRIUM_PROJECT_ID",
            "user_id": "BOHRIUM_USER_ID",
            "user_no": "BOHRIUM_USER_NO",
        },
    ),
]

_INT_VALIDATED: set[str] = {"project_id"}
_SKIP_VALUES: set[str] = {"-1"}


def _collect(session: Any) -> dict[str, str]:
    """Build env dict from session credentials."""
    env: dict[str, str] = {}
    for attr_name, mapping in _CREDENTIAL_SOURCES:
        creds = getattr(session, attr_name, None)
        if not isinstance(creds, dict):
            continue
        ak = (creds.get("access_key") or "").strip()
        if not ak:
            continue
        for cred_key, env_name in mapping.items():
            val = creds.get(cred_key)
            if val is None:
                continue
            s = str(val).strip()
            if not s or s in _SKIP_VALUES:
                continue
            if cred_key in _INT_VALIDATED:
                try:
                    int(s)
                except (TypeError, ValueError):
                    continue
            env[env_name] = s
    if env.get("BOHRIUM_ACCESS_KEY") and "BOHRIUM_BASE_URL" not in env:
        try:
            from src.utils.constant import BOHRIUM_OPENAPI_HOST

            env["BOHRIUM_BASE_URL"] = BOHRIUM_OPENAPI_HOST
        except ImportError:
            env["BOHRIUM_BASE_URL"] = "https://open.bohrium.com"
    return env


# -- public API ------------------------------------------------------------


def inject(cmd: str, session: Any) -> str:
    """Wrap shell command with session credentials as env vars.

    Always attempts file-based injection first (credentials off command line).
    Falls back to inline prefix if write_file or chmod raises.
    Returns cmd unchanged if no credentials found.
    """
    env = _collect(session)
    if not env:
        return cmd
    try:
        return _via_file(cmd, env, session)
    except Exception as exc:
        logger.warning("Env file injection failed: %s; falling back to inline", exc)
        return _inline(cmd, env)


# -- injection strategies --------------------------------------------------


def _via_file(cmd: str, env: dict[str, str], session: Any) -> str:
    """Write env to remote temp file, source in subshell."""
    path = f"/tmp/.mm_env_{uuid.uuid4().hex[:12]}"
    content = (
        "\n".join(f"export {k}={shlex.quote(v)}" for k, v in sorted(env.items())) + "\n"
    )
    session.write_file(path, content)
    session.exec_bash(f"chmod 600 {shlex.quote(path)}")
    return (
        f"( . {shlex.quote(path)} && {cmd}; "
        f"_ec=$?; rm -f {shlex.quote(path)}; exit $_ec )"
    )


def _inline(cmd: str, env: dict[str, str]) -> str:
    """Prefix command with env assignments (fallback)."""
    prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env.items()))
    return f"{prefix} {cmd}"
