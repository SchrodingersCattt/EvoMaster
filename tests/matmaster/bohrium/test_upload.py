from __future__ import annotations

import builtins
from pathlib import Path

import pytest

import matmaster.bohrium.upload as upload_module
from matmaster.bohrium.upload import UploadedArchive, upload_input_archive


def test_upload_input_archive_returns_oss_key_and_download_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zip_path = tmp_path / "input.zip"
    zip_path.write_bytes(b"zip-bytes")

    def fake_upload_file(*, create_data, zip_path, manifest_root=None):
        del create_data, manifest_root
        return UploadedArchive(
            oss_key="sandbox/jobs/run-1/input.zip",
            download_url=(
                "https://store.example.com/api/download/"
                "sandbox/jobs/run-1/input.zip?token=token-123"
            ),
        )

    monkeypatch.setattr(
        "matmaster.bohrium.upload._upload_input_archive_sdk_free",
        fake_upload_file,
    )

    uploaded = upload_input_archive(
        create_data={
            "storePath": "sandbox/jobs/run-1/",
            "storeHost": "https://store.example.com",
            "token": "token-123",
        },
        zip_path=zip_path,
    )

    assert uploaded.oss_key == "sandbox/jobs/run-1/input.zip"
    assert uploaded.download_url.startswith("https://store.example.com/api/download/")


def test_upload_input_archive_does_not_import_bohrium_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("bohrium"):
            raise AssertionError("bohrium-sdk must not be imported")
        return original_import(name, globals, locals, fromlist, level)

    zip_path = tmp_path / "input.zip"
    zip_path.write_bytes(b"zip-bytes")
    calls: list[dict] = []

    def fake_upload_file(*, create_data, zip_path, manifest_root=None):
        del manifest_root
        calls.append({"create_data": create_data, "zip_path": zip_path})
        return UploadedArchive(
            oss_key="sandbox/jobs/run-2/input.zip",
            download_url=(
                "https://store.example.com/api/download/"
                "sandbox/jobs/run-2/input.zip?token=token-456"
            ),
        )

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(
        "matmaster.bohrium.upload._upload_input_archive_sdk_free",
        fake_upload_file,
    )

    uploaded = upload_input_archive(
        create_data={
            "storePath": "sandbox/jobs/run-2/",
            "storeHost": "https://store.example.com",
            "token": "token-456",
        },
        zip_path=zip_path,
    )

    assert uploaded.oss_key == "sandbox/jobs/run-2/input.zip"
    assert calls[0]["zip_path"] == zip_path


def test_upload_input_archive_legacy_flag_still_uses_sdk_free_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zip_path = tmp_path / "input.zip"
    zip_path.write_bytes(b"zip-bytes")
    calls: list[Path] = []

    def fake_upload(*, create_data, zip_path, manifest_root=None):
        del manifest_root
        del create_data
        calls.append(zip_path)
        return UploadedArchive(
            oss_key="sdk-free/input.zip",
            download_url="https://store.example/api/download/sdk-free/input.zip?token=t",
        )

    monkeypatch.setenv("BOHRIUM_TRANSFER_USE_LEGACY", "1")
    monkeypatch.setattr(
        "matmaster.bohrium.upload._upload_input_archive_sdk_free",
        fake_upload,
    )

    uploaded = upload_input_archive(
        create_data={
            "storePath": "legacy/",
            "storeHost": "https://store.example",
            "token": "t",
        },
        zip_path=zip_path,
    )

    assert uploaded.oss_key == "sdk-free/input.zip"
    assert calls == [zip_path]


def test_submit_archive_upload_uses_serial_multipart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zip_path = tmp_path / "input.zip"
    zip_path.write_bytes(b"zip-bytes")
    captured: dict = {}

    def fake_upload_file_multipart(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        upload_module,
        "upload_file_multipart",
        fake_upload_file_multipart,
    )

    uploaded = upload_module._upload_input_archive_sdk_free(
        create_data={
            "storePath": "sandbox/jobs/run-3/",
            "storeHost": "https://store.example.com",
            "token": "token-789",
        },
        zip_path=zip_path,
    )

    assert uploaded.oss_key == "sandbox/jobs/run-3/input.zip"
    assert captured.get("concurrency") == 1
