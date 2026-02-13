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

_THIS_FILE = Path(__file__).resolve()
for parent in (_THIS_FILE.parent, *_THIS_FILE.parents):
    if (parent / "evomaster").is_dir():
        p = str(parent)
        if p not in sys.path:
            sys.path.insert(0, p)
        break

from evomaster.adaptors.calculation.job_service import (
    download_job_file,
    get_file_token,
    get_job_results,
    iterate_job_files,
    query_job_status,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TERMINAL_SUCCESS = frozenset(
    {
        'Done',
        'Success',
        'Finished',
        'Completed',
        'done',
        'success',
        'finished',
        'completed',
    }
)
TERMINAL_FAILURE = frozenset(
    {'Failed', 'Error', 'Cancelled', 'failed', 'error', 'cancelled'}
)
UNKNOWN_STATUSES = frozenset({'Unknown', 'unknown'})

# Built-in fix strategies keyed by canonical error code.
FIX_STRATEGIES: dict[str, dict[str, Any]] = {
    'scf_diverged': {
        'action': 'update_parameter',
        'description': 'SCF not converging — reduce mixing, switch algorithm',
        'params': {'ALGO': 'All', 'AMIX': '0.1', 'BMIX': '0.0001'},
    },
    'scf_diagonalization_error': {
        'action': 'update_parameter',
        'description': 'Diagonalization failure — switch to more robust algorithm',
        'params': {'ALGO': 'Normal', 'PREC': 'Accurate'},
    },
    'kpoints_error': {
        'action': 'reduce_kpoints',
        'description': 'K-point / IBZKPT error — reduce k-mesh density by half',
        'factor': 0.5,
    },
    'grid_too_coarse': {
        'action': 'increase_cutoff',
        'description': 'FFT grid too coarse — increase energy cutoff',
        'increment': 50,
    },
    'lost_atoms': {
        'action': 'reduce_timestep',
        'description': 'Lost atoms in MD — halve the timestep',
        'factor': 0.5,
    },
    'out_of_range': {
        'action': 'reduce_timestep',
        'description': 'Out of range — halve the timestep',
        'factor': 0.5,
    },
    'walltime_exceeded': {
        'action': 'increase_walltime',
        'description': 'Job killed by walltime — double the walltime limit',
        'factor': 2.0,
    },
    'oom_error': {
        'action': 'reduce_parallelism',
        'description': 'Out of memory — reduce parallelism or memory per node',
        'suggestion': 'Reduce NCORE/NPAR or split into smaller systems',
    },
}

# Log file name patterns per software.
# New software can be added here; any unlisted software uses the generic fallback.
LOG_PATTERNS: dict[str, list[str]] = {
    'vasp': ['OUTCAR', 'vasp.out', '*.out'],
    'abacus': ['OUT.ABACUS', 'running_*.log', '*.log'],
    'lammps': ['log.lammps', '*.log'],
    'cp2k': ['*.out', 'cp2k.out', '*.log'],
    'gaussian': ['*.log', '*.out'],
    'qe': ['*.out', '*.log'],
    'abinit': ['*.out', '*.log'],
    'orca': ['*.out', '*.log'],
    'dpa': ['*.log', '*.out', '*.json'],
}

_AUTO_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024

# ---------------------------------------------------------------------------
# Error diagnosis  (reuses log_diagnostics skill logic)
# ---------------------------------------------------------------------------


def _get_log_diagnostics_dir() -> Path | None:
    """Resolve path to log_diagnostics/scripts/ relative to this skill."""
    skill_dir = Path(__file__).resolve().parent.parent  # job-manager/
    log_diag = skill_dir.parent / 'log_diagnostics' / 'scripts'
    return log_diag if log_diag.exists() else None


def _diagnose_log(log_path: str, software: str = '') -> str:
    """Run log_diagnostics analysis and return a canonical error code."""
    diag_dir = _get_log_diagnostics_dir()
    if diag_dir is None:
        return 'unknown_error'

    # Import the analysis functions from the sibling skill
    if str(diag_dir) not in sys.path:
        sys.path.insert(0, str(diag_dir))
    try:
        from extract_error import (  # type: ignore[import-untyped]
            analyze_lammps_log,
            analyze_vasp_log,
        )

        sw_lower = software.lower()
        lower = log_path.lower()
        # DPA / MLP jobs: typically output JSON; no specialised analyser yet
        if sw_lower == 'dpa':
            return 'unknown_error'
        if sw_lower in ('vasp', 'abacus', 'abinit', 'qe') or any(
            tok in lower for tok in ('outcar', 'vasp', 'abacus', 'abinit', 'qe')
        ):
            return analyze_vasp_log(log_path)
        if sw_lower in ('lammps',) or 'lammps' in lower:
            return analyze_lammps_log(log_path)
        # Generic fallback: try VASP-style analysis first
        return analyze_vasp_log(log_path)
    except Exception:
        return 'unknown_error'


def _find_log_file(workspace: str, software: str) -> str | None:
    """Find the most recent log file in *workspace* for *software*.

    Falls back to generic ``*.log`` / ``*.out`` patterns for any software
    not listed in LOG_PATTERNS.
    """
    ws = Path(workspace)
    if not ws.exists():
        return None
    patterns = LOG_PATTERNS.get(software.lower(), ['*.log', '*.out', '*.json'])
    for pat in patterns:
        matches = sorted(ws.rglob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return str(matches[0])
    return None


# ---------------------------------------------------------------------------
# OSS / result download
# ---------------------------------------------------------------------------


def _download_from_results_txt(
    workspace: str,
    bohr_job_id: str | None,
    download_tag: str | None = None,
    access_key: str | None = None,
) -> dict[str, Any]:
    """Download files referenced by results.txt (aligned with legacy job.py)."""
    if not bohr_job_id:
        return {
            'status': 'skip',
            'reason': 'bohr_job_id missing for results.txt download',
        }

    tag_raw = (download_tag or str(bohr_job_id) or "unknown_job").strip()
    safe_job = re.sub(r"[^\w.\-]", "_", tag_raw)[:80] or "unknown_job"
    run_stamp = time.strftime("%Y%m%d_%H%M%S")
    # Use per-job + per-run directory to avoid cross-task overwrite.
    download_dir = Path(workspace)/ "calculation_results" / f"run_{safe_job}_{run_stamp}"
    download_dir.mkdir(parents=True, exist_ok=True)

    results_txt_local = download_dir / "result_0_results.txt"
    download_job_file("results.txt", bohr_job_id, results_txt_local, access_key=access_key)

    parsed: Any
    text = results_txt_local.read_text(encoding="utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = {}

    if not isinstance(parsed, dict):
        return {
            "status": "failed",
            "downloaded": [results_txt_local.resolve().as_posix()],
            "download_dir": download_dir.resolve().as_posix(),
            "download_errors": ["results.txt parsed payload is not a dict"],
        }

    def _extract_path_from_py_reduce(v: Any) -> str | None:
        if not isinstance(v, dict):
            return None
        reduce_items = v.get("py/reduce")
        if not isinstance(reduce_items, list) or len(reduce_items) < 2:
            return None
        tuple_item = reduce_items[1]
        if not isinstance(tuple_item, dict):
            return None
        tuple_vals = tuple_item.get("py/tuple")
        if not isinstance(tuple_vals, list) or not tuple_vals:
            return None
        raw = tuple_vals[0]
        if not isinstance(raw, str) or not raw.strip():
            return None
        return raw.replace("\\", "/").strip()

    referenced_files: list[str] = []
    for v in parsed.values():
        if isinstance(v, str) and v.strip():
            if "/" in v or "\\" in v or "." in v:
                referenced_files.append(v.replace("\\", "/").strip())
            continue
        extracted = _extract_path_from_py_reduce(v)
        if extracted:
            referenced_files.append(extracted)

    root_prefix = ""
    try:
        _, token_root_path, _ = get_file_token("", bohr_job_id, access_key=access_key)
        root_prefix = str(token_root_path or "").replace("\\", "/")
        if root_prefix and not root_prefix.endswith("/"):
            root_prefix += "/"
    except Exception:
        root_prefix = ""

    def _to_rel_path(remote_path: str) -> str:
        p = remote_path.replace("\\", "/").strip()
        if root_prefix and p.startswith(root_prefix):
            return p[len(root_prefix):].lstrip("/")
        return p

    size_map: dict[str, int] = {}
    try:
        file_objs = iterate_job_files(bohr_job_id, access_key=access_key)
        for obj in file_objs:
            if not isinstance(obj, dict):
                continue
            p = obj.get('path')
            s = obj.get('size')
            if isinstance(p, str) and isinstance(s, int):
                size_map[p.replace('\\', '/')] = s
    except Exception:
        size_map = {}

    downloaded: list[str] = [results_txt_local.resolve().as_posix()]
    skipped: list[str] = []
    errors: list[str] = []

    for i, remote_path in enumerate(referenced_files, start=1):
        if not isinstance(remote_path, str) or not remote_path.strip():
            continue
        rp = remote_path.strip()
        rel_rp = _to_rel_path(rp)
        size = size_map.get(rp.replace("\\", "/"))
        if isinstance(size, int) and size > _AUTO_DOWNLOAD_MAX_BYTES:
            skipped.append(f"{rp}: skipped by size policy ({size} bytes)")
            continue
        segment = rp.rsplit('/', 1)[-1] or f"artifact_{i}"
        segment = re.sub(r'[^\w.\-]', '_', segment) or f"artifact_{i}"
        dest = download_dir / f"result_{i}_{segment}"
        try:
            path = download_job_file(rel_rp, bohr_job_id, dest, access_key=access_key)
            downloaded.append(path.resolve().as_posix())
        except Exception as exc:
            errors.append(f"{rp}: {exc}")

    info: dict[str, Any] = {
        'downloaded': downloaded,
        'download_dir': download_dir.resolve().as_posix(),
    }
    if skipped:
        info['download_skipped'] = skipped
    if errors:
        info['download_errors'] = errors
    info["referenced_files"] = referenced_files
    return info


# ---------------------------------------------------------------------------
# Job status & results  (via OpenAPI requests)
# ---------------------------------------------------------------------------


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
    download_tag: str | None = None,
    access_key: str | None = None,
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
            status = str(
                query_job_status(
                    current_job_id,
                    bohr_job_id=bohr_job_id,
                    software=None,
                    access_key=access_key,
                )
            )

            # -- Success --
            if status in TERMINAL_SUCCESS:
                raw_results = get_job_results(
                    current_job_id,
                    bohr_job_id=bohr_job_id,
                    software=None,
                    access_key=access_key,
                )
                results = (
                    raw_results
                    if isinstance(raw_results, dict)
                    else {'raw': raw_results}
                )
                resolved_bohr_job_id = bohr_job_id or (
                    results.get('bohr_job_id')
                    if isinstance(results.get('bohr_job_id'), str)
                    else None
                )
                download_info: dict[str, Any] = {}
                if workspace:
                    download_info['results_txt_downloads'] = _download_from_results_txt(
                        workspace,
                        resolved_bohr_job_id,
                        download_tag=download_tag,
                        access_key=access_key,
                    )

                # Check whether downloads actually succeeded.
                # If nothing was downloaded but errors exist, report partial failure
                # so the outer exit_code != 0 mechanism marks status="error".
                total_downloaded: list[str] = []
                total_errors: list[str] = []
                for section in download_info.values():
                    if isinstance(section, dict):
                        total_downloaded.extend(section.get('downloaded') or [])
                        total_errors.extend(section.get('download_errors') or [])

                if total_errors and not total_downloaded:
                    return {
                        'status': 'failed',
                        'job_id': current_job_id,
                        'bohr_job_id': resolved_bohr_job_id,
                        'retries': retries,
                        'results': results,
                        'downloads': download_info,
                        'message': (
                            f"Job {current_job_id} finished but all result downloads failed "
                            f"({len(total_errors)} errors). Check download_errors for details."
                        ),
                    }

                out_status = 'success' if not total_errors else 'partial_success'
                return {
                    'status': out_status,
                    'job_id': current_job_id,
                    'bohr_job_id': resolved_bohr_job_id,
                    'retries': retries,
                    'results': results,
                    'downloads': download_info,
                    'message': (
                        f"Job {current_job_id} completed successfully."
                        if out_status == 'success'
                        else f"Job {current_job_id} completed but {len(total_errors)} file(s) failed to download."
                    ),
                }

            # -- Failure --
            if status in TERMINAL_FAILURE or status.startswith('Error:'):
                break

            # -- Unknown: retry a few times then give up --
            if status in UNKNOWN_STATUSES:
                unknown_count += 1
                if unknown_count >= max_unknown:
                    return {
                        'status': 'unknown',
                        'job_id': current_job_id,
                        'bohr_job_id': bohr_job_id,
                        'retries': retries,
                        'message': (
                            f"Job status returned 'Unknown' {unknown_count} times.  "
                            'Possible causes: (1) Bohrium access_key not set or invalid — '
                            'check BOHRIUM_ACCESS_KEY in .env; (2) job ID could not be resolved '
                            '— for ABACUS / dpdispatcher jobs, pass --bohr_job_id explicitly '
                            '(from extra_info.bohr_job_id in the submit response).'
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
        error_code = (
            _diagnose_log(log_path, software=software) if log_path else 'unknown_error'
        )

        fix = FIX_STRATEGIES.get(error_code)
        if not fix:
            return {
                'status': 'failed',
                'job_id': current_job_id,
                'bohr_job_id': bohr_job_id,
                'retries': retries,
                'error_code': error_code,
                'log_file': log_path,
                'message': (
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
            'status': 'needs_fix',
            'job_id': current_job_id,
            'bohr_job_id': bohr_job_id,
            'retries': retries,
            'error_code': error_code,
            'fix_strategy': fix,
            'log_file': log_path,
            'message': (
                f"Job {current_job_id} failed with '{error_code}' (retry {retries}/{max_retries}). "
                f"Suggested fix: {fix['description']}. "
                f"Apply the fix to input files, re-submit via MCP, then call job-manager again with the new job_id."
            ),
        }

    # Exhausted retries — signal that agent should consider asking human
    return {
        'status': 'failed',
        'job_id': current_job_id,
        'bohr_job_id': bohr_job_id,
        'retries': retries,
        'exhausted_retries': True,
        'message': (
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
        description='Resilient job lifecycle manager — monitors a remote calculation job.',
    )
    parser.add_argument(
        '--job_id', required=True, help='Job ID from the MCP submit tool'
    )
    parser.add_argument(
        '--bohr_job_id',
        default=None,
        help=(
            'Explicit Bohrium job ID (from extra_info.bohr_job_id in the submit response).  '
            'Required for dpdispatcher jobs (ABACUS, etc.) whose MCP job_id contains a hex hash.'
        ),
    )
    parser.add_argument(
        '--software',
        required=True,
        help='Software name (case-insensitive): dpa, abacus, lammps, cp2k, qe, abinit, orca, gaussian, or any registered async software',
    )
    parser.add_argument(
        '--workspace',
        default='.',
        help='Workspace directory for result downloads (default: current dir)',
    )
    parser.add_argument(
        '--poll_interval',
        type=int,
        default=30,
        help='Seconds between status checks (default: 30)',
    )
    parser.add_argument(
        '--max_retries',
        type=int,
        default=5,
        help='Maximum diagnosis-and-retry cycles (default: 5)',
    )
    parser.add_argument(
        '--access_key',
        default=None,
        help='Bohrium access key (optional; else uses BOHRIUM_ACCESS_KEY env). Passed from chat session when available.',
    )
    parser.add_argument(
        "--download_tag",
        default=None,
        help=(
            "Folder tag for downloaded results. "
            "Use this to separate outputs across tasks; timestamp subfolder is always added."
        ),
    )
    args = parser.parse_args()

    result = run_lifecycle(
        job_id=args.job_id,
        software=args.software,
        workspace=args.workspace,
        poll_interval=args.poll_interval,
        max_retries=args.max_retries,
        bohr_job_id=args.bohr_job_id,
        download_tag=args.download_tag,
        access_key=args.access_key,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get('status') == 'success' else 1)


if __name__ == '__main__':
    main()
