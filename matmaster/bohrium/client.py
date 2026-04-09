from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

from .errors import BohriumAPIError
from .status import FAILURE_CODES, SUCCESS_CODE, status_name
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
    response = _post(ctx.credentials.base_url, path, ctx.credentials.access_key, payload)
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
    response = _post(ctx.credentials.base_url, path, ctx.credentials.access_key, payload)
    if response.get("code") != 0:
        raise BohriumAPIError(f"job/add failed: {response}")
    return response["data"]


def get_job_detail(ctx: BohriumContext, *, job_id: int | str) -> dict[str, Any]:
    path = (
        f"/openapi/v1/sandbox/job/{job_id}"
        if ctx.sandbox
        else f"/openapi/v1/job/{job_id}"
    )
    return _get(ctx.credentials.base_url, path, ctx.credentials.access_key).get("data") or {}


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


def terminate_job(
    ctx: BohriumContext,
    *,
    bohr_job_id: str,
) -> tuple[bool, dict[str, Any]]:
    bid = (bohr_job_id or "").strip()
    if not bid:
        return False, {"error": "bohr_job_id is required"}
    try:
        result = _post(
            ctx.credentials.base_url,
            f"/openapi/v1/sandbox/kill/{bid}",
            ctx.credentials.access_key,
            {},
        )
        if result.get("code") == 0:
            return True, {
                "bohr_job_id": bid,
                "result": "terminate_requested",
                "response": result,
            }
        return False, {
            "bohr_job_id": bid,
            "result": "terminate_failed",
            "error": f"API returned code={result.get('code')}",
        }
    except Exception as exc:
        return False, {
            "bohr_job_id": bid,
            "result": "terminate_failed",
            "error": str(exc),
        }


_SANDBOX_MACHINES: list[dict[str, Any]] = [
    {"skuEnName": "c2_m4_cpu", "cpuCoreNum": 2, "memory": 4},
    {"skuEnName": "c2_m8_cpu", "cpuCoreNum": 2, "memory": 8},
    {"skuEnName": "c8_m32_cpu", "cpuCoreNum": 8, "memory": 32},
    {"skuEnName": "c32_m128_cpu", "cpuCoreNum": 32, "memory": 128},
    {
        "skuEnName": "c8_m32_1 * NVIDIA 4090",
        "cpuCoreNum": 8,
        "memory": 32,
        "gpu": "NVIDIA GeForce RTX 4090",
        "gpuCoreNum": 1,
    },
    {
        "skuEnName": "c16_m64_1 * NVIDIA 4090",
        "cpuCoreNum": 16,
        "memory": 64,
        "gpu": "NVIDIA GeForce RTX 4090",
        "gpuCoreNum": 1,
    },
    {
        "skuEnName": "c16_m64_1 * NVIDIA 5090",
        "cpuCoreNum": 16,
        "memory": 64,
        "gpu": "NVIDIA GeForce RTX 5090",
        "gpuCoreNum": 1,
    },
]


def list_sandbox_machines(
    *,
    machine_type: str,
    keyword: str,
    max_results: int,
) -> list[dict[str, Any]]:
    if machine_type == "gpu":
        candidates = [machine for machine in _SANDBOX_MACHINES if "gpu" in machine]
    else:
        candidates = [machine for machine in _SANDBOX_MACHINES if "gpu" not in machine]
    lowered = keyword.lower()
    if lowered:
        candidates = [
            machine
            for machine in candidates
            if lowered in str(machine.get("skuEnName") or "").lower()
        ]
    return candidates[:max_results]


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

    data = _get(
        ctx.credentials.base_url,
        "/openapi/v2/image/public",
        ctx.credentials.access_key,
        params={"page": 1, "pageSize": 1000},
    )
    all_images = (data.get("data") or {}).get("items") or []

    if lowered_keyword:
        filtered = [
            record
            for record in all_images
            if lowered_keyword
            in str(record.get("name") or record.get("imageName") or "").lower()
            or lowered_keyword in str(record.get("description") or "").lower()
        ]
    else:
        filtered = all_images

    to_fetch: list[tuple[Any, str, str]] = []
    for record in filtered[:max_results]:
        img_id = record.get("id") or record.get("imageId")
        if img_id is not None:
            name = record.get("name") or record.get("imageName") or ""
            description = record.get("description") or ""
            to_fetch.append((img_id, name, description))

    def _fetch_versions(item: tuple[Any, str, str]) -> dict[str, Any]:
        img_id, name, description = item
        try:
            version_data = _get(
                ctx.credentials.base_url,
                f"/openapi/v2/image/public/{img_id}/version",
                ctx.credentials.access_key,
                params={
                    "current": 1,
                    "pageSize": 10,
                    "page": 1,
                    "resourceType": "",
                    "version": "",
                },
            )
            versions = (version_data.get("data") or {}).get("items") or []
        except Exception:
            versions = []

        version_list = []
        for version in versions:
            entry: dict[str, Any] = {}
            for key in ("url", "version", "resourceType", "desc", "size"):
                value = version.get(key)
                if value is not None and value != "":
                    entry[key] = value
            if entry:
                version_list.append(entry)

        result: dict[str, Any] = {
            "id": img_id,
            "name": name,
            "versions": version_list,
        }
        if description:
            result["description"] = description
        return result

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_fetch_versions, to_fetch))

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
        results = list_sandbox_machines(
            machine_type=machine_type,
            keyword=lowered_keyword,
            max_results=max_results,
        )
        return {
            "success": True,
            "type": machine_type,
            "sandbox": True,
            "note": "Sandbox mode: machine list is a fixed preset, not queried from the platform.",
            "total_found": len(results),
            "returned": len(results),
            "machines": results,
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
