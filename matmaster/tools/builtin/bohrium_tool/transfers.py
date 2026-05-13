from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from matmaster.bohrium.client import get_file_token
from matmaster.bohrium.types import BohriumContext
from matmaster.bohrium.upload import (
    UploadedArchive,
    _build_download_url,
    upload_input_archive,
)

from .errors import BohriumTransferError
from .models import BohriumDownloadTarget, BohriumInputSource
from .remote_runner import run_remote_transfer

logger = logging.getLogger(__name__)


def _new_transfer_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _zip_local_dir(input_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in input_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(input_dir))


@contextmanager
def prepare_input_archive(source: BohriumInputSource, *, session):
    with tempfile.TemporaryDirectory(prefix="bohrium_submit_") as tmp_dir:
        zip_path = Path(tmp_dir) / "input.zip"
        if source.kind == "remote_share_dir":
            raise BohriumTransferError("remote input_dir must use direct remote upload")
        else:
            _zip_local_dir(Path(source.resolved_path), zip_path)
        yield zip_path


def upload_input_source(
    source: BohriumInputSource,
    *,
    create_data: dict,
    session,
) -> UploadedArchive:
    if source.kind == "remote_share_dir":
        store_path = str(create_data["storePath"]).strip()
        if not store_path.endswith("/"):
            store_path += "/"
        store_host = str(create_data["storeHost"]).rstrip("/")
        token = str(create_data["token"]).strip()
        payload = {
            "transfer_id": _new_transfer_id("submit"),
            "input_dir": source.resolved_path,
            "store_host": store_host,
            "store_path": store_path,
            "token": token,
            "object_name": "input.zip",
        }
        result = run_remote_transfer(
            session,
            subcommand="upload-submit",
            payload=payload,
        )
        oss_key = str(result.get("oss_key") or "").strip()
        if not oss_key:
            raise BohriumTransferError("remote helper did not return oss_key")
        return UploadedArchive(
            oss_key=oss_key,
            download_url=_build_download_url(store_host, oss_key, token),
        )

    with prepare_input_archive(source, session=session) as zip_path:
        return upload_input_archive(create_data=create_data, zip_path=zip_path)


def publish_download_target(target: BohriumDownloadTarget, *, session) -> str:
    if target.publish_mode == "direct":
        target.staging_dir.mkdir(parents=True, exist_ok=True)
        return target.resolved_path
    if target.publish_mode == "remote_direct":
        return target.resolved_path
    try:
        session.upload_directory(str(target.staging_dir), target.resolved_path)
        shutil.rmtree(target.staging_dir, ignore_errors=True)
        return target.resolved_path
    except Exception:
        logger.warning(
            "Failed to upload results to remote share %s",
            target.resolved_path,
            exc_info=True,
        )
        return str(target.staging_dir)


def download_remote_results(
    target: BohriumDownloadTarget,
    *,
    job_id: int | str,
    detail_data: dict,
    ctx: BohriumContext,
    session,
) -> tuple[list[str], str, str]:
    payload: dict = {
        "transfer_id": _new_transfer_id("download"),
        "job_id": str(job_id),
        "result_dir": target.resolved_path,
        "sandbox": ctx.sandbox,
        "detail_data": detail_data,
    }
    if ctx.sandbox:
        try:
            host, path, token = get_file_token(
                ctx,
                file_path="log",
                bohr_job_id=str(job_id),
            )
            if host and path and token:
                payload["sandbox_log_file"] = {
                    "host": host,
                    "path": path,
                    "token": token,
                }
        except Exception:
            logger.debug("sandbox log token prefetch failed", exc_info=True)

    result = run_remote_transfer(
        session,
        subcommand="download-results",
        payload=payload,
    )
    files = result.get("files") or []
    if not isinstance(files, list):
        files = []
    log_tail = str(result.get("log_tail") or "")
    result_dir = str(result.get("result_dir") or target.resolved_path)
    return [str(item) for item in files], log_tail, result_dir
