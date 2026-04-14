from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

from matmaster.tools.figure_artifacts import (
    FigureCollectionResult,
    build_figure_env,
    collect_figures_from_session,
)
from matmaster.types.figures import FigureUploadConfig


def test_build_figure_env_uses_tool_call_scoped_paths() -> None:
    artifact_dir, manifest_path = build_figure_env("/share", "call-1")

    assert artifact_dir == "/share/.matmaster/figures/call-1/artifacts"
    assert manifest_path == "/share/.matmaster/figures/call-1/manifest.json"


def _upload_cfg(
    upload_bytes=lambda data, key: f"https://oss.example/{key}",
) -> FigureUploadConfig:
    return FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="matmaster/chat_figures",
        upload_bytes=upload_bytes,
    )


def test_collect_figures_missing_manifest_returns_empty_result() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = False

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result == FigureCollectionResult(figures=[], failure_ids=[], warnings=[])


def test_collect_figures_invalid_manifest_returns_warning_and_no_figures() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"../../etc/passwd","caption":"bad"}]}'
    )

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == []
    assert result.warnings == ["invalid_manifest: unsafe_path:../../etc/passwd"]


def test_collect_figures_duplicate_ids_returns_warning_and_no_figures() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = """
    {"figures":[
      {"figure_id":"band","path":"plots/band.png","caption":"band"},
      {"figure_id":"band","path":"plots/band-2.png","caption":"band2"}
    ]}
    """.strip()

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == []
    assert result.warnings == ["invalid_manifest: duplicate_figure_id:band"]


def test_collect_figures_rejects_unsupported_format_and_keeps_other_success() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = """
    {"figures":[
      {"figure_id":"band","path":"plots/band.png","caption":"band"},
      {"figure_id":"raw","path":"plots/raw.gif","caption":"raw"}
    ]}
    """.strip()
    fake_session.download.side_effect = [
        b"\x89PNG\r\n\x1a\n" + b"a" * 32,
        b"GIF89a" + b"b" * 32,
    ]

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert [fig.figure_id for fig in result.figures] == ["band"]
    assert result.failure_ids == ["raw"]


def test_collect_figures_rejects_oversized_image() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * (10 * 1024 * 1024)

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == ["band"]


def test_collect_figures_keeps_successful_entries_when_one_upload_fails() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = """
    {"figures":[
      {"figure_id":"band","path":"plots/band.png","caption":"band"},
      {"figure_id":"dos","path":"plots/dos.png","caption":"dos"}
    ]}
    """.strip()
    fake_session.download.side_effect = [
        b"\x89PNG\r\n\x1a\n" + b"a" * 32,
        b"\x89PNG\r\n\x1a\n" + b"b" * 32,
    ]

    def upload_bytes(data: bytes, key: str) -> str:
        if key.endswith("dos.png"):
            raise RuntimeError("upload failed")
        return f"https://oss.example/{key}"

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(upload_bytes=upload_bytes),
    )

    assert isinstance(result, FigureCollectionResult)
    assert [fig.figure_id for fig in result.figures] == ["band"]
    assert result.failure_ids == ["dos"]
    assert result.warnings == []


def test_collect_figures_retries_remote_download_once_before_failing() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.side_effect = [
        TimeoutError("ssh hiccup"),
        TimeoutError("ssh still down"),
    ]

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == ["band"]
    assert fake_session.download.call_count == 2


def test_collect_figures_retries_upload_before_success() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 64

    attempts = {"count": 0}

    def upload_bytes(data: bytes, key: str) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient oss failure")
        return f"https://oss.example/{key}"

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(upload_bytes=upload_bytes),
    )

    assert [fig.figure_id for fig in result.figures] == ["band"]
    assert result.failure_ids == []
    assert attempts["count"] == 3
    assert result.figures[0].source_tool_call_id == "call-1"


def test_collect_figures_extension_header_mismatch_fails_without_warning() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = b"\xff\xd8\xff" + b"x" * 64

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == ["band"]
    assert result.warnings == []


def test_collect_figures_asset_key_is_deterministic_and_preserves_basename() -> None:
    captured_keys: list[str] = []

    def upload_bytes(data: bytes, key: str) -> str:
        captured_keys.append(key)
        return f"https://oss.example/{key}"

    manifest = (
        '{"figures":[{"figure_id":"band","path":"plots/band-plot.png","caption":"band"}]}'
    )
    artifact_dir = "/share/.matmaster/figures/call-1/artifacts"
    manifest_path = "/share/.matmaster/figures/call-1/manifest.json"

    for _ in range(2):
        fake_session = MagicMock()
        fake_session.path_exists.return_value = True
        fake_session.read_file.return_value = manifest
        fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 64
        result = collect_figures_from_session(
            session=fake_session,
            artifact_dir=artifact_dir,
            manifest_path=manifest_path,
            tool_call_id="call-1",
            upload_config=_upload_cfg(upload_bytes=upload_bytes),
        )

        assert [fig.figure_id for fig in result.figures] == ["band"]

    assert captured_keys[0] == captured_keys[1]
    assert captured_keys[0].endswith("/band-plot.png")


def test_collect_figures_asset_key_uses_stable_sanitized_segments() -> None:
    captured_keys: list[str] = []

    def upload_bytes(data: bytes, key: str) -> str:
        captured_keys.append(key)
        return f"https://oss.example/{key}"

    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"Band Figure 01","path":"plots/final image.png","caption":"band"}]}'
    )
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 64
    fake_session.download.return_value = payload

    collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call 1/alpha",
        upload_config=FigureUploadConfig(
            session_id="sess 1/main",
            task_id="task:1",
            asset_key_prefix="matmaster/chat_figures",
            upload_bytes=upload_bytes,
        ),
    )

    expected_digest = hashlib.sha256(payload).hexdigest()[:16]
    assert captured_keys == [
        "matmaster/chat_figures/sess-1-main/task-1/call-1-alpha/Band-Figure-01/"
        f"{expected_digest}/final image.png"
    ]
