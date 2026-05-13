from __future__ import annotations

import base64
import hashlib
import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import requests

from .errors import ExtractError, TransferError
from .progress import NoopProgressSink, ProgressSink, TransferProgressEvent
from .security import redact_secrets
from .transport import build_download_url
from .version import PROTOCOL_VERSION, SCHEMA_VERSION


@dataclass(frozen=True)
class RangeCapability:
    resume_supported: bool
    bytes_total: int | None
    reason: str


@dataclass(frozen=True)
class DownloadSummary:
    path: Path
    bytes_total: int | None
    bytes_done: int
    resume_supported: bool
    sha256: str
    server_hash_checked: bool
    server_hash_value: str | None
    hash_validation_skipped: bool


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _md5_file(path: Path, *, as_base64: bool) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    if as_base64:
        return base64.b64encode(digest.digest()).decode()
    return digest.hexdigest()


def _first_header(
    headers: dict[str, str], *names: str
) -> tuple[str | None, str | None]:
    lowered = {key.lower(): value for key, value in headers.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return name, str(value).strip()
    return None, None


def _validate_download_hash(
    path: Path,
    headers: dict[str, str],
) -> tuple[bool, str | None, bool]:
    _name, md5_base64 = _first_header(headers, "Content-MD5", "X-OSS-Content-Md5")
    if md5_base64:
        expected = _md5_file(path, as_base64=True)
        if md5_base64.strip() != expected:
            raise TransferError("download_verify", "download Content-MD5 mismatch")
        return True, md5_base64.strip(), False

    _name, etag = _first_header(headers, "ETag")
    if etag:
        normalized = etag.strip().strip('"')
        if len(normalized) == 32 and all(
            char in "0123456789abcdefABCDEF" for char in normalized
        ):
            expected = _md5_file(path, as_base64=False)
            if normalized.lower() != expected:
                raise TransferError("download_verify", "download ETag MD5 mismatch")
            return True, etag.strip(), False
        return False, etag.strip(), True

    _name, crc64 = _first_header(headers, "X-OSS-Hash-Crc64ecma")
    if crc64:
        return False, crc64.strip(), True
    return False, None, True


def _summary_with_integrity(
    *,
    dest: Path,
    bytes_total: int | None,
    bytes_done: int,
    resume_supported: bool,
    headers: dict[str, str],
) -> DownloadSummary:
    digest = sha256_file(dest)
    server_hash_checked, server_hash_value, hash_validation_skipped = (
        _validate_download_hash(dest, headers)
    )
    return DownloadSummary(
        path=dest,
        bytes_total=bytes_total,
        bytes_done=bytes_done,
        resume_supported=resume_supported,
        sha256=digest,
        server_hash_checked=server_hash_checked,
        server_hash_value=server_hash_value,
        hash_validation_skipped=hash_validation_skipped,
    )


def probe_range(response) -> RangeCapability:
    raw_length = (
        response.headers.get("Content-Length") if hasattr(response, "headers") else None
    )
    if not raw_length:
        return RangeCapability(False, None, "missing_content_length")
    try:
        total = int(raw_length)
    except ValueError:
        return RangeCapability(False, None, "invalid_content_length")
    accept_ranges = str(response.headers.get("Accept-Ranges", "")).lower()
    return RangeCapability(
        "bytes" in accept_ranges,
        total,
        "ok" if "bytes" in accept_ranges else "range_not_advertised",
    )


def _range_specs(total: int, part_size: int) -> list[tuple[int, int, int]]:
    specs: list[tuple[int, int, int]] = []
    start = 0
    index = 0
    while start < total:
        end = min(start + part_size - 1, total - 1)
        specs.append((index, start, end))
        start = end + 1
        index += 1
    return specs


def _download_stream(
    session,
    url: str,
    dest: Path,
    *,
    timeout: int,
    progress_sink: ProgressSink,
    transfer_id: str,
) -> DownloadSummary:
    response = session.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    total_header = (
        response.headers.get("Content-Length") if hasattr(response, "headers") else None
    )
    bytes_total = int(total_header) if total_header and total_header.isdigit() else None
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    bytes_done = 0
    with open(tmp, "wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            fh.write(chunk)
            bytes_done += len(chunk)
            progress_sink.emit(
                TransferProgressEvent(
                    event_type="download_chunk_completed",
                    transfer_id=transfer_id,
                    phase="download",
                    direction="download",
                    bytes_done=bytes_done,
                    bytes_total=bytes_total,
                    resume_supported=False,
                )
            )
    tmp.replace(dest)
    progress_sink.emit(
        TransferProgressEvent(
            event_type="download_completed",
            transfer_id=transfer_id,
            phase="download",
            direction="download",
            bytes_done=bytes_done,
            bytes_total=bytes_total,
            resume_supported=False,
        )
    )
    return _summary_with_integrity(
        dest=dest,
        bytes_total=bytes_total,
        bytes_done=bytes_done,
        resume_supported=False,
        headers=dict(getattr(response, "headers", {}) or {}),
    )


def _download_one_range(
    *,
    session,
    url: str,
    part_path: Path,
    start: int,
    end: int,
    timeout: int,
) -> int:
    expected = end - start + 1
    if part_path.exists() and part_path.stat().st_size == expected:
        return expected
    tmp = part_path.with_suffix(part_path.suffix + ".tmp")
    response = session.get(
        url,
        headers={"Range": f"bytes={start}-{end}"},
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()
    with open(tmp, "wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fh.write(chunk)
    if tmp.stat().st_size != expected:
        raise OSError(
            f"range download size mismatch for bytes={start}-{end}: "
            f"expected={expected} got={tmp.stat().st_size}"
        )
    tmp.replace(part_path)
    return expected


def _download_ranges(
    session,
    url: str,
    dest: Path,
    *,
    bytes_total: int,
    part_size: int,
    concurrency: int,
    timeout: int,
    progress_sink: ProgressSink,
    transfer_id: str,
    server_headers: dict[str, str] | None = None,
) -> DownloadSummary:
    specs = _range_specs(bytes_total, part_size)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part_paths = {
        index: dest.with_suffix(dest.suffix + f".part.{index}")
        for index, _start, _end in specs
    }
    bytes_done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(
                _download_one_range,
                session=session,
                url=url,
                part_path=part_paths[index],
                start=start,
                end=end,
                timeout=timeout,
            )
            for index, start, end in specs
        ]
        for future in as_completed(futures):
            bytes_done += future.result()
            progress_sink.emit(
                TransferProgressEvent(
                    event_type="download_part_completed",
                    transfer_id=transfer_id,
                    phase="download",
                    direction="download",
                    bytes_done=bytes_done,
                    bytes_total=bytes_total,
                    resume_supported=True,
                )
            )
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as out:
        for index, _start, _end in specs:
            with open(part_paths[index], "rb") as part:
                shutil.copyfileobj(part, out)
    if tmp.stat().st_size != bytes_total:
        raise OSError(
            "assembled download size mismatch: "
            f"expected={bytes_total} got={tmp.stat().st_size}"
        )
    tmp.replace(dest)
    for part_path in part_paths.values():
        part_path.unlink(missing_ok=True)
    progress_sink.emit(
        TransferProgressEvent(
            event_type="download_completed",
            transfer_id=transfer_id,
            phase="download",
            direction="download",
            bytes_done=bytes_total,
            bytes_total=bytes_total,
            resume_supported=True,
        )
    )
    return _summary_with_integrity(
        dest=dest,
        bytes_total=bytes_total,
        bytes_done=bytes_done,
        resume_supported=True,
        headers=server_headers or {},
    )


def download_file(
    url: str,
    dest: str | Path,
    *,
    session=None,
    part_size: int = 64 * 1024 * 1024,
    concurrency: int = 4,
    timeout: int = 300,
    progress_sink: ProgressSink | None = None,
    transfer_id: str = "download",
) -> DownloadSummary:
    http = session or requests.Session()
    target = Path(dest)
    sink = progress_sink or NoopProgressSink()
    try:
        head = http.head(url, allow_redirects=True, timeout=30)
        capability = probe_range(head)
        head_headers = dict(getattr(head, "headers", {}) or {})
    except Exception:
        capability = RangeCapability(False, None, "head_failed")
        head_headers = {}
    sink.emit(
        TransferProgressEvent(
            event_type="download_started",
            transfer_id=transfer_id,
            phase="download",
            direction="download",
            bytes_done=0,
            bytes_total=capability.bytes_total,
            resume_supported=capability.resume_supported,
        )
    )
    if (
        capability.resume_supported
        and capability.bytes_total is not None
        and concurrency > 1
    ):
        return _download_ranges(
            http,
            url,
            target,
            bytes_total=capability.bytes_total,
            part_size=part_size,
            concurrency=concurrency,
            timeout=timeout,
            progress_sink=sink,
            transfer_id=transfer_id,
            server_headers=head_headers,
        )
    return _download_stream(
        http,
        url,
        target,
        timeout=timeout,
        progress_sink=sink,
        transfer_id=transfer_id,
    )


def choose_sandbox_zip_object(
    job_id: int | str, objects: list[dict[str, Any]]
) -> str | None:
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


def extract_zip_safe(archive: str | Path, extract_dir: str | Path) -> list[str]:
    archive_path = Path(archive)
    root = Path(extract_dir)
    root.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.infolist():
            if member.filename.endswith("/"):
                continue
            target = root / member.filename
            resolved_root = root.resolve()
            resolved_target = target.resolve()
            if resolved_root not in (resolved_target, *resolved_target.parents):
                raise ExtractError(
                    "extract", f"unsafe zip member path: {member.filename}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            files.append(member.filename)
    return files


_SANDBOX_OBJECT_DOWNLOAD_LIMIT = 128


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing required payload field: {key}")
    return value


def read_log(result_dir: str | Path, *, max_chars: int = 4000) -> str:
    root = Path(result_dir)
    for name in ("log", "STDOUTERR"):
        file_path = root / name
        if file_path.exists():
            size = file_path.stat().st_size
            with open(file_path, "rb") as fh:
                if size > max_chars * 4:
                    fh.seek(-(max_chars * 4), os.SEEK_END)
                raw = fh.read()
            return raw.decode("utf-8", errors="replace")[-max_chars:]
    return "(no log file found in result directory)"


def publish_result_dir(staging: str | Path, result_dir: str | Path) -> None:
    staging_path = Path(staging)
    result_path = Path(result_dir)
    lockdir = result_path.with_name(result_path.name + ".lock")
    backup = result_path.with_name(result_path.name + f".bak.{uuid4().hex}")
    lock_acquired = False
    try:
        lockdir.mkdir()
        lock_acquired = True
        if result_path.exists():
            result_path.rename(backup)
        staging_path.replace(result_path)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if backup.exists() and not result_path.exists():
            backup.rename(result_path)
        raise
    finally:
        if lock_acquired:
            shutil.rmtree(lockdir, ignore_errors=True)


def _parse_sandbox_result_url(result_url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(result_url)
    host = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    token = parse_qs(parsed.query).get("token", [""])[0].strip()
    object_path = unquote(parsed.path.removeprefix("/api/download/")).strip("/")
    if not host or not token or not object_path:
        raise ValueError("invalid sandbox resultUrl")
    prefix = object_path.rsplit("/", 1)[0] + "/" if "/" in object_path else ""
    return host, token, object_path, prefix


def _iterate_objects(
    host: str,
    token: str,
    prefix: str,
    *,
    session=None,
) -> list[dict[str, Any]]:
    http = session or requests.Session()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    objects: list[dict[str, Any]] = []
    next_token = ""
    while True:
        payload: dict[str, Any] = {"prefix": prefix}
        if next_token:
            payload["nextToken"] = next_token
        response = http.post(
            f"{host.rstrip('/')}/api/iterate",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json() or {}
        if body.get("code") not in (None, 0):
            raise ValueError(f"sandbox iterate failed: {body}")
        data = body.get("data") or {}
        objects.extend(data.get("objects") or [])
        if not data.get("hasNext"):
            break
        next_token = str(data.get("nextToken") or "").strip()
        if not next_token:
            break
    return objects


def _download_object_url(host: str, token: str, object_path: str) -> str:
    return build_download_url(host, object_path, token)


def _download_object(
    host: str,
    token: str,
    object_path: str,
    dest_path: Path,
    *,
    session=None,
) -> DownloadSummary:
    return download_file(
        _download_object_url(host, token, object_path),
        dest_path,
        session=session,
    )


def _merge_log_file(files: list[str], log_downloaded: bool) -> list[str]:
    if not log_downloaded or "log" in files:
        return files
    return ["log", *files]


def _sandbox_relative_object_path(object_path: str, root_prefix: str) -> str:
    path = object_path.strip()
    if root_prefix and path.startswith(root_prefix):
        path = path[len(root_prefix) :]
    return path.lstrip("/")


def _record_fallback_attempt(
    attempts: list[dict[str, object]],
    name: str,
    *,
    ok: bool,
    error: BaseException | str | None = None,
) -> None:
    item: dict[str, object] = {"name": name, "ok": ok}
    if error is not None:
        item["error"] = redact_secrets(str(error))
    attempts.append(item)


def _transfer_metadata_from_summary(summary: DownloadSummary) -> dict[str, object]:
    return {
        "download_sha256": summary.sha256,
        "server_hash_checked": summary.server_hash_checked,
        "server_hash_value": summary.server_hash_value,
        "hash_validation_skipped": summary.hash_validation_skipped,
    }


def _extract_result_zip(zip_path: Path, staging: Path) -> list[str]:
    verify_zip_archive(zip_path)
    return extract_zip_safe(zip_path, staging)


def verify_zip_archive(path: str | Path) -> None:
    archive_path = Path(path)
    if not zipfile.is_zipfile(archive_path):
        raise TransferError("download_verify", "invalid zip archive")
    with zipfile.ZipFile(archive_path, "r") as zf:
        bad_member = zf.testzip()
    if bad_member:
        raise TransferError(
            "download_verify",
            f"zip archive CRC check failed for {bad_member}",
        )


def _download_sandbox_log(
    *,
    payload: dict[str, Any],
    staging: Path,
    root_host: str,
    root_token: str,
    objects: list[dict[str, Any]],
    session=None,
) -> tuple[bool, int]:
    log_file = payload.get("sandbox_log_file")
    if isinstance(log_file, dict):
        host = str(log_file.get("host") or "").strip()
        path = str(log_file.get("path") or "").strip()
        token = str(log_file.get("token") or "").strip()
        if host and path and token:
            try:
                summary = _download_object(
                    host, token, path, staging / "log", session=session
                )
                return True, summary.bytes_done
            except Exception:
                pass
    if root_host and root_token:
        for obj in objects:
            object_path = str(obj.get("path") or obj.get("key") or "").strip()
            if object_path and Path(object_path).name == "log":
                summary = _download_object(
                    root_host,
                    root_token,
                    object_path,
                    staging / "log",
                    session=session,
                )
                return True, summary.bytes_done
    return False, 0


def _download_sandbox_results(
    *,
    payload: dict[str, Any],
    staging: Path,
    fallback_attempts: list[dict[str, object]],
    session=None,
) -> tuple[list[str], str, int, dict[str, object]]:
    job_id = _required_str(payload, "job_id")
    detail_data = payload.get("detail_data") or {}
    if not isinstance(detail_data, dict):
        raise ValueError("detail_data must be a JSON object")
    result_url = str(detail_data.get("resultUrl") or detail_data.get("result") or "")
    objects: list[dict[str, Any]] = []
    root_host = ""
    root_token = ""
    root_prefix = ""
    bytes_transferred = 0
    transfer_metadata: dict[str, object] = {}
    first_transfer_error: TransferError | None = None

    if result_url:
        try:
            root_host, root_token, _object_path, root_prefix = (
                _parse_sandbox_result_url(result_url)
            )
            objects = _iterate_objects(
                root_host, root_token, root_prefix, session=session
            )
            _record_fallback_attempt(fallback_attempts, "sandbox_iterate", ok=True)
        except Exception:
            _record_fallback_attempt(
                fallback_attempts, "sandbox_iterate", ok=False, error="failed"
            )
            objects = []

    log_downloaded, log_bytes = _download_sandbox_log(
        payload=payload,
        staging=staging,
        root_host=root_host,
        root_token=root_token,
        objects=objects,
        session=session,
    )
    bytes_transferred += log_bytes

    zip_key = choose_sandbox_zip_object(job_id, objects)
    if zip_key and root_host and root_token:
        try:
            zip_path = staging / Path(zip_key).name
            summary = _download_object(
                root_host, root_token, zip_key, zip_path, session=session
            )
            bytes_transferred += summary.bytes_done
            files = _extract_result_zip(zip_path, staging)
            transfer_metadata.update(_transfer_metadata_from_summary(summary))
            _record_fallback_attempt(fallback_attempts, "sandbox_zip_object", ok=True)
            return (
                _merge_log_file(files, log_downloaded),
                read_log(staging),
                bytes_transferred,
                transfer_metadata,
            )
        except Exception as exc:
            _record_fallback_attempt(
                fallback_attempts, "sandbox_zip_object", ok=False, error=exc
            )
            if isinstance(exc, TransferError) and first_transfer_error is None:
                first_transfer_error = exc

    if result_url:
        try:
            zip_path = staging / "out.zip"
            summary = download_file(result_url, zip_path, session=session)
            bytes_transferred += summary.bytes_done
            files = _extract_result_zip(zip_path, staging)
            transfer_metadata.update(_transfer_metadata_from_summary(summary))
            _record_fallback_attempt(fallback_attempts, "sandbox_result_url", ok=True)
            return (
                _merge_log_file(files, log_downloaded),
                read_log(staging),
                bytes_transferred,
                transfer_metadata,
            )
        except Exception as exc:
            _record_fallback_attempt(
                fallback_attempts, "sandbox_result_url", ok=False, error=exc
            )
            if isinstance(exc, TransferError) and first_transfer_error is None:
                first_transfer_error = exc

    if objects and root_host and root_token:
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
            summary = _download_object(
                root_host,
                root_token,
                object_path,
                staging / relative_path,
                session=session,
            )
            bytes_transferred += summary.bytes_done
            downloaded.append(relative_path)
            count += 1
        downloaded = _merge_log_file(downloaded, log_downloaded)
        if downloaded:
            _record_fallback_attempt(
                fallback_attempts, "sandbox_individual_objects", ok=True
            )
            return downloaded, read_log(staging), bytes_transferred, transfer_metadata

    if log_downloaded:
        return ["log"], read_log(staging), bytes_transferred, transfer_metadata
    if first_transfer_error:
        first_transfer_error.diagnostics = {"fallback_attempts": fallback_attempts}
        raise first_transfer_error
    if result_url:
        return (
            [],
            "(sandbox resultUrl download failed)",
            bytes_transferred,
            transfer_metadata,
        )
    return [], "(no resultUrl in job detail)", bytes_transferred, transfer_metadata


def _download_standard_results(
    *,
    detail_data: dict[str, Any],
    staging: Path,
    session=None,
) -> tuple[list[str], str, int, dict[str, object]]:
    result_url = str(detail_data.get("resultUrl") or detail_data.get("result") or "")
    if not result_url:
        out_files = (detail_data.get("jobFiles") or {}).get("outFiles") or []
        if out_files and isinstance(out_files[0], dict):
            result_url = str(out_files[0].get("url") or "")
    if not result_url:
        return [], "(no resultUrl in job detail)", 0, {}
    zip_path = staging / "out.zip"
    summary = download_file(result_url, zip_path, session=session)
    files = _extract_result_zip(zip_path, staging)
    return (
        files,
        read_log(staging),
        summary.bytes_done,
        _transfer_metadata_from_summary(summary),
    )


def run_download_results_payload(
    payload: dict[str, Any],
    *,
    session=None,
) -> dict[str, Any]:
    started = time.monotonic()
    result_dir = Path(_required_str(payload, "result_dir"))
    transfer_id = str(payload.get("transfer_id") or "download").strip() or "download"
    staging = result_dir.with_name(result_dir.name + f".tmp.{uuid4().hex}")
    detail_data = payload.get("detail_data") or {}
    if not isinstance(detail_data, dict):
        raise ValueError("detail_data must be a JSON object")
    fallback_attempts: list[dict[str, object]] = []
    try:
        staging.mkdir(parents=True, exist_ok=False)
        if bool(payload.get("sandbox")):
            files, log_tail, bytes_transferred, transfer_metadata = (
                _download_sandbox_results(
                    payload=payload,
                    staging=staging,
                    fallback_attempts=fallback_attempts,
                    session=session,
                )
            )
        else:
            files, log_tail, bytes_transferred, transfer_metadata = (
                _download_standard_results(
                    detail_data=detail_data,
                    staging=staging,
                    session=session,
                )
            )
        publish_result_dir(staging, result_dir)
    except TransferError as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if fallback_attempts and not exc.diagnostics:
            exc.diagnostics = {"fallback_attempts": fallback_attempts}
        raise
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    elapsed = max(time.monotonic() - started, 0.001)
    transfer_metadata = {
        "transfer_id": transfer_id,
        "download_sha256": transfer_metadata.get("download_sha256"),
        "server_hash_checked": transfer_metadata.get("server_hash_checked", False),
        "server_hash_value": transfer_metadata.get("server_hash_value"),
        "hash_validation_skipped": transfer_metadata.get(
            "hash_validation_skipped", True
        ),
        "fallback_attempts": fallback_attempts,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "ok": True,
        "result_dir": str(result_dir),
        "files": files,
        "log_tail": log_tail,
        "bytes_transferred": bytes_transferred,
        "transfer_rate_mbps": round(bytes_transferred * 8 / elapsed / 1_000_000, 3),
        "metadata": {"transfer": transfer_metadata},
    }
