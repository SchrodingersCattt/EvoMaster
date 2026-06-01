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
