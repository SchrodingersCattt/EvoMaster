from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from matmaster_bohrium_transfer.client import StoreHostClient
from matmaster_bohrium_transfer.download import (
    build_download_url,
    download_file,
    verify_zip_archive,
)
from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.multipart import upload_file_multipart

SKIP_REASON = (
    "set MATMASTER_BOHRIUM_TRANSFER_CONTRACT=1 to run real StoreHost contract tests"
)


@pytest.fixture
def contract_env() -> dict[str, str]:
    if os.environ.get("MATMASTER_BOHRIUM_TRANSFER_CONTRACT") != "1":
        pytest.skip(SKIP_REASON)
    required = {
        "store_host": os.environ.get("BOHRIUM_STORE_HOST", "").rstrip("/"),
        "token": os.environ.get("BOHRIUM_STORE_TOKEN", "").strip(),
        "prefix": os.environ.get("BOHRIUM_STORE_PREFIX", "").strip().strip("/"),
    }
    if not all(required.values()):
        pytest.skip(
            "set BOHRIUM_STORE_HOST, BOHRIUM_STORE_TOKEN, and "
            "BOHRIUM_STORE_PREFIX to run real StoreHost contract tests"
        )
    return required


class RecordingStoreHostClient(StoreHostClient):
    def __init__(self, store_host: str, token: str) -> None:
        super().__init__(store_host, token)
        self.part_md5_values: list[str] = []

    def upload_part(self, **kwargs):
        self.part_md5_values.append(kwargs["md5_base64"])
        return super().upload_part(**kwargs)


def _assert_manifest_has_no_raw_token(manifest_root: Path, token: str) -> None:
    for manifest_path in manifest_root.rglob("manifest.json"):
        assert token not in manifest_path.read_text(encoding="utf-8")


def test_real_storehost_multipart_upload_complete_and_download(
    tmp_path: Path,
    contract_env: dict[str, str],
) -> None:
    source = tmp_path / "contract.bin"
    payload = os.urandom(110 * 1024 * 1024)
    source.write_bytes(payload)
    fixture_sha256 = hashlib.sha256(payload).hexdigest()
    manifest_root = tmp_path / "manifest"
    object_key = f"{contract_env['prefix']}/contract-{uuid4().hex}.bin"
    transfer_id = f"contract-{uuid4().hex}"
    client = RecordingStoreHostClient(contract_env["store_host"], contract_env["token"])

    upload_file_multipart(
        client=client,
        file_path=source,
        object_key=object_key,
        manifest_store=ManifestStore(manifest_root),
        transfer_id=transfer_id,
        concurrency=2,
        part_retries=2,
    )

    assert client.part_md5_values
    _assert_manifest_has_no_raw_token(manifest_root, contract_env["token"])

    download_url = build_download_url(
        contract_env["store_host"],
        object_key,
        contract_env["token"],
    )
    downloaded = tmp_path / "downloaded.bin"
    summary = download_file(download_url, downloaded, concurrency=2)

    assert summary.sha256 == fixture_sha256
    if downloaded.suffix == ".zip":
        verify_zip_archive(downloaded)


def test_real_storehost_concurrent_uploads_use_distinct_transfer_dirs(
    tmp_path: Path,
    contract_env: dict[str, str],
) -> None:
    source = tmp_path / "contract.bin"
    source.write_bytes(os.urandom(110 * 1024 * 1024))
    manifest_root = tmp_path / "manifest"
    transfer_ids = [f"contract-a-{uuid4().hex}", f"contract-b-{uuid4().hex}"]
    object_keys = [
        f"{contract_env['prefix']}/contract-a-{uuid4().hex}.bin",
        f"{contract_env['prefix']}/contract-b-{uuid4().hex}.bin",
    ]

    def upload(index: int) -> dict:
        client = RecordingStoreHostClient(
            contract_env["store_host"], contract_env["token"]
        )
        return upload_file_multipart(
            client=client,
            file_path=source,
            object_key=object_keys[index],
            manifest_store=ManifestStore(manifest_root),
            transfer_id=transfer_ids[index],
            concurrency=2,
            part_retries=2,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        summaries = list(executor.map(upload, (0, 1)))

    assert all(summary["ok"] for summary in summaries)
    assert (manifest_root / transfer_ids[0] / "manifest.json").exists()
    assert (manifest_root / transfer_ids[1] / "manifest.json").exists()
    assert transfer_ids[0] != transfer_ids[1]
    _assert_manifest_has_no_raw_token(manifest_root, contract_env["token"])
