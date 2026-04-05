"""Session credential -> script environment bridge.

Env injection for shell commands. The runtime bridge resolves credentials;
this module handles wrapping commands with export statements.
"""

from __future__ import annotations

import logging
import shlex
import uuid
from typing import Any

logger = logging.getLogger(__name__)


# -- public API ------------------------------------------------------------


def inject_env(cmd: str, env: dict[str, str], session: Any) -> str:
    """Wrap shell command with explicit env vars.

    Returns *cmd* unchanged if *env* is empty.
    """
    if not env:
        return cmd
    try:
        return _via_file(cmd, env, session)
    except Exception as exc:
        logger.warning("Env file injection failed: %s; falling back to inline", exc)
        return _inline(cmd, env)


def inject(cmd: str, session: Any) -> str:
    """Wrap shell command with session credentials as env vars.

    Uses the runtime bridge to resolve Bohrium credentials.
    Returns cmd unchanged if no credentials found.
    """
    from matmaster.integration.runtime_bridge import build_service_env

    env = build_service_env("bohrium", session=session)
    return inject_env(cmd, env, session)


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
