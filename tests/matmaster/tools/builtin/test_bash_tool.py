"""tests/matmaster/tools/builtin/test_bash_tool.py"""
import asyncio
import pytest
from unittest.mock import MagicMock
from matmaster.tools.builtin.bash_tool import BashTool


def make_session(output="hello", exit_code=0, working_dir="/workspace"):
    s = MagicMock()
    s.exec_bash.return_value = {
        "output": output,
        "exit_code": exit_code,
        "working_dir": working_dir,
    }
    return s


class TestBashToolMetadata:
    def test_name(self):
        assert BashTool.name == "Bash"

    def test_plane(self):
        from matmaster.types.topology import ToolPlane
        assert BashTool.plane == ToolPlane.SESSION_SHELL

    def test_has_prompt(self):
        tool = BashTool()
        assert tool.prompt() is not None
        assert "Read" in tool.prompt()


class TestBashExecution:
    def test_simple_command(self):
        session = make_session(output="hello")
        tool = BashTool(session=session, workdir="/workspace")
        result = asyncio.run(tool.execute({"command": "echo hello"}))
        assert "hello" in result

    def test_empty_command_error(self):
        tool = BashTool(session=make_session())
        result = asyncio.run(tool.execute({"command": ""}))
        assert "error" in result.lower()

    def test_exit_code_in_output(self):
        session = make_session(exit_code=1)
        tool = BashTool(session=session)
        result = asyncio.run(tool.execute({"command": "false"}))
        assert "exit code 1" in result.lower()

    def test_timeout_conversion_ms_to_s(self):
        session = make_session()
        tool = BashTool(session=session)
        asyncio.run(tool.execute({"command": "ls", "timeout": 5000}))
        call_args = session.exec_bash.call_args
        assert call_args.kwargs.get("timeout") == 5 or call_args[1].get("timeout") == 5

    def test_no_session_error(self):
        tool = BashTool()
        result = asyncio.run(tool.execute({"command": "ls"}))
        assert "error" in result.lower()
