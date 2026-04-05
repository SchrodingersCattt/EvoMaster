"""Output path classification and sync-decision logic.

Classifies paths as relative, local absolute, or remote share, and
determines whether a remote session is required to access them.
"""

from __future__ import annotations

import os
from typing import Any

from matmaster.integration.runtime_bridge.models import OutputPathDecision

# Prefixes that indicate a Bohrium shared filesystem path.
_REMOTE_SHARE_PREFIXES = ("/share/", "/personal/")


def resolve_output_path(
    *,
    raw_path: str,
    execution_workdir: str,
    session: Any | None = None,
) -> OutputPathDecision:
    """Classify an output path and decide if remote access is needed.

    Args:
        raw_path: The path string as provided by the user or tool.
        execution_workdir: The current working directory of the execution
            context (local or remote).
        session: Optional remote execution session. If present and has
            ``is_open=True``, the remote share is considered accessible.

    Returns:
        An ``OutputPathDecision`` with classification and access info.
    """
    normalized = raw_path.strip()

    # Remote share detection
    if _is_remote_share(normalized):
        needs_session = not _session_is_open(session)
        return OutputPathDecision(
            kind="remote_share",
            normalized_path=normalized,
            requires_remote_session=needs_session,
        )

    # Absolute local path
    if os.path.isabs(normalized):
        return OutputPathDecision(
            kind="local_abs",
            normalized_path=normalized,
            requires_remote_session=False,
        )

    # Relative path -- resolve against execution_workdir
    resolved = os.path.normpath(os.path.join(execution_workdir, normalized))
    return OutputPathDecision(
        kind="relative",
        normalized_path=resolved,
        requires_remote_session=False,
    )


def _is_remote_share(path: str) -> bool:
    """Check if the path is under a known remote share prefix."""
    for prefix in _REMOTE_SHARE_PREFIXES:
        if path.startswith(prefix) or path == prefix.rstrip("/"):
            return True
    return False


def _session_is_open(session: Any | None) -> bool:
    """Check if a session object represents an active remote connection."""
    if session is None:
        return False
    return bool(getattr(session, "is_open", False))
