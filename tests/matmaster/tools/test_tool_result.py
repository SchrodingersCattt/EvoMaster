"""Tests for ToolResult and normalize_tool_result."""

from __future__ import annotations

from matmaster.tools.tool_result import ToolResult, normalize_tool_result


class TestToolResult:
    def test_defaults(self) -> None:
        result = ToolResult()
        assert result.status == "success"
        assert result.content == ""
        assert result.info == {}

    def test_normalize_success_string(self) -> None:
        result = normalize_tool_result("hello")
        assert result.status == "success"
        assert result.content == "hello"
        assert result.info == {}

    def test_normalize_error_prefixed_string(self) -> None:
        result = normalize_tool_result("Error: boom")
        assert result.status == "error"
        assert result.content == "Error: boom"

    def test_normalize_none(self) -> None:
        result = normalize_tool_result(None)
        assert result.status == "success"
        assert result.content == ""

    def test_explicit_tool_result_is_preserved(self) -> None:
        raw = ToolResult(
            status="success",
            content="Error: literal text",
            info={"source": "explicit"},
        )
        result = normalize_tool_result(raw)
        assert result is raw
        assert result.status == "success"
        assert result.info == {"source": "explicit"}
