"""Tests for DevStreamHook terminal output formatting."""
from __future__ import annotations

import io
from typing import Any

from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import StreamChunk, ToolCallData
from matmaster.types.guards import GuardResult


class TestDevStreamHook:
    def _make_hook(self, verbose: bool = False) -> tuple:
        from matmaster.devshell.stream_hook import DevStreamHook

        buf = io.StringIO()
        hook = DevStreamHook(output=buf, verbose=verbose)
        return hook, buf

    async def test_stream_chunk_content(self) -> None:
        hook, buf = self._make_hook()
        chunk = StreamChunk(content="Hello", stream_state="streaming", stream_id="s1")
        await hook.on_stream_chunk(chunk)
        assert buf.getvalue() == "Hello"

    async def test_stream_chunk_start_end_no_content(self) -> None:
        hook, buf = self._make_hook()
        await hook.on_stream_chunk(StreamChunk(stream_state="start", stream_id="s1"))
        await hook.on_stream_chunk(StreamChunk(stream_state="end", stream_id="s1"))
        # end should add newline
        assert buf.getvalue() == "\n"

    async def test_pre_tool_call_display(self) -> None:
        from matmaster.core.hooks import HookAction

        hook, buf = self._make_hook()
        tc = ToolCallData(id="tc-1", name="bash", arguments={"command": "ls"})
        action = await hook.pre_tool_call(tc)

        assert action == HookAction.CONTINUE
        output = buf.getvalue()
        assert "tool_call: bash" in output
        assert "command" in output

    async def test_post_tool_call_success(self) -> None:
        hook, buf = self._make_hook()
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        await hook.post_tool_call(tc, ToolResult(content="file1.py\nfile2.py"))

        output = buf.getvalue()
        assert "tool_result:" in output
        assert "file1.py" in output

    async def test_post_tool_call_truncation(self) -> None:
        hook, buf = self._make_hook()
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        long_result = "x" * 2000
        await hook.post_tool_call(tc, ToolResult(content=long_result))

        output = buf.getvalue()
        assert "..." in output or len(output) < 2000

    async def test_post_tool_call_error_status(self) -> None:
        hook, buf = self._make_hook()
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        await hook.post_tool_call(
            tc,
            ToolResult(status="error", content="Error: boom"),
        )

        output = buf.getvalue()
        assert "tool_error:" in output
        assert "Error: boom" in output

    async def test_guard_blocked(self) -> None:
        hook, buf = self._make_hook()
        tc = ToolCallData(id="tc-1", name="rm_rf", arguments={})
        gr = GuardResult(allowed=False, reason="dangerous operation")
        await hook.on_guard_blocked(tc, gr)

        output = buf.getvalue()
        assert "guard_blocked:" in output
        assert "dangerous operation" in output
