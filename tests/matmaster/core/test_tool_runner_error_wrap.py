"""Tests for error content wrapping in FullToolRunner._execute_one pipeline."""

from matmaster.tools.tool_result import ToolResult, normalize_tool_result


class TestErrorContentWrapping:
    """Test the error wrapping logic that will be added to tool_runner.

    These tests validate the wrapping behavior in isolation,
    matching the logic inserted in FullToolRunner._execute_one().
    """

    def _apply_error_wrap(self, tr: ToolResult) -> ToolResult:
        """Replicate the error_wrap step from _execute_one."""
        if tr.status == "error" and not tr.content.lstrip().startswith("<error>\n"):
            tr = tr.model_copy(update={"content": f"<error>\n{tr.content}\n</error>"})
        return tr

    def test_error_result_gets_wrapped(self):
        tr = ToolResult(status="error", content="something failed")
        wrapped = self._apply_error_wrap(tr)
        assert wrapped.content == "<error>\nsomething failed\n</error>"
        assert wrapped.status == "error"

    def test_success_result_not_wrapped(self):
        tr = ToolResult(status="success", content="all good")
        result = self._apply_error_wrap(tr)
        assert result.content == "all good"

    def test_already_wrapped_not_double_wrapped(self):
        tr = ToolResult(status="error", content="<error>\nalready tagged\n</error>")
        result = self._apply_error_wrap(tr)
        assert result.content.count("<error>") == 1

    def test_empty_content_error_wrapped(self):
        tr = ToolResult(status="error", content="")
        wrapped = self._apply_error_wrap(tr)
        assert wrapped.content == "<error>\n\n</error>"

    def test_normalize_then_wrap_pipeline(self):
        """Simulate the full normalize -> error_wrap pipeline for a bash error."""
        raw = ToolResult(
            status="error", content="Traceback...\n[Command finished with exit code 1]"
        )
        normalized = normalize_tool_result(raw)
        wrapped = self._apply_error_wrap(normalized)
        assert wrapped.status == "error"
        assert wrapped.content.startswith("<error>\n")
        assert wrapped.content.endswith("\n</error>")
        assert "Traceback" in wrapped.content

    def test_normalize_error_prefix_then_wrap(self):
        """base.py exception path: 'Error: ...' string -> normalize -> wrap."""
        normalized = normalize_tool_result("Error: something broke")
        wrapped = self._apply_error_wrap(normalized)
        assert wrapped.status == "error"
        assert "<error>" in wrapped.content
