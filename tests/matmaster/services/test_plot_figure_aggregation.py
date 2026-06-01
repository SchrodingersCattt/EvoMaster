"""tests/matmaster/services/test_plot_figure_aggregation.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.plot_figure_tool import PlotFigure
from matmaster.types.events import ToolResultEvent
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext
from src.services.response_figures_service import ResponseFiguresAccumulator

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_session(payload=_PNG, exit_code=0, file_after=True):
    s = MagicMock()
    s.exec_bash.return_value = {
        "output": "done", "exit_code": exit_code, "working_dir": "/share", "stdout": "",
    }
    s.path_exists.return_value = file_after
    s.is_file.return_value = True
    s.download.return_value = payload
    return s


def make_upload_config(url):
    return FigureUploadConfig(
        session_id="s", task_id="t", asset_key_prefix="figs",
        upload_bytes=lambda payload, key: url,
    )


def publish(tool, args, session, url, tool_call_id):
    state = ToolRunnerState()
    state.set("figure_upload_config", make_upload_config(url))
    ctx = ToolExecutionContext(runner_state=state, tool_call_id=tool_call_id)
    result = asyncio.run(tool.execute_with_context(args, ctx))
    # Mirror agent_tool_dispatch: status and payload set independently.
    return ToolResultEvent(
        source="agent",
        call_id=tool_call_id,
        tool_name="PlotFigure",
        result=result.content,
        status=result.status,
        payload=result.payload,
    )


def test_no_command_publish_reaches_snapshot():
    session = make_session()
    tool = PlotFigure(session=session, workdir="/share")
    event = publish(tool, {"output_path": "band.png", "caption": "Band"}, session,
                    "https://a/1.png", "call-1")
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is True
    snap = acc.build_snapshot_event_if_dirty()
    assert snap is not None
    assert len(snap.figures) == 1


def test_command_mode_reaches_snapshot():
    session = make_session(exit_code=0)
    tool = PlotFigure(session=session, workdir="/share")
    event = publish(tool, {"command": "python p.py", "output_path": "xrd.png", "caption": "XRD"},
                    session, "https://a/2.png", "call-2")
    assert event.status == "success"
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is True
    snap = acc.build_snapshot_event_if_dirty()
    assert snap is not None and len(snap.figures) == 1


def test_failed_command_with_figure_still_aggregates():
    session = make_session(exit_code=1, file_after=True)
    tool = PlotFigure(session=session, workdir="/share")
    event = publish(tool, {"command": "python p.py", "output_path": "xrd.png", "caption": "XRD"},
                    session, "https://a/3.png", "call-3")
    assert event.status == "error"
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is True  # error status, payload still ingested


def test_multiple_publishes_build_incremental_snapshots():
    acc = ResponseFiguresAccumulator()
    s1 = make_session()
    e1 = publish(PlotFigure(session=s1, workdir="/share"),
                 {"output_path": "a.png", "caption": "A"}, s1, "https://a/a.png", "call-a")
    acc.add_tool_result(e1)
    snap1 = acc.build_snapshot_event_if_dirty()
    acc.mark_snapshot_emitted()
    s2 = make_session()
    e2 = publish(PlotFigure(session=s2, workdir="/share"),
                 {"output_path": "b.png", "caption": "B"}, s2, "https://a/b.png", "call-b")
    acc.add_tool_result(e2)
    snap2 = acc.build_snapshot_event_if_dirty()
    assert len(snap1.figures) == 1
    assert len(snap2.figures) == 2
    assert snap1.figures[0].figure_id == snap2.figures[0].figure_id


def test_duplicate_figure_id_first_writer_wins():
    acc = ResponseFiguresAccumulator()
    # Same bytes + same output_path basename -> identical figure_id.
    s1 = make_session()
    e1 = publish(PlotFigure(session=s1, workdir="/share"),
                 {"output_path": "dup.png", "caption": "first"}, s1, "https://a/x.png", "call-x")
    s2 = make_session()
    e2 = publish(PlotFigure(session=s2, workdir="/share"),
                 {"output_path": "dup.png", "caption": "second"}, s2, "https://a/y.png", "call-y")
    assert acc.add_tool_result(e1) is True
    assert acc.add_tool_result(e2) is False  # duplicate id ignored
    snap = acc.build_snapshot_event_if_dirty()
    assert len(snap.figures) == 1
    assert snap.figures[0].caption == "first"


def test_child_spawn_figure_promotes_only_with_include_spawned():
    # A child agent's PlotFigure result carries spawn_id. The accumulator gates
    # it out by default and promotes it only when include_spawned=True — exactly
    # what FigureCoordinator.child_event_sink passes. Assert the accumulator
    # boundary here without touching FigureCoordinator.
    session = make_session()
    tool = PlotFigure(session=session, workdir="/share")
    state = ToolRunnerState()
    state.set("figure_upload_config", make_upload_config("https://a/child.png"))
    ctx = ToolExecutionContext(runner_state=state, tool_call_id="call-child")
    result = asyncio.run(
        tool.execute_with_context({"output_path": "child.png", "caption": "Child"}, ctx)
    )
    event = ToolResultEvent(
        source="agent",
        call_id="call-child",
        tool_name="PlotFigure",
        result=result.content,
        status=result.status,
        payload=result.payload,
        spawn_id="child-1",
    )
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is False  # gated: spawn_id set, not included
    assert acc.add_tool_result(event, include_spawned=True) is True  # promoted
