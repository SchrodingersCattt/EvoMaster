"""monitor_job — built-in tool replacing the job-manager skill script.

Runs entirely inside the agent backend process so it can import
evomaster.adaptors.calculation.job_service without shipping source code to
the remote Bohrium node.

Workflow
--------
1. Poll Bohrium OpenAPI until the job reaches a terminal state.
2. On success: download result files via the NAS file-token API.
   - Local session  → write directly to ``workspace/calculation_results/``.
   - SSH session    → download to a temp dir on the backend, then SFTP-push
                      each file to the container's ``workspace/``, then clean up.
3. On failure: diagnose the error log and return a fix strategy.

Files larger than ``_AUTO_DOWNLOAD_MAX_BYTES`` (100 MB) are skipped; their
paths are listed in ``download_skipped`` so the user can fetch them manually
using the ``bohr_job_id``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from ..base import BaseTool, BaseToolParams

if TYPE_CHECKING:
    from evomaster.agent.session import BaseSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (mirrors run_resilient_job.py)
# ---------------------------------------------------------------------------

TERMINAL_SUCCESS = frozenset(
    {'Done', 'Success', 'Finished', 'Completed', 'done', 'success', 'finished', 'completed'}
)
TERMINAL_FAILURE = frozenset({'Failed', 'Error', 'Cancelled', 'failed', 'error', 'cancelled'})
UNKNOWN_STATUSES = frozenset({'Unknown', 'unknown'})

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

_AUTO_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024  # 100 MB

# ---------------------------------------------------------------------------
# Log diagnostics (inline — avoids importing the skill script)
# ---------------------------------------------------------------------------

def _find_log_diagnostics_scripts() -> Path | None:
    """Locate log_diagnostics/scripts/ relative to the mat_master skills tree."""
    # Try to find from evomaster package location upward to project root
    pkg = Path(__file__).resolve().parent.parent.parent.parent  # project root
    candidate = pkg / 'playground' / 'mat_master' / 'skills' / 'log_diagnostics' / 'scripts'
    return candidate if candidate.is_dir() else None


def _diagnose_log(log_path: str, software: str = '') -> str:
    diag_dir = _find_log_diagnostics_scripts()
    if diag_dir is None:
        return 'unknown_error'
    if str(diag_dir) not in sys.path:
        sys.path.insert(0, str(diag_dir))
    try:
        from extract_error import (  # type: ignore[import-untyped]
            analyze_lammps_log,
            analyze_vasp_log,
        )
        sw = software.lower()
        lower = log_path.lower()
        if sw == 'dpa':
            return 'unknown_error'
        if sw in ('vasp', 'abacus', 'abinit', 'qe') or any(
            tok in lower for tok in ('outcar', 'vasp', 'abacus', 'abinit', 'qe')
        ):
            return analyze_vasp_log(log_path)
        if sw == 'lammps' or 'lammps' in lower:
            return analyze_lammps_log(log_path)
        return analyze_vasp_log(log_path)
    except Exception:
        return 'unknown_error'


def _find_log_file_local(workspace: str, software: str) -> str | None:
    ws = Path(workspace)
    if not ws.exists():
        return None
    patterns = LOG_PATTERNS.get(software.lower(), ['*.log', '*.out', '*.json'])
    for pat in patterns:
        matches = sorted(ws.rglob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return str(matches[0])
    return None


def _find_log_file_remote(session: 'BaseSession', workspace: str, software: str) -> str | None:
    """Return path of the most-recently-modified log on the remote node, or None."""
    try:
        from evomaster.agent.session.ssh import SSHSession
        if not isinstance(session, SSHSession):
            return None
        patterns = LOG_PATTERNS.get(software.lower(), ['*.log', '*.out', '*.json'])
        for pat in patterns:
            # Use find to locate files matching the pattern
            result = session._env.ssh_exec(
                f"find {workspace!r} -name {pat!r} -type f 2>/dev/null "
                f"| xargs ls -t 2>/dev/null | head -1"
            )
            path = (result or '').strip()
            if path:
                return path
    except Exception:
        pass
    return None

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download_results_to_local_dir(
    download_dir: Path,
    bohr_job_id: str,
    access_key: str | None,
) -> dict[str, Any]:
    """Download files referenced by results.txt into *download_dir* (local path).

    Returns a dict with 'downloaded', 'download_dir', and optionally
    'download_skipped' / 'download_errors' / 'referenced_files'.
    """
    from evomaster.adaptors.calculation.job_service import (
        download_job_file,
        get_file_token,
        iterate_job_files,
    )

    download_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: fetch results.txt
    results_txt_local = download_dir / 'result_0_results.txt'
    try:
        download_job_file('results.txt', bohr_job_id, results_txt_local, access_key=access_key)
    except Exception as exc:
        return {
            'status': 'failed',
            'download_errors': [f'results.txt: {exc}'],
        }

    text = results_txt_local.read_text(encoding='utf-8', errors='replace')
    try:
        parsed: Any = json.loads(text)
    except Exception:
        parsed = {}

    if not isinstance(parsed, dict):
        return {
            'downloaded': [results_txt_local.resolve().as_posix()],
            'download_dir': download_dir.resolve().as_posix(),
            'download_errors': ['results.txt payload is not a JSON object'],
        }

    # Step 2: extract referenced file paths
    def _extract_path_from_py_reduce(v: Any) -> str | None:
        if not isinstance(v, dict):
            return None
        reduce_items = v.get('py/reduce')
        if not isinstance(reduce_items, list) or len(reduce_items) < 2:
            return None
        tuple_item = reduce_items[1]
        if not isinstance(tuple_item, dict):
            return None
        tuple_vals = tuple_item.get('py/tuple')
        if not isinstance(tuple_vals, list) or not tuple_vals:
            return None
        parts: list[str] = []
        for item in tuple_vals:
            if not isinstance(item, str):
                continue
            segment = item.replace('\\', '/').strip()
            if segment:
                parts.append(segment)
        if not parts:
            return None
        return str(PurePosixPath(parts[0], *parts[1:]))

    referenced_files: list[str] = []
    for v in parsed.values():
        if isinstance(v, str) and v.strip():
            if '/' in v or '\\' in v or '.' in v:
                referenced_files.append(v.replace('\\', '/').strip())
            continue
        extracted = _extract_path_from_py_reduce(v)
        if extracted:
            referenced_files.append(extracted)

    # Step 3: resolve root prefix for path normalisation
    root_prefix = ''
    try:
        _, token_root_path, _ = get_file_token('', bohr_job_id, access_key=access_key)
        root_prefix = str(token_root_path or '').replace('\\', '/')
        if root_prefix and not root_prefix.endswith('/'):
            root_prefix += '/'
    except Exception:
        root_prefix = ''

    def _to_rel(remote_path: str) -> str:
        p = remote_path.replace('\\', '/').strip()
        if root_prefix and p.startswith(root_prefix):
            return p[len(root_prefix):].lstrip('/')
        return p

    # Step 4: get file sizes for size-gating
    size_map: dict[str, int] = {}
    try:
        for obj in iterate_job_files(bohr_job_id, access_key=access_key):
            if not isinstance(obj, dict):
                continue
            p = obj.get('path')
            s = obj.get('size')
            if isinstance(p, str) and isinstance(s, int):
                size_map[p.replace('\\', '/')] = s
    except Exception:
        pass

    # Step 5: download each referenced file
    downloaded: list[str] = [results_txt_local.resolve().as_posix()]
    skipped: list[str] = []
    errors: list[str] = []

    for i, remote_path in enumerate(referenced_files, start=1):
        if not isinstance(remote_path, str) or not remote_path.strip():
            continue
        rp = remote_path.strip()
        size = size_map.get(rp.replace('\\', '/'))
        if isinstance(size, int) and size > _AUTO_DOWNLOAD_MAX_BYTES:
            skipped.append(f'{rp}: skipped ({size} bytes > {_AUTO_DOWNLOAD_MAX_BYTES})')
            continue
        segment = rp.rsplit('/', 1)[-1] or f'artifact_{i}'
        segment = re.sub(r'[^\w.\-]', '_', segment) or f'artifact_{i}'
        dest = download_dir / f'result_{i}_{segment}'
        try:
            path = download_job_file(_to_rel(rp), bohr_job_id, dest, access_key=access_key)
            downloaded.append(path.resolve().as_posix())
        except Exception as exc:
            errors.append(f'{rp}: {exc}')

    info: dict[str, Any] = {
        'downloaded': downloaded,
        'download_dir': download_dir.resolve().as_posix(),
        'referenced_files': referenced_files,
    }
    if skipped:
        info['download_skipped'] = skipped
    if errors:
        info['download_errors'] = errors
    return info


def _sftp_push_directory(session: 'BaseSession', local_dir: Path, remote_dir: str) -> list[str]:
    """Upload all files in *local_dir* to *remote_dir* on the SSH node.

    Returns list of remote paths uploaded.
    """
    from evomaster.agent.session.ssh import SSHSession
    if not isinstance(session, SSHSession):
        return []
    env = session._env
    pushed: list[str] = []
    for local_file in local_dir.rglob('*'):
        if not local_file.is_file():
            continue
        rel = local_file.relative_to(local_dir).as_posix()
        remote_path = f'{remote_dir}/{rel}'
        try:
            env.upload_file(str(local_file), remote_path)
            pushed.append(remote_path)
        except Exception as exc:
            logger.warning('monitor_job: SFTP push failed %s → %s: %s', local_file, remote_path, exc)
    return pushed

# ---------------------------------------------------------------------------
# Core lifecycle (backend-native version of run_lifecycle)
# ---------------------------------------------------------------------------

def _run_lifecycle(
    job_id: str,
    software: str,
    workspace: str,
    session: 'BaseSession',
    poll_interval: int = 30,
    max_retries: int = 5,
    bohr_job_id: str | None = None,
    download_tag: str | None = None,
    access_key: str | None = None,
) -> dict[str, Any]:
    from evomaster.adaptors.calculation.job_service import get_job_results, query_job_status
    from evomaster.agent.session.ssh import SSHSession

    is_ssh = isinstance(session, SSHSession)

    current_job_id = job_id
    retries = 0
    max_polls = 720
    unknown_count = 0
    max_unknown = 3

    while retries <= max_retries:
        polls = 0
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
                results = raw_results if isinstance(raw_results, dict) else {'raw': raw_results}
                resolved_bid = bohr_job_id or (
                    results.get('bohr_job_id')
                    if isinstance(results.get('bohr_job_id'), str)
                    else None
                )

                download_info: dict[str, Any] = {}
                if workspace and resolved_bid:
                    tag_raw = (download_tag or str(resolved_bid) or 'unknown_job').strip()
                    safe_job = re.sub(r'[^\w.\-]', '_', tag_raw)[:80] or 'unknown_job'
                    run_stamp = time.strftime('%Y%m%d_%H%M%S')
                    subdir_name = f'run_{safe_job}_{run_stamp}'

                    if is_ssh:
                        # Download to backend temp dir, then SFTP push to container
                        tmp_root = Path(tempfile.mkdtemp(prefix='monitor_job_'))
                        try:
                            dl_info = _download_results_to_local_dir(
                                tmp_root, resolved_bid, access_key
                            )
                            remote_calc_dir = (
                                f'{workspace}/calculation_results/{subdir_name}'
                            )
                            pushed = _sftp_push_directory(session, tmp_root, remote_calc_dir)
                            dl_info['remote_download_dir'] = remote_calc_dir
                            dl_info['remote_files'] = pushed
                            # Replace local paths with remote paths in 'downloaded'
                            dl_info['downloaded'] = pushed
                            download_info['results_txt_downloads'] = dl_info
                        finally:
                            shutil.rmtree(tmp_root, ignore_errors=True)
                    else:
                        local_calc_dir = (
                            Path(workspace) / 'calculation_results' / subdir_name
                        ).resolve()
                        dl_info = _download_results_to_local_dir(
                            local_calc_dir, resolved_bid, access_key
                        )
                        download_info['results_txt_downloads'] = dl_info

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
                        'bohr_job_id': resolved_bid,
                        'retries': retries,
                        'results': results,
                        'downloads': download_info,
                        'message': (
                            f'Job {current_job_id} finished but all result downloads failed '
                            f'({len(total_errors)} errors). Check download_errors for details.'
                        ),
                    }

                out_status = 'success' if not total_errors else 'partial_success'
                return {
                    'status': out_status,
                    'job_id': current_job_id,
                    'bohr_job_id': resolved_bid,
                    'retries': retries,
                    'results': results,
                    'downloads': download_info,
                    'message': (
                        f'Job {current_job_id} completed successfully.'
                        if out_status == 'success'
                        else f'Job {current_job_id} completed but {len(total_errors)} file(s) failed to download.'
                    ),
                }

            # -- Failure --
            if status in TERMINAL_FAILURE or status.startswith('Error:'):
                break

            # -- Unknown --
            if status in UNKNOWN_STATUSES:
                unknown_count += 1
                if unknown_count >= max_unknown:
                    return {
                        'status': 'unknown',
                        'job_id': current_job_id,
                        'bohr_job_id': bohr_job_id,
                        'retries': retries,
                        'message': (
                            f"Job status returned 'Unknown' {unknown_count} times. "
                            'Possible causes: (1) Bohrium access_key not set or invalid — '
                            'check BOHRIUM_ACCESS_KEY in .env; (2) job ID could not be resolved '
                            '— for ABACUS / dpdispatcher jobs, pass bohr_job_id explicitly '
                            '(from extra_info.bohr_job_id in the submit response).'
                        ),
                    }
                time.sleep(min(poll_interval, 10))
                continue

            # -- Still running --
            unknown_count = 0
            time.sleep(poll_interval)
            polls += 1

        # ── Failed — diagnose ──
        if is_ssh:
            log_path = _find_log_file_remote(session, workspace, software)
            if log_path:
                try:
                    log_content = session._env.read_file_content(log_path)
                    tmp_log = Path(tempfile.mktemp(suffix='.log'))
                    tmp_log.write_text(log_content, encoding='utf-8', errors='replace')
                    error_code = _diagnose_log(str(tmp_log), software=software)
                    tmp_log.unlink(missing_ok=True)
                except Exception:
                    error_code = 'unknown_error'
            else:
                error_code = 'unknown_error'
        else:
            log_path = _find_log_file_local(workspace, software)
            error_code = _diagnose_log(log_path, software=software) if log_path else 'unknown_error'

        fix = FIX_STRATEGIES.get(error_code)
        if not fix:
            return {
                'status': 'failed',
                'job_id': current_job_id,
                'bohr_job_id': bohr_job_id,
                'retries': retries,
                'error_code': error_code,
                'log_file': log_path if not is_ssh else None,
                'message': (
                    f"Job {current_job_id} failed with error '{error_code}'. "
                    'No built-in fix strategy. Review the log file and fix manually.'
                ),
            }

        retries += 1
        if retries > max_retries:
            break

        return {
            'status': 'needs_fix',
            'job_id': current_job_id,
            'bohr_job_id': bohr_job_id,
            'retries': retries,
            'error_code': error_code,
            'fix_strategy': fix,
            'log_file': log_path if not is_ssh else None,
            'message': (
                f"Job {current_job_id} failed with '{error_code}' (retry {retries}/{max_retries}). "
                f"Suggested fix: {fix['description']}. "
                'Apply the fix to input files, re-submit via MCP, then call monitor_job again with the new job_id.'
            ),
        }

    return {
        'status': 'failed',
        'job_id': current_job_id,
        'bohr_job_id': bohr_job_id,
        'retries': retries,
        'exhausted_retries': True,
        'message': (
            f'Job {current_job_id} failed after {retries} retries (limit: {max_retries}). '
            'All built-in fix strategies have been attempted. '
            'Consider asking the human user (ask_human skill) whether to: '
            '(1) provide modified parameters or suggestions, '
            '(2) skip this calculation, or '
            '(3) abort. '
            'Default behaviour if no human response: skip this calculation and continue.'
        ),
    }


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

class MonitorJobParams(BaseToolParams):
    """Monitor a submitted remote calculation job (DPA, ABACUS, LAMMPS, CP2K, QE, ABINIT, ORCA,
    Gaussian, etc.) until it reaches a terminal state.

    Polls the Bohrium OpenAPI for job status, downloads result files on success, and diagnoses
    errors on failure. Call ONCE after submitting a job via MCP — this tool blocks until the job
    completes or all retries are exhausted.

    On success: returns status + downloaded file paths.
    On failure: returns error_code + fix_strategy. Apply the fix, re-submit via MCP, and call
    monitor_job again with the new job_id.
    """

    name: ClassVar[str] = 'monitor_job'

    job_id: str = Field(description='Job ID returned by the MCP submit tool.')
    software: str = Field(
        description=(
            'Software name (case-insensitive): dpa, abacus, lammps, cp2k, qe, abinit, orca, '
            'gaussian, or any registered async software.'
        )
    )
    workspace: str = Field(
        default='.',
        description='Workspace directory for result downloads. Defaults to current directory.',
    )
    bohr_job_id: str | None = Field(
        default=None,
        description=(
            'Explicit Bohrium OpenAPI job ID (from extra_info.bohr_job_id in submit response). '
            'Required for dpdispatcher-based jobs (ABACUS, etc.) whose MCP job_id contains a hex hash.'
        ),
    )
    poll_interval: int = Field(default=30, description='Seconds between status checks.')
    max_retries: int = Field(default=5, description='Maximum diagnosis-and-retry cycles.')
    access_key: str | None = Field(
        default=None,
        description='Bohrium access key. Falls back to BOHRIUM_ACCESS_KEY env var.',
    )
    download_tag: str | None = Field(
        default=None,
        description='Folder tag for downloaded results (timestamp subfolder always added).',
    )


class MonitorJobTool(BaseTool):
    """Built-in tool: monitor a remote Bohrium calculation job."""

    name: ClassVar[str] = 'monitor_job'
    params_class: ClassVar[type[BaseToolParams]] = MonitorJobParams

    def execute(self, session: 'BaseSession', args_json: str) -> tuple[str, dict[str, Any]]:
        try:
            params = self.parse_params(args_json)
        except Exception as exc:
            return f'Parameter validation error: {exc}', {'error': str(exc)}

        assert isinstance(params, MonitorJobParams)

        # Inject access_key from session._bohrium_credentials if not explicitly provided
        access_key = params.access_key
        if not access_key:
            creds = getattr(session, '_bohrium_credentials', None)
            if isinstance(creds, dict):
                access_key = creds.get('access_key') or creds.get('bohrium_access_key')
            if not access_key:
                access_key = os.environ.get('BOHRIUM_ACCESS_KEY')

        result = _run_lifecycle(
            job_id=params.job_id,
            software=params.software,
            workspace=params.workspace,
            session=session,
            poll_interval=params.poll_interval,
            max_retries=params.max_retries,
            bohr_job_id=params.bohr_job_id,
            download_tag=params.download_tag,
            access_key=access_key,
        )

        output = json.dumps(result, indent=2, ensure_ascii=False)
        info = {
            'status': result.get('status'),
            'job_id': result.get('job_id'),
            'bohr_job_id': result.get('bohr_job_id'),
        }
        return output, info
