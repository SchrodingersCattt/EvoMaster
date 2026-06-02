"""Figure artifact path, id, validation, prepare, and publish tests."""

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


# --------------------------------------------------------------------------- #
# prepare_declared_figure / publish_prepared_figure (publish-only split)
# --------------------------------------------------------------------------- #

from matmaster.tools.figure_artifacts import (
    FigurePrepareResult,
    FigurePublishResult,
    PreparedFigure,
    prepare_declared_figure,
    publish_prepared_figure,
)


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
    assert prepared.figure_id.startswith("band-")
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
        figure_id="band-abc123",
        image_bytes=_PNG,
        resolved_path="/share/band.png",
        output_path="/share/band.png",
        caption="Band structure",
    )
    result = publish_prepared_figure(
        prepared=prepared,
        upload_config=make_upload_config("https://assets.test/u/band.png"),
        tool_call_id="call-1",
    )
    assert isinstance(result, FigurePublishResult)
    assert result.failure_reason is None
    fig = result.figure
    assert fig is not None
    assert fig.figure_id == "band-abc123"
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
        figure_id="band-abc123",
        image_bytes=_PNG,
        resolved_path="/share/band.png",
        output_path="/share/band.png",
        caption="c",
    )
    result = publish_prepared_figure(
        prepared=prepared, upload_config=cfg, tool_call_id="call-1"
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
        upload_config=make_upload_config("https://assets.test/u/xrd.png"),
        tool_call_id="call-9",
    )
    assert pub.figure is not None
    assert pub.figure.figure_id == prep.prepared.figure_id
    assert pub.figure.remote_path == "/share/results/xrd.png"
