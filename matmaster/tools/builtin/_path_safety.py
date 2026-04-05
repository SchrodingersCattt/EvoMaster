"""matmaster/tools/builtin/_path_safety.py

Path safety and shell argument sanitization for builtin tools.

resolve_safe_path: ensures user paths stay within workdir boundary.
shell_escape: wraps values for safe shell interpolation (shlex.quote).
"""

from __future__ import annotations

import posixpath
import shlex


def resolve_safe_path(user_path: str, workdir: str) -> str:
    """Resolve user-provided path to a safe absolute path within workdir.

    - Empty or '.' -> workdir
    - Absolute path within workdir -> normalized
    - Absolute path outside workdir -> fallback to workdir
    - Relative path -> joined with workdir, checked for containment

    The outside-workdir fallback is defense-in-depth; StructuralValidation
    (Layer A) catches boundary violations before _execute() runs.
    """
    # Normalize workdir first to handle trailing slashes and dot segments
    workdir = posixpath.normpath(workdir)

    if not user_path or user_path == ".":
        return workdir

    if user_path.startswith("/"):
        normalized = posixpath.normpath(user_path)
        if normalized == workdir or normalized.startswith(workdir + "/"):
            return normalized
        return workdir

    joined = posixpath.join(workdir, user_path)
    normalized = posixpath.normpath(joined)
    if normalized == workdir or normalized.startswith(workdir + "/"):
        return normalized
    return workdir


def shell_escape(value: str) -> str:
    """Escape a string for safe interpolation into shell commands.

    Uses shlex.quote() to prevent shell injection ($(...), backticks,
    semicolons, etc.) when building commands for session.exec_bash().
    """
    return shlex.quote(value)
