from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

import requests

from .errors import BohriumTransferError
from .models import BohriumContext, BohriumDownloadTarget, BohriumInputSource

logger = logging.getLogger(__name__)
_SANDBOX_OBJECT_DOWNLOAD_LIMIT = 128


def _zip_local_dir(input_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in input_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(input_dir))


def _prepare_remote_input_zip(*, source: BohriumInputSource, session, zip_path: Path) -> None:
    remote_zip_path = f"/tmp/bohrium_input_{uuid4().hex}.zip"
    script = (
        "python3 - <<'PY'\n"
        "import pathlib, zipfile\n"
        f"source = pathlib.Path({json.dumps(source.resolved_path)})\n"
        f"archive = pathlib.Path({json.dumps(remote_zip_path)})\n"
        "with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:\n"
        "    for path in source.rglob('*'):\n"
        "        if path.is_file():\n"
        "            zf.write(path, path.relative_to(source))\n"
        "PY"
    )
    cleanup_cmd = f"rm -f {remote_zip_path}"
    try:
        result = session.exec_bash(script)
        if result.get("exit_code") != 0:
            detail = str(
                result.get("stderr")
                or result.get("output")
                or result.get("stdout")
                or "unknown error"
            ).strip()
            raise BohriumTransferError(
                f"Failed to package remote input_dir '{source.resolved_path}': {detail}"
            )
        try:
            zip_path.write_bytes(session.download(remote_zip_path))
        except Exception as exc:
            raise BohriumTransferError(
                f"Failed to download remote input_dir '{source.resolved_path}': {exc}"
            ) from exc
    finally:
        try:
            session.exec_bash(cleanup_cmd)
        except Exception:
            logger.warning(
                "Failed to clean up temporary remote input zip %s",
                remote_zip_path,
                exc_info=True,
            )


@contextmanager
def prepare_input_archive(source: BohriumInputSource, *, session):
    with tempfile.TemporaryDirectory(prefix="bohrium_submit_") as tmp_dir:
        zip_path = Path(tmp_dir) / "input.zip"
        if source.kind == "remote_share_dir":
            _prepare_remote_input_zip(source=source, session=session, zip_path=zip_path)
        else:
            _zip_local_dir(Path(source.resolved_path), zip_path)
        yield zip_path


def publish_download_target(target: BohriumDownloadTarget, *, session) -> str:
    if target.publish_mode == "direct":
        target.staging_dir.mkdir(parents=True, exist_ok=True)
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


def _download_to_file(url: str, dest: Path, *, timeout: int = 300) -> None:
    response = requests.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in response.iter_content(chunk_size=65536):
            fh.write(chunk)


def _extract_zip(zip_path: Path, extract_dir: Path) -> list[str]:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
            return zf.namelist()
    except zipfile.BadZipFile:
        return [f"(bad zip: {zip_path.name})"]


def _read_log(result_dir: Path, *, max_chars: int = 4000) -> str:
    for name in ("log", "STDOUTERR"):
        file_path = result_dir / name
        if file_path.exists():
            size = file_path.stat().st_size
            with open(file_path, "rb") as fh:
                if size > max_chars * 4:
                    fh.seek(-(max_chars * 4), 2)
                raw = fh.read()
            return raw.decode("utf-8", errors="replace")[-max_chars:]
    return "(no log file found in result directory)"


def _parse_sandbox_result_url(result_url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(result_url)
    host = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    token = parse_qs(parsed.query).get("token", [""])[0].strip()
    object_path = unquote(parsed.path.removeprefix("/api/download/")).strip("/")
    if not host or not token or not object_path:
        raise BohriumTransferError(f"invalid sandbox resultUrl: {result_url}")
    prefix = object_path.rsplit("/", 1)[0] + "/" if "/" in object_path else ""
    return host, token, object_path, prefix


def _sandbox_iterate_objects(host: str, token: str, prefix: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    objects: list[dict] = []
    next_token = ""
    while True:
        payload = {"prefix": prefix}
        if next_token:
            payload["nextToken"] = next_token
        response = requests.post(
            f"{host.rstrip('/')}/api/iterate",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json() or {}
        if body.get("code") not in (None, 0):
            raise BohriumTransferError(f"sandbox iterate failed: {body}")
        data = body.get("data") or {}
        objects.extend(data.get("objects") or [])
        if not data.get("hasNext"):
            break
        next_token = str(data.get("nextToken") or "").strip()
        if not next_token:
            break
    return objects


def _sandbox_download_object(host: str, token: str, object_path: str, dest_path: Path) -> None:
    encoded_path = quote(object_path, safe="/")
    _download_to_file(
        f"{host.rstrip('/')}/api/download/{encoded_path}?token={token}&Response-Content-Type=application/octet-stream",
        dest_path,
    )


def _sandbox_choose_zip_object(job_id: int | str, objects: list[dict]) -> str | None:
    preferred_name = f"{job_id}.zip"
    for obj in objects:
        object_path = str(obj.get("path") or obj.get("key") or "").strip()
        if object_path and Path(object_path).name == preferred_name:
            return object_path
    for obj in objects:
        object_path = str(obj.get("path") or obj.get("key") or "").strip()
        if object_path.endswith(".zip") and Path(object_path).name != "task.zip":
            return object_path
    for obj in objects:
        object_path = str(obj.get("path") or obj.get("key") or "").strip()
        if object_path.endswith(".zip"):
            return object_path
    return None


def _sandbox_download_log(
    *,
    job_id: int | str,
    result_dir: Path,
    ctx: BohriumContext,
    root_host: str,
    root_token: str,
    objects: list[dict],
) -> bool:
    log_path = result_dir / "log"
    try:
        response = requests.post(
            f"{ctx.base_url}/openapi/v1/sandbox/job/file/token",
            headers={"accessKey": ctx.access_key, "Content-Type": "application/json"},
            json={"filePath": "log", "jobId": str(job_id)},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json() or {}
        if body.get("code") == 0:
            data = body.get("data") or {}
            log_host = str(data.get("host") or data.get("storeHost") or "")
            log_token = str(data.get("token") or "").strip()
            log_object_path = str(data.get("path") or data.get("storePath") or "").strip(
                "/"
            )
            if log_host and log_token and log_object_path:
                _sandbox_download_object(log_host, log_token, log_object_path, log_path)
                return True
    except Exception:
        logger.debug("sandbox log token download failed", exc_info=True)

    if root_host and root_token:
        for obj in objects:
            object_path = str(obj.get("path") or obj.get("key") or "").strip()
            if object_path and Path(object_path).name == "log":
                _sandbox_download_object(root_host, root_token, object_path, log_path)
                return True
    return False


def _merge_log_file(files: list[str], log_downloaded: bool) -> list[str]:
    if not log_downloaded or "log" in files:
        return files
    return ["log", *files]


def _sandbox_relative_object_path(object_path: str, root_prefix: str) -> str:
    path = object_path.strip()
    if root_prefix and path.startswith(root_prefix):
        path = path[len(root_prefix) :]
    return path.lstrip("/")


def _sandbox_download_objects(
    *, objects: list[dict], root_host: str, root_token: str, root_prefix: str, result_dir: Path
) -> list[str]:
    downloaded: list[str] = []
    count = 0
    for obj in objects:
        if count >= _SANDBOX_OBJECT_DOWNLOAD_LIMIT:
            break
        if not isinstance(obj, dict) or obj.get("isDir"):
            continue
        object_path = str(obj.get("path") or obj.get("key") or "").strip()
        if not object_path:
            continue
        relative_path = _sandbox_relative_object_path(object_path, root_prefix)
        if not relative_path or relative_path.endswith(".zip"):
            continue
        dest_path = result_dir / relative_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _sandbox_download_object(root_host, root_token, object_path, dest_path)
            downloaded.append(relative_path)
            count += 1
        except Exception:
            logger.debug(
                "sandbox object download failed object=%s",
                object_path,
                exc_info=True,
            )
    return downloaded


def _sandbox_download_results(
    *, job_id: int | str, detail_data: dict, result_dir: Path, ctx: BohriumContext
) -> tuple[list[str], str]:
    result_url = str(detail_data.get("resultUrl") or detail_data.get("result") or "")
    objects: list[dict] = []
    root_host = ""
    root_token = ""
    root_prefix = ""

    if result_url:
        try:
            root_host, root_token, _object_path, root_prefix = _parse_sandbox_result_url(
                result_url
            )
            objects = _sandbox_iterate_objects(root_host, root_token, root_prefix)
        except Exception:
            logger.debug("sandbox resultUrl iteration failed", exc_info=True)

    log_downloaded = _sandbox_download_log(
        job_id=job_id,
        result_dir=result_dir,
        ctx=ctx,
        root_host=root_host,
        root_token=root_token,
        objects=objects,
    )

    zip_key = _sandbox_choose_zip_object(job_id, objects)
    if zip_key and root_host and root_token:
        try:
            zip_path = result_dir / Path(zip_key).name
            _sandbox_download_object(root_host, root_token, zip_key, zip_path)
            files = _extract_zip(zip_path, result_dir)
            return _merge_log_file(files, log_downloaded), _read_log(result_dir)
        except Exception:
            logger.debug("sandbox zip-object download failed", exc_info=True)

    if result_url:
        try:
            zip_path = result_dir / "out.zip"
            _download_to_file(result_url, zip_path)
            files = _extract_zip(zip_path, result_dir)
            return _merge_log_file(files, log_downloaded), _read_log(result_dir)
        except Exception:
            logger.debug("sandbox resultUrl zip download failed", exc_info=True)

    if objects and root_host and root_token:
        downloaded_files = _sandbox_download_objects(
            objects=objects,
            root_host=root_host,
            root_token=root_token,
            root_prefix=root_prefix,
            result_dir=result_dir,
        )
        downloaded_files = _merge_log_file(downloaded_files, log_downloaded)
        if downloaded_files:
            return downloaded_files, _read_log(result_dir)

    if log_downloaded:
        return ["log"], _read_log(result_dir)
    if result_url:
        return [], "(sandbox resultUrl download failed)"
    return [], "(no resultUrl in job detail)"


def download_job_artifacts(
    *, job_id: int | str, detail_data: dict, target: BohriumDownloadTarget, ctx: BohriumContext
) -> tuple[list[str], str]:
    result_dir = target.staging_dir
    result_dir.mkdir(parents=True, exist_ok=True)
    if ctx.sandbox:
        return _sandbox_download_results(
            job_id=job_id,
            detail_data=detail_data,
            result_dir=result_dir,
            ctx=ctx,
        )
    result_url = str(detail_data.get("resultUrl") or detail_data.get("result") or "")
    if not result_url:
        return [], "(no resultUrl in job detail)"

    zip_path = result_dir / "out.zip"
    _download_to_file(result_url, zip_path)
    files = _extract_zip(zip_path, result_dir)
    return files, _read_log(result_dir)
