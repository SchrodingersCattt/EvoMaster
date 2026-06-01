"""tests/matmaster/tools/test_collect_declared_figure.py"""

from matmaster.tools.figure_artifacts import resolve_workspace_output_path


def test_relative_path_joins_workspace():
    assert (
        resolve_workspace_output_path(raw_path="band.png", workdir="/share")
        == "/share/band.png"
    )


def test_nested_relative_path():
    assert (
        resolve_workspace_output_path(raw_path="results/xrd.png", workdir="/share")
        == "/share/results/xrd.png"
    )


def test_absolute_inside_workspace_ok():
    assert (
        resolve_workspace_output_path(raw_path="/share/a/b.png", workdir="/share")
        == "/share/a/b.png"
    )


def test_escape_relative_denied():
    assert (
        resolve_workspace_output_path(raw_path="../escape.png", workdir="/share")
        is None
    )


def test_escape_absolute_denied():
    assert (
        resolve_workspace_output_path(raw_path="/etc/passwd", workdir="/share")
        is None
    )


from matmaster.tools.figure_artifacts import build_figure_id

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_figure_id_sanitizes_spaces():
    fid = build_figure_id(output_path="plots/band structure.png", image_bytes=_PNG)
    stem, _, digest = fid.rpartition("-")
    assert stem == "band-structure"
    assert len(digest) == 12


def test_figure_id_non_ascii_stem_falls_back_to_figure():
    fid = build_figure_id(output_path="结果图.png", image_bytes=_PNG)
    assert fid.startswith("figure-")


def test_figure_id_is_deterministic_for_same_bytes():
    a = build_figure_id(output_path="x.png", image_bytes=_PNG)
    b = build_figure_id(output_path="x.png", image_bytes=_PNG)
    assert a == b


def test_figure_id_changes_with_bytes():
    a = build_figure_id(output_path="x.png", image_bytes=_PNG)
    b = build_figure_id(output_path="x.png", image_bytes=_PNG + b"x")
    assert a != b


def test_figure_id_length_bounded_and_charset():
    fid = build_figure_id(output_path="A" * 200 + ".png", image_bytes=_PNG)
    assert len(fid) <= 64
    assert all(c.isalnum() or c in "._-" for c in fid)
    assert "/" not in fid


import pytest

from matmaster.tools.figure_artifacts import (
    FigureValidationError,
    _validate_image_bytes,
)

_JPG = b"\xff\xd8\xff" + b"\x00" * 64


def test_validate_unsupported_format_reason():
    with pytest.raises(FigureValidationError) as exc:
        _validate_image_bytes(payload=_PNG, path="/share/x.gif")
    assert exc.value.reason == "unsupported_format"


def test_validate_header_mismatch_reason():
    # .png suffix but JPG magic bytes
    with pytest.raises(FigureValidationError) as exc:
        _validate_image_bytes(payload=_JPG, path="/share/x.png")
    assert exc.value.reason == "image_header_mismatch"


def test_validate_too_large_reason():
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 1)
    with pytest.raises(FigureValidationError) as exc:
        _validate_image_bytes(payload=big, path="/share/x.png")
    assert exc.value.reason == "figure_too_large"


def test_validation_error_is_value_error_subclass():
    # Keeps the old manifest pipeline's `except Exception` / ValueError contract.
    assert issubclass(FigureValidationError, ValueError)


from unittest.mock import MagicMock

from matmaster.tools.figure_artifacts import _link_figure_flat


def test_link_figure_flat_builds_relative_symlink():
    session = MagicMock()
    session.exec_bash.return_value = {"exit_code": 0, "stdout": ""}
    _link_figure_flat(
        session=session,
        flat_dir="/share/.matmaster/figures",
        resolved_path="/share/results/band.png",
        figure_id="band-abc123",
    )
    cmd = session.exec_bash.call_args.kwargs.get("command") or session.exec_bash.call_args.args[0]
    assert "/share/.matmaster/figures/band-abc123.png" in cmd
    # rel target from flat_dir to resolved_path
    assert "../../results/band.png" in cmd


from matmaster.tools.figure_artifacts import (
    DeclaredFigureResult,
    collect_declared_figure,
)
from matmaster.types.figures import FigureUploadConfig


def make_upload_config(url="https://assets.test/u/fig.png"):
    return FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="figs",
        upload_bytes=lambda payload, key: url,
    )


def make_fig_session(*, exists=True, is_file=True, payload=_PNG):
    s = MagicMock()
    s.path_exists.return_value = exists
    s.is_file.return_value = is_file
    s.download.return_value = payload
    s.exec_bash.return_value = {"exit_code": 0, "stdout": ""}
    return s


def test_collect_relative_success():
    session = make_fig_session()
    result = collect_declared_figure(
        session=session,
        workdir="/share",
        output_path="band.png",
        caption="Band structure",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert isinstance(result, DeclaredFigureResult)
    assert result.failure_reason is None
    assert result.figure is not None
    assert result.figure.caption == "Band structure"
    assert result.figure.source_tool_call_id == "call-1"
    assert result.figure.asset_url == "https://assets.test/u/fig.png"
    assert result.figure_id.startswith("band-")
    assert result.resolved_path == "/share/band.png"
    assert result.figure.remote_path == "/share/band.png"


def test_collect_escape_returns_outside_workspace():
    result = collect_declared_figure(
        session=make_fig_session(),
        workdir="/share",
        output_path="../escape.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.figure is None
    assert result.failure_reason == "outside_workspace"
    assert result.guidance


def test_collect_missing_file_returns_file_not_found():
    result = collect_declared_figure(
        session=make_fig_session(exists=False),
        workdir="/share",
        output_path="band.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.failure_reason == "file_not_found"


def test_collect_directory_returns_not_a_file():
    result = collect_declared_figure(
        session=make_fig_session(is_file=False),
        workdir="/share",
        output_path="plots",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.failure_reason == "not_a_file"


def test_collect_non_image_returns_classification():
    result = collect_declared_figure(
        session=make_fig_session(payload=b"not an image"),
        workdir="/share",
        output_path="band.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.failure_reason == "image_header_mismatch"


def test_collect_download_failure_classified():
    session = make_fig_session()
    session.download.side_effect = RuntimeError("transport down")
    result = collect_declared_figure(
        session=session,
        workdir="/share",
        output_path="band.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.failure_reason == "download_failed"


def test_collect_upload_failure_classified():
    def boom(payload, key):
        raise RuntimeError("upload down")

    cfg = FigureUploadConfig(
        session_id="s", task_id="t", asset_key_prefix="figs", upload_bytes=boom
    )
    result = collect_declared_figure(
        session=make_fig_session(),
        workdir="/share",
        output_path="band.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=cfg,
    )
    assert result.failure_reason == "upload_failed"
    assert result.figure_id is not None  # id is computed before upload
