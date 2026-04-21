from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from matmaster_bohrium_transfer.client import StoreHostClient
from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.multipart import upload_file_multipart

from .errors import BohriumTransferError

_tiefblue_cls = None


@dataclass(frozen=True)
class UploadedArchive:
    oss_key: str
    download_url: str


def _load_tiefblue_client():
    global _tiefblue_cls
    if _tiefblue_cls is not None:
        return _tiefblue_cls
    try:
        from bohrium.resources.tiefblue import Tiefblue
    except ImportError as exc:
        raise BohriumTransferError(
            "bohrium-sdk not installed. Run: pip install bohrium-sdk"
        ) from exc
    _tiefblue_cls = Tiefblue
    return _tiefblue_cls


def _archive_location(create_data: dict) -> tuple[str, str, str, str]:
    store_path = str(create_data["storePath"]).strip()
    if not store_path.endswith("/"):
        store_path += "/"
    store_host = str(create_data["storeHost"]).rstrip("/")
    token = str(create_data["token"]).strip()
    oss_key = f"{store_path}input.zip"
    return store_path, store_host, token, oss_key


def _build_download_url(store_host: str, oss_key: str, token: str) -> str:
    encoded_key = quote(oss_key, safe="/")
    return (
        f"{store_host}/api/download/{encoded_key}?token={token}"
        "&Response-Content-Type=application/octet-stream"
    )


def _upload_input_archive_legacy(
    *,
    create_data: dict,
    zip_path: Path,
) -> UploadedArchive:
    tiefblue_client = _load_tiefblue_client()
    _store_path, store_host, token, oss_key = _archive_location(create_data)

    client = tiefblue_client(base_url=store_host)
    response = client.upload_From_file_multi_part(
        object_key=oss_key,
        file_path=str(zip_path),
        token=token,
        progress_bar=False,
    )
    if response is not None and hasattr(response, 'status_code'):
        if response.status_code >= 400:
            raise BohriumTransferError(f"Upload failed: {response.text}")

    return UploadedArchive(
        oss_key=oss_key,
        download_url=_build_download_url(store_host, oss_key, token),
    )


def _upload_input_archive_sdk_free(
    *,
    create_data: dict,
    zip_path: Path,
    manifest_root: Path | None = None,
) -> UploadedArchive:
    _store_path, store_host, token, oss_key = _archive_location(create_data)
    root = manifest_root or (Path(zip_path).parent / ".matmaster" / "transfers")
    client = StoreHostClient(store_host, token)
    upload_file_multipart(
        client=client,
        file_path=zip_path,
        object_key=oss_key,
        manifest_store=ManifestStore(root),
        transfer_id=f"submit-input-{abs(hash(oss_key))}",
    )
    return UploadedArchive(
        oss_key=oss_key,
        download_url=_build_download_url(store_host, oss_key, token),
    )


def upload_input_archive(*, create_data: dict, zip_path: Path) -> UploadedArchive:
    if os.environ.get("BOHRIUM_TRANSFER_USE_LEGACY") == "1":
        return _upload_input_archive_legacy(create_data=create_data, zip_path=zip_path)
    return _upload_input_archive_sdk_free(create_data=create_data, zip_path=zip_path)
