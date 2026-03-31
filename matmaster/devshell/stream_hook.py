"""DevStreamHook -- real-time terminal output for devshell REPL."""

from __future__ import annotations

import json
import sys
from typing import TextIO

from matmaster.core.hooks import BaseHook, HookAction
from matmaster.tools.tool_result import ToolResult
from matmaster.types.guards import GuardResult
from matmaster.types.messages import StreamChunk, ToolCallData

_MAX_RESULT_LEN = 1000


class DevStreamHook(BaseHook):
    """Hook that formats kernel events for terminal display.

    Writes directly to the provided output stream (default: sys.stdout).
    """

    def __init__(
        self,
        output: TextIO | None = None,
        verbose: bool = False,
    ) -> None:
        self._out = output or sys.stdout
        self._verbose = verbose

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        if chunk.stream_state == "start":
            return
        if chunk.stream_state == "end":
            self._out.write("\n")
            self._out.flush()
            return
        if chunk.content:
            self._out.write(chunk.content)
            self._out.flush()

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        args_str = json.dumps(tool_call.arguments, ensure_ascii=False, indent=2)
        self._out.write(f"\n\U0001f4ce tool_call: {tool_call.name}\n")
        for line in args_str.split("\n"):
            self._out.write(f"   {line}\n")
        self._out.flush()
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        is_error = result.status == "error"
        prefix = "\u274c tool_error:" if is_error else "\u2705 tool_result:"
        display = result.content
        if len(display) > _MAX_RESULT_LEN:
            display = display[:_MAX_RESULT_LEN] + "..."
        self._out.write(f"\n{prefix} {display}\n\n")
        self._out.flush()

    async def on_guard_blocked(
        self, tool_call: ToolCallData, result: GuardResult
    ) -> None:
        self._out.write(f"\n\U0001f6e1\ufe0f guard_blocked: {result.reason}\n\n")
        self._out.flush()

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None:
        if segment_type == "thought" and self._verbose:
            self._out.write("── thought complete ──\n")
            self._out.flush()
