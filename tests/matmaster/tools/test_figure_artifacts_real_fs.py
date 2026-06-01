"""Real-filesystem tests for figure flat-view symlinks.

These tests use LocalSession and tmp_path to execute the actual guard plus
ln script through the declared-figure pipeline. Mock-based tests cannot verify
the important POSIX behavior where a bare ln -s would create a link inside an
existing directory.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from matmaster.sessions.local import LocalSession
from matmaster.tools.figure_artifacts import (
    build_figure_id,
    collect_declared_figure,
)
from matmaster.types.figures import FigureUploadConfig

pytestmark = pytest.mark.skipif(
    not hasattr(os, "symlink"),
    reason="filesystem does not support symlink",
)

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff\x3f\x00\x05\xfe\x02\xfe\xdc\xccY\xe7"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

_OUTPUT_PATH = "results/band.png"


def _upload_cfg() -> FigureUploadConfig:
    return FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="matmaster/chat_figures",
        upload_bytes=lambda data, key: f"https://oss.example/{key}",
    )


def _write_image(workdir: Path, rel_path: str = _OUTPUT_PATH) -> None:
    """Write a tiny real PNG at workdir/rel_path."""
    path = workdir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_TINY_PNG)


def _expected_figure_id() -> str:
    return build_figure_id(output_path=_OUTPUT_PATH, image_bytes=_TINY_PNG)


@pytest.fixture
def local_session(tmp_path: Path):
    """Opened LocalSession with tmp_path as workspace."""

    session = LocalSession(tmp_path)
    session.open()
    try:
        yield session
    finally:
        session.close()


def _collect(local_session: LocalSession, workdir: Path, tool_call_id: str):
    return collect_declared_figure(
        session=local_session,
        workdir=str(workdir),
        output_path=_OUTPUT_PATH,
        caption="c",
        tool_call_id=tool_call_id,
        upload_config=_upload_cfg(),
    )


def test_real_fs_creates_symlink(local_session: LocalSession, tmp_path: Path) -> None:
    workdir = tmp_path
    _write_image(workdir)

    result = _collect(local_session, workdir, "call-1")

    assert result.figure is not None
    assert result.failure_reason is None

    link_path = workdir / ".matmaster" / "figures" / f"{_expected_figure_id()}.png"
    assert link_path.is_symlink()
    assert os.readlink(link_path) == "../../results/band.png"
    assert link_path.read_bytes() == _TINY_PNG


def test_real_fs_rejects_existing_regular_file(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    _write_image(workdir)

    flat_dir = workdir / ".matmaster" / "figures"
    flat_dir.mkdir(parents=True, exist_ok=True)
    squatter = flat_dir / f"{_expected_figure_id()}.png"
    squatter.write_bytes(b"SQUATTER")

    result = _collect(local_session, workdir, "call-1")

    # Collection still succeeds; the flat-view symlink is best-effort only.
    assert result.figure is not None
    assert squatter.read_bytes() == b"SQUATTER"
    assert not squatter.is_symlink()
    assert any("figure_symlink_exists" in r.getMessage() for r in caplog.records)


def test_real_fs_rejects_existing_directory(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    _write_image(workdir)

    flat_dir = workdir / ".matmaster" / "figures"
    flat_dir.mkdir(parents=True, exist_ok=True)
    squatter_dir = flat_dir / f"{_expected_figure_id()}.png"
    squatter_dir.mkdir()
    (squatter_dir / "untouched").write_text("keep me")

    result = _collect(local_session, workdir, "call-1")

    assert result.figure is not None
    assert squatter_dir.is_dir()
    assert not squatter_dir.is_symlink()
    assert (squatter_dir / "untouched").read_text() == "keep me"
    entries = sorted(p.name for p in squatter_dir.iterdir())
    assert entries == [
        "untouched"
    ], f"guard should reject directory; found extra entries: {entries}"
    assert any("figure_symlink_exists" in r.getMessage() for r in caplog.records)


def test_real_fs_rejects_existing_dangling_symlink(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    _write_image(workdir)

    flat_dir = workdir / ".matmaster" / "figures"
    flat_dir.mkdir(parents=True, exist_ok=True)
    dangling = flat_dir / f"{_expected_figure_id()}.png"
    os.symlink("nowhere-to-be-seen", dangling)
    assert dangling.is_symlink()
    assert not dangling.exists()

    result = _collect(local_session, workdir, "call-1")

    assert result.figure is not None
    assert os.readlink(dangling) == "nowhere-to-be-seen"
    assert any("figure_symlink_exists" in r.getMessage() for r in caplog.records)


def test_real_fs_success_then_collision_same_workdir(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    _write_image(workdir)

    result_1 = _collect(local_session, workdir, "call-1")
    assert result_1.figure is not None

    # Same output_path + same bytes -> same figure_id -> same flat-view link.
    link_path = workdir / ".matmaster" / "figures" / f"{_expected_figure_id()}.png"
    assert link_path.is_symlink()
    assert os.readlink(link_path) == "../../results/band.png"

    caplog.clear()
    result_2 = _collect(local_session, workdir, "call-2")
    assert result_2.figure is not None

    assert os.readlink(link_path) == "../../results/band.png"
    assert any("figure_symlink_exists" in r.getMessage() for r in caplog.records)
