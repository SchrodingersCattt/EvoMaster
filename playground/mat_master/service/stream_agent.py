"""StreamingMatMasterAgent: MatMasterAgent that emits thought/tool_call/tool_result via callback."""

from __future__ import annotations

import json
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


def _extract_think_content(args_str: str) -> str | None:
    """If args parse as { \"thought\": \"...\" }, return the thought text."""
    if not args_str or not args_str.strip():
        return None
    try:
        obj = json.loads(args_str)
        if isinstance(obj, dict) and "thought" in obj:
            t = obj["thought"]
            return str(t) if t is not None else None
    except (json.JSONDecodeError, TypeError):
        pass
    return None


class StreamingMatMasterAgent(MatMasterAgent):
    """
    MatMasterAgent that reports state in real time via event_callback.
    Overrides _on_assistant_message and _on_tool_message to emit events.
    LLM 原生文本通过 thought 事件推送；think 工具的参数也作为 thought 推送，便于前端展示推理。
    """

    def __init__(self, event_callback: Callable[[str, str, Any], None] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.event_callback = event_callback

    def _emit(self, source: str, event_type: str, content: Any) -> None:
        if self.event_callback:
            self.event_callback(source, event_type, content)

    def _on_assistant_message(self, msg: AssistantMessage) -> None:
        agent_name = getattr(self, "_agent_name", None) or "MatMaster"
        # 始终推送 LLM 原生文本（含空字符串），前端可区分空与有内容
        native_text = msg.content if msg.content is not None else ""
        self._emit(agent_name, "thought", native_text)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                # think 工具的参数作为“思考”再推一条，方便前端当作文本展示
                if tc.function.name == "think":
                    thought_text = _extract_think_content(tc.function.arguments or "")
                    if thought_text:
                        self._emit(agent_name, "thought", thought_text)
                source = _source_for_tool(tc.function.name)
                self._emit(
                    source,
                    "tool_call",
                    {"name": tc.function.name, "args": tc.function.arguments},
                )
                if tc.function.name == "use_skill":
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                        if isinstance(args, dict) and args.get("skill_name"):
                            self._emit("ToolExecutor", "skill_hit", args.get("skill_name"))
                    except (json.JSONDecodeError, TypeError):
                        pass

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
