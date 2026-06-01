"""tests/matmaster/tools/builtin/test_plot_figure_tool.py"""

import asyncio

from matmaster.tools.builtin.plot_figure_tool import PlotFigure
from matmaster.types.topology import ToolPlane


def validate(tool, args):
    return asyncio.run(tool.validate_input(args))


class TestPlotFigureMetadata:
    def test_name(self):
        assert PlotFigure.name == "PlotFigure"

    def test_plane(self):
        assert PlotFigure.plane == ToolPlane.SESSION_SHELL

    def test_schema_requires_output_path_and_caption(self):
        assert PlotFigure.json_schema["required"] == ["output_path", "caption"]
        assert PlotFigure.json_schema["additionalProperties"] is False

    def test_has_prompt(self):
        assert "PlotFigure" in (PlotFigure(workdir="/share").prompt() or "")


class TestPlotFigureValidateInput:
    def test_missing_output_path_denied(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"caption": "c"})
        assert d is not None and d.decision == "deny"

    def test_missing_caption_denied(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "band.png"})
        assert d is not None and d.decision == "deny"

    def test_empty_command_denied(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "band.png", "caption": "c", "command": "   "})
        assert d is not None and d.decision == "deny"

    def test_escape_output_path_denied(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "../x.png", "caption": "c"})
        assert d is not None and d.decision == "deny"

    def test_valid_relative_allowed(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "results/band.png", "caption": "c"})
        assert d is None

    def test_valid_with_command_allowed(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "band.png", "caption": "c", "command": "python p.py"})
        assert d is None


from unittest.mock import MagicMock

from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_session(payload=_PNG):
    s = MagicMock()
    s.path_exists.return_value = True
    s.is_file.return_value = True
    s.download.return_value = payload
    s.exec_bash.return_value = {"exit_code": 0, "stdout": ""}
    return s


def make_upload_config(url="https://assets.test/u/fig.png"):
    return FigureUploadConfig(
        session_id="s", task_id="t", asset_key_prefix="figs",
        upload_bytes=lambda payload, key: url,
    )


def make_ctx(session, upload_config, tool_call_id="call-1"):
    state = ToolRunnerState()
    state.set("figure_upload_config", upload_config)
    return ToolExecutionContext(
        runner_state=state,
        tool_call_id=tool_call_id,
    )


def run_ctx(tool, args, ctx):
    return asyncio.run(tool.execute_with_context(args, ctx))


class TestPlotFigureNoCommand:
    def test_publishes_existing_image(self):
        session = make_session()
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(tool, {"output_path": "results/band.png", "caption": "Band"}, ctx)
        assert result.status == "success"
        assert result.payload["figures"]
        fig = result.payload["figures"][0]
        assert fig["caption"] == "Band"
        assert f"[[fig:{fig['figure_id']}]]" in result.content
        assert fig["figure_id"] in result.content

    def test_does_not_exec_shell(self):
        session = make_session()
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        run_ctx(tool, {"output_path": "band.png", "caption": "c"}, ctx)
        # The only exec_bash allowed is the flat-view symlink; never a user command.
        for call in session.exec_bash.call_args_list:
            cmd = call.kwargs.get("command") or (call.args[0] if call.args else "")
            assert "ln -s" in cmd or "mkdir -p" in cmd

    def test_missing_file_returns_error(self):
        session = make_session()
        session.path_exists.return_value = False
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(tool, {"output_path": "band.png", "caption": "c"}, ctx)
        assert result.status == "error"
        assert "file_not_found" in result.content
        assert not result.payload.get("figures")

    def test_missing_upload_config_returns_error(self):
        session = make_session()
        tool = PlotFigure(session=session, workdir="/share")
        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state, tool_call_id="call-1")
        result = run_ctx(tool, {"output_path": "band.png", "caption": "c"}, ctx)
        assert result.status == "error"
        assert "not configured" in result.content

    def test_missing_tool_call_id_returns_error(self):
        session = make_session()
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config(), tool_call_id=None)
        result = run_ctx(tool, {"output_path": "band.png", "caption": "c"}, ctx)
        assert result.status == "error"


def make_cmd_session(exit_code=0, output="done", payload=_PNG, file_after=True):
    s = MagicMock()
    s.exec_bash.return_value = {
        "output": output,
        "exit_code": exit_code,
        "working_dir": "/share",
        "stdout": "",
    }
    s.path_exists.return_value = file_after
    s.is_file.return_value = True
    s.download.return_value = payload
    return s


class TestPlotFigureWithCommand:
    def test_command_success_and_figure(self):
        session = make_cmd_session(exit_code=0)
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(
            tool,
            {"command": "python plot.py", "output_path": "xrd.png", "caption": "XRD"},
            ctx,
        )
        assert result.status == "success"
        assert result.payload["figures"]
        assert "[Command finished with exit code 0]" in result.content
        assert "[[fig:" in result.content

    def test_command_fails_but_figure_collected(self):
        session = make_cmd_session(exit_code=1, file_after=True)
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(
            tool,
            {"command": "python plot.py", "output_path": "xrd.png", "caption": "XRD"},
            ctx,
        )
        assert result.status == "error"
        assert result.payload["figures"]  # figure survives a failed command
        assert "[Command finished with exit code 1]" in result.content

    def test_command_succeeds_but_no_figure(self):
        session = make_cmd_session(exit_code=0, file_after=False)
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(
            tool,
            {"command": "python plot.py", "output_path": "xrd.png", "caption": "XRD"},
            ctx,
        )
        assert result.status == "error"
        assert not result.payload.get("figures")
        assert "file_not_found" in result.content
