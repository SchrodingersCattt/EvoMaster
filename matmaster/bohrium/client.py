from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

from .errors import BohriumAPIError
from .status import FAILURE_CODES, status_name
from .types import BohriumContext
from .upload import UploadedArchive

logger = logging.getLogger(__name__)
_SANDBOX_CATALOG: dict[str, Any] | None = None


def mask_secret(secret: str) -> str:
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


def _load_sandbox_catalog() -> dict[str, Any]:
    global _SANDBOX_CATALOG
    if _SANDBOX_CATALOG is not None:
        return _SANDBOX_CATALOG

    config_path = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
    if not config_path.exists():
        _SANDBOX_CATALOG = {}
        return _SANDBOX_CATALOG

    try:
        import yaml

        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        _SANDBOX_CATALOG = (raw.get("bohrium") or {}).get("sandbox") or {}
    except Exception:
        logger.warning("Failed to load bohrium sandbox catalog", exc_info=True)
        _SANDBOX_CATALOG = {}
    return _SANDBOX_CATALOG


def create_job(ctx: BohriumContext, *, job_name: str) -> dict[str, Any]:
    path = "/openapi/v1/sandbox/job/create" if ctx.sandbox else "/openapi/v1/job/create"
    payload = (
        {"projectId": ctx.credentials.project_id, "name": job_name}
        if ctx.sandbox
        else {
            "projectId": ctx.credentials.project_id,
            "jobName": job_name,
        }
    )
    response = _post(
        ctx.credentials.base_url, path, ctx.credentials.access_key, payload
    )
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
            "projectId": ctx.credentials.project_id,
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
    response = _post(
        ctx.credentials.base_url, path, ctx.credentials.access_key, payload
    )
    if response.get("code") != 0:
        raise BohriumAPIError(f"job/add failed: {response}")
    return response["data"]


def get_job_detail(ctx: BohriumContext, *, job_id: int | str) -> dict[str, Any]:
    path = (
        f"/openapi/v1/sandbox/job/{job_id}"
        if ctx.sandbox
        else f"/openapi/v1/job/{job_id}"
    )
    return (
        _get(ctx.credentials.base_url, path, ctx.credentials.access_key).get("data")
        or {}
    )


def confirm_terminal_status(
    ctx: BohriumContext,
    *,
    job_id: int | str,
    detail_data: dict[str, Any],
    attempts: int = 3,
    sleep_seconds: int = 3,
) -> tuple[int, str, dict[str, Any]]:
    code = int(detail_data.get("status", 0))
    current_status = status_name(code)
    if code not in FAILURE_CODES:
        return code, current_status, detail_data

    latest = detail_data
    for _attempt in range(1, attempts):
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        latest = get_job_detail(ctx, job_id=job_id)
        code = int(latest.get("status", 0))
        current_status = status_name(code)
        if code not in FAILURE_CODES:
            break
    return code, current_status, latest


def get_file_token(
    ctx: BohriumContext,
    *,
    file_path: str,
    bohr_job_id: str,
) -> tuple[str, str, str]:
    result = _post(
        ctx.credentials.base_url,
        "/openapi/v1/sandbox/job/file/token",
        ctx.credentials.access_key,
        {"filePath": file_path, "jobId": bohr_job_id},
    )
    data = result.get("data", {})
    return data.get("host", ""), data.get("path", ""), data.get("token", "")


def terminate_job(
    ctx: BohriumContext,
    *,
    job_id: int | str,
) -> dict[str, Any]:
    """Request termination of a Bohrium job.

    Currently only supported in sandbox mode. The non-sandbox kill endpoint
    has not been wired up; calling this on a non-sandbox context raises
    ``BohriumAPIError``.

    The underlying RPC is asynchronous: a successful response only means the
    kill request was accepted. Callers should follow up with ``get_job_detail``
    (or the tool-layer ``poll`` action) to confirm the job reached a terminal
    state.
    """
    if not ctx.sandbox:
        raise BohriumAPIError(
            "kill is only supported in sandbox mode; "
            "the non-sandbox termination endpoint is not wired up"
        )

    bid = str(job_id or "").strip()
    if not bid:
        raise BohriumAPIError("kill requires a non-empty job_id")

    response = _post(
        ctx.credentials.base_url,
        f"/openapi/v1/sandbox/kill/{bid}",
        ctx.credentials.access_key,
        {},
    )
    if response.get("code") != 0:
        raise BohriumAPIError(f"kill failed: {response}")
    return response.get("data") or {}


def list_images(
    ctx: BohriumContext,
    *,
    keyword: str,
    max_results: int,
) -> dict[str, Any]:
    lowered_keyword = (keyword or "").strip().lower()

    if ctx.sandbox:
        catalog = _load_sandbox_catalog()
        sandbox_images = catalog.get("images") or []
        if sandbox_images:
            filtered = [
                img
                for img in sandbox_images
                if not lowered_keyword
                or lowered_keyword in str(img.get("name", "")).lower()
                or lowered_keyword in str(img.get("description", "")).lower()
            ]
            results = filtered[:max_results]
            return {
                "success": True,
                "keyword": lowered_keyword,
                "total_found": len(filtered),
                "returned": len(results),
                "images": results,
                "source": "sandbox_catalog",
            }

    # Fetch private images only (paginated).
    # Server defaults to pageSize=10, so we page through until we've collected
    # every item the caller can see. Safety cap prevents runaway loops if the
    # server ever returns a bad `total`.
    private_images: list[dict[str, Any]] = []
    page = 1
    page_size = 200
    max_pages = 50  # hard ceiling: 10k images — way beyond any real account
    while page <= max_pages:
        private_data = _get(
            ctx.credentials.base_url,
            "/openapi/v2/image/private",
            ctx.credentials.access_key,
            params={
                "page": page,
                "pageSize": page_size,
                "device": "container",
                "type": "image",
            },
        )
        data_block = private_data.get("data") or {}
        batch = data_block.get("items") or []
        if not batch:
            break
        private_images.extend(batch)
        total = data_block.get("total")
        if isinstance(total, int) and len(private_images) >= total:
            break
        if len(batch) < page_size:
            break
        page += 1

    if lowered_keyword:
        filtered = [
            record
            for record in private_images
            if lowered_keyword
            in str(record.get("name") or record.get("imageName") or "").lower()
            or lowered_keyword in str(record.get("description") or "").lower()
        ]
    else:
        filtered = private_images

    # Private images from /openapi/v2/image/private carry a ready-to-use `url`
    # field, so we build the result entries directly without fetching public
    # version metadata.
    private_results: list[dict[str, Any]] = []
    for record in filtered[:max_results]:
        img_id = record.get("id") or record.get("imageId")
        if img_id is None:
            continue
        name = record.get("name") or record.get("imageName") or ""
        description = record.get("description") or ""
        direct_url = record.get("url") or ""
        entry: dict[str, Any] = {}
        if direct_url:
            entry["url"] = direct_url
        ver_str = name.split(":")[-1] if ":" in name else ""
        if ver_str:
            entry["version"] = ver_str
        size = record.get("size") or ""
        if size:
            entry["size"] = size

        result: dict[str, Any] = {
            "id": img_id,
            "name": name,
            "versions": [entry] if entry else [],
            "private": True,
        }
        if description:
            result["description"] = description
        private_results.append(result)

    results = private_results

    return {
        "success": True,
        "keyword": lowered_keyword,
        "total_found": len(filtered),
        "returned": len(results),
        "images": results,
    }


def list_machines(
    ctx: BohriumContext,
    *,
    machine_type: str,
    keyword: str,
    max_results: int,
) -> dict[str, Any]:
    lowered_keyword = (keyword or "").strip().lower()

    if ctx.sandbox:
        catalog = _load_sandbox_catalog()
        sandbox_machines = (catalog.get("machines") or {}).get(machine_type) or []
        if sandbox_machines:
            if lowered_keyword:
                filtered = [
                    machine
                    for machine in sandbox_machines
                    if lowered_keyword
                    in str(
                        machine.get("skuEnName") or machine.get("skuName") or ""
                    ).lower()
                ]
            else:
                filtered = list(sandbox_machines)
            results = filtered[:max_results]
            return {
                "success": True,
                "type": machine_type,
                "keyword": lowered_keyword,
                "total_found": len(filtered),
                "returned": len(results),
                "machines": results,
                "source": "sandbox_catalog",
            }

    data = _get(
        ctx.credentials.base_url,
        "/openapi/v1/calc/list",
        ctx.credentials.access_key,
        params={
            "page": 1,
            "pageSize": 512,
            "scene": "job",
            "isVirtualNode": "false",
            "chooseType": machine_type,
            "productLine": "bohrium",
        },
    )
    all_machines = (data.get("data") or {}).get("items") or []

    if lowered_keyword:
        filtered = [
            record
            for record in all_machines
            if lowered_keyword
            in str(record.get("skuEnName") or record.get("skuName") or "").lower()
        ]
    else:
        filtered = all_machines

    results = []
    for record in filtered[:max_results]:
        entry: dict[str, Any] = {}
        for key in (
            "skuEnName",
            "cpuCoreNum",
            "memory",
            "gpu",
            "gpuCoreNum",
            "price",
            "hasStock",
        ):
            value = record.get(key)
            if value is not None and value != "":
                entry[key] = value
        if entry:
            results.append(entry)

    return {
        "success": True,
        "type": machine_type,
        "keyword": lowered_keyword,
        "total_found": len(filtered),
        "returned": len(results),
        "machines": results,
    }
