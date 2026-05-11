from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from matmaster_bohrium_transfer.download import (
    build_download_url,
    choose_sandbox_zip_object,
    download_file,
    extract_zip_safe,
    probe_range,
    run_download_results_payload,
    verify_zip_archive,
)
from matmaster_bohrium_transfer.errors import TransferError


class FakeResponse:
    def __init__(
        self, content: bytes = b"", headers: dict[str, str] | None = None
    ) -> None:
        self.content = content
        self.headers = headers or {}
        self.status_code = 200
        self.ok = True

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]


class FakeRangeSession:
    def __init__(self) -> None:
        self.content = b"0123456789"
        self.get_headers: list[dict[str, str]] = []

    def head(self, url, *, allow_redirects=True, timeout=30):
        return FakeResponse(
            headers={
                "Content-Length": str(len(self.content)),
                "Accept-Ranges": "bytes",
            }
        )

    def get(self, url, *, headers=None, timeout=300, stream=True):
        request_headers = headers or {}
        self.get_headers.append(request_headers)
        range_header = request_headers.get("Range")
        if not range_header:
            return FakeResponse(self.content)
        start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
        start = int(start_text)
        end = int(end_text)
        return FakeResponse(
            self.content[start : end + 1],
            headers={"Content-Length": str(end - start + 1)},
        )


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


def test_choose_sandbox_zip_prefers_job_id_and_skips_task_zip() -> None:
    objects = [
        {"path": "prefix/task.zip", "isDir": False},
        {"path": "prefix/other.zip", "isDir": False},
        {"path": "prefix/job-1.zip", "isDir": False},
    ]

    assert choose_sandbox_zip_object("job-1", objects) == "prefix/job-1.zip"


def test_choose_sandbox_zip_falls_back_to_non_task_zip() -> None:
    objects = [
        {"path": "prefix/task.zip", "isDir": False},
        {"path": "prefix/other.zip", "isDir": False},
    ]

    assert choose_sandbox_zip_object("job-1", objects) == "prefix/other.zip"


def test_extract_zip_safe_rejects_zip_slip(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "bad")

    try:
        extract_zip_safe(archive, tmp_path / "out")
    except Exception as exc:
        assert "unsafe zip member" in str(exc)
    else:
        raise AssertionError("zip slip was accepted")


def test_probe_range_handles_missing_content_length() -> None:
    class Response:
        status_code = 200
        headers = {}

    capability = probe_range(Response())

    assert capability.resume_supported is False
    assert capability.bytes_total is None


def test_build_download_url_encodes_object_key_and_token() -> None:
    url = build_download_url(
        "https://store.example",
        "prefix/job 1/out.zip",
        "a+b/c==",
    )

    assert "/prefix%2Fjob%201%2Fout.zip?" in url
    assert "token=a%2Bb%2Fc%3D%3D" in url
    assert "a+b/c==" not in url


def test_download_file_uses_concurrent_range_requests(tmp_path: Path) -> None:
    session = FakeRangeSession()
    dest = tmp_path / "out.zip"
    sink = RecordingSink()

    summary = download_file(
        "https://store.example/api/download/out.zip?token=t",
        dest,
        session=session,
        part_size=4,
        concurrency=3,
        progress_sink=sink,
    )

    assert dest.read_bytes() == b"0123456789"
    ranges = sorted(
        headers.get("Range") for headers in session.get_headers if headers.get("Range")
    )
    assert ranges == ["bytes=0-3", "bytes=4-7", "bytes=8-9"]
    assert summary.bytes_total == 10
    assert summary.resume_supported is True
    assert summary.sha256 == hashlib.sha256(b"0123456789").hexdigest()
    assert summary.server_hash_checked is False
    assert summary.hash_validation_skipped is True
    event_types = [event.event_type for event in sink.events]
    assert "download_started" in event_types
    assert "download_part_completed" in event_types
    assert "download_completed" in event_types


def test_download_file_validates_content_md5_header(tmp_path: Path) -> None:
    content = b"hello"
    md5_base64 = base64.b64encode(
        hashlib.md5(content, usedforsecurity=False).digest()
    ).decode()

    class Session:
        def head(self, url, *, allow_redirects=True, timeout=30):
            del url, allow_redirects, timeout
            return FakeResponse(headers={})

        def get(self, url, *, headers=None, timeout=300, stream=True):
            del url, headers, timeout, stream
            return FakeResponse(content, headers={"Content-MD5": md5_base64})

    summary = download_file(
        "https://store.example/api/download/out.zip?token=t",
        tmp_path / "out.zip",
        session=Session(),
        concurrency=1,
    )

    assert summary.sha256 == hashlib.sha256(content).hexdigest()
    assert summary.server_hash_checked is True
    assert summary.server_hash_value == md5_base64
    assert summary.hash_validation_skipped is False


def test_verify_zip_archive_rejects_invalid_zip(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    archive.write_bytes(b"not-a-zip")

    with pytest.raises(TransferError, match="invalid zip"):
        verify_zip_archive(archive)


def test_run_download_results_payload_preserves_sandbox_fallback_order(
    tmp_path: Path,
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("log", "done")
    zip_bytes = buffer.getvalue()
    calls: list[str] = []

    class Response:
        def __init__(self, content=b"", json_data=None, headers=None):
            self.content = content
            self._json = json_data or {}
            self.ok = True
            self.status_code = 200
            self.headers = headers or {"Content-Length": str(len(content))}

        def raise_for_status(self):
            return None

        def json(self):
            return self._json

        def iter_content(self, chunk_size=65536):
            yield self.content

    class FakeSession:
        def post(self, url, **kwargs):
            calls.append(url)
            return Response(
                json_data={
                    "code": 0,
                    "data": {
                        "objects": [
                            {"path": "prefix/task.zip", "isDir": False},
                            {"path": "prefix/job-55.zip", "isDir": False},
                        ],
                        "hasNext": False,
                    },
                }
            )

        def head(self, url, **kwargs):
            calls.append(f"HEAD {url}")
            return Response(headers={"Content-Length": str(len(zip_bytes))})

        def get(self, url, **kwargs):
            calls.append(url)
            return Response(content=zip_bytes)

    result = run_download_results_payload(
        {
            "job_id": "job-55",
            "detail_data": {
                "resultUrl": "https://store.example/api/download/prefix/job-55.zip?token=t"
            },
            "result_dir": str(tmp_path / "results"),
            "sandbox": True,
        },
        session=FakeSession(),
    )

    assert "log" in result["files"]
    assert "done" in result["log_tail"]
    transfer_metadata = result["metadata"]["transfer"]
    assert transfer_metadata["download_sha256"] == hashlib.sha256(zip_bytes).hexdigest()
    assert transfer_metadata["fallback_attempts"]
    assert any("iterate" in call for call in calls)
    assert any("job-55.zip" in call for call in calls)


def test_run_download_results_payload_treats_bad_zip_as_transfer_failure(
    tmp_path: Path,
) -> None:
    class Response:
        content = b"not-a-zip"
        ok = True
        status_code = 200
        headers = {"Content-Length": str(len(content))}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            yield self.content

    class FakeSession:
        def head(self, url, **kwargs):
            return Response()

        def get(self, url, **kwargs):
            return Response()

    with pytest.raises(TransferError, match="invalid zip"):
        run_download_results_payload(
            {
                "job_id": "job-1",
                "detail_data": {"resultUrl": "https://store.example/out.zip"},
                "result_dir": str(tmp_path / "results"),
                "sandbox": False,
            },
            session=FakeSession(),
        )
