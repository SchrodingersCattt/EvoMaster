from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest
from matmaster_bohrium_transfer.client import StoreHostClient
from matmaster_bohrium_transfer.download import download_file
from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.multipart import upload_file_multipart


def test_real_storehost_multipart_upload_complete_and_download(
    tmp_path: Path,
) -> None:
    store_host = os.environ.get("BOHRIUM_STOREHOST_CONTRACT_HOST", "").rstrip("/")
    token = os.environ.get("BOHRIUM_STOREHOST_CONTRACT_TOKEN", "").strip()
    prefix = os.environ.get("BOHRIUM_STOREHOST_CONTRACT_PREFIX", "").strip().strip("/")
    if not store_host or not token or not prefix:
        pytest.skip(
            "set BOHRIUM_STOREHOST_CONTRACT_HOST, "
            "BOHRIUM_STOREHOST_CONTRACT_TOKEN, and "
            "BOHRIUM_STOREHOST_CONTRACT_PREFIX to run StoreHost contract test"
        )

    source = tmp_path / "contract.bin"
    payload = b"matmaster-storehost-contract\n" * 1024
    source.write_bytes(payload)
    object_key = f"{prefix}/contract-{uuid4().hex}.bin"
    client = StoreHostClient(store_host, token)

    upload_file_multipart(
        client=client,
        file_path=source,
        object_key=object_key,
        manifest_store=ManifestStore(tmp_path / "manifest"),
        transfer_id="contract",
        part_size=4096,
        concurrency=2,
        part_retries=2,
    )

    encoded = quote(object_key, safe="/")
    download_url = (
        f"{store_host}/api/download/{encoded}?token={token}"
        "&Response-Content-Type=application/octet-stream"
    )
    downloaded = tmp_path / "downloaded.bin"
    download_file(download_url, downloaded, part_size=4096, concurrency=2)

    assert downloaded.read_bytes() == payload
