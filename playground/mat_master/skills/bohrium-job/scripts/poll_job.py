"""poll_job.py - monitor a Bohrium job by job_id and download results."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

ACCESS_KEY = os.environ.get("BOHRIUM_ACCESS_KEY", "").strip()
OPENAPI_BASE = os.environ.get("BOHRIUM_BASE_URL", "https://openapi.dp.tech").rstrip("/")

_HEADER = {"accessKey": ACCESS_KEY}
_STATUS_MAP = {
    0: "Pending",
    1: "Running",
    2: "Finished",
    3: "Scheduling",
    -1: "Failed",
}
_SUCCESS_CODE = 2
_RUNNING_CODES = {0, 1, 3}
_FAILURE_CODES = {-1}
_MAX_FAILURE_CONFIRMS = 3
_MAX_UNKNOWN_COUNT = 3


def _get(path: str, timeout: int = 30) -> dict:
    response = requests.get(
        f"{OPENAPI_BASE}{path}",
        headers=_HEADER,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _get_job_detail(job_id: int) -> dict:
    response = _get(f"/openapi/v1/job/{job_id}")
    return response.get("data", {})


def poll_until_done(job_id: int, max_polls: int, interval: int) -> str:
    """Poll /openapi/v1/job/{job_id} until terminal status."""
    print(f"[poll] job_id={job_id}, max_polls={max_polls}, interval={interval}s", flush=True)
    failure_confirms = 0
    unknown_count = 0
    last_non_running_status = "Timeout"

    for idx in range(max_polls):
        detail = _get_job_detail(job_id)
        code = detail.get("status", 0)
        name = _STATUS_MAP.get(code, f"Unknown({code})")
        print(f"[poll] [{idx + 1:02d}/{max_polls}] {name}", flush=True)

        if code == _SUCCESS_CODE:
            return name

        if code in _FAILURE_CODES:
            failure_confirms += 1
            last_non_running_status = name
            print(f"[poll] failure confirm {failure_confirms}/{_MAX_FAILURE_CONFIRMS}", flush=True)
            if failure_confirms >= _MAX_FAILURE_CONFIRMS:
                return name
            time.sleep(min(interval, 10))
            continue

        if code not in _RUNNING_CODES:
            unknown_count += 1
            last_non_running_status = f"Unknown({code})"
            print(f"[poll] unknown code={code} count={unknown_count}/{_MAX_UNKNOWN_COUNT}", flush=True)
            if unknown_count >= _MAX_UNKNOWN_COUNT:
                return f"Unknown({code})"
            time.sleep(min(interval, 10))
            continue

        failure_confirms = 0
        unknown_count = 0
        time.sleep(interval)

    return last_non_running_status


def read_log_from_dir(extract_dir: Path, max_chars: int = 4000) -> str:
    """Read log tail from extracted result directory.

    Priority: log > STDOUTERR
    Returns the last max_chars characters of the first matching file.
    """
    for name in ("log", "STDOUTERR"):
        f = extract_dir / name
        if f.exists():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                return text[-max_chars:] if len(text) > max_chars else text
            except Exception:
                continue
    return "(no log file found in result directory)"


def download_and_extract(job_id: int, result_dir: Path) -> tuple[list[str], str]:
    """Download out.zip from resultUrl and extract to local directory."""
    result_dir.mkdir(parents=True, exist_ok=True)

    detail = _get_job_detail(job_id)
    result_url = detail.get("resultUrl") or detail.get("result") or ""
    if not result_url:
        out_files = (detail.get("jobFiles") or {}).get("outFiles") or []
        if out_files and isinstance(out_files[0], dict):
            result_url = out_files[0].get("url", "")
    if not result_url:
        raise RuntimeError("resultUrl not found in job detail response")

    zip_path = result_dir / "out.zip"
    with requests.get(result_url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with open(zip_path, "wb") as file_obj:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                file_obj.write(chunk)

    extract_dir = result_dir / "extracted"
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zip_file:
        names = zip_file.namelist()
        zip_file.extractall(extract_dir)

    return names, str(extract_dir.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor Bohrium job by job_id")
    parser.add_argument("--job-id", type=int, required=True, help="Bohrium job id")
    parser.add_argument(
        "--max-polls",
        type=int,
        default=2880,
        help="Max polling attempts (2880 x 30s = 24 hours)",
    )
    parser.add_argument("--poll-interval", type=int, default=30, help="Seconds between polls")
    parser.add_argument(
        "--result-dir",
        default=None,
        help="Directory to save results (default: results/run_<job_id>)",
    )
    args = parser.parse_args()

    if not ACCESS_KEY:
        print(json.dumps({"success": False, "error": "BOHRIUM_ACCESS_KEY not set"}))
        sys.exit(1)

    job_id = int(args.job_id)
    status = poll_until_done(job_id, args.max_polls, args.poll_interval)

    detail = {}
    try:
        detail = _get_job_detail(job_id)
    except Exception:
        detail = {}
    bohr_job_id = detail.get("bohrJobId") or job_id

    # Timeout means the loop exhausted before a terminal status — job may still be running.
    if status == "Timeout":
        print(
            json.dumps(
                {
                    "success": False,
                    "job_id": job_id,
                    "bohr_job_id": bohr_job_id,
                    "status": status,
                    "log_tail": "",
                    "error": (
                        f"Polling exhausted (max_polls={args.max_polls}). "
                        f"Job may still be running on Bohrium (job_id={job_id}). "
                        "Re-run poll_job.py with --max-polls to continue polling."
                    ),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    # Finished or Failed: always attempt to download result zip (failed jobs also produce logs).
    result_dir = Path(args.result_dir) if args.result_dir else Path(f"results/run_{job_id}")
    extract_dir: Path | None = None
    files: list[str] = []
    download_error: str | None = None

    try:
        files, extract_dir_str = download_and_extract(job_id, result_dir)
        extract_dir = Path(extract_dir_str)
    except Exception as exc:
        download_error = str(exc)

    # Read log from the downloaded files; this gives the full log without API pagination issues.
    log_tail = ""
    if extract_dir and extract_dir.exists():
        log_tail = read_log_from_dir(extract_dir)

    if status != "Finished":
        error_msg = f"job ended with status: {status}"
        if download_error:
            error_msg += f"; download error: {download_error}"
        print(
            json.dumps(
                {
                    "success": False,
                    "job_id": job_id,
                    "bohr_job_id": bohr_job_id,
                    "status": status,
                    "result_dir": str(extract_dir.resolve()) if extract_dir else None,
                    "files": files,
                    "log_tail": log_tail,
                    "error": error_msg,
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    if download_error:
        print(
            json.dumps(
                {
                    "success": False,
                    "job_id": job_id,
                    "bohr_job_id": bohr_job_id,
                    "status": status,
                    "log_tail": log_tail,
                    "error": f"download failed: {download_error}",
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    print(
        json.dumps(
            {
                "success": True,
                "job_id": job_id,
                "bohr_job_id": bohr_job_id,
                "status": status,
                "result_dir": str(extract_dir.resolve()),
                "files": files,
                "log_tail": log_tail,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
