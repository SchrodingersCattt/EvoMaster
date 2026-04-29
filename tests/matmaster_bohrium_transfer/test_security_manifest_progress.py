from __future__ import annotations

import json
from pathlib import Path

from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.progress import (
    LoggingProgressSink,
    TransferProgressEvent,
)
from matmaster_bohrium_transfer.security import redact_secrets, secure_write_json


def test_redact_secrets_masks_headers_json_and_urls() -> None:
    raw = {
        "Authorization": "Bearer secret-token",
        "token": "abc123",
        "url": "https://store/api/download/a?token=secret-token",
    }

    redacted = redact_secrets(raw)

    assert "secret-token" not in redacted
    assert "abc123" not in redacted
    assert "<redacted>" in redacted


def test_secure_write_json_uses_0600(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"

    secure_write_json(path, {"token": "secret"})

    mode = path.stat().st_mode & 0o777
    assert mode == 0o600
    assert json.loads(path.read_text())["token"] == "secret"


def test_manifest_store_round_trip_with_permissions(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "transfers")
    manifest = {"schema_version": "v1", "transfer_id": "t1", "token": "secret"}

    store.write("t1", manifest)

    loaded = store.read("t1")
    assert loaded == manifest
    assert (
        tmp_path / "transfers" / "t1" / "manifest.json"
    ).stat().st_mode & 0o777 == 0o600


def test_logging_progress_sink_limits_chunk_events(caplog) -> None:
    sink = LoggingProgressSink(min_bytes=32 * 1024 * 1024, min_seconds=1.0)
    event = TransferProgressEvent(
        event_type="download_chunk_completed",
        transfer_id="t1",
        phase="download",
        direction="download",
        bytes_done=1024,
        bytes_total=None,
    )

    sink.emit(event)
    sink.emit(event)

    assert len(caplog.records) <= 1
