"""SkillHitHook -- emits SkillHitEvent when a skill tool is invoked.

Skills are identified by the "skill:" prefix in tool_call.name.
"""

from __future__ import annotations

import logging

from matmaster.bus.queue import MessageBus
from matmaster.engine.hooks import BaseHook
from matmaster.engine.types import ToolCallData
from matmaster.types.events import SkillHitEvent

logger = logging.getLogger(__name__)

_SKILL_PREFIX = "skill:"


class SkillHitHook(BaseHook):
    """Hook that emits SkillHitEvent when a skill tool is called.

    Skills are identified by tool names starting with "skill:" prefix.
    The skill_name in the emitted event is the part after the prefix.
    """

    def __init__(self, bus: MessageBus, *, source: str = "MatMaster") -> None:
        self._bus = bus
        self._source = source

    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        """Emit SkillHitEvent if tool_call.name starts with 'skill:' prefix."""
        if not tool_call.name.startswith(_SKILL_PREFIX):
            return

        skill_name = tool_call.name[len(_SKILL_PREFIX) :]
        self._bus.emit(
            SkillHitEvent(
                source=self._source,
                skill_name=skill_name,
            )
        )
