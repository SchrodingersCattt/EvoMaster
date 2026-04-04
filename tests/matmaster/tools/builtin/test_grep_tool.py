"""tests/matmaster/tools/builtin/test_grep_tool.py"""
import asyncio
from unittest.mock import MagicMock
from matmaster.tools.builtin.grep_tool import GrepTool


def make_session(output="", exit_code=0):
    s = MagicMock()
    s.exec_bash.return_value = {"output": output, "exit_code": exit_code}
    return s


class TestGrepToolMetadata:
    def test_name(self):
        assert GrepTool.name == "Grep"

    def test_schema_has_output_mode(self):
        assert "output_mode" in GrepTool.json_schema["properties"]

    def test_schema_has_context_flags(self):
        props = GrepTool.json_schema["properties"]
        assert "-A" in props
        assert "-B" in props
        assert "-C" in props


class TestGrepExecution:
    def test_no_matches(self):
        tool = GrepTool(session=make_session(output=""), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": "notfound"}))
        assert "no matches" in result.lower()

    def test_files_with_matches_mode(self):
        tool = GrepTool(
            session=make_session(output="/workspace/a.py\n/workspace/b.py"),
            workdir="/workspace",
        )
        result = asyncio.run(tool.execute({
            "pattern": "import",
            "output_mode": "files_with_matches",
        }))
        assert "a.py" in result

    def test_content_mode(self):
        output = "/workspace/a.py:1:import os"
        tool = GrepTool(session=make_session(output=output), workdir="/workspace")
        result = asyncio.run(tool.execute({
            "pattern": "import",
            "output_mode": "content",
        }))
        assert "import os" in result

    def test_shell_escape_pattern(self):
        session = make_session(output="")
        tool = GrepTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "$(evil)"}))
        cmd = session.exec_bash.call_args[1].get("command") or session.exec_bash.call_args[0][0]
        assert "$(" not in cmd.split("'")[0]  # pattern should be escaped


class TestGrepRgDetection:
    def test_rg_detection_cached(self):
        session = make_session(output="")
        # First call detects rg
        rg_check = MagicMock()
        rg_check.return_value = {"output": "/usr/bin/rg", "exit_code": 0}
        session.exec_bash.side_effect = [
            {"output": "/usr/bin/rg", "exit_code": 0},  # which rg
            {"output": "", "exit_code": 1},               # actual grep
        ]
        tool = GrepTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "test"}))
        assert tool._use_rg is True
