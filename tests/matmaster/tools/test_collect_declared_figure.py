"""Figure artifact path, id, validation, prepare, and publish tests."""

import hashlib
from unittest.mock import MagicMock

import pytest

from matmaster.tools.figure_artifacts import (
    FigurePrepareResult,
    FigurePublishResult,
    FigureValidationError,
    PreparedFigure,
    _validate_image_bytes,
    assign_figure_id,
    build_figure_id,
    prepare_declared_figure,
    publish_prepared_figure,
    resolve_workspace_output_path,
)
from matmaster.types.figures import FigureUploadConfig


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
        resolve_workspace_output_path(raw_path="/etc/passwd", workdir="/share") is None
    )


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_PNG_SHA = hashlib.sha256(_PNG).hexdigest()


def test_figure_id_sanitizes_spaces():
    assert build_figure_id(output_path="plots/band structure.png") == "band-structure"


def test_figure_id_non_ascii_stem_falls_back_to_figure():
    assert build_figure_id(output_path="结果图.png") == "figure"


def test_figure_id_depends_only_on_path_not_bytes():
    # No content hash in the base id: the id is a function of the path alone.
    assert build_figure_id(output_path="x.png") == "x"


def test_figure_id_length_bounded_and_charset():
    fid = build_figure_id(output_path="A" * 200 + ".png")
    assert len(fid) <= 64
    assert all(c.isalnum() or c in "._-" for c in fid)
    assert "/" not in fid


def test_assign_figure_id_suffixes_on_clash():
    used: set[str] = set()
    assert assign_figure_id(used, "band") == "band"
    assert assign_figure_id(used, "band") == "band-2"
    assert assign_figure_id(used, "band") == "band-3"
    assert assign_figure_id(used, "dos") == "dos"
    assert used == {"band", "band-2", "band-3", "dos"}


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


def test_prepare_success_returns_prepared_and_does_not_upload():
    session = make_fig_session()
    result = prepare_declared_figure(
        session=session,
        workdir="/share",
        output_path="/share/band.png",
        caption="Band structure",
    )
    assert isinstance(result, FigurePrepareResult)
    assert result.failure_reason is None
    prepared = result.prepared
    assert isinstance(prepared, PreparedFigure)
    assert prepared.image_bytes == _PNG
    assert prepared.resolved_path == "/share/band.png"
    assert prepared.output_path == "/share/band.png"
    assert prepared.caption == "Band structure"
    # prepare never uploads: it takes no upload_config and touches no uploader.
    session.download.assert_called_once()


def test_prepare_outside_workspace():
    result = prepare_declared_figure(
        session=make_fig_session(),
        workdir="/share",
        output_path="/etc/passwd.png",
        caption="c",
    )
    assert result.prepared is None
    assert result.failure_reason == "outside_workspace"
    assert result.guidance


def test_prepare_missing_file():
    result = prepare_declared_figure(
        session=make_fig_session(exists=False),
        workdir="/share",
        output_path="/share/band.png",
        caption="c",
    )
    assert result.failure_reason == "file_not_found"


def test_prepare_not_a_file():
    result = prepare_declared_figure(
        session=make_fig_session(is_file=False),
        workdir="/share",
        output_path="/share/plots",
        caption="c",
    )
    assert result.failure_reason == "not_a_file"


def test_prepare_header_mismatch():
    result = prepare_declared_figure(
        session=make_fig_session(payload=b"not an image"),
        workdir="/share",
        output_path="/share/band.png",
        caption="c",
    )
    assert result.failure_reason == "image_header_mismatch"


def test_prepare_download_failure():
    session = make_fig_session()
    session.download.side_effect = RuntimeError("transport down")
    result = prepare_declared_figure(
        session=session,
        workdir="/share",
        output_path="/share/band.png",
        caption="c",
    )
    assert result.failure_reason == "download_failed"


def test_publish_success_builds_descriptor():
    prepared = PreparedFigure(
        image_bytes=_PNG,
        content_sha256=_PNG_SHA,
        resolved_path="/share/band.png",
        output_path="/share/band.png",
        caption="Band structure",
    )
    result = publish_prepared_figure(
        prepared=prepared,
        figure_id="band",
        upload_config=make_upload_config("https://assets.test/u/band.png"),
        tool_call_id="call-1",
    )
    assert isinstance(result, FigurePublishResult)
    assert result.failure_reason is None
    fig = result.figure
    assert fig is not None
    assert fig.figure_id == "band"
    assert fig.asset_url == "https://assets.test/u/band.png"
    assert fig.caption == "Band structure"
    assert fig.source_tool_call_id == "call-1"
    assert fig.remote_path == "/share/band.png"


def test_publish_upload_failure_classified():
    def boom(payload, key):
        raise RuntimeError("upload down")

    cfg = FigureUploadConfig(
        session_id="s", task_id="t", asset_key_prefix="figs", upload_bytes=boom
    )
    prepared = PreparedFigure(
        image_bytes=_PNG,
        content_sha256=_PNG_SHA,
        resolved_path="/share/band.png",
        output_path="/share/band.png",
        caption="c",
    )
    result = publish_prepared_figure(
        prepared=prepared, figure_id="band", upload_config=cfg, tool_call_id="call-1"
    )
    assert result.figure is None
    assert result.failure_reason == "upload_failed"
    assert result.guidance


def test_prepare_then_publish_roundtrip():
    session = make_fig_session()
    prep = prepare_declared_figure(
        session=session,
        workdir="/share",
        output_path="/share/results/xrd.png",
        caption="XRD",
    )
    assert prep.prepared is not None
    pub = publish_prepared_figure(
        prepared=prep.prepared,
        figure_id="xrd",
        upload_config=make_upload_config("https://assets.test/u/xrd.png"),
        tool_call_id="call-9",
    )
    assert pub.figure is not None
    assert pub.figure.figure_id == "xrd"
    assert pub.figure.remote_path == "/share/results/xrd.png"
