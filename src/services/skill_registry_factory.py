"""Service-layer SkillRegistry construction.

This factory centralizes roots resolution, disabled-name collection, and
registry cleanup for active-skill prompt rendering. Core-layer SkillRegistry
construction in Exp._init_skill_tools intentionally remains independent:
that registry exists for SkillTool registration, not ActiveSkill DTO rendering.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from matmaster.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


def _local_user_skills_root(session: Any | None) -> Path | None:
    if session is None:
        return None
    raw = getattr(session, "local_user_skills_root", None)
    if not isinstance(raw, str):
        return None
    root = raw.strip()
    return Path(root) if root else None


def _remote_skill_roots(session: Any | None) -> list[str]:
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
        if root in seen:
            continue
        seen.add(root)
        unique.append(root)
    return unique


def _disabled_skill_names_from_settings(root: Path) -> set[str]:
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
    disabled = payload.get("disabled") if isinstance(payload, dict) else None
    if not isinstance(disabled, list):
        return set()
    return {name.strip() for name in disabled if isinstance(name, str) and name.strip()}


def build_skill_registry(
    *,
    config_roots: Iterable[str | Path],
    session: Any | None,
    config_disabled: Iterable[str] = (),
) -> SkillRegistry | None:
    """Build a registry for service-layer active-skill resolution.

    Returns None when no local or remote roots are configured. The caller owns
    the skills.enabled guard; this factory stays stateless and unopinionated.
    """
    roots = [Path(root) for root in config_roots if root]
    local = _local_user_skills_root(session)
    if local is not None:
        roots.append(local)
    remote_roots = _remote_skill_roots(session)
    if not roots and not remote_roots:
        return None

    registry = SkillRegistry(
        roots,
        remote_session=session if remote_roots else None,
        remote_roots=remote_roots,
    )
    disabled = {
        name.strip()
        for name in config_disabled
        if isinstance(name, str) and name.strip()
    }
    for root in roots:
        disabled.update(_disabled_skill_names_from_settings(root))
    if disabled:
        registry.remove_skills(disabled)
    return registry
