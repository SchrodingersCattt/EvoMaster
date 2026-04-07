from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from matmaster.tools.builtin.bohrium_tool.tool import submit_job_via_runtime


def submit_job(
    input_dir: Path,
    image: str,
    cmd: str,
    machine: str,
    job_name: str,
    disk_size: int,
) -> tuple[int | str, int | str]:
    return submit_job_via_runtime(
        input_dir=input_dir,
        image=image,
        cmd=cmd,
        machine=machine,
        job_name=job_name,
        disk_size=disk_size,
        workdir=input_dir.parent,
        session=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit Bohrium job and return job_id")
    parser.add_argument("--input-dir", required=True, help="Input directory to upload")
    parser.add_argument("--image", required=True, help="Docker image for the job")
    parser.add_argument(
        "--cmd", required=True, help="Shell command to run inside container"
    )
    parser.add_argument("--machine", default="c32_m128_cpu", help="Bohrium machine type")
    parser.add_argument("--job-name", default=None, help="Human-readable job name")
    parser.add_argument(
        "--software", default="unknown", help="Software label for default job name"
    )
    parser.add_argument("--disk-size", type=int, default=50, help="Disk size in GB")

    args = parser.parse_args()

    if not os.environ.get("BOHRIUM_ACCESS_KEY", "").strip():
        print(json.dumps({"success": False, "error": "BOHRIUM_ACCESS_KEY not set"}))
        sys.exit(1)
    if int(os.environ.get("BOHRIUM_PROJECT_ID", "-1") or "-1") <= 0:
        print(
            json.dumps(
                {"success": False, "error": "BOHRIUM_PROJECT_ID not set or invalid"}
            )
        )
        sys.exit(1)

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        print(
            json.dumps(
                {"success": False, "error": f"input_dir not found or not a directory: {input_dir}"}
            )
        )
        sys.exit(1)

    job_name = args.job_name or f"{args.software}-job"

    try:
        job_id, bohr_job_id = submit_job(
            input_dir=input_dir,
            image=args.image,
            cmd=args.cmd,
            machine=args.machine,
            job_name=job_name,
            disk_size=args.disk_size,
        )
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        sys.exit(1)

    print(
        json.dumps(
            {"success": True, "job_id": job_id, "bohr_job_id": bohr_job_id},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
