"""OutputProcessorHook -- emits auto_save/summarize events after tool calls.

Inspects tool_call.name against configurable patterns and emits ToolResultEvent
with appropriate info flags to the bus for downstream processing.
"""

from __future__ import annotations

import logging

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import BaseHook
from matmaster.types.messages import ToolCallData
from matmaster.types.events import ToolResultEvent

logger = logging.getLogger(__name__)


class OutputProcessorHook(BaseHook):
    """Hook that emits auto_save and summarize events after tool execution.

    Uses simple substring matching against tool_call.name to determine
    whether to emit events. Patterns are checked via 'in' operator
    (same logic as current auto_save_tool_output_patterns).
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        auto_save_patterns: list[str] | None = None,
        summarize_patterns: list[str] | None = None,
        source: str = "MatMaster",
    ) -> None:
        self._bus = bus
        self._auto_save_patterns = auto_save_patterns or []
        self._summarize_patterns = summarize_patterns or []
        self._source = source

    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        """Check tool_call.name against patterns and emit events if matched."""
        tool_name = tool_call.name

        if self._matches(tool_name, self._auto_save_patterns):
            self._bus.emit(
                ToolResultEvent(
                    source=self._source,
                    call_id=tool_call.id,
                    tool_name=tool_name,
                    result=result,
                    info={"auto_save": True},
                )
            )
            return

        if self._matches(tool_name, self._summarize_patterns):
            self._bus.emit(
                ToolResultEvent(
                    source=self._source,
                    call_id=tool_call.id,
                    tool_name=tool_name,
                    result=result,
                    info={"summarize": True},
                )
            )

    @staticmethod
    def _matches(tool_name: str, patterns: list[str]) -> bool:
        """Check if tool_name contains any of the patterns (substring match)."""
        return any(pattern in tool_name for pattern in patterns)
