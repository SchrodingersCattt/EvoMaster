"""Integration test: Bohrium submit with small (<100MB) and large (>=100MB) input.

Usage:
    uv run python scripts/test_submit_upload_size.py

Requires .env with BOHRIUM_ACCESS_KEY, BOHRIUM_PROJECT_ID, BOHRIUM_BASE_URL.
Set BOHRIUM_USE_SANDBOX=1 for sandbox mode (default).

The script creates temporary directories with dummy payload files,
submits two jobs (one small, one large), and prints the results.
Both jobs run a trivial `ls -lh` command so they finish quickly.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env", override=True)

from matmaster.bohrium.client import add_job, create_job, get_job_detail  # noqa: E402
from matmaster.bohrium.credentials import build_bohrium_context  # noqa: E402
from matmaster.bohrium.status import status_name  # noqa: E402
from matmaster.bohrium.upload import upload_input_archive  # noqa: E402
from matmaster.tools.builtin.bohrium_tool.models import BohriumInputSource  # noqa: E402
from matmaster.tools.builtin.bohrium_tool.transfers import (  # noqa: E402
    prepare_input_archive,
)

SMALL_SIZE_MB = 5
LARGE_SIZE_MB = 110
IMAGE = "registry.dp.tech/dev/test/ubuntu:20.04-py3.10"
MACHINE = "c2_m4_cpu"
CMD = "ls -lh > log 2>&1"


def _create_input_dir(size_mb: int) -> Path:
    """Create a temp dir with a random-data file that resists zip compression."""
    tmp = Path(tempfile.mkdtemp(prefix=f"bohrium_test_{size_mb}mb_"))
    payload = tmp / "payload.bin"
    with open(payload, "wb") as f:
        for _ in range(size_mb):
            f.write(os.urandom(1024 * 1024))
    actual_mb = payload.stat().st_size / (1024 * 1024)
    print(f"  Created {payload} ({actual_mb:.1f} MB)")
    return tmp


def _submit_one(label: str, input_dir: Path) -> dict:
    """Run the full submit flow: create → upload → add."""
    ctx = build_bohrium_context(session=None, require_project=True)
    print(f"\n{'='*60}")
    print(f"[{label}] sandbox={ctx.sandbox}  base_url={ctx.credentials.base_url}")
    print(f"[{label}] input_dir={input_dir}")

    source = BohriumInputSource(
        kind="local_dir",
        raw_path=str(input_dir),
        resolved_path=str(input_dir),
    )

    job_name = f"test-upload-{label}-{int(time.time())}"

    t0 = time.time()

    with prepare_input_archive(source, session=None) as zip_path:
        zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"[{label}] zip size: {zip_size_mb:.2f} MB")
        threshold_mb = 100
        if zip_size_mb < threshold_mb:
            print(f"[{label}] -> will use SINGLE upload (< {threshold_mb} MB)")
        else:
            print(f"[{label}] -> will use MULTIPART upload (>= {threshold_mb} MB)")

        print(f"[{label}] Creating job...")
        create_data = create_job(ctx, job_name=job_name)
        print(f"[{label}] create_data keys: {list(create_data.keys())}")
        print(f"[{label}] storeHost: {create_data.get('storeHost', '(missing)')}")

        print(f"[{label}] Uploading archive...")
        upload = upload_input_archive(create_data=create_data, zip_path=zip_path)
        print(f"[{label}] Upload done. oss_key={upload.oss_key}")

        print(f"[{label}] Adding job...")
        add_data = add_job(
            ctx,
            create_data=create_data,
            upload=upload,
            image=IMAGE,
            cmd=CMD,
            machine=MACHINE,
            job_name=job_name,
            disk_size=50,
        )

    elapsed = time.time() - t0

    if ctx.sandbox:
        job_id = str(add_data.get("jobId", "")).strip()
        bohr_job_id = str(add_data.get("bohrJobId", job_id)).strip()
    else:
        job_id = int(add_data["jobId"])
        bohr_job_id = int(add_data.get("bohrJobId") or job_id)

    result = {
        "label": label,
        "job_id": job_id,
        "bohr_job_id": bohr_job_id,
        "job_name": job_name,
        "elapsed_seconds": round(elapsed, 2),
        "sandbox": ctx.sandbox,
    }
    print(f"[{label}] Submit OK in {elapsed:.2f}s  job_id={job_id}")
    return result


def _poll_once(label: str, job_id) -> str:
    ctx = build_bohrium_context(session=None)
    detail = get_job_detail(ctx, job_id=job_id)
    code = detail.get("status", 0)
    name = status_name(code)
    print(f"[{label}] poll -> status={name} (code={code})")
    return name


def main():
    print("Bohrium submit upload-size integration test")
    print(f"  BOHRIUM_BASE_URL = {os.environ.get('BOHRIUM_BASE_URL', '(unset)')}")
    print(f"  BOHRIUM_USE_SANDBOX = {os.environ.get('BOHRIUM_USE_SANDBOX', '(unset)')}")
    print(f"  Small payload: {SMALL_SIZE_MB} MB")
    print(f"  Large payload: {LARGE_SIZE_MB} MB")

    small_dir = _create_input_dir(SMALL_SIZE_MB)
    large_dir = _create_input_dir(LARGE_SIZE_MB)

    results = []
    try:
        r1 = _submit_one("small", small_dir)
        results.append(r1)
    except Exception as exc:
        print(f"[small] FAILED: {exc}")
        results.append({"label": "small", "error": str(exc)})

    try:
        r2 = _submit_one("large", large_dir)
        results.append(r2)
    except Exception as exc:
        print(f"[large] FAILED: {exc}")
        results.append({"label": "large", "error": str(exc)})

    print(f"\n{'='*60}")
    print("Waiting 10s before first poll...")
    time.sleep(10)

    for r in results:
        if "error" in r:
            continue
        _poll_once(r["label"], r["job_id"])

    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
