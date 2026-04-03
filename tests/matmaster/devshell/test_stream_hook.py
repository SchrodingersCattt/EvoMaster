"""Tests for DevStreamHook terminal output formatting."""

from __future__ import annotations

import io

from matmaster.types.events import ResponseEvent, ThoughtEvent, ToolCallEvent, ToolResultEvent


class TestDevStreamHook:
    def _make_hook(self, verbose: bool = False) -> tuple:
        from matmaster.devshell.stream_hook import DevStreamHook

        buf = io.StringIO()
        hook = DevStreamHook(output=buf, verbose=verbose)
        return hook, buf

    def test_on_event_writes_stream_content(self) -> None:
        hook, buf = self._make_hook()

        hook.on_event(ThoughtEvent(source="agent", content="Hello", stream_state="streaming", stream_id="s1"))
        hook.on_event(ResponseEvent(source="agent", content=" world", stream_state="streaming", stream_id="s1"))

        assert buf.getvalue() == "Hello world"

    def test_on_event_handles_start_and_end_markers(self) -> None:
        hook, buf = self._make_hook()

        hook.on_event(ThoughtEvent(source="agent", content="", stream_state="start", stream_id="s1"))
        hook.on_event(ResponseEvent(source="agent", content="", stream_state="end", stream_id="s1"))

        assert buf.getvalue() == "\n"

    def test_on_event_formats_tool_call(self) -> None:
        hook, buf = self._make_hook()

        hook.on_event(
            ToolCallEvent(
                source="agent",
                call_id="tc-1",
                tool_name="bash",
                arguments={"command": "ls"},
            )
        )

        output = buf.getvalue()
        assert "tool_call: bash" in output
        assert "command" in output

    def test_on_event_formats_successful_tool_result(self) -> None:
        hook, buf = self._make_hook()

        hook.on_event(
            ToolResultEvent(
                source="agent",
                call_id="tc-1",
                tool_name="bash",
                result="file1.py\nfile2.py",
                status="success",
            )
        )

        output = buf.getvalue()
        assert "tool_result:" in output
        assert "file1.py" in output

    def test_on_event_formats_error_tool_result(self) -> None:
        hook, buf = self._make_hook()

        hook.on_event(
            ToolResultEvent(
                source="agent",
                call_id="tc-1",
                tool_name="bash",
                result="Error: boom",
                status="error",
            )
        )

        output = buf.getvalue()
        assert "tool_error:" in output
        assert "Error: boom" in output
