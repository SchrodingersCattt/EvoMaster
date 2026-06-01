from __future__ import annotations

import logging
from pathlib import Path

from matmaster_bohrium_transfer.download import run_download_results_payload

from .client import get_file_token
from .types import BohriumContext

logger = logging.getLogger(__name__)


def download_job_artifacts(
    *,
    job_id: int | str,
    detail_data: dict,
    result_dir: Path,
    ctx: BohriumContext,
) -> tuple[list[str], str]:
    payload: dict = {
        "job_id": str(job_id),
        "detail_data": detail_data,
        "result_dir": str(result_dir),
        "sandbox": ctx.sandbox,
    }
    if ctx.sandbox:
        try:
            host, path, token = get_file_token(
                ctx,
                file_path="log",
                job_id=str(job_id),
            )
            if host and path and token:
                payload["sandbox_log_file"] = {
                    "host": host,
                    "path": path,
                    "token": token,
                }
        except Exception:
            logger.debug("sandbox log token prefetch failed", exc_info=True)

    result = run_download_results_payload(payload)
    files = result.get("files") or []
    if not isinstance(files, list):
        files = []
    return [str(item) for item in files], str(result.get("log_tail") or "")
