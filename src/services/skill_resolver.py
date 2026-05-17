"""Service-layer SkillResolver implementation."""

from __future__ import annotations

import logging
from typing import Any

from matmaster.context.ports import ActiveSkill, SessionEvent
from matmaster.context.scanner import scan_skill_hits

logger = logging.getLogger(__name__)


class SkillRegistryResolver:
    """Resolve typed session events into ActiveSkill DTOs."""

    def __init__(self, skill_registry: Any | None) -> None:
        self._registry = skill_registry

    def __call__(self, events: tuple[SessionEvent, ...]) -> tuple[ActiveSkill, ...]:
        if self._registry is None:
            return ()
        active: list[ActiveSkill] = []
        for record in scan_skill_hits(events):
            try:
                skill = self._registry.get_skill(record.skill_name)
            except Exception:
                logger.warning(
                    "active skill resolver: get_skill(%r) raised, skipping",
                    record.skill_name,
                    exc_info=True,
                )
                continue
            if skill is None:
                continue
            meta = skill.meta_info
            active.append(
                ActiveSkill(
                    name=meta.name,
                    description=meta.description or "",
                    mcp_server=meta.mcp_server,
                )
            )
        return tuple(active)
