"""Unit tests for reused figure_artifacts helpers."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from matmaster.tools.figure_artifacts import (
    _build_asset_key,
    _download_with_retry,
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
        content_sha256=hashlib.sha256(_PNG).hexdigest(),
    )
    key2 = _build_asset_key(
        upload_config=cfg,
        tool_call_id="call-1",
        figure_id="band",
        source_path="/share/plots/band-plot.png",
        content_sha256=hashlib.sha256(_PNG).hexdigest(),
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
        content_sha256=hashlib.sha256(_PNG).hexdigest(),
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

    url = _upload_with_retry(
        upload_bytes=upload_bytes, payload=_PNG, asset_key="k/x.png"
    )
    assert url == "https://oss.example/k/x.png"
    assert attempts["count"] == 3


def test_upload_exhausts_attempts_then_raises() -> None:
    def always_fail(data: bytes, key: str) -> str:
        raise RuntimeError("upload dead")

    with pytest.raises(RuntimeError):
        _upload_with_retry(upload_bytes=always_fail, payload=_PNG, asset_key="k/x.png")
