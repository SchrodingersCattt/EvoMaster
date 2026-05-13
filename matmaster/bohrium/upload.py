from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from matmaster_bohrium_transfer.client import StoreHostClient
from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.multipart import upload_file_multipart
from matmaster_bohrium_transfer.transport import build_download_url

_SUBMIT_UPLOAD_CONCURRENCY = 1


@dataclass(frozen=True)
class UploadedArchive:
    oss_key: str
    download_url: str


def _archive_location(create_data: dict) -> tuple[str, str, str, str]:
    store_path = str(create_data["storePath"]).strip()
    if not store_path.endswith("/"):
        store_path += "/"
    store_host = str(create_data["storeHost"]).rstrip("/")
    token = str(create_data["token"]).strip()
    oss_key = f"{store_path}input.zip"
    return store_path, store_host, token, oss_key


def _build_download_url(store_host: str, oss_key: str, token: str) -> str:
    return build_download_url(store_host, oss_key, token)


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
        transfer_id=f"submit-{uuid4().hex}",
        concurrency=_SUBMIT_UPLOAD_CONCURRENCY,
    )
    return UploadedArchive(
        oss_key=oss_key,
        download_url=_build_download_url(store_host, oss_key, token),
    )


def upload_input_archive(*, create_data: dict, zip_path: Path) -> UploadedArchive:
    return _upload_input_archive_sdk_free(create_data=create_data, zip_path=zip_path)
