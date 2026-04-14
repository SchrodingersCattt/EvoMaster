"""tests/matmaster/tools/builtin/test_bash_tool.py"""

import asyncio
from unittest.mock import MagicMock

import matmaster.tools.builtin.bash_tool as bash_tool_module
from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext
from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.figure_artifacts import FigureCollectionResult, build_figure_env
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext
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
        desc_ctx = ToolDescriptionContext(
            session_kind="local",
            workspace_root="/tmp/workspace",
            topology=topology,
        )

        defs = catalog.build_definitions(desc_ctx)

        bash_def = next(d for d in defs if d["function"]["name"] == "Bash")
        assert bash_def["function"]["description"] == BashTool().prompt(desc_ctx)
        assert (
            "Use dedicated tools instead of shell equivalents"
            in bash_def["function"]["description"]
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
    def _figure_state(
        self,
        figure_upload_config: FigureUploadConfig | dict[str, object],
    ) -> ToolRunnerState:
        state = ToolRunnerState()
        state.set("figure_upload_config", figure_upload_config)
        return state

    def _valid_figure_config(self) -> FigureUploadConfig:
        return FigureUploadConfig(
            session_id="sess-1",
            task_id="task-1",
            asset_key_prefix="matmaster/chat_figures",
            upload_bytes=lambda data, key: f"https://oss.example/{key}",
        )

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

    def test_bash_injects_figure_env_and_returns_payload_figures(self) -> None:
        session = MagicMock()
        session.exec_bash.return_value = {
            "output": "done",
            "exit_code": 0,
            "working_dir": "/share",
        }
        session.path_exists.return_value = True
        session.read_file.return_value = (
            '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
        )
        session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 64

        tool = BashTool(session=session, workdir="/share")
        result = asyncio.run(
            tool.execute_with_context(
                {"command": "python render.py"},
                ToolExecutionContext(
                    runner_state=self._figure_state(self._valid_figure_config()),
                    tool_call_id="call-band",
                ),
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.payload["figures"][0]["figure_id"] == "band"

        artifact_dir, _manifest_path = build_figure_env("/share", "call-band")
        mkdir_calls = [
            call
            for call in session.exec_bash.call_args_list
            if call.args and call.args[0] == f"mkdir -p {artifact_dir}"
        ]
        assert mkdir_calls, "expected BashTool to create ARTIFACT_DIR before execution"

        final_exec_call = session.exec_bash.call_args_list[-1]
        assert "python render.py" in final_exec_call.kwargs["command"]

        write_payloads = [call.args[1] for call in session.write_file.call_args_list]
        assert any("ARTIFACT_DIR=" in payload for payload in write_payloads)
        assert any("MANIFEST_PATH=" in payload for payload in write_payloads)

    def test_bash_without_manifest_keeps_legacy_success_string(self) -> None:
        session = MagicMock()
        session.exec_bash.return_value = {
            "output": "hello",
            "exit_code": 0,
            "working_dir": "/share",
        }
        session.path_exists.return_value = False

        tool = BashTool(session=session, workdir="/share")
        result = asyncio.run(tool.execute({"command": "echo hello"}))

        assert isinstance(result, str)
        assert "hello" in result

    def test_invalid_figure_upload_config_does_not_block_command_execution(self) -> None:
        session = MagicMock()
        session.exec_bash.return_value = {
            "output": "hello",
            "exit_code": 0,
            "working_dir": "/share",
        }

        tool = BashTool(session=session, workdir="/share")
        result = asyncio.run(
            tool.execute_with_context(
                {"command": "echo hello"},
                ToolExecutionContext(
                    runner_state=self._figure_state({"session_id": "sess-1"}),
                    tool_call_id="call-invalid",
                ),
            )
        )

        assert isinstance(result, str)
        assert "hello" in result
        assert session.exec_bash.call_count == 1
        final_exec_call = session.exec_bash.call_args_list[-1]
        assert final_exec_call.kwargs["command"] == "echo hello"

    def test_nonzero_exit_with_manifest_activity_returns_error_tool_result(self) -> None:
        session = MagicMock()
        session.exec_bash = MagicMock(
            side_effect=[
                {"stdout": "", "stderr": "", "exit_code": 0},
                {"stdout": "", "stderr": "", "exit_code": 0},
                {
                    "output": "boom",
                    "exit_code": 2,
                    "working_dir": "/share",
                },
            ]
        )
        session.path_exists.return_value = True
        session.read_file.return_value = (
            '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
        )
        session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 64

        tool = BashTool(session=session, workdir="/share")
        result = asyncio.run(
            tool.execute_with_context(
                {"command": "python render.py"},
                ToolExecutionContext(
                    runner_state=self._figure_state(self._valid_figure_config()),
                    tool_call_id="call-band",
                ),
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert result.payload["figures"][0]["figure_id"] == "band"
        assert "exit code 2" in result.content

    def test_script_mode_with_figure_env_injects_exports_via_script_file(self) -> None:
        session = MagicMock()
        session.write_file = MagicMock()
        session.exec_bash = MagicMock(
            side_effect=[
                {"stdout": "", "stderr": "", "exit_code": 0},
                {"stdout": "", "stderr": "", "exit_code": 0},
                {
                    "stdout": "ok",
                    "stderr": "",
                    "exit_code": 0,
                    "working_dir": "/share",
                    "output": "ok",
                },
            ]
        )
        session.path_exists.return_value = False

        tool = BashTool(session=session, workdir="/share")
        asyncio.run(
            tool.execute_with_context(
                {"command": "python3 << 'PYEOF'\nprint(1)\nPYEOF"},
                ToolExecutionContext(
                    runner_state=self._figure_state(self._valid_figure_config()),
                    tool_call_id="call-script",
                ),
            )
        )

        artifact_dir, manifest_path = build_figure_env("/share", "call-script")
        written_script = session.write_file.call_args[0][1]
        assert f"export ARTIFACT_DIR={artifact_dir}" in written_script
        assert f"export MANIFEST_PATH={manifest_path}" in written_script
        final_exec_call = session.exec_bash.call_args_list[-1]
        assert final_exec_call.kwargs["command"].startswith("bash ")

    def test_warnings_only_collection_returns_tool_result_with_empty_figures(
        self,
        monkeypatch,
    ) -> None:
        session = MagicMock()
        session.exec_bash = MagicMock(
            side_effect=[
                {"stdout": "", "stderr": "", "exit_code": 0},
                {"stdout": "", "stderr": "", "exit_code": 0},
                {
                    "output": "done",
                    "exit_code": 0,
                    "working_dir": "/share",
                },
            ]
        )
        monkeypatch.setattr(
            bash_tool_module,
            "collect_figures_from_session",
            lambda **kwargs: FigureCollectionResult(
                figures=[],
                failure_ids=[],
                warnings=["invalid_manifest: malformed_or_missing_figures_list"],
            ),
        )

        tool = BashTool(session=session, workdir="/share")
        result = asyncio.run(
            tool.execute_with_context(
                {"command": "python render.py"},
                ToolExecutionContext(
                    runner_state=self._figure_state(self._valid_figure_config()),
                    tool_call_id="call-warn",
                ),
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.payload == {"figures": []}
        assert "Figure manifest ignored" in result.content

    def test_failure_only_collection_returns_tool_result_with_empty_figures(
        self,
        monkeypatch,
    ) -> None:
        session = MagicMock()
        session.exec_bash = MagicMock(
            side_effect=[
                {"stdout": "", "stderr": "", "exit_code": 0},
                {"stdout": "", "stderr": "", "exit_code": 0},
                {
                    "output": "done",
                    "exit_code": 0,
                    "working_dir": "/share",
                },
            ]
        )
        monkeypatch.setattr(
            bash_tool_module,
            "collect_figures_from_session",
            lambda **kwargs: FigureCollectionResult(
                figures=[],
                failure_ids=["band"],
                warnings=[],
            ),
        )

        tool = BashTool(session=session, workdir="/share")
        result = asyncio.run(
            tool.execute_with_context(
                {"command": "python render.py"},
                ToolExecutionContext(
                    runner_state=self._figure_state(self._valid_figure_config()),
                    tool_call_id="call-fail",
                ),
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.payload == {"figures": []}
        assert "Figure pipeline: 1 failed: band" in result.content
