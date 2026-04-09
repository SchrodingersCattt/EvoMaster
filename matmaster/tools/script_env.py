"""Session credential -> script environment bridge.

Env injection for shell commands. The Bohrium runtime resolves credentials;
this module handles wrapping commands with export statements.
"""

from __future__ import annotations

import logging
import shlex
import uuid
from typing import Any

logger = logging.getLogger(__name__)


# -- public API ------------------------------------------------------------


def prepare_inline_command(cmd: str, env: dict[str, str], session: Any) -> str:
    """Wrap an inline shell command with explicit env vars.

    Returns *cmd* unchanged if *env* is empty.
    """
    if not env:
        return cmd
    try:
        return _via_file(cmd, env, session)
    except Exception as exc:
        logger.warning("Env file injection failed: %s; falling back to inline", exc)
        return _inline(cmd, env)


def prepare_script_command(
    cmd: str,
    env: dict[str, str],
    session: Any,
    *,
    shell_path: str,
) -> str:
    """Write a script file with env exports and return the cleanup wrapper."""
    path = f"/tmp/.mm_cmd_{uuid.uuid4().hex[:12]}.sh"
    env_block = "\n".join(
        f"export {k}={shlex.quote(v)}" for k, v in sorted(env.items())
    )
    script = "#!/usr/bin/env bash\nset -e\n"
    if env_block:
        script += env_block + "\n"
    script += cmd + "\n"
    session.write_file(path, script)
    session.exec_bash(f"chmod 700 {shlex.quote(path)}")
    return (
        f"{shell_path} {shlex.quote(path)}; "
        f"_ec=$?; rm -f {shlex.quote(path)}; exit $_ec"
    )


def inject_env(cmd: str, env: dict[str, str], session: Any) -> str:
    """Backward-compatible wrapper for inline env injection."""
    return prepare_inline_command(cmd, env, session)


def inject(cmd: str, session: Any) -> str:
    """Wrap shell command with runtime-backed env vars when available."""
    from matmaster.bohrium.runtime import get_runtime

    runtime = get_runtime(session)
    if runtime is None:
        return cmd
    env = runtime.build_env()
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
