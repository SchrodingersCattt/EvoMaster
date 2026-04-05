"""tests/matmaster/tools/builtin/test_bash_tool.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.topology import RuntimeTopology


def make_session(output="hello", exit_code=0, working_dir="/workspace"):
    s = MagicMock()
    s.exec_bash.return_value = {
        "output": output,
        "exit_code": exit_code,
        "working_dir": working_dir,
    }
    return s


def make_desc_ctx(session_kind="ssh", workspace_root="/share"):
    topo = RuntimeTopology(
        session_kind=session_kind,
        control_root="/control",
        workspace_root=workspace_root,
    )
    return ToolDescriptionContext(
        session_kind=session_kind,
        workspace_root=workspace_root,
        topology=topo,
    )


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

    def test_success_output_labels_session_working_directory(self):
        session = make_session(output="hello", working_dir="/workspace")
        tool = BashTool(session=session, workdir="/workspace")
        result = asyncio.run(tool.execute({"command": "pwd"}))

        assert "[Session working directory: /workspace]" in result
        assert "[Current working directory:" not in result

    def test_empty_command_error(self):
        tool = BashTool(session=make_session())
        result = asyncio.run(tool.execute({"command": ""}))
        assert "error" in result.lower()

    def test_exit_code_in_output(self):
        session = make_session(exit_code=1)
        tool = BashTool(session=session)
        result = asyncio.run(tool.execute({"command": "false"}))
        # After fix: non-zero exit returns ToolResult, not str
        assert isinstance(result, ToolResult)
        assert "exit code 1" in result.content.lower()

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


class TestBashErrorStatus:
    def test_nonzero_exit_returns_error_status(self):
        session = make_session(
            output="Traceback...\nModuleNotFoundError: No module named 'pymatgen'",
            exit_code=1,
        )
        tool = BashTool(session=session, workdir="/workspace")
        result = asyncio.run(tool.execute({"command": "python -c 'import pymatgen'"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "exit code 1" in result.content.lower()

    def test_nonzero_exit_preserves_full_content(self):
        session = make_session(output="some output", exit_code=2, working_dir="/tmp")
        tool = BashTool(session=session, workdir="/workspace")
        result = asyncio.run(tool.execute({"command": "exit 2"}))
        assert isinstance(result, ToolResult)
        assert "some output" in result.content
        assert "/tmp" in result.content
        assert "exit code 2" in result.content

    def test_zero_exit_returns_success_string(self):
        session = make_session(output="hello", exit_code=0)
        tool = BashTool(session=session, workdir="/workspace")
        result = asyncio.run(tool.execute({"command": "echo hello"}))
        assert isinstance(result, str)
        assert "hello" in result
