"""Bohrium job service helpers in the new runtime namespace."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from matmaster.bohrium.endpoints import get_bohrium_base_url
from matmaster.bohrium.runtime import get_runtime
from matmaster.mcp.calculation.config_env import get_current_env

logger = logging.getLogger(__name__)


def _openapi_host() -> str:
    return get_bohrium_base_url()


def _tiefblue_nas_host() -> str:
    env = get_current_env()
    if env in ("test", "uat"):
        return "https://tiefblue-nas-acs-bj.test.bohrium.com"
    return "https://tiefblue-nas-acs-bj.bohrium.com"


def _get_access_key(access_key: str | None = None, session: Any = None) -> str:
    if access_key:
        return access_key.strip()
    runtime = get_runtime(session) if session is not None else None
    if runtime is not None and runtime.credentials().access_key:
        return runtime.credentials().access_key
    env_ak = (os.getenv("BOHRIUM_ACCESS_KEY") or "").strip()
    if env_ak:
        return env_ak
    raise ValueError(
        "Bohrium credentials unavailable for current run. "
        "Provide via session or BOHRIUM_ACCESS_KEY env var."
    )


_STATUS_MAP: dict[int, str] = {
    -1: "Failed",
    -2: "Deleted",
    0: "Pending",
    1: "Running",
    2: "Finished",
    3: "Scheduling",
    4: "Stopping",
    5: "Stopped",
    6: "Terminating",
    7: "Killing",
    8: "Uploading",
    9: "Wait",
}

RUNNING_STATUSES = frozenset(
    {
        "Running",
        "Pending",
        "Scheduling",
        "Wait",
        "Uploading",
    }
)


def _mapping_status(code: int) -> str:
    return _STATUS_MAP.get(code, "Unknown")


def _extract_bohr_job_id(
    job_id: str,
    bohr_job_id: str | None = None,
) -> str | None:
    if bohr_job_id:
        return bohr_job_id.strip()

    if not job_id:
        return None

    parts = job_id.rsplit("/", 1)
    candidate = (parts[1] if len(parts) == 2 else job_id).strip()

    if candidate.isdigit():
        return candidate

    clean = candidate.replace("-", "")
    if re.fullmatch(r"[0-9a-fA-F]{32}", clean):
        return clean

    if re.fullmatch(r"[0-9a-fA-F]{33,}", clean):
        return None

    return candidate


_UA = "MatMaster-JobService/1.0"


def _get_json(
    url: str, headers: dict[str, str] | None = None, timeout: int = 30
) -> dict:
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(
    url: str,
    body: dict,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict:
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    req = Request(url, data=data, headers=hdrs, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download_binary(
    url: str, dest: Path, headers: dict[str, str] | None = None, timeout: int = 120
) -> Path:
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(req, timeout=timeout) as resp:
        dest.write_bytes(resp.read())
    return dest


def _extract_openapi_error(detail: dict[str, Any]) -> str | None:
    if not isinstance(detail, dict):
        return "Invalid OpenAPI response: expected JSON object."

    code = detail.get("code")
    err_obj = detail.get("error")
    err_msg = ""
    if isinstance(err_obj, dict):
        err_msg = str(err_obj.get("msg") or err_obj.get("title") or "").strip()
    elif err_obj:
        err_msg = str(err_obj).strip()

    if isinstance(code, int) and code != 0:
        return f"OpenAPI code={code}: {err_msg}" if err_msg else f"OpenAPI code={code}"

    if err_msg:
        return err_msg

    return None


def _http_error_message(exc: HTTPError) -> str:
    body = ""
    try:
        raw = exc.read()
        if raw:
            body = raw.decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""

    base = f"HTTP {exc.code} {exc.reason}"
    if body:
        return f"{base}: {body}"
    return base


def get_job_detail_raw(
    bohr_job_id: str,
    *,
    access_key: str | None = None,
) -> dict[str, Any]:
    ak = _get_access_key(access_key)
    api = f"{_openapi_host()}/openapi/v1/sandbox/job/{bohr_job_id}"
    logger.debug("get_job_detail_raw: GET %s", api)
    return _get_json(api, headers={"accessKey": ak})


def get_file_token(
    file_path: str,
    bohr_job_id: str,
    *,
    access_key: str | None = None,
) -> tuple[str, str, str]:
    ak = _get_access_key(access_key)
    api = f"{_openapi_host()}/openapi/v1/sandbox/job/file/token?accessKey={ak}"
    body = {"filePath": file_path, "jobId": bohr_job_id}
    result = _post_json(api, body)
    data = result.get("data", {})
    return data.get("host", ""), data.get("path", ""), data.get("token", "")


def iterate_job_files(
    bohr_job_id: str,
    *,
    prefix: str | None = None,
    access_key: str | None = None,
) -> list[dict[str, Any]]:
    host, path, token = get_file_token("", bohr_job_id, access_key=access_key)
    if not host or not token:
        logger.warning("iterate_job_files: empty token for job %s", bohr_job_id)
        return []

    if prefix is None:
        prefix = path.replace("results.txt", "") if path else ""
    prefix = prefix.replace("\\", "/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    nas_host = _tiefblue_nas_host()
    result = _post_json(
        f"{nas_host}/api/iterate",
        body={"prefix": prefix},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    return result.get("data", {}).get("objects", [])


def download_job_file(
    file_path: str,
    bohr_job_id: str,
    dest: Path,
    *,
    access_key: str | None = None,
) -> Path:
    normalized = str(file_path or "").replace("\\", "/").strip()
    if normalized:
        try:
            _, root_path, _ = get_file_token("", bohr_job_id, access_key=access_key)
            root_prefix = str(root_path or "").replace("\\", "/")
            if root_prefix and not root_prefix.endswith("/"):
                root_prefix += "/"
            if root_prefix and normalized.startswith(root_prefix):
                normalized = normalized[len(root_prefix) :].lstrip("/")
        except Exception:
            pass

    host, remote_path, token = get_file_token(
        normalized, bohr_job_id, access_key=access_key
    )
    if not host or not remote_path or not token:
        raise RuntimeError(
            f"Cannot download '{normalized or file_path}' from job {bohr_job_id}: "
            "incomplete file-token response (host/path/token empty)."
        )
    url = f"{host}/api/download/{remote_path}?token={token}"
    return _download_binary(url, dest)


def download_job_directory(
    dir_path: str,
    bohr_job_id: str,
    dest_dir: Path,
    *,
    access_key: str | None = None,
    max_bytes_per_file: int | None = None,
) -> list[Path]:
    normalized_dir = str(dir_path or "").replace("\\", "/").strip().rstrip("/")

    root_prefix = ""
    try:
        _, root_path, _ = get_file_token("", bohr_job_id, access_key=access_key)
        root_prefix = str(root_path or "").replace("\\", "/")
        if root_prefix and not root_prefix.endswith("/"):
            root_prefix += "/"
    except Exception:
        pass

    abs_prefix = root_prefix + normalized_dir if root_prefix else normalized_dir

    objects = iterate_job_files(bohr_job_id, prefix=abs_prefix, access_key=access_key)
    file_objects = [o for o in objects if isinstance(o, dict) and not o.get("isDir")]

    if not file_objects:
        raise RuntimeError(
            f"download_job_directory: no files found under '{normalized_dir}' "
            f"in job {bohr_job_id} (prefix='{abs_prefix}')."
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []

    for obj in file_objects:
        remote_full_path = str(obj.get("path", "")).replace("\\", "/")
        rel_path = remote_full_path
        if root_prefix and rel_path.startswith(root_prefix):
            rel_path = rel_path[len(root_prefix) :].lstrip("/")

        if max_bytes_per_file is not None:
            size = obj.get("size")
            if isinstance(size, int) and size > max_bytes_per_file:
                logger.info(
                    "download_job_directory: skipping %s (%d bytes > limit %d)",
                    rel_path,
                    size,
                    max_bytes_per_file,
                )
                continue

        rel_inside_dir = rel_path
        if rel_inside_dir.startswith(normalized_dir + "/"):
            rel_inside_dir = rel_inside_dir[len(normalized_dir) + 1 :]

        file_dest = dest_dir / rel_inside_dir
        file_dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            path = download_job_file(
                rel_path, bohr_job_id, file_dest, access_key=access_key
            )
            downloaded.append(path)
            logger.debug("download_job_directory: downloaded %s -> %s", rel_path, path)
        except Exception as exc:
            logger.warning(
                "download_job_directory: failed to download %s: %s", rel_path, exc
            )

    return downloaded


def query_job_status(
    job_id: str,
    *,
    bohr_job_id: str | None = None,
    software: str | None = None,
    access_key: str | None = None,
) -> str:
    bid = _extract_bohr_job_id(job_id, bohr_job_id)
    if not bid:
        return "Unknown"
    try:
        detail = get_job_detail_raw(bid, access_key=access_key)
        openapi_error = _extract_openapi_error(detail)
        if openapi_error:
            logger.warning(
                "query_job_status(%s) business error: %s", bid, openapi_error
            )
            return f"Error:{openapi_error}"

        data = detail.get("data")
        if not isinstance(data, dict):
            return "Error:Invalid OpenAPI response: missing data object."
        code = data.get("status", -999)
        status = _mapping_status(code)
        logger.info(
            "query_job_status(job_id=%s, bohr=%s) -> status=%s (code=%s)",
            job_id,
            bid,
            status,
            code,
        )
        return status
    except HTTPError as exc:
        msg = _http_error_message(exc)
        logger.warning("query_job_status(%s) HTTP error: %s", bid, msg)
        return f"Error:{msg}"
    except URLError as exc:
        msg = str(exc.reason or exc)
        logger.warning("query_job_status(%s) URL error: %s", bid, msg)
        return f"Error:{msg}"
    except ValueError as exc:
        logger.warning("query_job_status(%s) value error: %s", bid, exc)
        return f"Error:{exc}"
    except Exception as exc:
        logger.error("query_job_status(%s) unexpected error: %s", bid, exc, exc_info=True)
        return f"Error:{exc}"


def get_job_results(
    job_id: str,
    *,
    bohr_job_id: str | None = None,
    software: str | None = None,
    access_key: str | None = None,
) -> dict[str, Any]:
    bid = _extract_bohr_job_id(job_id, bohr_job_id)
    if not bid:
        return {
            "error": "Cannot resolve Bohrium job ID.  Pass --bohr_job_id explicitly."
        }

    try:
        detail = get_job_detail_raw(bid, access_key=access_key)
        openapi_error = _extract_openapi_error(detail)
        if openapi_error:
            return {"bohr_job_id": bid, "error": openapi_error}

        data = detail.get("data")
        if not isinstance(data, dict):
            return {
                "bohr_job_id": bid,
                "error": "Invalid OpenAPI response: missing data object.",
            }
        status_code = data.get("status", -999)
        status_str = _mapping_status(status_code)

        result: dict[str, Any] = {
            "bohr_job_id": bid,
            "status": status_str,
            "raw_status": status_code,
        }

        for key in (
            "name",
            "jobGroupId",
            "startTime",
            "endTime",
            "machineType",
            "image",
        ):
            if key in data:
                result[key] = data[key]

        if status_str == "Finished":
            try:
                files = iterate_job_files(bid, access_key=access_key)
                result["output_files"] = [
                    f.get("path", "") for f in files if not f.get("isDir")
                ]
            except Exception as exc:
                logger.warning(
                    "get_job_results: file listing failed for %s: %s", bid, exc
                )
                result["output_files_error"] = str(exc)

        return result
    except Exception as exc:
        logger.error("get_job_results(%s) failed: %s", bid, exc, exc_info=True)
        return {"error": str(exc)}


def terminate_job(
    bohr_job_id: str,
    *,
    access_key: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    bid = (bohr_job_id or "").strip()
    if not bid:
        return False, {"error": "bohr_job_id is required"}

    ak = _get_access_key(access_key)
    url = f"{_openapi_host()}/openapi/v1/sandbox/kill/{bid}?accessKey={ak}"

    try:
        req = Request(url, method="POST")
        with urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            result = json.loads(body)

        code = result.get("code")
        if code == 0:
            return True, {
                "bohr_job_id": bid,
                "result": "terminate_requested",
                "response": result,
                "endpoint": url,
            }
        return False, {
            "bohr_job_id": bid,
            "result": "terminate_failed",
            "error": f"API returned code={code}, msg={result.get('msg')}",
            "response": result,
        }
    except HTTPError as exc:
        return False, {
            "bohr_job_id": bid,
            "result": "terminate_failed",
            "error": f"HTTP error {exc.code}: {exc.reason}",
        }
    except URLError as exc:
        return False, {
            "bohr_job_id": bid,
            "result": "terminate_failed",
            "error": f"URL error: {exc.reason}",
        }
    except Exception as exc:
        return False, {
            "bohr_job_id": bid,
            "result": "terminate_failed",
            "error": f"Unexpected error: {exc}",
        }
