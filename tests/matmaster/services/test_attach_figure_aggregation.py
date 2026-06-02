"""tests/matmaster/services/test_attach_figure_aggregation.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.attach_figure_tool import AttachFigure
from matmaster.types.events import ToolResultEvent
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext
from src.services.response_figures_service import ResponseFiguresAccumulator

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _png(tag: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag + b"\x00" * 64


def make_session(path_bytes=None, default=_PNG, exists=True):
    s = MagicMock()
    s.path_exists.return_value = exists
    s.is_file.return_value = True
    if path_bytes is None:
        s.download.return_value = default
    else:
        s.download.side_effect = lambda p: path_bytes[p]
    return s


def make_upload_config(url):
    return FigureUploadConfig(
        session_id="s",
        task_id="t",
        asset_key_prefix="figs",
        upload_bytes=lambda payload, key: url,
    )


def publish(tool, args, upload_config, tool_call_id):
    state = ToolRunnerState()
    state.set("figure_upload_config", upload_config)
    ctx = ToolExecutionContext(runner_state=state, tool_call_id=tool_call_id)
    result = asyncio.run(tool.execute_with_context(args, ctx))
    # Mirror agent_tool_dispatch: status and payload set independently.
    return ToolResultEvent(
        source="agent",
        call_id=tool_call_id,
        tool_name="AttachFigure",
        result=result.content,
        status=result.status,
        payload=result.payload,
    )


def test_batch_publish_reaches_snapshot():
    path_bytes = {"/share/a.png": _png(b"a"), "/share/b.png": _png(b"b")}
    tool = AttachFigure(session=make_session(path_bytes=path_bytes), workdir="/share")
    event = publish(
        tool,
        {
            "figures": [
                {"output_path": "/share/a.png", "caption": "A"},
                {"output_path": "/share/b.png", "caption": "B"},
            ]
        },
        make_upload_config("https://a/x.png"),
        "call-1",
    )
    assert event.status == "success"
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is True
    snap = acc.build_snapshot_event_if_dirty()
    assert snap is not None
    assert len(snap.figures) == 2


def test_failure_yields_zero_figures_invariant():
    # Phase B failure on the third upload -> status error, payload has NO figures.
    # The accumulator reads only payload.figures (never status), so it must
    # absorb nothing and produce no snapshot.
    path_bytes = {
        "/share/a.png": _png(b"a"),
        "/share/b.png": _png(b"b"),
        "/share/c.png": _png(b"c"),
    }

    def upload_bytes(payload, key):
        if key.endswith("c.png"):
            raise RuntimeError("upload down")
        return "https://a/" + key

    cfg = FigureUploadConfig(
        session_id="s", task_id="t", asset_key_prefix="figs", upload_bytes=upload_bytes
    )
    tool = AttachFigure(session=make_session(path_bytes=path_bytes), workdir="/share")
    event = publish(
        tool,
        {
            "figures": [
                {"output_path": "/share/a.png", "caption": "A"},
                {"output_path": "/share/b.png", "caption": "B"},
                {"output_path": "/share/c.png", "caption": "C"},
            ]
        },
        cfg,
        "call-err",
    )
    assert event.status == "error"
    assert not (event.payload or {}).get("figures")
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is False
    assert acc.build_snapshot_event_if_dirty() is None


def test_multiple_publishes_build_incremental_snapshots():
    acc = ResponseFiguresAccumulator()
    t1 = AttachFigure(session=make_session(default=_png(b"a")), workdir="/share")
    e1 = publish(
        t1,
        {"figures": [{"output_path": "/share/a.png", "caption": "A"}]},
        make_upload_config("https://a/a.png"),
        "call-a",
    )
    acc.add_tool_result(e1)
    snap1 = acc.build_snapshot_event_if_dirty()
    acc.mark_snapshot_emitted()

    t2 = AttachFigure(session=make_session(default=_png(b"b")), workdir="/share")
    e2 = publish(
        t2,
        {"figures": [{"output_path": "/share/b.png", "caption": "B"}]},
        make_upload_config("https://a/b.png"),
        "call-b",
    )
    acc.add_tool_result(e2)
    snap2 = acc.build_snapshot_event_if_dirty()

    assert len(snap1.figures) == 1
    assert len(snap2.figures) == 2
    assert snap1.figures[0].figure_id == snap2.figures[0].figure_id


def test_duplicate_figure_id_first_writer_wins_across_calls():
    # Two separate calls, same basename + same bytes -> identical figure_id.
    acc = ResponseFiguresAccumulator()
    t1 = AttachFigure(session=make_session(default=_PNG), workdir="/share")
    e1 = publish(
        t1,
        {"figures": [{"output_path": "/share/dup.png", "caption": "first"}]},
        make_upload_config("https://a/x.png"),
        "call-x",
    )
    t2 = AttachFigure(session=make_session(default=_PNG), workdir="/share")
    e2 = publish(
        t2,
        {"figures": [{"output_path": "/share/dup.png", "caption": "second"}]},
        make_upload_config("https://a/y.png"),
        "call-y",
    )
    assert acc.add_tool_result(e1) is True
    assert acc.add_tool_result(e2) is False  # duplicate id ignored
    snap = acc.build_snapshot_event_if_dirty()
    assert len(snap.figures) == 1
    assert snap.figures[0].caption == "first"


def test_child_spawn_figure_promotes_only_with_include_spawned():
    # A child agent's AttachFigure result carries spawn_id. The accumulator gates
    # it out by default and promotes it only when include_spawned=True.
    tool = AttachFigure(session=make_session(), workdir="/share")
    state = ToolRunnerState()
    state.set("figure_upload_config", make_upload_config("https://a/child.png"))
    ctx = ToolExecutionContext(runner_state=state, tool_call_id="call-child")
    result = asyncio.run(
        tool.execute_with_context(
            {"figures": [{"output_path": "/share/child.png", "caption": "Child"}]}, ctx
        )
    )
    event = ToolResultEvent(
        source="agent",
        call_id="call-child",
        tool_name="AttachFigure",
        result=result.content,
        status=result.status,
        payload=result.payload,
        spawn_id="child-1",
    )
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is False  # gated: spawn_id set, not included
    assert acc.add_tool_result(event, include_spawned=True) is True  # promoted
