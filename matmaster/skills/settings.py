"""Skill settings and roots utilities.

Pure utility module shared by core-layer (Exp._init_skill_tools) and
service-layer (skill_registry_factory). No business logic—just file I/O,
JSON parsing, and session attribute extraction with safe fallbacks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skill roots resolution
# ---------------------------------------------------------------------------


def local_user_skills_root(session: Any | None) -> Path | None:
    """Extract the local user skills root path from a session object."""
    if session is None:
        return None
    raw = getattr(session, "local_user_skills_root", None)
    if not isinstance(raw, str):
        return None
    root = raw.strip()
    return Path(root) if root else None


def remote_skill_roots(session: Any | None) -> list[str]:
    """Extract deduplicated remote skill root paths from a session object."""
    if session is None:
        return []

    roots: list[str] = []
    raw_roots = getattr(session, "remote_skill_roots", None)
    if isinstance(raw_roots, (list, tuple, set)):
        roots.extend(
            root.strip() for root in raw_roots if isinstance(root, str) and root.strip()
        )

    raw_user_root = getattr(session, "remote_user_skills_root", None)
    if isinstance(raw_user_root, str) and raw_user_root.strip():
        roots.append(raw_user_root.strip())

    seen: set[str] = set()
    unique: list[str] = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


# ---------------------------------------------------------------------------
# Disabled skill names
# ---------------------------------------------------------------------------


def disabled_skill_names_from_settings(root: Path) -> set[str]:
    """Read disabled skill names from a local .settings.json file."""
    settings_path = root / ".settings.json"
    if not settings_path.is_file():
        return set()
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning(
            "Failed to read skill settings: %s",
            settings_path,
            exc_info=True,
        )
        return set()
    return _extract_disabled_names(payload)


def disabled_skill_names_from_remote_settings(
    session: Any, remote_root: str
) -> set[str]:
    """Read disabled skill names from a remote .settings.json via session SFTP."""
    settings_path = remote_root.rstrip("/") + "/.settings.json"
    try:
        if not session.path_exists(settings_path):
            return set()
        content = session.read_file(settings_path)
        payload = json.loads(content)
    except Exception:
        logger.warning(
            "Failed to read remote skill settings: %s",
            settings_path,
            exc_info=True,
        )
        return set()
    return _extract_disabled_names(payload)


def _extract_disabled_names(payload: Any) -> set[str]:
    """Extract the 'disabled' list from a parsed .settings.json payload."""
    disabled = payload.get("disabled") if isinstance(payload, dict) else None
    if not isinstance(disabled, list):
        return set()
    return {name.strip() for name in disabled if isinstance(name, str) and name.strip()}
