from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from matmaster.bohrium.errors import BohriumTransferError
from matmaster.bohrium.upload import upload_input_archive
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    _install_fake_tiefblue,
)


def test_upload_input_archive_returns_oss_key_and_download_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_calls: list[tuple[str, str, dict]] = []
    _install_fake_tiefblue(monkeypatch, upload_calls)
    zip_path = tmp_path / "input.zip"
    zip_path.write_bytes(b"zip-bytes")

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
    assert upload_calls[0][2]["Authorization"] == "Bearer token-123"


def test_upload_input_archive_surfaces_missing_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("bohrium_open_sdk"):
            raise ImportError("bohrium_open_sdk is unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("matmaster.bohrium.upload._oss2", None, raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    zip_path = tmp_path / "input.zip"
    zip_path.write_bytes(b"zip-bytes")

    with pytest.raises(BohriumTransferError, match="bohrium_open_sdk"):
        upload_input_archive(
            create_data={
                "storePath": "sandbox/jobs/run-2/",
                "storeHost": "https://store.example.com",
                "token": "token-456",
            },
            zip_path=zip_path,
        )
