from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from .errors import BohriumAPIError
from .models import BohriumContext
from .open_sdk import UploadedArchive

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    -10: "Prepared",
    -2: "Deleted",
    -1: "Failed",
    0: "Pending",
    1: "Running",
    2: "Finished",
    3: "Scheduling",
    6: "Unknown",
}
_SUCCESS_CODE = 2
_RUNNING_CODES = {-10, 0, 1, 3}
_FAILURE_CODES = {-2, -1}


def _mask_secret(secret: str) -> str:
    raw = (secret or "").strip()
    if not raw:
        return "(empty)"
    if len(raw) <= 4:
        return raw[0] + "..."
    return raw[:4] + "..."


def _compact_log_text(text: str, *, max_chars: int = 200) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return "(empty)"
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


def _log_http_error(method: str, url: str, response: Any) -> None:
    logger.warning(
        "Bohrium HTTP error method=%s url=%s status=%s response_body=%s",
        method,
        url,
        getattr(response, "status_code", "unknown"),
        _compact_log_text(getattr(response, "text", "")),
    )


def _get(
    base_url: str,
    path: str,
    access_key: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    response = requests.get(
        f"{base_url}{path}",
        headers={"accessKey": access_key, "Accept": "application/json"},
        params=params or {},
        timeout=timeout,
    )
    if not response.ok:
        _log_http_error("GET", f"{base_url}{path}", response)
    response.raise_for_status()
    return response.json()


def _post(
    base_url: str,
    path: str,
    access_key: str,
    payload: dict[str, Any],
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    response = requests.post(
        f"{base_url}{path}",
        headers={"accessKey": access_key, "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if not response.ok:
        _log_http_error("POST", f"{base_url}{path}", response)
    response.raise_for_status()
    return response.json()


def use_sandbox() -> bool:
    return os.environ.get("BOHRIUM_USE_SANDBOX", "1").strip() == "1"


def create_job(ctx: BohriumContext, *, job_name: str) -> dict[str, Any]:
    path = "/openapi/v1/sandbox/job/create" if ctx.sandbox else "/openapi/v1/job/create"
    payload = (
        {"projectId": ctx.project_id, "name": job_name}
        if ctx.sandbox
        else {
            "projectId": ctx.project_id,
            "jobName": job_name,
        }
    )
    response = _post(ctx.base_url, path, ctx.access_key, payload)
    if response.get("code") != 0:
        raise BohriumAPIError(f"job/create failed: {response}")
    return response["data"]


def add_job(
    ctx: BohriumContext,
    *,
    create_data: dict[str, Any],
    upload: UploadedArchive,
    image: str,
    cmd: str,
    machine: str,
    job_name: str,
    disk_size: int,
) -> dict[str, Any]:
    if ctx.sandbox:
        payload = {
            "imageName": image,
            "scassType": machine,
            "jobName": job_name,
            "cmd": cmd,
            "jobId": str(create_data["jobId"]).strip(),
            "ossPath": [upload.download_url],
        }
        path = "/openapi/v1/sandbox/job/add"
    else:
        payload = {
            "projectId": ctx.project_id,
            "jobName": job_name,
            "jobType": "indicate",
            "scassType": machine,
            "cmd": cmd,
            "imageName": image,
            "ossPath": [upload.oss_key],
            "inputFileMethod": 1,
            "inputFileType": 3,
            "diskSize": disk_size,
            "logFiles": ["log"],
        }
        path = "/openapi/v2/job/add"
    response = _post(ctx.base_url, path, ctx.access_key, payload)
    if response.get("code") != 0:
        raise BohriumAPIError(f"job/add failed: {response}")
    return response["data"]


def get_job_detail(ctx: BohriumContext, *, job_id: int | str) -> dict[str, Any]:
    path = (
        f"/openapi/v1/sandbox/job/{job_id}"
        if ctx.sandbox
        else f"/openapi/v1/job/{job_id}"
    )
    return _get(ctx.base_url, path, ctx.access_key).get("data") or {}


def confirm_terminal_status(
    ctx: BohriumContext,
    *,
    job_id: int | str,
    detail_data: dict[str, Any],
    attempts: int = 3,
    sleep_seconds: int = 3,
) -> tuple[int, str, dict[str, Any]]:
    code = detail_data.get("status", 0)
    status_name = _STATUS_MAP.get(code, f"Unknown({code})")
    if code not in _FAILURE_CODES:
        return code, status_name, detail_data

    latest = detail_data
    for _attempt in range(1, attempts):
        time.sleep(sleep_seconds)
        latest = get_job_detail(ctx, job_id=job_id)
        code = latest.get("status", 0)
        status_name = _STATUS_MAP.get(code, f"Unknown({code})")
        if code not in _FAILURE_CODES:
            break
    return code, status_name, latest


def list_public_images(
    ctx: BohriumContext, *, keyword: str, max_results: int
) -> list[dict[str, Any]]:
    response = _get(
        ctx.base_url,
        "/openapi/v2/image/public",
        ctx.access_key,
        params={"page": 1, "pageSize": 1000},
    )
    items = (response.get("data") or {}).get("items") or []
    lowered = keyword.lower()
    filtered = [
        item
        for item in items
        if not lowered
        or lowered in str(item.get("name") or item.get("imageName") or "").lower()
        or lowered in str(item.get("description") or "").lower()
    ]
    return filtered[:max_results]


def list_machine_types(
    ctx: BohriumContext, *, machine_type: str, keyword: str, max_results: int
) -> list[dict[str, Any]]:
    response = _get(
        ctx.base_url,
        "/openapi/v1/calc/list",
        ctx.access_key,
        params={
            "page": 1,
            "pageSize": 512,
            "scene": "job",
            "isVirtualNode": "false",
            "chooseType": machine_type,
            "productLine": "bohrium",
        },
    )
    items = (response.get("data") or {}).get("items") or []
    lowered = keyword.lower()
    filtered = [
        item
        for item in items
        if not lowered
        or lowered in str(item.get("skuEnName") or item.get("skuName") or "").lower()
    ]
    return filtered[:max_results]
