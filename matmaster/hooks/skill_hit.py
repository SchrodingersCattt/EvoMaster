"""SkillHitHook -- emits SkillHitEvent when the use_skill tool is invoked.

Skills are identified by tool_call.name == "use_skill" and the
skill_name is extracted from tool_call.arguments.
"""

from __future__ import annotations

import logging

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import BaseHook
from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import ToolCallData
from matmaster.types.events import SkillHitEvent

logger = logging.getLogger(__name__)

_SKILL_TOOL_NAME = "use_skill"


class SkillHitHook(BaseHook):
    """Hook that emits SkillHitEvent when the use_skill tool is called.

    Extracts skill_name from tool_call.arguments. Silently skips
    if skill_name is missing or not a string.

    Uses bus.emit_nowait() for thread-safe emit from sync kernel context.
    """

    def __init__(self, bus: MessageBus, *, source: str = "MatMaster") -> None:
        self._bus = bus
        self._source = source

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        """Emit SkillHitEvent if tool is use_skill with a valid skill_name."""
        if tool_call.name != _SKILL_TOOL_NAME:
            return

        raw = tool_call.arguments.get("skill_name")
        if not isinstance(raw, str) or not raw:
            return

        self._bus.emit_nowait(
            SkillHitEvent(
                source=self._source,
                skill_name=raw,
            )
        )
