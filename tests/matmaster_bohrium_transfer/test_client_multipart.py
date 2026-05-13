from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from matmaster_bohrium_transfer.client import StoreHostClient, decode_storage_param
from matmaster_bohrium_transfer.errors import StorageInitError, StoragePartUploadError
from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.multipart import upload_file_multipart
from matmaster_bohrium_transfer.security import token_fingerprint


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


def _md5_fields(data: bytes) -> tuple[str, str]:
    digest = hashlib.md5(data, usedforsecurity=False)
    return base64.b64encode(digest.digest()).decode(), digest.hexdigest()


def test_init_multipart_rejects_nonzero_business_code() -> None:
    class BusinessFailureSession:
        def post(self, url, *, headers=None, json=None, data=None, timeout=None):
            del url, headers, json, data, timeout
            return FakeResponse(
                payload={
                    "code": 50001,
                    "message": "multipart init failed",
                    "data": {},
                }
            )

    client = StoreHostClient(
        "https://store.example", "token-1", session=BusinessFailureSession()
    )

    with pytest.raises(StorageInitError, match="multipart init failed"):
        client.init_multipart("prefix/input.zip")


def test_store_host_upload_part_uses_tiefblue_multipart_param_contract() -> None:
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)

    result = client.upload_part(
        object_key="prefix/input.zip",
        initial_key="init-1",
        number=2,
        part_size=5,
        data=b"abcde",
        md5_base64=base64.b64encode(
            hashlib.md5(b"abcde", usedforsecurity=False).digest()
        ).decode(),
        md5_hex=hashlib.md5(b"abcde", usedforsecurity=False).hexdigest(),
    )

    assert result.part_string == "part-2"
    _, headers, _ = session.calls[-1]
    decoded = decode_storage_param(headers["X-Storage-Param"])
    assert decoded["initialKey"] == "init-1"
    assert decoded["number"] == 2
    assert decoded["partSize"] == 5
    assert "objectKey" not in decoded
    assert "contentMd5" not in decoded
    assert "Content-MD5" not in headers
    assert headers["Content-Length"] == "5"
    assert headers["Authorization"] == "Bearer token-1"


def test_complete_multipart_accepts_success_response_without_data() -> None:
    class CompleteWithoutDataSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, dict]] = []

        def post(self, url, *, headers=None, json=None, data=None, timeout=None):
            self.calls.append(
                (url, headers or {}, {"json": json, "data": data, "timeout": timeout})
            )
            return FakeResponse(payload={"code": 0, "message": "ok"})

    session = CompleteWithoutDataSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)

    client.complete_multipart(
        object_key="prefix/input.zip",
        initial_key="init-1",
        part_strings=["part-1"],
    )

    assert session.calls[0][2]["json"] == {
        "initialKey": "init-1",
        "partString": ["part-1"],
    }


def test_upload_file_multipart_sends_part_bytes_with_content_length(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "input.zip"
    file_path.write_bytes(b"abcdef")
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)
    store = ManifestStore(tmp_path / "manifest")

    upload_file_multipart(
        client=client,
        file_path=file_path,
        object_key="prefix/input.zip",
        manifest_store=store,
        transfer_id="t1",
        part_size=3,
        concurrency=1,
        part_retries=1,
    )

    upload_calls = [
        call
        for call in session.calls
        if call[0].endswith("/api/upload/multipart/upload")
    ]
    _, headers, request = upload_calls[0]
    body = request["data"]
    assert body == b"abc"
    assert headers["Content-Length"] == "3"
    assert "Transfer-Encoding" not in headers
    decoded = decode_storage_param(headers["X-Storage-Param"])
    assert decoded == {"initialKey": "init-1", "number": 1, "partSize": 3}


def test_upload_file_multipart_preserves_storehost_part_error_message(
    tmp_path: Path,
) -> None:
    class PartFailureSession(FakeSession):
        def post(self, url, *, headers=None, json=None, data=None, timeout=None):
            if url.endswith("/api/upload/multipart/upload"):
                return FakeResponse(
                    payload={
                        "code": 40001,
                        "message": "unsupported multipart header",
                    }
                )
            return super().post(
                url,
                headers=headers,
                json=json,
                data=data,
                timeout=timeout,
            )

    file_path = tmp_path / "input.zip"
    file_path.write_bytes(b"abc")
    client = StoreHostClient(
        "https://store.example",
        "token-1",
        session=PartFailureSession(),
    )

    with pytest.raises(StoragePartUploadError, match="unsupported multipart header"):
        upload_file_multipart(
            client=client,
            file_path=file_path,
            object_key="prefix/input.zip",
            manifest_store=ManifestStore(tmp_path / "manifest"),
            transfer_id="t1",
            part_size=3,
            concurrency=1,
            part_retries=1,
        )


def test_upload_file_multipart_writes_manifest_v2_without_raw_token(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "input.zip"
    file_path.write_bytes(b"abcdef")
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)
    store = ManifestStore(tmp_path / "manifest")

    upload_file_multipart(
        client=client,
        file_path=file_path,
        object_key="prefix/input.zip",
        manifest_store=store,
        transfer_id="t1",
        part_size=3,
        concurrency=1,
        part_retries=1,
    )

    manifest = store.read("t1")
    assert manifest["schema_version"] == "v2"
    assert "token" not in manifest
    assert manifest["token_fingerprint"]
    assert all(part["md5_base64"] for part in manifest["parts"])


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
    md5_1, md5_1_hex = _md5_fields(b"abcd")
    md5_2, md5_2_hex = _md5_fields(b"efgh")
    md5_3, md5_3_hex = _md5_fields(b"ij")
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)
    store = ManifestStore(tmp_path / "manifest")
    store.write(
        "t1",
        {
            "schema_version": "v2",
            "transfer_id": "t1",
            "object_key": "prefix/input.zip",
            "initial_key": "init-resume",
            "token_fingerprint": token_fingerprint("token-1", "t1"),
            "part_size": 4,
            "file_size": 10,
            "file_mtime_ns": file_path.stat().st_mtime_ns,
            "parts": [
                {
                    "number": 1,
                    "offset": 0,
                    "size": 4,
                    "md5_base64": md5_1,
                    "md5_hex": md5_1_hex,
                    "part_string": "part-1-old",
                    "server_hash_checked": False,
                    "server_hash_value": None,
                    "status": "completed",
                },
                {
                    "number": 2,
                    "offset": 4,
                    "size": 4,
                    "md5_base64": md5_2,
                    "md5_hex": md5_2_hex,
                    "status": "pending",
                },
                {
                    "number": 3,
                    "offset": 8,
                    "size": 2,
                    "md5_base64": md5_3,
                    "md5_hex": md5_3_hex,
                    "status": "pending",
                },
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
    assert "path" not in complete_call[2]["json"]
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
