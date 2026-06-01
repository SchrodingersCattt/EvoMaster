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
