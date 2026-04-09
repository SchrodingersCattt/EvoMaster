"""tests/matmaster/tools/builtin/test_bash_tool.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext
from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
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


def attach_test_runtime(session: MagicMock) -> None:
    runtime = BohriumRuntimeHandle(
        credentials=BohriumCredentials(
            access_key="ak",
            project_id=42,
            user_id=7,
            user_no="U001",
            base_url="https://openapi.test.dp.tech",
        ),
        execution=BohriumExecutionContext(
            session_type="ssh",
            execution_workdir="/share",
            remote_workspace_root="/share",
            remote_project_root="/share/.matmaster",
            node_id=1,
            node_ip="10.0.0.1",
            ssh_attached=True,
        ),
        execution_session=session,
    )
    attach_runtime(session, runtime)


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

    def test_describe_returns_static_description(self):
        tool = BashTool()
        ctx = make_desc_ctx(session_kind="local", workspace_root="/tmp/workspace")
        assert tool.describe(ctx) == tool.description
        assert tool.describe(ctx) != tool.prompt(ctx)

    def test_definition_description_does_not_include_prompt_text(self):
        registry = ToolRegistry()
        registry.register(BashTool(), source="builtin")
        topology = RuntimeTopology(
            session_kind="local",
            control_root="/tmp/control",
            workspace_root="/tmp/workspace",
        )
        catalog = ToolCatalog(registry, topology=topology)

        defs = catalog.build_definitions(
            ToolDescriptionContext(
                session_kind="local",
                workspace_root="/tmp/workspace",
                topology=topology,
            )
        )

        bash_def = next(d for d in defs if d["function"]["name"] == "Bash")
        assert bash_def["function"]["description"] == BashTool.description
        assert (
            "Use dedicated tools instead of shell equivalents"
            not in bash_def["function"]["description"]
        )

    def test_schema_disallows_additional_properties(self):
        assert BashTool.json_schema["additionalProperties"] is False

    def test_bash_claims_workspace_and_session_exclusive(self):
        claims = {(claim.resource, claim.mode) for claim in BashTool.resource_claims}
        assert ("workspace", "exclusive") in claims
        assert ("session", "exclusive") in claims


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

    def test_bash_tool_uses_script_mode_for_heredoc(self):
        session = MagicMock()
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            side_effect=[
                {"stdout": "", "stderr": "", "exit_code": 0},
                {
                    "stdout": "ok",
                    "stderr": "",
                    "exit_code": 0,
                    "working_dir": "/workspace",
                    "output": "ok",
                },
            ]
        )
        tool = BashTool(session=session, workdir="/workspace")
        asyncio.run(
            tool.execute(
                {"command": "python3 << 'PYEOF'\nprint(1)\nPYEOF", "description": "run"}
            )
        )
        written = session.write_file.call_args[0][1]
        assert "python3 << 'PYEOF'" in written
        final_cmd = session.exec_bash.call_args.kwargs["command"]
        assert final_cmd.startswith("bash ")

    def test_bash_heredoc_does_not_inline_original_command(self):
        session = MagicMock()
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            side_effect=[
                {"stdout": "", "stderr": "", "exit_code": 0},
                {
                    "stdout": "ok",
                    "stderr": "",
                    "exit_code": 0,
                    "working_dir": "/workspace",
                    "output": "ok",
                },
            ]
        )
        tool = BashTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"command": "python3 << 'PYEOF'\nprint(1)\nPYEOF"}))
        final_cmd = session.exec_bash.call_args.kwargs["command"]
        assert "python3 << 'PYEOF'" not in final_cmd
        assert final_cmd.startswith("bash ")


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


class TestBashEnvInjection:
    def test_bash_reads_runtime_env(self):
        session = MagicMock()
        attach_test_runtime(session)
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            side_effect=[
                {"stdout": "", "stderr": "", "exit_code": 0},
                {
                    "stdout": "hi",
                    "stderr": "",
                    "exit_code": 0,
                    "working_dir": "/workspace",
                    "output": "hi",
                },
            ]
        )
        tool = BashTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"command": "echo hi"}))
        final_call = session.exec_bash.call_args_list[-1]
        assert final_call.kwargs["command"] != "echo hi"
        assert "echo hi" in final_call.kwargs["command"]
        assert session.write_file.called
