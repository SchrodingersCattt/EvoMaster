"""Unit tests for the reused figure_artifacts helpers.

The legacy manifest collection pipeline was decommissioned in favour of the
declared-figure pipeline (see test_collect_declared_figure.py). These tests
exercise the still-public helpers that both pipelines relied on, calling them
directly instead of through the removed manifest collector.
"""

from __future__ import annotations

import hashlib
import logging
import shlex
from unittest.mock import MagicMock

import pytest

from matmaster.tools.figure_artifacts import (
    _build_asset_key,
    _download_with_retry,
    _link_figure_flat,
    _sniff_image_format,
    _upload_with_retry,
)
from matmaster.types.figures import FigureUploadConfig

_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


def _upload_cfg(
    upload_bytes=lambda data, key: f"https://oss.example/{key}",
) -> FigureUploadConfig:
    return FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="matmaster/chat_figures",
        upload_bytes=upload_bytes,
    )


def _exec_bash_command(call_args) -> str:
    """_link_figure_flat always invokes session.exec_bash(command=...)."""
    return call_args.kwargs.get("command") or call_args.args[0]


# --------------------------------------------------------------------------- #
# _sniff_image_format
# --------------------------------------------------------------------------- #


def test_sniff_image_format_recognizes_supported_and_rejects_others() -> None:
    assert _sniff_image_format(b"\x89PNG\r\n\x1a\n" + b"x") == ".png"
    assert _sniff_image_format(b"\xff\xd8\xff" + b"x") == ".jpg"
    assert _sniff_image_format(b"RIFF\x00\x00\x00\x00WEBP" + b"x") == ".webp"
    assert _sniff_image_format(b"GIF89a" + b"x") is None


# --------------------------------------------------------------------------- #
# _build_asset_key / _sanitize_key_segment
# --------------------------------------------------------------------------- #


def test_asset_key_is_deterministic_and_preserves_basename() -> None:
    cfg = _upload_cfg()
    key1 = _build_asset_key(
        upload_config=cfg,
        tool_call_id="call-1",
        figure_id="band",
        source_path="/share/plots/band-plot.png",
        payload=_PNG,
    )
    key2 = _build_asset_key(
        upload_config=cfg,
        tool_call_id="call-1",
        figure_id="band",
        source_path="/share/plots/band-plot.png",
        payload=_PNG,
    )
    assert key1 == key2
    assert key1.endswith("/band-plot.png")


def test_asset_key_uses_stable_sanitized_segments() -> None:
    cfg = FigureUploadConfig(
        session_id="sess 1/main",
        task_id="task:1",
        asset_key_prefix="matmaster/chat_figures",
        upload_bytes=lambda data, key: "unused",
    )
    key = _build_asset_key(
        upload_config=cfg,
        tool_call_id="call 1/alpha",
        figure_id="Band Figure 01",
        source_path="/share/plots/final image.png",
        payload=_PNG,
    )
    expected_digest = hashlib.sha256(_PNG).hexdigest()[:16]
    assert key == (
        "matmaster/chat_figures/sess-1-main/task-1/call-1-alpha/Band-Figure-01/"
        f"{expected_digest}/final image.png"
    )


# --------------------------------------------------------------------------- #
# _download_with_retry
# --------------------------------------------------------------------------- #


def test_download_retries_once_before_failing() -> None:
    session = MagicMock()
    session.download.side_effect = [
        TimeoutError("ssh hiccup"),
        TimeoutError("ssh still down"),
    ]
    with pytest.raises(TimeoutError):
        _download_with_retry(session=session, path="/share/plots/band.png")
    assert session.download.call_count == 2


def test_download_retry_then_success() -> None:
    session = MagicMock()
    session.download.side_effect = [TimeoutError("ssh hiccup"), _PNG]
    payload = _download_with_retry(session=session, path="/share/plots/band.png")
    assert payload == _PNG
    assert session.download.call_count == 2


# --------------------------------------------------------------------------- #
# _upload_with_retry
# --------------------------------------------------------------------------- #


def test_upload_retries_before_success() -> None:
    attempts = {"count": 0}

    def upload_bytes(data: bytes, key: str) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient oss failure")
        return f"https://oss.example/{key}"

    url = _upload_with_retry(upload_bytes=upload_bytes, payload=_PNG, asset_key="k/x.png")
    assert url == "https://oss.example/k/x.png"
    assert attempts["count"] == 3


def test_upload_exhausts_attempts_then_raises() -> None:
    def always_fail(data: bytes, key: str) -> str:
        raise RuntimeError("upload dead")

    with pytest.raises(RuntimeError):
        _upload_with_retry(upload_bytes=always_fail, payload=_PNG, asset_key="k/x.png")


# --------------------------------------------------------------------------- #
# _link_figure_flat (flat-view symlink)
# --------------------------------------------------------------------------- #


def test_link_flat_emits_guard_and_link_path() -> None:
    session = MagicMock()
    session.exec_bash.return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
    _link_figure_flat(
        session=session,
        flat_dir="/share/.matmaster/figures",
        resolved_path="/share/results/band.png",
        figure_id="band",
    )
    session.exec_bash.assert_called_once()
    cmd = _exec_bash_command(session.exec_bash.call_args)
    assert "mkdir -p --" in cmd
    assert "[ -e " in cmd
    assert "[ -L " in cmd
    assert "ln -s --" in cmd
    assert "FIGURE_SYMLINK_EXISTS" in cmd
    assert "exit 73" in cmd
    assert "/share/.matmaster/figures/band.png" in cmd


def test_link_flat_target_is_relative() -> None:
    session = MagicMock()
    session.exec_bash.return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
    _link_figure_flat(
        session=session,
        flat_dir="/share/.matmaster/figures",
        resolved_path="/share/results/band.png",
        figure_id="band",
    )
    cmd = _exec_bash_command(session.exec_bash.call_args)
    assert "../../results/band.png" in cmd
    ln_idx = cmd.index("ln -s --")
    first_token = cmd[ln_idx + len("ln -s --") :].strip().split()[0]
    assert not first_token.startswith("/"), f"ln target must be relative, got {first_token!r}"


def test_link_flat_first_writer_wins_via_exit_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")
    session = MagicMock()
    session.exec_bash.return_value = {
        "exit_code": 73,
        "stdout": "FIGURE_SYMLINK_EXISTS\n",
        "stderr": "",
    }
    _link_figure_flat(
        session=session,
        flat_dir="/share/.matmaster/figures",
        resolved_path="/share/results/band.png",
        figure_id="band",
    )
    assert any(
        "figure_symlink_exists:'band'" in record.getMessage()
        for record in caplog.records
    )


def test_link_flat_first_writer_wins_via_stdout_marker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")
    session = MagicMock()
    # exit code remapped to 1, but the stdout marker still classifies as "exists".
    session.exec_bash.return_value = {
        "exit_code": 1,
        "stdout": "FIGURE_SYMLINK_EXISTS\n",
        "stderr": "",
    }
    _link_figure_flat(
        session=session,
        flat_dir="/share/.matmaster/figures",
        resolved_path="/share/results/band.png",
        figure_id="band",
    )
    exists_msgs = [
        r.getMessage() for r in caplog.records if "figure_symlink_exists" in r.getMessage()
    ]
    failed_msgs = [
        r.getMessage() for r in caplog.records if "figure_symlink_failed" in r.getMessage()
    ]
    assert exists_msgs and not failed_msgs


def test_link_flat_generic_failure_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")
    session = MagicMock()
    session.exec_bash.return_value = {
        "exit_code": 1,
        "stdout": "",
        "stderr": "ln: cannot create symbolic link 'x.png': Permission denied\n",
    }
    _link_figure_flat(
        session=session,
        flat_dir="/share/.matmaster/figures",
        resolved_path="/share/results/band.png",
        figure_id="band",
    )
    failed = [
        r.getMessage()
        for r in caplog.records
        if "figure_symlink_failed:'band'" in r.getMessage()
    ]
    assert failed and "Permission denied" in failed[0]
    assert not any("figure_symlink_exists" in r.getMessage() for r in caplog.records)


def test_link_flat_exec_raises_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")
    session = MagicMock()
    session.exec_bash.side_effect = RuntimeError("session closed")
    _link_figure_flat(
        session=session,
        flat_dir="/share/.matmaster/figures",
        resolved_path="/share/results/band.png",
        figure_id="band",
    )
    assert any(
        "figure_symlink_failed:'band'" in r.getMessage()
        and "session closed" in r.getMessage()
        for r in caplog.records
    )


def test_link_flat_shell_quoting() -> None:
    session = MagicMock()
    session.exec_bash.return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
    _link_figure_flat(
        session=session,
        flat_dir="/share/foo bar/.matmaster/figures",
        resolved_path="/share/foo bar/results/band.png",
        figure_id="band-alpha",
    )
    cmd = _exec_bash_command(session.exec_bash.call_args)
    tokens = shlex.split(cmd)
    assert "/share/foo bar/.matmaster/figures" in tokens
    assert "/share/foo bar/.matmaster/figures/band-alpha.png" in tokens
    assert "FIGURE_SYMLINK_EXISTS" in tokens
