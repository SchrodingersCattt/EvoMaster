"""matmaster/tools/builtin/_path_safety.py

Path safety and shell argument sanitization for builtin tools.

resolve_safe_path: ensures user paths stay within workdir boundary.
shell_escape: wraps values for safe shell interpolation (shlex.quote).
"""

from __future__ import annotations

import posixpath
import shlex
from collections.abc import Sequence
from pathlib import PurePosixPath


def _is_under(path: str, root: str) -> bool:
    return PurePosixPath(path).is_relative_to(PurePosixPath(root))


def resolve_safe_path(
    user_path: str,
    workdir: str,
    *,
    allowed_roots: Sequence[str] = (),
) -> str:
    """Resolve user-provided path to a safe absolute path within workdir.

    - Empty or '.' -> workdir
    - Absolute path within workdir -> normalized
    - Absolute path within an allowed extra root -> normalized
    - Absolute path outside allowed roots -> fallback to workdir
    - Relative path -> joined with workdir, checked for containment

    The outside-workdir fallback is defense-in-depth; StructuralValidation
    (Layer A) catches boundary violations before _execute() runs.
    """
    # Normalize workdir first to handle trailing slashes and dot segments
    workdir = posixpath.normpath(workdir)
    roots = [workdir]
    for root in allowed_roots:
        normalized_root = posixpath.normpath(str(root))
        if normalized_root and normalized_root != "." and normalized_root not in roots:
            roots.append(normalized_root)

    if not user_path or user_path == ".":
        return workdir

    if user_path.startswith("/"):
        normalized = posixpath.normpath(user_path)
        if any(_is_under(normalized, root) for root in roots):
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
