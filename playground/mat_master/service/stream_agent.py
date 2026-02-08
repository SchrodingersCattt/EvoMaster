"""StreamingMatMasterAgent: MatMasterAgent that emits thought/tool_call/tool_result via callback."""

from __future__ import annotations

from typing import Any, Callable

from evomaster.utils.types import AssistantMessage, ToolMessage

from ..core.agent import MatMasterAgent


# Map tool names to display source for multi-agent UI
TOOL_SOURCE_MAP = {
    "think_plan": "Planner",
    "write_code": "Coder",
    "run_python": "Coder",
}

RESULT_PREVIEW_LEN = 500


def _source_for_tool(tool_name: str) -> str:
    return TOOL_SOURCE_MAP.get(tool_name, "MatMaster")


class StreamingMatMasterAgent(MatMasterAgent):
    """
    MatMasterAgent that reports state in real time via event_callback.
    Overrides _on_assistant_message and _on_tool_message to emit events.
    """

    def __init__(self, event_callback: Callable[[str, str, Any], None] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.event_callback = event_callback

    def _emit(self, source: str, event_type: str, content: Any) -> None:
        if self.event_callback:
            self.event_callback(source, event_type, content)

    def _on_assistant_message(self, msg: AssistantMessage) -> None:
        agent_name = getattr(self, "_agent_name", None) or "MatMaster"
        if msg.content:
            self._emit(agent_name, "thought", msg.content)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                source = _source_for_tool(tc.function.name)
                self._emit(
                    source,
                    "tool_call",
                    {"name": tc.function.name, "args": tc.function.arguments},
                )

    def _on_tool_message(self, msg: ToolMessage) -> None:
        content_preview = (
            (msg.content[:RESULT_PREVIEW_LEN] + "...")
            if msg.content and len(msg.content) > RESULT_PREVIEW_LEN
            else (msg.content or "")
        )
        self._emit(
            "ToolExecutor",
            "tool_result",
            {"id": msg.tool_call_id, "name": msg.name, "result": content_preview},
        )
