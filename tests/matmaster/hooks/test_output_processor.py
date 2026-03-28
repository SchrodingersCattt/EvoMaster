"""Tests for OutputProcessorHook."""

from __future__ import annotations

from unittest.mock import MagicMock

from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import ToolCallData


class TestOutputProcessorHook:
    """OutputProcessorHook post_tool_call behavior."""

    def test_emits_auto_save_when_tool_matches_pattern(self) -> None:
        """post_tool_call emits ToolResultEvent with auto_save info when matched."""
        from matmaster.hooks.output_processor import OutputProcessorHook
        from matmaster.types.events import ToolResultEvent

        bus = MagicMock()
        hook = OutputProcessorHook(
            bus=bus,
            auto_save_patterns=["write_file", "save_"],
        )
        tc = ToolCallData(id="tc-1", name="write_file", arguments={})
        hook.post_tool_call(
            tc,
            ToolResult(
                status="error",
                content="file written",
                info={"error": "boom"},
            ),
        )

        bus.emit_nowait.assert_called()
        emitted = bus.emit_nowait.call_args[0][0]
        assert isinstance(emitted, ToolResultEvent)
        assert emitted.status == "error"
        assert emitted.info == {"error": "boom", "auto_save": True}

    def test_emits_summarize_when_tool_matches_pattern(self) -> None:
        """post_tool_call emits ToolResultEvent with summarize info when matched."""
        from matmaster.hooks.output_processor import OutputProcessorHook
        from matmaster.types.events import ToolResultEvent

        bus = MagicMock()
        hook = OutputProcessorHook(
            bus=bus,
            summarize_patterns=["read_large", "fetch_data"],
        )
        tc = ToolCallData(id="tc-1", name="read_large_document", arguments={})
        hook.post_tool_call(
            tc,
            ToolResult(
                content="very long text...",
                info={"saved_path": "/tmp/out.txt"},
            ),
        )

        bus.emit_nowait.assert_called()
        emitted = bus.emit_nowait.call_args[0][0]
        assert isinstance(emitted, ToolResultEvent)
        assert emitted.status == "success"
        assert emitted.info == {
            "saved_path": "/tmp/out.txt",
            "summarize": True,
        }

    def test_does_nothing_when_no_pattern_matches(self) -> None:
        """post_tool_call does nothing when tool_name matches no patterns."""
        from matmaster.hooks.output_processor import OutputProcessorHook

        bus = MagicMock()
        hook = OutputProcessorHook(
            bus=bus,
            auto_save_patterns=["write_file"],
            summarize_patterns=["read_large"],
        )
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        hook.post_tool_call(tc, ToolResult(content="result"))

        bus.emit_nowait.assert_not_called()

    def test_does_nothing_when_no_patterns_configured(self) -> None:
        """post_tool_call does nothing when no patterns configured."""
        from matmaster.hooks.output_processor import OutputProcessorHook

        bus = MagicMock()
        hook = OutputProcessorHook(bus=bus)
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        hook.post_tool_call(tc, ToolResult(content="result"))

        bus.emit_nowait.assert_not_called()
