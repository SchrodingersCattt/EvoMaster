"""tests/matmaster/tools/builtin/test_glob_tool.py"""
import asyncio
from unittest.mock import MagicMock
from matmaster.tools.builtin.glob_tool import GlobTool


def make_session(output="", exit_code=0):
    s = MagicMock()
    s.exec_bash.return_value = {"output": output, "exit_code": exit_code}
    return s


class TestGlobToolMetadata:
    def test_name(self):
        assert GlobTool.name == "Glob"

    def test_effect_level(self):
        assert GlobTool.effect_level == "none"

    def test_fast_path(self):
        assert GlobTool.fast_path_eligible is True


class TestGlobExecution:
    def test_no_results(self):
        tool = GlobTool(session=make_session(output=""), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": "*.xyz"}))
        assert "no files" in result.lower()

    def test_results_returned(self):
        tool = GlobTool(session=make_session(output="/workspace/a.py\n/workspace/b.py"), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": "*.py"}))
        assert "a.py" in result

    def test_shell_escape_applied(self):
        """Pattern with shell-dangerous chars should be escaped."""
        session = make_session(output="")
        tool = GlobTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "$(rm -rf /)"}))
        cmd = session.exec_bash.call_args[1].get("command") or session.exec_bash.call_args[0][0]
        assert "$(" not in cmd or "'" in cmd  # should be quoted
