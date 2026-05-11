from __future__ import annotations

import base64
import hashlib
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .manifest import ManifestStore
from .progress import NoopProgressSink, ProgressSink, TransferProgressEvent
from .security import token_fingerprint

PART_CHUNK_SIZE = 1024 * 1024
DEFAULT_PART_SIZE = 16 * 1024 * 1024
DEFAULT_CONCURRENCY = 2
_STOREHOST_PART_LIMITER = threading.BoundedSemaphore(4)


def _read_part(file_path: Path, offset: int, size: int) -> bytes:
    with open(file_path, "rb") as fh:
        fh.seek(offset)
        return fh.read(size)


def _hash_part(path: Path, offset: int, size: int) -> tuple[str, str]:
    digest = hashlib.md5(usedforsecurity=False)
    remaining = size
    with open(path, "rb") as fh:
        fh.seek(offset)
        while remaining > 0:
            chunk = fh.read(min(PART_CHUNK_SIZE, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return base64.b64encode(digest.digest()).decode(), digest.hexdigest()


def _stream_part(path: Path, offset: int, size: int):
    remaining = size
    with open(path, "rb") as fh:
        fh.seek(offset)
        while remaining > 0:
            chunk = fh.read(min(PART_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _part_specs(file_size: int, part_size: int) -> list[dict[str, int]]:
    count = max(math.ceil(file_size / part_size), 1)
    specs: list[dict[str, int]] = []
    for index in range(count):
        offset = index * part_size
        size = min(part_size, max(file_size - offset, 0))
        specs.append({"number": index + 1, "offset": offset, "size": size})
    return specs


def _part_entries(path: Path, parts: list[dict[str, int]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for part in parts:
        md5_base64, md5_hex = _hash_part(path, part["offset"], part["size"])
        entries.append(
            {
                "number": part["number"],
                "offset": part["offset"],
                "size": part["size"],
                "md5_base64": md5_base64,
                "md5_hex": md5_hex,
            }
        )
    return entries


def _completed_from_manifest(
    *,
    manifest_store: ManifestStore,
    transfer_id: str,
    object_key: str,
    file_size: int,
    file_mtime_ns: int,
    part_size: int,
    token: str,
) -> tuple[str | None, dict[int, dict[str, Any]]]:
    try:
        manifest = manifest_store.read(transfer_id)
    except FileNotFoundError:
        return None, {}
    if manifest.get("schema_version") != "v2":
        return None, {}
    if manifest.get("object_key") != object_key:
        return None, {}
    if int(manifest.get("file_size") or -1) != file_size:
        return None, {}
    if int(manifest.get("file_mtime_ns") or -1) != file_mtime_ns:
        return None, {}
    if int(manifest.get("part_size") or -1) != part_size:
        return None, {}
    if str(manifest.get("token_fingerprint") or "") != token_fingerprint(
        token, transfer_id
    ):
        return None, {}
    initial_key = str(manifest.get("initial_key") or "")
    if not initial_key:
        return None, {}
    completed: dict[int, dict[str, Any]] = {}
    for part in manifest.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if part.get("status") != "completed":
            continue
        part_string = str(part.get("part_string") or "")
        if not part_string:
            continue
        if not part.get("md5_base64") or not part.get("md5_hex"):
            continue
        completed[int(part["number"])] = dict(part)
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
    parts: list[dict[str, Any]],
    completed: dict[int, dict[str, Any]],
) -> None:
    manifest_store.write(
        transfer_id,
        {
            "schema_version": "v2",
            "transfer_id": transfer_id,
            "object_key": object_key,
            "initial_key": initial_key,
            "token_fingerprint": token_fingerprint(token, transfer_id),
            "part_size": part_size,
            "file_size": file_size,
            "file_mtime_ns": file_mtime_ns,
            "parts": [
                {
                    "number": part["number"],
                    "offset": part["offset"],
                    "size": part["size"],
                    "md5_base64": part["md5_base64"],
                    "md5_hex": part["md5_hex"],
                    "part_string": completed.get(part["number"], {}).get("part_string"),
                    "server_hash_checked": completed.get(part["number"], {}).get(
                        "server_hash_checked", False
                    ),
                    "server_hash_value": completed.get(part["number"], {}).get(
                        "server_hash_value"
                    ),
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
    part_size: int = DEFAULT_PART_SIZE,
    concurrency: int = DEFAULT_CONCURRENCY,
    part_retries: int = 3,
    progress_sink: ProgressSink | None = None,
) -> dict[str, Any]:
    path = Path(file_path)
    stat_result = path.stat()
    file_size = stat_result.st_size
    file_mtime_ns = stat_result.st_mtime_ns
    parts = _part_specs(file_size, part_size)
    part_entries = _part_entries(path, parts)
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
    with manifest_store.lock(transfer_id):
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
    with manifest_store.lock(transfer_id):
        _write_manifest(
            manifest_store=manifest_store,
            transfer_id=transfer_id,
            object_key=object_key,
            initial_key=initial_key,
            token=token,
            part_size=part_size,
            file_size=file_size,
            file_mtime_ns=file_mtime_ns,
            parts=part_entries,
            completed=completed,
        )

    def upload_one(spec: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        last_error: BaseException | None = None
        for attempt in range(1, part_retries + 1):
            try:
                with _STOREHOST_PART_LIMITER:
                    result = client.upload_part(
                        object_key=object_key,
                        initial_key=initial_key,
                        number=spec["number"],
                        part_size=spec["size"],
                        data=lambda: _stream_part(path, spec["offset"], spec["size"]),
                        md5_base64=spec["md5_base64"],
                        md5_hex=spec["md5_hex"],
                    )
                completed_part = dict(spec)
                completed_part.update(
                    {
                        "part_string": result.part_string,
                        "server_hash_checked": result.server_hash_checked,
                        "server_hash_value": result.server_hash_value,
                        "status": "completed",
                    }
                )
                return spec["number"], completed_part
            except Exception as exc:
                last_error = exc
                if attempt < part_retries:
                    time.sleep(min(2 ** (attempt - 1), 30) + random.uniform(0, 1.0))
        raise RuntimeError(f"part {spec['number']} failed") from last_error

    pending_parts = [part for part in part_entries if part["number"] not in completed]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(upload_one, spec) for spec in pending_parts]
        for future in as_completed(futures):
            number, completed_part = future.result()
            with manifest_store.lock(transfer_id):
                completed[number] = completed_part
                _write_manifest(
                    manifest_store=manifest_store,
                    transfer_id=transfer_id,
                    object_key=object_key,
                    initial_key=initial_key,
                    token=token,
                    part_size=part_size,
                    file_size=file_size,
                    file_mtime_ns=file_mtime_ns,
                    parts=part_entries,
                    completed=completed,
                )
            bytes_done = sum(
                part["size"] for part in part_entries if part["number"] in completed
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
                    parts_total=len(part_entries),
                )
            )
    part_strings = [completed[number]["part_string"] for number in sorted(completed)]
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
            parts_done=len(part_entries),
            parts_total=len(part_entries),
        )
    )
    return {
        "ok": True,
        "object_key": object_key,
        "parts_total": len(part_entries),
        "bytes_total": file_size,
        "resume_used": resume_used,
    }
