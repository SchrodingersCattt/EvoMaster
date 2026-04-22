from __future__ import annotations

import math
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .manifest import ManifestStore
from .progress import NoopProgressSink, ProgressSink, TransferProgressEvent


def _read_part(file_path: Path, offset: int, size: int) -> bytes:
    with open(file_path, "rb") as fh:
        fh.seek(offset)
        return fh.read(size)


def _part_specs(file_size: int, part_size: int) -> list[dict[str, int]]:
    count = max(math.ceil(file_size / part_size), 1)
    specs: list[dict[str, int]] = []
    for index in range(count):
        offset = index * part_size
        size = min(part_size, max(file_size - offset, 0))
        specs.append({"number": index + 1, "offset": offset, "size": size})
    return specs


def _completed_from_manifest(
    *,
    manifest_store: ManifestStore,
    transfer_id: str,
    object_key: str,
    file_size: int,
    file_mtime_ns: int,
    part_size: int,
    token: str,
) -> tuple[str | None, dict[int, str]]:
    try:
        manifest = manifest_store.read(transfer_id)
    except FileNotFoundError:
        return None, {}
    if manifest.get("object_key") != object_key:
        return None, {}
    if int(manifest.get("file_size") or -1) != file_size:
        return None, {}
    if int(manifest.get("file_mtime_ns") or -1) != file_mtime_ns:
        return None, {}
    if int(manifest.get("part_size") or -1) != part_size:
        return None, {}
    if str(manifest.get("token") or "") != token:
        return None, {}
    initial_key = str(manifest.get("initial_key") or "")
    if not initial_key:
        return None, {}
    completed: dict[int, str] = {}
    for part in manifest.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if part.get("status") != "completed":
            continue
        part_string = str(part.get("part_string") or "")
        if not part_string:
            continue
        completed[int(part["number"])] = part_string
    return initial_key, completed


def _write_manifest(
    *,
    manifest_store: ManifestStore,
    transfer_id: str,
    object_key: str,
    initial_key: str,
    token: str,
    part_size: int,
    file_size: int,
    file_mtime_ns: int,
    parts: list[dict[str, int]],
    completed: dict[int, str],
) -> None:
    manifest_store.write(
        transfer_id,
        {
            "schema_version": "v1",
            "transfer_id": transfer_id,
            "object_key": object_key,
            "initial_key": initial_key,
            "token": token,
            "part_size": part_size,
            "file_size": file_size,
            "file_mtime_ns": file_mtime_ns,
            "parts": [
                {
                    "number": part["number"],
                    "offset": part["offset"],
                    "size": part["size"],
                    "part_string": completed.get(part["number"]),
                    "status": (
                        "completed" if part["number"] in completed else "pending"
                    ),
                }
                for part in parts
            ],
        },
    )


def upload_file_multipart(
    *,
    client,
    file_path: str | Path,
    object_key: str,
    manifest_store: ManifestStore,
    transfer_id: str,
    part_size: int = 64 * 1024 * 1024,
    concurrency: int = 4,
    part_retries: int = 3,
    progress_sink: ProgressSink | None = None,
) -> dict[str, Any]:
    path = Path(file_path)
    stat_result = path.stat()
    file_size = stat_result.st_size
    file_mtime_ns = stat_result.st_mtime_ns
    parts = _part_specs(file_size, part_size)
    sink = progress_sink or NoopProgressSink()
    sink.emit(
        TransferProgressEvent(
            event_type="upload_started",
            transfer_id=transfer_id,
            phase="upload",
            direction="upload",
            bytes_done=0,
            bytes_total=file_size,
            parts_done=0,
            parts_total=len(parts),
        )
    )
    token = str(getattr(client, "token", ""))
    manifest_store.gc()
    initial_key, completed = _completed_from_manifest(
        manifest_store=manifest_store,
        transfer_id=transfer_id,
        object_key=object_key,
        file_size=file_size,
        file_mtime_ns=file_mtime_ns,
        part_size=part_size,
        token=token,
    )
    resume_used = bool(initial_key and completed)
    if not initial_key:
        initial_key = client.init_multipart(object_key)
    _write_manifest(
        manifest_store=manifest_store,
        transfer_id=transfer_id,
        object_key=object_key,
        initial_key=initial_key,
        token=token,
        part_size=part_size,
        file_size=file_size,
        file_mtime_ns=file_mtime_ns,
        parts=parts,
        completed=completed,
    )

    def upload_one(spec: dict[str, int]) -> tuple[int, str]:
        last_error: BaseException | None = None
        for attempt in range(1, part_retries + 1):
            try:
                data = _read_part(path, spec["offset"], spec["size"])
                return spec["number"], client.upload_part(
                    object_key=object_key,
                    initial_key=initial_key,
                    number=spec["number"],
                    part_size=spec["size"],
                    data=data,
                )
            except Exception as exc:
                last_error = exc
                if attempt < part_retries:
                    time.sleep(min(2 ** (attempt - 1), 30) + random.uniform(0, 1.0))
        raise RuntimeError(f"part {spec['number']} failed") from last_error

    pending_parts = [part for part in parts if part["number"] not in completed]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(upload_one, spec) for spec in pending_parts]
        for future in as_completed(futures):
            number, part_string = future.result()
            completed[number] = part_string
            bytes_done = sum(
                part["size"] for part in parts if part["number"] in completed
            )
            _write_manifest(
                manifest_store=manifest_store,
                transfer_id=transfer_id,
                object_key=object_key,
                initial_key=initial_key,
                token=token,
                part_size=part_size,
                file_size=file_size,
                file_mtime_ns=file_mtime_ns,
                parts=parts,
                completed=completed,
            )
            sink.emit(
                TransferProgressEvent(
                    event_type="upload_part_completed",
                    transfer_id=transfer_id,
                    phase="upload",
                    direction="upload",
                    bytes_done=bytes_done,
                    bytes_total=file_size,
                    parts_done=len(completed),
                    parts_total=len(parts),
                )
            )
    part_strings = [completed[number] for number in sorted(completed)]
    client.complete_multipart(
        object_key=object_key,
        initial_key=initial_key,
        part_strings=part_strings,
    )
    sink.emit(
        TransferProgressEvent(
            event_type="upload_completed",
            transfer_id=transfer_id,
            phase="upload",
            direction="upload",
            bytes_done=file_size,
            bytes_total=file_size,
            parts_done=len(parts),
            parts_total=len(parts),
        )
    )
    return {
        "ok": True,
        "object_key": object_key,
        "parts_total": len(parts),
        "bytes_total": file_size,
        "resume_used": resume_used,
    }
