"""Real-filesystem tests for figure flat-view symlinks.

These tests use LocalSession and tmp_path to execute the actual guard plus
ln script. Mock-based tests cannot verify the important POSIX behavior where
bare ln -s would create a link inside an existing directory.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from matmaster.sessions.local import LocalSession
from matmaster.tools.figure_artifacts import (
    build_figure_env,
    collect_figures_from_session,
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


def _upload_cfg() -> FigureUploadConfig:
    return FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="matmaster/chat_figures",
        upload_bytes=lambda data, key: f"https://oss.example/{key}",
    )


def _setup_artifact(workdir: Path, call_id: str, figure_id: str) -> tuple[str, str]:
    """Write artifact file and manifest. Return (artifact_dir, manifest_path)."""

    artifact_dir, manifest_path = build_figure_env(str(workdir), call_id)
    artifact_path = Path(artifact_dir) / f"{figure_id}.png"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(_TINY_PNG)
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_path).write_text(
        '{"figures":[{"figure_id":"'
        + figure_id
        + '","path":"'
        + figure_id
        + '.png","caption":"c"}]}'
    )
    return artifact_dir, manifest_path


@pytest.fixture
def local_session(tmp_path: Path):
    """Opened LocalSession with tmp_path as workspace."""

    session = LocalSession(tmp_path)
    session.open()
    try:
        yield session
    finally:
        session.close()


def test_real_fs_creates_symlink(local_session: LocalSession, tmp_path: Path) -> None:
    workdir = tmp_path
    artifact_dir, manifest_path = _setup_artifact(workdir, "call-1", "band")

    result = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    assert result.warnings == []

    link_path = workdir / ".matmaster" / "figures" / "band.png"
    assert link_path.is_symlink()
    assert os.readlink(link_path) == "call-1/artifacts/band.png"
    assert link_path.read_bytes() == _TINY_PNG


def test_real_fs_rejects_existing_regular_file(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    artifact_dir, manifest_path = _setup_artifact(workdir, "call-1", "band")

    flat_dir = workdir / ".matmaster" / "figures"
    flat_dir.mkdir(parents=True, exist_ok=True)
    squatter = flat_dir / "band.png"
    squatter.write_bytes(b"SQUATTER")

    result = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    assert squatter.read_bytes() == b"SQUATTER"
    assert not squatter.is_symlink()
    assert any("figure_symlink_exists:'band'" in r.getMessage() for r in caplog.records)


def test_real_fs_rejects_existing_directory(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    artifact_dir, manifest_path = _setup_artifact(workdir, "call-1", "band")

    flat_dir = workdir / ".matmaster" / "figures"
    flat_dir.mkdir(parents=True, exist_ok=True)
    squatter_dir = flat_dir / "band.png"
    squatter_dir.mkdir()
    (squatter_dir / "untouched").write_text("keep me")

    result = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    assert squatter_dir.is_dir()
    assert not squatter_dir.is_symlink()
    assert (squatter_dir / "untouched").read_text() == "keep me"
    entries = sorted(p.name for p in squatter_dir.iterdir())
    assert entries == [
        "untouched"
    ], f"guard should reject directory; found extra entries: {entries}"
    assert any("figure_symlink_exists:'band'" in r.getMessage() for r in caplog.records)


def test_real_fs_rejects_existing_dangling_symlink(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    artifact_dir, manifest_path = _setup_artifact(workdir, "call-1", "band")

    flat_dir = workdir / ".matmaster" / "figures"
    flat_dir.mkdir(parents=True, exist_ok=True)
    dangling = flat_dir / "band.png"
    os.symlink("nowhere-to-be-seen", dangling)
    assert dangling.is_symlink()
    assert not dangling.exists()

    result = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    assert os.readlink(dangling) == "nowhere-to-be-seen"
    assert any("figure_symlink_exists:'band'" in r.getMessage() for r in caplog.records)


def test_real_fs_success_then_collision_same_workdir(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    artifact_dir_1, manifest_path_1 = _setup_artifact(workdir, "call-1", "band")
    result_1 = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir_1,
        manifest_path=manifest_path_1,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )
    assert len(result_1.figures) == 1

    link_path = workdir / ".matmaster" / "figures" / "band.png"
    assert link_path.is_symlink()
    assert os.readlink(link_path) == "call-1/artifacts/band.png"

    artifact_dir_2, manifest_path_2 = _setup_artifact(workdir, "call-2", "band")
    caplog.clear()
    result_2 = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir_2,
        manifest_path=manifest_path_2,
        tool_call_id="call-2",
        upload_config=_upload_cfg(),
    )
    assert len(result_2.figures) == 1

    assert os.readlink(link_path) == "call-1/artifacts/band.png"
    assert any("figure_symlink_exists:'band'" in r.getMessage() for r in caplog.records)
