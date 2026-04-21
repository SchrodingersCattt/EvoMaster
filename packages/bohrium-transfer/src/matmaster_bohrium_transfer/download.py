from __future__ import annotations

import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .errors import ExtractError
from .progress import NoopProgressSink, ProgressSink, TransferProgressEvent


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
    return DownloadSummary(
        path=dest,
        bytes_total=bytes_total,
        bytes_done=bytes_done,
        resume_supported=False,
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
    return DownloadSummary(
        path=dest,
        bytes_total=bytes_total,
        bytes_done=bytes_done,
        resume_supported=True,
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
    except Exception:
        capability = RangeCapability(False, None, "head_failed")
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
        )
    return _download_stream(
        http,
        url,
        target,
        timeout=timeout,
        progress_sink=sink,
        transfer_id=transfer_id,
    )


def choose_sandbox_zip_object(job_id: int | str, objects: list[dict[str, Any]]) -> str | None:
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
                raise ExtractError("extract", f"unsafe zip member path: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            files.append(member.filename)
    return files
