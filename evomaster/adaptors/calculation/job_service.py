"""Bohrium OpenAPI job service: status query, result retrieval, file download.

Provides **synchronous** ``query_job_status`` / ``get_job_results`` for the
job-manager skill (``run_resilient_job.py``).  API surface mirrors
``_tmp/MatMaster/agents/matmaster_agent/services/job.py`` (async → sync,
aiohttp → urllib).

Environment variables (via ``.env`` at project root):
    BOHRIUM_ACCESS_KEY   – Bohrium platform access key
    BOHRIUM_PROJECT_ID   – Bohrium project ID
    SERVICE_ENV          – ``prod`` (default) or ``test``

References:
    _tmp/MatMaster/agents/matmaster_agent/services/job.py
    _tmp/MatMaster/agents/matmaster_agent/utils/job_utils.py
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .env_config import get_current_env

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Bohrium OpenAPI host (aligned with _tmp/MatMaster constant.py)
# ═══════════════════════════════════════════════════════════════════════

def _openapi_host() -> str:
    """``https://openapi.dp.tech`` (prod) or ``https://openapi.test.dp.tech`` (test)."""
    env = get_current_env()
    url_part = f".{env}" if env != "prod" else ""
    return f"https://openapi{url_part}.dp.tech"


def _tiefblue_nas_host() -> str:
    env = get_current_env()
    if env in ("test", "uat"):
        return "https://tiefblue-nas-acs-bj.test.bohrium.com"
    return "https://tiefblue-nas-acs-bj.bohrium.com"


# ═══════════════════════════════════════════════════════════════════════
# Bohrium credentials helper  (lazy import to avoid circular deps)
# ═══════════════════════════════════════════════════════════════════════

def _get_access_key(access_key: str | None = None) -> str:
    """Return a valid access key or raise."""
    if access_key:
        return access_key.strip()
    try:
        from evomaster.env import get_bohrium_credentials
        cred = get_bohrium_credentials()
        ak = cred.get("access_key", "")
    except Exception:
        ak = os.getenv("BOHRIUM_ACCESS_KEY", "").strip()
    if not ak:
        raise ValueError(
            "Bohrium access_key required.  "
            "Set BOHRIUM_ACCESS_KEY in .env or pass access_key explicitly."
        )
    return ak


# ═══════════════════════════════════════════════════════════════════════
# Status mapping  (from _tmp/MatMaster/utils/job_utils.py)
# ═══════════════════════════════════════════════════════════════════════

_STATUS_MAP: dict[int, str] = {
    -1: "Failed",
    -2: "Deleted",
    0:  "Pending",
    1:  "Running",
    2:  "Finished",
    3:  "Scheduling",
    4:  "Stopping",
    5:  "Stopped",
    6:  "Terminating",
    7:  "Killing",
    8:  "Uploading",
    9:  "Wait",
}

# Status strings that mean "still in progress" (used by callers)
RUNNING_STATUSES = frozenset({
    "Running", "Pending", "Scheduling", "Wait", "Uploading",
})


def _mapping_status(code: int) -> str:
    return _STATUS_MAP.get(code, "Unknown")


# ═══════════════════════════════════════════════════════════════════════
# Bohrium job-ID extraction from MCP job_id
# ═══════════════════════════════════════════════════════════════════════

def _extract_bohr_job_id(
    job_id: str,
    bohr_job_id: str | None = None,
) -> str | None:
    """Best-effort extraction of the Bohrium OpenAPI job ID.

    MCP ``job_id`` format: ``"{timestamp}/{task_id}"``.

    * **binary_calc** servers (CP2K, LAMMPS …):
      ``task_id`` is numeric → *is* the Bohrium job ID.
    * **dpdispatcher** servers (ABACUS …):
      ``task_id`` is a hex hash; the real Bohrium job ID lives in
      ``extra_info.bohr_job_id`` returned by the submit tool.

    Parameters
    ----------
    job_id : str
        The MCP ``job_id`` string.
    bohr_job_id : str | None
        Explicit Bohrium job ID (from ``extra_info.bohr_job_id``).
        Takes priority when provided.

    Returns
    -------
    str | None
        Bohrium job ID if resolved, else ``None``.
    """
    if bohr_job_id:
        return bohr_job_id.strip()

    if not job_id:
        return None

    # Split "timestamp/task_id"
    parts = job_id.rsplit("/", 1)
    candidate = (parts[1] if len(parts) == 2 else job_id).strip()

    # Numeric → Bohrium job ID directly (binary_calc convention)
    if candidate.isdigit():
        return candidate

    # 32-char hex string → likely a UUID-style Bohrium ID
    clean = candidate.replace("-", "")
    if re.fullmatch(r"[0-9a-fA-F]{32}", clean):
        return clean

    # Longer hex hash (40-char dpdispatcher SHA) → cannot resolve without bohr_job_id
    if re.fullmatch(r"[0-9a-fA-F]{33,}", clean):
        return None

    # Fallback: try as-is
    return candidate


# ═══════════════════════════════════════════════════════════════════════
# Synchronous HTTP helpers  (stdlib only, like oss_io.py)
# ═══════════════════════════════════════════════════════════════════════

_UA = "EvoMaster-JobService/1.0"


def _get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict:
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


def _download_binary(url: str, dest: Path, headers: dict[str, str] | None = None, timeout: int = 120) -> Path:
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(req, timeout=timeout) as resp:
        dest.write_bytes(resp.read())
    return dest


# ═══════════════════════════════════════════════════════════════════════
# Low-level Bohrium OpenAPI calls
# (sync equivalents of _tmp/MatMaster/services/job.py async functions)
# ═══════════════════════════════════════════════════════════════════════

def get_job_detail_raw(
    bohr_job_id: str,
    *,
    access_key: str | None = None,
) -> dict[str, Any]:
    """GET ``/openapi/v1/sandbox/job/{bohr_job_id}``.

    Returns the full JSON response from the Bohrium API.
    Raises on HTTP / auth errors.
    """
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
    """Get NAS download token for a file inside a Bohrium job.

    Returns ``(host, remote_path, token)``.
    """
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
    """List files in a Bohrium job's working directory.

    Returns a list of ``{"path": ..., "isDir": bool, "size": int, ...}`` dicts.
    """
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
    """Download a single file from a Bohrium job to *dest*.

    Uses the NAS file-token API.
    """
    host, remote_path, token = get_file_token(file_path, bohr_job_id, access_key=access_key)
    if not host or not remote_path or not token:
        raise RuntimeError(
            f"Cannot download '{file_path}' from job {bohr_job_id}: "
            "incomplete file-token response (host/path/token empty)."
        )
    url = f"{host}/api/download/{remote_path}?token={token}"
    return _download_binary(url, dest)


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API  (imported by run_resilient_job.py via __init__)
# ═══════════════════════════════════════════════════════════════════════

def query_job_status(
    job_id: str,
    *,
    bohr_job_id: str | None = None,
    software: str | None = None,
    access_key: str | None = None,
) -> str:
    """Query the status of a remote calculation job.

    Parameters
    ----------
    job_id : str
        MCP ``job_id`` (e.g. ``"2026-02-11-09:47:40.249175/614883"``).
    bohr_job_id : str | None
        Explicit Bohrium job ID (from ``extra_info.bohr_job_id``).
        Required for dpdispatcher-style jobs (ABACUS, etc.) whose
        MCP ``job_id`` contains a hex hash rather than a numeric ID.
    software : str | None
        Software name (reserved for future per-software handling).
    access_key : str | None
        Bohrium access key; falls back to ``BOHRIUM_ACCESS_KEY`` env var.

    Returns
    -------
    str
        One of: ``Finished``, ``Running``, ``Failed``, ``Pending``,
        ``Scheduling``, ``Unknown``, or ``Error:<msg>``.
    """
    bid = _extract_bohr_job_id(job_id, bohr_job_id)
    if not bid:
        return "Unknown"
    try:
        detail = get_job_detail_raw(bid, access_key=access_key)
        code = detail.get("data", {}).get("status", -999)
        status = _mapping_status(code)
        logger.info(
            "query_job_status(job_id=%s, bohr=%s) → status=%s (code=%s)",
            job_id, bid, status, code,
        )
        return status
    except (HTTPError, URLError) as exc:
        logger.warning("query_job_status(%s) HTTP error: %s", bid, exc)
        return "Unknown"
    except ValueError as exc:
        logger.warning("query_job_status(%s) value error: %s", bid, exc)
        return "Unknown"
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
    """Retrieve job result metadata.

    Returns a dict with at least ``status`` and ``bohr_job_id``.
    On success includes ``output_files`` (list of remote paths).
    """
    bid = _extract_bohr_job_id(job_id, bohr_job_id)
    if not bid:
        return {"error": "Cannot resolve Bohrium job ID.  Pass --bohr_job_id explicitly."}

    try:
        detail = get_job_detail_raw(bid, access_key=access_key)
        data = detail.get("data", {})
        status_code = data.get("status", -999)
        status_str = _mapping_status(status_code)

        result: dict[str, Any] = {
            "bohr_job_id": bid,
            "status": status_str,
            "raw_status": status_code,
        }

        # Copy useful metadata fields
        for key in ("name", "jobGroupId", "startTime", "endTime", "machineType", "image"):
            if key in data:
                result[key] = data[key]

        # For finished jobs, try to list output files
        if status_str == "Finished":
            try:
                files = iterate_job_files(bid, access_key=access_key)
                result["output_files"] = [
                    f.get("path", "") for f in files if not f.get("isDir")
                ]
            except Exception as exc:
                logger.warning("get_job_results: file listing failed for %s: %s", bid, exc)
                result["output_files_error"] = str(exc)

        return result
    except Exception as exc:
        logger.error("get_job_results(%s) failed: %s", bid, exc, exc_info=True)
        return {"error": str(exc)}
