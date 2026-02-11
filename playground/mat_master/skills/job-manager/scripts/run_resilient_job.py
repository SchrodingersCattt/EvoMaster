"""Resilient job lifecycle manager.

Monitors a submitted remote calculation job (DPA, ABACUS, LAMMPS, CP2K, QE,
ABINIT, ORCA, Gaussian, and any future software), polls status, downloads
results on success, diagnoses errors on failure, and returns a structured JSON
summary.

The agent calls this ONCE after submitting a job via MCP. The script blocks until
the job reaches a terminal state (success or permanent failure).

Usage (via use_skill):
    use_skill(
        skill_name="job-manager",
        action="run_script",
        script_name="run_resilient_job.py",
        script_args="--job_id <ID> --software <SW> --workspace <PATH>"
    )

Exit codes:
    0 — success (job completed, results downloaded)
    1 — failure (job failed permanently or retries exhausted)
    2 — usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TERMINAL_SUCCESS = frozenset({"Done", "Success", "Finished", "Completed", "done", "success", "finished", "completed"})
TERMINAL_FAILURE = frozenset({"Failed", "Error", "Cancelled", "failed", "error", "cancelled"})
UNKNOWN_STATUSES = frozenset({"Unknown", "unknown"})

# Built-in fix strategies keyed by canonical error code.
FIX_STRATEGIES: dict[str, dict[str, Any]] = {
    "scf_diverged": {
        "action": "update_parameter",
        "description": "SCF not converging — reduce mixing, switch algorithm",
        "params": {"ALGO": "All", "AMIX": "0.1", "BMIX": "0.0001"},
    },
    "scf_diagonalization_error": {
        "action": "update_parameter",
        "description": "Diagonalization failure — switch to more robust algorithm",
        "params": {"ALGO": "Normal", "PREC": "Accurate"},
    },
    "kpoints_error": {
        "action": "reduce_kpoints",
        "description": "K-point / IBZKPT error — reduce k-mesh density by half",
        "factor": 0.5,
    },
    "grid_too_coarse": {
        "action": "increase_cutoff",
        "description": "FFT grid too coarse — increase energy cutoff",
        "increment": 50,
    },
    "lost_atoms": {
        "action": "reduce_timestep",
        "description": "Lost atoms in MD — halve the timestep",
        "factor": 0.5,
    },
    "out_of_range": {
        "action": "reduce_timestep",
        "description": "Out of range — halve the timestep",
        "factor": 0.5,
    },
    "walltime_exceeded": {
        "action": "increase_walltime",
        "description": "Job killed by walltime — double the walltime limit",
        "factor": 2.0,
    },
    "oom_error": {
        "action": "reduce_parallelism",
        "description": "Out of memory — reduce parallelism or memory per node",
        "suggestion": "Reduce NCORE/NPAR or split into smaller systems",
    },
}

# Log file name patterns per software.
# New software can be added here; any unlisted software uses the generic fallback.
LOG_PATTERNS: dict[str, list[str]] = {
    "vasp": ["OUTCAR", "vasp.out", "*.out"],
    "abacus": ["OUT.ABACUS", "running_*.log", "*.log"],
    "lammps": ["log.lammps", "*.log"],
    "cp2k": ["*.out", "cp2k.out", "*.log"],
    "gaussian": ["*.log", "*.out"],
    "qe": ["*.out", "*.log"],
    "abinit": ["*.out", "*.log"],
    "orca": ["*.out", "*.log"],
    "dpa": ["*.log", "*.out", "*.json"],
}


# ---------------------------------------------------------------------------
# Error diagnosis  (reuses log_diagnostics skill logic)
# ---------------------------------------------------------------------------

def _get_log_diagnostics_dir() -> Path | None:
    """Resolve path to log_diagnostics/scripts/ relative to this skill."""
    skill_dir = Path(__file__).resolve().parent.parent  # job-manager/
    log_diag = skill_dir.parent / "log_diagnostics" / "scripts"
    return log_diag if log_diag.exists() else None


def _diagnose_log(log_path: str, software: str = "") -> str:
    """Run log_diagnostics analysis and return a canonical error code."""
    diag_dir = _get_log_diagnostics_dir()
    if diag_dir is None:
        return "unknown_error"

    # Import the analysis functions from the sibling skill
    if str(diag_dir) not in sys.path:
        sys.path.insert(0, str(diag_dir))
    try:
        from extract_error import analyze_lammps_log, analyze_vasp_log  # type: ignore[import-untyped]

        sw_lower = software.lower()
        lower = log_path.lower()
        # DPA / MLP jobs: typically output JSON; no specialised analyser yet
        if sw_lower == "dpa":
            return "unknown_error"
        if sw_lower in ("vasp", "abacus", "abinit", "qe") or any(
            tok in lower for tok in ("outcar", "vasp", "abacus", "abinit", "qe")
        ):
            return analyze_vasp_log(log_path)
        if sw_lower in ("lammps",) or "lammps" in lower:
            return analyze_lammps_log(log_path)
        # Generic fallback: try VASP-style analysis first
        return analyze_vasp_log(log_path)
    except Exception:
        return "unknown_error"


def _find_log_file(workspace: str, software: str) -> str | None:
    """Find the most recent log file in *workspace* for *software*.

    Falls back to generic ``*.log`` / ``*.out`` patterns for any software
    not listed in LOG_PATTERNS.
    """
    ws = Path(workspace)
    if not ws.exists():
        return None
    patterns = LOG_PATTERNS.get(software.lower(), ["*.log", "*.out", "*.json"])
    for pat in patterns:
        matches = sorted(ws.rglob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return str(matches[0])
    return None


# ---------------------------------------------------------------------------
# OSS / result download
# ---------------------------------------------------------------------------

def _download_results(result_urls: list[str], workspace: str) -> dict[str, Any]:
    """Download result URLs (OSS / HTTP) to workspace/calculation_results/."""
    try:
        from evomaster.adaptors.calculation import download_oss_to_local  # type: ignore[import-untyped]
    except ImportError:
        return {"status": "skip", "reason": "download_oss_to_local not available; results are at remote URLs"}

    download_dir = Path(workspace) / "calculation_results"
    download_dir.mkdir(parents=True, exist_ok=True)

    downloaded: list[str] = []
    errors: list[str] = []
    for i, url in enumerate(result_urls):
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        try:
            segment = (url.split("?")[0].rstrip("/") or "").rsplit("/", 1)[-1] or "file"
            segment = re.sub(r"[^\w.\-]", "_", segment) or "file"
            rel = f"result_{i}_{segment}"
            path = download_oss_to_local(url, download_dir, dest_relative_path=rel)
            downloaded.append(str(path))
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    info: dict[str, Any] = {"downloaded": downloaded, "download_dir": str(download_dir)}
    if errors:
        info["download_errors"] = errors
    return info


# ---------------------------------------------------------------------------
# Job status & results  (via calculation adaptor when available)
# ---------------------------------------------------------------------------

def _check_job_status(job_id: str, software: str, bohr_job_id: str | None = None) -> str:
    """Query job status via the Bohrium OpenAPI (calculation adaptor).

    Returns one of: Finished / Failed / Running / Pending / Scheduling / Unknown.
    Falls back to "Unknown" when the adaptor is not available.
    """
    try:
        from evomaster.adaptors.calculation import query_job_status  # type: ignore[import-untyped]
        return str(query_job_status(job_id, bohr_job_id=bohr_job_id, software=software))
    except ImportError:
        return "Unknown"
    except Exception as exc:
        return f"Error:{exc}"


def _get_job_results(job_id: str, software: str, bohr_job_id: str | None = None) -> dict[str, Any]:
    """Fetch job result payload (metadata, file listing) via the Bohrium OpenAPI."""
    try:
        from evomaster.adaptors.calculation import get_job_results  # type: ignore[import-untyped]
        result = get_job_results(job_id, bohr_job_id=bohr_job_id, software=software)
        return result if isinstance(result, dict) else {"raw": result}
    except ImportError:
        return {}
    except Exception as exc:
        return {"error": str(exc)}


def _collect_result_urls(results: dict[str, Any]) -> list[str]:
    """Extract all HTTP URLs from a results dict."""
    urls: list[str] = []
    for key in ("output_files", "result_urls", "artifacts", "urls", "output_urls"):
        val = results.get(key)
        if not val:
            continue
        if isinstance(val, str):
            val = [val]
        if isinstance(val, list):
            urls.extend(u for u in val if isinstance(u, str) and u.startswith("http"))
    return urls


# ---------------------------------------------------------------------------
# Main lifecycle
# ---------------------------------------------------------------------------

def run_lifecycle(
    job_id: str,
    software: str,
    workspace: str,
    poll_interval: int = 30,
    max_retries: int = 5,
    bohr_job_id: str | None = None,
) -> dict[str, Any]:
    """Block until the job succeeds, fails permanently, or retries are exhausted.

    Returns a JSON-serialisable dict summarising the outcome.

    Parameters
    ----------
    bohr_job_id : str | None
        Explicit Bohrium job ID (from ``extra_info.bohr_job_id``).
        Required for dpdispatcher-style MCP servers (ABACUS, etc.)
        where the MCP ``job_id`` contains a hex hash.
    """
    current_job_id = job_id
    retries = 0
    max_polls = 720  # safety cap: 720 * 30s = 6 hours
    unknown_count = 0
    max_unknown = 3  # allow a few retries before giving up on unknown

    while retries <= max_retries:
        polls = 0
        # ── Poll loop ──
        while polls < max_polls:
            status = _check_job_status(current_job_id, software, bohr_job_id=bohr_job_id)

            # -- Success --
            if status in TERMINAL_SUCCESS:
                results = _get_job_results(current_job_id, software, bohr_job_id=bohr_job_id)
                urls = _collect_result_urls(results)
                download_info: dict[str, Any] = {}
                if urls and workspace:
                    download_info = _download_results(urls, workspace)
                return {
                    "status": "success",
                    "job_id": current_job_id,
                    "bohr_job_id": bohr_job_id or results.get("bohr_job_id"),
                    "retries": retries,
                    "results": results,
                    "downloads": download_info,
                    "message": f"Job {current_job_id} completed successfully.",
                }

            # -- Failure --
            if status in TERMINAL_FAILURE or status.startswith("Error:"):
                break

            # -- Unknown: retry a few times then give up --
            if status in UNKNOWN_STATUSES:
                unknown_count += 1
                if unknown_count >= max_unknown:
                    return {
                        "status": "unknown",
                        "job_id": current_job_id,
                        "bohr_job_id": bohr_job_id,
                        "retries": retries,
                        "message": (
                            f"Job status returned 'Unknown' {unknown_count} times.  "
                            "Possible causes: (1) Bohrium access_key not set or invalid — "
                            "check BOHRIUM_ACCESS_KEY in .env; (2) job ID could not be resolved "
                            "— for ABACUS / dpdispatcher jobs, pass --bohr_job_id explicitly "
                            "(from extra_info.bohr_job_id in the submit response)."
                        ),
                    }
                # Short retry before giving up
                time.sleep(min(poll_interval, 10))
                continue

            # -- Still running: wait --
            unknown_count = 0  # reset on non-unknown status
            time.sleep(poll_interval)
            polls += 1

        # ── Job failed — diagnose ──
        log_path = _find_log_file(workspace, software)
        error_code = _diagnose_log(log_path, software=software) if log_path else "unknown_error"

        fix = FIX_STRATEGIES.get(error_code)
        if not fix:
            return {
                "status": "failed",
                "job_id": current_job_id,
                "bohr_job_id": bohr_job_id,
                "retries": retries,
                "error_code": error_code,
                "log_file": log_path,
                "message": (
                    f"Job {current_job_id} failed with error '{error_code}'. "
                    f"No built-in fix strategy. Review the log file and fix manually."
                ),
            }

        retries += 1
        if retries > max_retries:
            break

        # ── Return diagnosis + fix suggestion to the agent ──
        # The agent should: apply the fix, resubmit via MCP, and call job-manager again.
        return {
            "status": "needs_fix",
            "job_id": current_job_id,
            "bohr_job_id": bohr_job_id,
            "retries": retries,
            "error_code": error_code,
            "fix_strategy": fix,
            "log_file": log_path,
            "message": (
                f"Job {current_job_id} failed with '{error_code}' (retry {retries}/{max_retries}). "
                f"Suggested fix: {fix['description']}. "
                f"Apply the fix to input files, re-submit via MCP, then call job-manager again with the new job_id."
            ),
        }

    # Exhausted retries — signal that agent should consider asking human
    return {
        "status": "failed",
        "job_id": current_job_id,
        "bohr_job_id": bohr_job_id,
        "retries": retries,
        "exhausted_retries": True,
        "message": (
            f"Job {current_job_id} failed after {retries} retries (limit: {max_retries}). "
            f"All built-in fix strategies have been attempted. "
            f"Consider asking the human user (ask_human skill) whether to: "
            f"(1) provide modified parameters or suggestions, "
            f"(2) skip this calculation, or "
            f"(3) abort. "
            f"Default behaviour if no human response: skip this calculation and continue."
        ),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resilient job lifecycle manager — monitors a remote calculation job.",
    )
    parser.add_argument("--job_id", required=True, help="Job ID from the MCP submit tool")
    parser.add_argument(
        "--bohr_job_id",
        default=None,
        help=(
            "Explicit Bohrium job ID (from extra_info.bohr_job_id in the submit response).  "
            "Required for dpdispatcher jobs (ABACUS, etc.) whose MCP job_id contains a hex hash."
        ),
    )
    parser.add_argument(
        "--software",
        required=True,
        help="Software name (case-insensitive): dpa, abacus, lammps, cp2k, qe, abinit, orca, gaussian, or any registered async software",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace directory for result downloads (default: current dir)",
    )
    parser.add_argument(
        "--poll_interval",
        type=int,
        default=30,
        help="Seconds between status checks (default: 30)",
    )
    parser.add_argument(
        "--max_retries",
        type=int,
        default=5,
        help="Maximum diagnosis-and-retry cycles (default: 5)",
    )

    args = parser.parse_args()

    result = run_lifecycle(
        job_id=args.job_id,
        software=args.software,
        workspace=args.workspace,
        poll_interval=args.poll_interval,
        max_retries=args.max_retries,
        bohr_job_id=args.bohr_job_id,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
