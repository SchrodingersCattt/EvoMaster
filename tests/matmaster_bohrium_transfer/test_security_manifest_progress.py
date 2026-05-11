from __future__ import annotations

import json
from pathlib import Path

from matmaster_bohrium_transfer.client import StoreHostClient, decode_storage_param
from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.multipart import upload_file_multipart
from matmaster_bohrium_transfer.progress import (
    LoggingProgressSink,
    TransferProgressEvent,
)
from matmaster_bohrium_transfer.security import (
    redact_secrets,
    secure_write_json,
    token_fingerprint,
)


class FakeResponse:
    def __init__(self, payload: dict | None = None) -> None:
        self.status_code = 200
        self._payload = payload or {"code": 0, "data": {}}
        self.text = json.dumps(self._payload)
        self.headers = {}

    @property
    def ok(self) -> bool:
        return True

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    def post(self, url, *, headers=None, json=None, data=None, timeout=None):
        self.calls.append(
            (url, headers or {}, {"json": json, "data": data, "timeout": timeout})
        )
        if url.endswith("/api/upload/multipart/init"):
            return FakeResponse({"code": 0, "data": {"initialKey": "init-new"}})
        if url.endswith("/api/upload/multipart/upload"):
            param = decode_storage_param((headers or {})["X-Storage-Param"])
            return FakeResponse(
                {"code": 0, "data": {"partString": f"part-{param['number']}"}}
            )
        if url.endswith("/api/upload/multipart/complete"):
            return FakeResponse({"code": 0, "data": {"done": True}})
        raise AssertionError(url)


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


def test_token_fingerprint_is_scoped_to_transfer_id() -> None:
    assert token_fingerprint("token-1", "t1") == token_fingerprint("token-1", "t1")
    assert token_fingerprint("token-1", "t1") != token_fingerprint("token-1", "t2")


def test_manifest_store_round_trip_with_permissions(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "transfers")
    manifest = {"schema_version": "v1", "transfer_id": "t1", "token": "secret"}

    store.write("t1", manifest)

    loaded = store.read("t1")
    assert loaded == manifest
    assert (tmp_path / "transfers" / "t1").stat().st_mode & 0o777 == 0o700
    assert (
        tmp_path / "transfers" / "t1" / "manifest.json"
    ).stat().st_mode & 0o777 == 0o600


def test_manifest_store_exposes_transfer_lock(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "transfers")

    with store.lock("t1"):
        lock_path = tmp_path / "transfers" / "t1" / "manifest.lock"
        assert lock_path.exists()
        assert lock_path.parent.stat().st_mode & 0o777 == 0o700


def test_upload_file_multipart_ignores_v1_manifest_with_raw_token(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "input.zip"
    file_path.write_bytes(b"abcdefghij")
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)
    store = ManifestStore(tmp_path / "manifest")
    store.write(
        "t1",
        {
            "schema_version": "v1",
            "transfer_id": "t1",
            "object_key": "prefix/input.zip",
            "initial_key": "init-old",
            "token": "token-1",
            "part_size": 4,
            "file_size": 10,
            "file_mtime_ns": file_path.stat().st_mtime_ns,
            "parts": [
                {
                    "number": 1,
                    "offset": 0,
                    "size": 4,
                    "part_string": "part-1-old",
                    "status": "completed",
                }
            ],
        },
    )

    summary = upload_file_multipart(
        client=client,
        file_path=file_path,
        object_key="prefix/input.zip",
        manifest_store=store,
        transfer_id="t1",
        part_size=4,
        concurrency=1,
        part_retries=1,
    )

    uploaded_part_numbers = [
        decode_storage_param(headers["X-Storage-Param"])["number"]
        for url, headers, _ in session.calls
        if url.endswith("/api/upload/multipart/upload")
    ]
    manifest = store.read("t1")
    assert uploaded_part_numbers == [1, 2, 3]
    assert summary["resume_used"] is False
    assert manifest["schema_version"] == "v2"
    assert manifest["initial_key"] == "init-new"
    assert "token" not in manifest
    assert manifest["token_fingerprint"]


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
