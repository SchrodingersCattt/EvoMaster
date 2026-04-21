from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

SCHEMA_VERSION = "v1"
_SANDBOX_OBJECT_DOWNLOAD_LIMIT = 128
_SECRET_RE = re.compile(r"(?i)\b(token|access_key|accessKey|authorization)=([^&\s]+)")
_requests_module = None


class HelperFailure(RuntimeError):
    """Structured failure raised by the remote transfer helper."""


def redact_secrets(text: object) -> str:
    raw = str(text)
    raw = re.sub(r"(?i)(Bearer\s+)[^&\s]+", r"\1<redacted>", raw)
    return _SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", raw)


def load_payload(payload_file: str | Path) -> dict[str, Any]:
    path = Path(payload_file)
    try:
        raw = path.read_text(encoding="utf-8")
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HelperFailure(f"invalid payload JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HelperFailure("payload must be a JSON object")
    validate_schema(payload)
    return payload


def validate_schema(payload: dict[str, Any]) -> None:
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise HelperFailure(
            f"schema_version mismatch: expected {SCHEMA_VERSION}, got {version!r}"
        )


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise HelperFailure(f"missing required payload field: {key}")
    return value


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def _ensure_free_space(path: Path, required_bytes: int) -> None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return
    if usage.free < required_bytes:
        raise HelperFailure(
            "disk full or insufficient remote temp space: "
            f"required={required_bytes} free={usage.free}"
        )


def zip_directory(input_dir: str | Path, archive: str | Path) -> list[str]:
    source = Path(input_dir)
    if not source.is_dir():
        raise HelperFailure(f"input_dir is not a directory: {source}")
    archive_path = Path(archive)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_free_space(archive_path.parent, max(_directory_size(source), 1))

    names: list[str] = []
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source.rglob("*")):
            if file_path.is_file():
                relative = file_path.relative_to(source).as_posix()
                zf.write(file_path, relative)
                names.append(relative)
    return names


def _safe_extract_member(
    zf: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    extract_dir: Path,
) -> str | None:
    name = member.filename
    if not name or name.endswith("/"):
        return None
    target = extract_dir / name
    resolved_target = target.resolve()
    resolved_root = extract_dir.resolve()
    if resolved_root not in (resolved_target, *resolved_target.parents):
        raise HelperFailure(f"unsafe zip member path: {name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member, "r") as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return name


def extract_zip(archive: str | Path, extract_dir: str | Path) -> list[str]:
    archive_path = Path(archive)
    target_dir = Path(extract_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            files = [
                name
                for member in zf.infolist()
                if (name := _safe_extract_member(zf, member, target_dir)) is not None
            ]
    except zipfile.BadZipFile as exc:
        raise HelperFailure(f"bad zip: {archive_path.name}") from exc
    return files


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
    except FileExistsError as exc:
        raise HelperFailure(
            f"concurrent result directory download: {result_path}"
        ) from exc

    try:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        if result_path.exists():
            result_path.rename(backup)
            try:
                staging_path.rename(result_path)
            except Exception:
                if backup.exists() and not result_path.exists():
                    backup.rename(result_path)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            staging_path.rename(result_path)
    except OSError as exc:
        if exc.errno == 28:
            raise HelperFailure(
                f"disk full while publishing result directory: {exc}"
            ) from exc
        raise HelperFailure(f"result directory publish failure: {exc}") from exc
    finally:
        if lock_acquired:
            try:
                lockdir.rmdir()
            except Exception:
                pass


def load_tiefblue_client():
    try:
        from bohrium.resources.tiefblue import Tiefblue
    except ImportError as exc:
        raise HelperFailure(
            "missing remote bohrium-sdk: cannot import Tiefblue"
        ) from exc
    return Tiefblue


def load_requests():
    global _requests_module
    if _requests_module is not None:
        return _requests_module
    try:
        import requests
    except ImportError as exc:
        raise HelperFailure("missing remote dependency: requests") from exc
    _requests_module = requests
    return _requests_module


def _build_oss_key(store_path: str, object_name: str) -> str:
    prefix = store_path.strip()
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return f"{prefix}{object_name.lstrip('/')}"


def run_upload_submit(payload: dict[str, Any]) -> dict[str, Any]:
    validate_schema(payload)
    started = time.monotonic()
    input_dir = Path(_required_str(payload, "input_dir"))
    store_host = _required_str(payload, "store_host").rstrip("/")
    store_path = _required_str(payload, "store_path")
    token = _required_str(payload, "token")
    object_name = str(payload.get("object_name") or "input.zip").strip()
    oss_key = _build_oss_key(store_path, object_name)
    client_cls = load_tiefblue_client()

    with tempfile.TemporaryDirectory(prefix="matmaster_bohrium_upload_") as tmp_dir:
        archive = Path(tmp_dir) / object_name
        zip_directory(input_dir, archive)
        size = archive.stat().st_size
        client = client_cls(base_url=store_host)
        response = client.upload_From_file_multi_part(
            object_key=oss_key,
            file_path=str(archive),
            token=token,
            progress_bar=False,
        )
        if response is not None and getattr(response, "status_code", 0) >= 400:
            raise HelperFailure(
                f"upload token failure: {getattr(response, 'text', '')}"
            )

    elapsed = max(time.monotonic() - started, 0.001)
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "oss_key": oss_key,
        "bytes_transferred": size,
        "transfer_rate_mbps": round(size * 8 / elapsed / 1_000_000, 3),
    }


def download_to_file(url: str, dest: Path, *, timeout: int = 300) -> int:
    requests = load_requests()
    response = requests.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with open(dest, "wb") as fh:
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                fh.write(chunk)
                total += len(chunk)
    except OSError as exc:
        if exc.errno == 28:
            raise HelperFailure(f"disk full while writing download: {exc}") from exc
        raise
    return total


def _parse_sandbox_result_url(result_url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(result_url)
    host = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    token = parse_qs(parsed.query).get("token", [""])[0].strip()
    object_path = unquote(parsed.path.removeprefix("/api/download/")).strip("/")
    if not host or not token or not object_path:
        raise HelperFailure(f"invalid sandbox resultUrl: {redact_secrets(result_url)}")
    prefix = object_path.rsplit("/", 1)[0] + "/" if "/" in object_path else ""
    return host, token, object_path, prefix


def _sandbox_iterate_objects(
    host: str,
    token: str,
    prefix: str,
) -> list[dict[str, Any]]:
    requests = load_requests()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    objects: list[dict[str, Any]] = []
    next_token = ""
    while True:
        payload: dict[str, Any] = {"prefix": prefix}
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
            raise HelperFailure(f"sandbox iterate failed: {body}")
        data = body.get("data") or {}
        objects.extend(data.get("objects") or [])
        if not data.get("hasNext"):
            break
        next_token = str(data.get("nextToken") or "").strip()
        if not next_token:
            break
    return objects


def _sandbox_download_object(
    host: str,
    token: str,
    object_path: str,
    dest_path: Path,
) -> int:
    encoded_path = quote(object_path, safe="/")
    url = (
        f"{host.rstrip('/')}/api/download/{encoded_path}?token={token}"
        "&Response-Content-Type=application/octet-stream"
    )
    return download_to_file(
        url,
        dest_path,
    )


def _sandbox_choose_zip_object(
    job_id: int | str,
    objects: list[dict[str, Any]],
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


def _sandbox_relative_object_path(object_path: str, root_prefix: str) -> str:
    path = object_path.strip()
    if root_prefix and path.startswith(root_prefix):
        path = path[len(root_prefix) :]
    return path.lstrip("/")


def _download_sandbox_log(
    *,
    payload: dict[str, Any],
    staging: Path,
    root_host: str,
    root_token: str,
    objects: list[dict[str, Any]],
) -> tuple[bool, int]:
    log_file = payload.get("sandbox_log_file")
    if isinstance(log_file, dict):
        host = str(log_file.get("host") or "").strip()
        path = str(log_file.get("path") or "").strip()
        token = str(log_file.get("token") or "").strip()
        if host and path and token:
            try:
                return (
                    True,
                    _sandbox_download_object(host, token, path, staging / "log"),
                )
            except Exception:
                pass

    if root_host and root_token:
        for obj in objects:
            object_path = str(obj.get("path") or obj.get("key") or "").strip()
            if object_path and Path(object_path).name == "log":
                return (
                    True,
                    _sandbox_download_object(
                        root_host,
                        root_token,
                        object_path,
                        staging / "log",
                    ),
                )
    return False, 0


def _merge_log_file(files: list[str], log_downloaded: bool) -> list[str]:
    if not log_downloaded or "log" in files:
        return files
    return ["log", *files]


def _download_sandbox_results(
    *,
    payload: dict[str, Any],
    staging: Path,
) -> tuple[list[str], str, int]:
    job_id = _required_str(payload, "job_id")
    detail_data = payload.get("detail_data") or {}
    if not isinstance(detail_data, dict):
        raise HelperFailure("detail_data must be a JSON object")
    result_url = str(detail_data.get("resultUrl") or detail_data.get("result") or "")
    objects: list[dict[str, Any]] = []
    root_host = ""
    root_token = ""
    root_prefix = ""
    bytes_transferred = 0

    if result_url:
        try:
            root_host, root_token, _object_path, root_prefix = (
                _parse_sandbox_result_url(result_url)
            )
            objects = _sandbox_iterate_objects(root_host, root_token, root_prefix)
        except Exception:
            objects = []

    log_downloaded, log_bytes = _download_sandbox_log(
        payload=payload,
        staging=staging,
        root_host=root_host,
        root_token=root_token,
        objects=objects,
    )
    bytes_transferred += log_bytes

    zip_key = _sandbox_choose_zip_object(job_id, objects)
    if zip_key and root_host and root_token:
        try:
            zip_path = staging / Path(zip_key).name
            bytes_transferred += _sandbox_download_object(
                root_host,
                root_token,
                zip_key,
                zip_path,
            )
            files = extract_zip(zip_path, staging)
            return (
                _merge_log_file(files, log_downloaded),
                read_log(staging),
                bytes_transferred,
            )
        except Exception:
            pass

    if result_url:
        try:
            zip_path = staging / "out.zip"
            bytes_transferred += download_to_file(result_url, zip_path)
            files = extract_zip(zip_path, staging)
            return (
                _merge_log_file(files, log_downloaded),
                read_log(staging),
                bytes_transferred,
            )
        except Exception:
            pass

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
            bytes_transferred += _sandbox_download_object(
                root_host,
                root_token,
                object_path,
                staging / relative_path,
            )
            downloaded.append(relative_path)
            count += 1
        downloaded = _merge_log_file(downloaded, log_downloaded)
        if downloaded:
            return downloaded, read_log(staging), bytes_transferred

    if log_downloaded:
        return ["log"], read_log(staging), bytes_transferred
    if result_url:
        return [], "(sandbox resultUrl download failed)", bytes_transferred
    return [], "(no resultUrl in job detail)", bytes_transferred


def _download_standard_results(
    *,
    detail_data: dict[str, Any],
    staging: Path,
) -> tuple[list[str], str, int]:
    result_url = str(detail_data.get("resultUrl") or detail_data.get("result") or "")
    if not result_url:
        out_files = (detail_data.get("jobFiles") or {}).get("outFiles") or []
        if out_files and isinstance(out_files[0], dict):
            result_url = str(out_files[0].get("url") or "")
    if not result_url:
        return [], "(no resultUrl in job detail)", 0
    zip_path = staging / "out.zip"
    bytes_transferred = download_to_file(result_url, zip_path)
    files = extract_zip(zip_path, staging)
    return files, read_log(staging), bytes_transferred


def run_download_results(payload: dict[str, Any]) -> dict[str, Any]:
    validate_schema(payload)
    started = time.monotonic()
    result_dir = Path(_required_str(payload, "result_dir"))
    staging = result_dir.with_name(result_dir.name + f".tmp.{uuid4().hex}")
    detail_data = payload.get("detail_data") or {}
    if not isinstance(detail_data, dict):
        raise HelperFailure("detail_data must be a JSON object")
    load_requests()

    try:
        staging.mkdir(parents=True, exist_ok=False)
        _ensure_free_space(staging.parent, 1)
        if bool(payload.get("sandbox")):
            files, log_tail, bytes_transferred = _download_sandbox_results(
                payload=payload,
                staging=staging,
            )
        else:
            files, log_tail, bytes_transferred = _download_standard_results(
                detail_data=detail_data,
                staging=staging,
            )
        publish_result_dir(staging, result_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    elapsed = max(time.monotonic() - started, 0.001)
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "result_dir": str(result_dir),
        "files": files,
        "log_tail": log_tail,
        "bytes_transferred": bytes_transferred,
        "transfer_rate_mbps": round(bytes_transferred * 8 / elapsed / 1_000_000, 3),
    }


def _error_result(exc: BaseException) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "error": redact_secrets(exc),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("subcommand", choices=("upload-submit", "download-results"))
    parser.add_argument("--payload-file", required=True)
    args = parser.parse_args(argv)

    try:
        payload = load_payload(args.payload_file)
        if args.subcommand == "upload-submit":
            result = run_upload_submit(payload)
        else:
            result = run_download_results(payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps(_error_result(exc), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
