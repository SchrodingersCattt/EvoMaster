from __future__ import annotations

import json
from pathlib import Path

from matmaster_bohrium_transfer.client import StoreHostClient, decode_storage_param
from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.multipart import upload_file_multipart


class FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {"code": 0, "data": {}}
        self.text = json.dumps(self._payload)

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(self.text)


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    def post(self, url, *, headers=None, json=None, data=None, timeout=None):
        self.calls.append(
            (url, headers or {}, {"json": json, "data": data, "timeout": timeout})
        )
        if url.endswith("/api/upload/multipart/init"):
            return FakeResponse(payload={"code": 0, "data": {"initialKey": "init-1"}})
        if url.endswith("/api/upload/multipart/upload"):
            param = decode_storage_param((headers or {})["X-Storage-Param"])
            return FakeResponse(
                payload={"code": 0, "data": {"partString": f"part-{param['number']}"}}
            )
        if url.endswith("/api/upload/multipart/complete"):
            return FakeResponse(payload={"code": 0, "data": {"done": True}})
        raise AssertionError(url)


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


def test_store_host_upload_part_sends_tiefblue_compatible_header() -> None:
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)

    result = client.upload_part(
        object_key="prefix/input.zip",
        initial_key="init-1",
        number=2,
        part_size=5,
        data=b"abcde",
    )

    assert result == "part-2"
    _, headers, _ = session.calls[-1]
    decoded = decode_storage_param(headers["X-Storage-Param"])
    assert decoded["initialKey"] == "init-1"
    assert decoded["number"] == 2
    assert decoded["partSize"] == 5
    assert decoded["objectKey"] == "prefix/input.zip"
    assert headers["Authorization"] == "Bearer token-1"


def test_upload_file_multipart_writes_manifest_and_completes(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "input.zip"
    file_path.write_bytes(b"a" * 10)
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)
    store = ManifestStore(tmp_path / "manifest")

    summary = upload_file_multipart(
        client=client,
        file_path=file_path,
        object_key="prefix/input.zip",
        manifest_store=store,
        transfer_id="t1",
        part_size=4,
        concurrency=2,
        part_retries=1,
    )

    assert summary["ok"] is True
    assert summary["parts_total"] == 3
    manifest = store.read("t1")
    assert [part["part_string"] for part in manifest["parts"]] == [
        "part-1",
        "part-2",
        "part-3",
    ]


def test_upload_file_multipart_resumes_completed_manifest_parts(
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
            "initial_key": "init-resume",
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
                },
                {"number": 2, "offset": 4, "size": 4, "status": "pending"},
                {"number": 3, "offset": 8, "size": 2, "status": "pending"},
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
        concurrency=2,
        part_retries=1,
    )

    uploaded_part_numbers = [
        decode_storage_param(headers["X-Storage-Param"])["number"]
        for url, headers, _ in session.calls
        if url.endswith("/api/upload/multipart/upload")
    ]
    complete_call = [
        call
        for call in session.calls
        if call[0].endswith("/api/upload/multipart/complete")
    ][0]
    assert sorted(uploaded_part_numbers) == [2, 3]
    assert complete_call[2]["json"]["initialKey"] == "init-resume"
    assert complete_call[2]["json"]["partString"] == [
        "part-1-old",
        "part-2",
        "part-3",
    ]
    assert summary["resume_used"] is True


def test_upload_file_multipart_emits_progress_events(tmp_path: Path) -> None:
    file_path = tmp_path / "input.zip"
    file_path.write_bytes(b"a" * 10)
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)
    store = ManifestStore(tmp_path / "manifest")
    sink = RecordingSink()

    upload_file_multipart(
        client=client,
        file_path=file_path,
        object_key="prefix/input.zip",
        manifest_store=store,
        transfer_id="t1",
        part_size=4,
        concurrency=2,
        part_retries=1,
        progress_sink=sink,
    )

    event_types = [event.event_type for event in sink.events]
    assert "upload_started" in event_types
    assert "upload_part_completed" in event_types
    assert "upload_completed" in event_types
