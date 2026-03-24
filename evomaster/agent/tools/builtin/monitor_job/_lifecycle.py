"""Core poll loop and success/failure handling for monitor_job."""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evomaster.adaptors.calculation.job_service import (
    download_job_file,
    get_job_results,
    query_job_status,
)
from evomaster.agent.session.ssh import SSHSession

from ._constants import (
    _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS,
    _LOG_PER_FILE_MAX_CHARS,
    _MAX_FAILURE_CONFIRMS,
    TERMINAL_FAILURE,
    TERMINAL_SUCCESS,
    UNKNOWN_STATUSES,
)
from ._download import _download_results_to_local_dir, _sftp_push_directory
from ._llm import _terminate_job_if_needed
from ._logs import (
    _find_log_file_local,
    _find_log_file_remote,
    _find_log_files_from_job,
    _read_log_tail,
    _read_log_tail_remote,
    run_monitor_decision_once,
)

if TYPE_CHECKING:
    from evomaster.agent.session import BaseSession

logger = logging.getLogger(__name__)


def _llm_decision_interval_at_poll(poll_index: int, base_interval: int) -> int:
    """Return the effective decision_check_interval at this poll (progressive schedule).

    - polls 0~9 (~5 min at 30s interval): every 5 polls → 1 LLM decision
    - polls 10~39 (~15 min): every 10 polls
    - polls 40+: every base_interval polls (default 10, ~5 min at 30s)
    """
    if poll_index < 10:
        return 5
    if poll_index < 40:
        return 10
    return max(1, base_interval)


def _sleep_until_stop(seconds: float, stop_event: Any) -> bool:
    """Sleep up to `seconds`; return True if stop was requested (caller should exit)."""
    is_set = getattr(stop_event, 'is_set', None)
    if not callable(is_set):
        time.sleep(seconds)
        return False
    end = time.time() + seconds
    while time.time() < end:
        if stop_event.is_set():
            return True
        time.sleep(min(1.0, max(0.1, end - time.time())))
    return False


def _run_lifecycle(
    job_id: str,
    software: str,
    workspace: str,
    session: BaseSession,
    poll_interval: int = 30,
    bohr_job_id: str | None = None,
    download_tag: str | None = None,
    access_key: str | None = None,
    task_intent: str | None = None,
    llm_decision_mode: str = 'off',
    llm_model_alias: str | None = None,
    llm_timeout_seconds: int = _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS,
    decision_check_interval: int = 10,
    max_polls_per_call: int | None = None,
    stop_event: Any = None,
) -> dict[str, Any]:
    is_ssh = isinstance(session, SSHSession)

    current_job_id = job_id
    max_polls = (
        min(720, max(1, int(max_polls_per_call)))
        if max_polls_per_call is not None
        else 720
    )
    unknown_count = 0
    max_unknown = 3
    failure_confirm_count = 0

    polls = 0
    llm_decision_history: list[dict[str, Any]] = []
    last_llm_decision: dict[str, Any] | None = None
    while polls < max_polls:
        if stop_event and getattr(stop_event, 'is_set', None) and stop_event.is_set():
            return {
                'status': 'cancelled',
                'job_id': current_job_id,
                'bohr_job_id': bohr_job_id,
                'message': 'User requested stop.',
                'llm_decision_history': llm_decision_history,
            }
        status = str(
            query_job_status(
                current_job_id,
                bohr_job_id=bohr_job_id,
                software=None,
                access_key=access_key,
            )
        )

        if (
            llm_decision_mode != 'off'
            and status not in TERMINAL_SUCCESS
            and status not in TERMINAL_FAILURE
        ):
            effective_interval = _llm_decision_interval_at_poll(
                polls, max(1, decision_check_interval)
            )
            if polls == 0 or polls % effective_interval == 0:
                try:
                    decision_data = run_monitor_decision_once(
                        job_id=current_job_id,
                        software=software,
                        bohr_job_id=bohr_job_id,
                        access_key=access_key,
                        poll_index=polls,
                        task_intent=task_intent,
                        llm_model_alias=llm_model_alias,
                        llm_timeout_seconds=llm_timeout_seconds,
                        enable_llm_decision=True,
                        status_override=status,
                    )
                    llm_decision_history.append(decision_data)
                    last_llm_decision = decision_data
                    llm_decision = decision_data.get('decision', {})
                    if (
                        llm_decision_mode == 'auto_terminate'
                        and isinstance(llm_decision, dict)
                        and str(llm_decision.get('decision', '')).lower() == 'terminate'
                    ):
                        terminate_result = _terminate_job_if_needed(
                            bohr_job_id=bohr_job_id,
                            access_key=access_key,
                        )
                        return {
                            'status': 'terminated',
                            'job_id': current_job_id,
                            'bohr_job_id': bohr_job_id,
                            'message': 'LLM 判定需终止任务，已执行终止流程。',
                            'llm_decision': llm_decision,
                            'llm_snapshot': decision_data.get('snapshot'),
                            'llm_decision_history': llm_decision_history,
                            'termination': terminate_result,
                        }
                except Exception as exc:
                    logger.warning('monitor_job: llm decision failed: %s', exc)

        # -- Success --
        if status in TERMINAL_SUCCESS:
            raw_results = get_job_results(
                current_job_id,
                bohr_job_id=bohr_job_id,
                software=None,
                access_key=access_key,
            )
            results = (
                raw_results if isinstance(raw_results, dict) else {'raw': raw_results}
            )
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
                        pushed = _sftp_push_directory(
                            session, tmp_root, remote_calc_dir
                        )
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
                    'results': results,
                    'downloads': download_info,
                    'llm_decision': last_llm_decision,
                    'llm_decision_history': llm_decision_history,
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
                'results': results,
                'downloads': download_info,
                'llm_decision': last_llm_decision,
                'llm_decision_history': llm_decision_history,
                'message': (
                    f'Job {current_job_id} completed successfully.'
                    if out_status == 'success'
                    else f'Job {current_job_id} completed but {len(total_errors)} file(s) failed to download.'
                ),
            }

        # -- Failure (with confirmation to filter transient API/network errors) --
        if status in TERMINAL_FAILURE or status.startswith('Error:'):
            failure_confirm_count += 1
            logger.warning(
                'monitor_job: failure status=%s (confirm %d/%d) job_id=%s',
                status,
                failure_confirm_count,
                _MAX_FAILURE_CONFIRMS,
                current_job_id,
            )
            if failure_confirm_count >= _MAX_FAILURE_CONFIRMS:
                break  # Confirmed failure — proceed to log-tail and return
            if _sleep_until_stop(min(poll_interval, 10), stop_event):
                return {
                    'status': 'cancelled',
                    'job_id': current_job_id,
                    'bohr_job_id': bohr_job_id,
                    'message': 'User requested stop.',
                    'llm_decision_history': llm_decision_history,
                }
            continue

        # -- Unknown --
        if status in UNKNOWN_STATUSES:
            unknown_count += 1
            if unknown_count >= max_unknown:
                return {
                    'status': 'unknown',
                    'job_id': current_job_id,
                    'bohr_job_id': bohr_job_id,
                    'llm_decision': last_llm_decision,
                    'llm_decision_history': llm_decision_history,
                    'message': (
                        f"Job status returned 'Unknown' {unknown_count} times. "
                        'Possible causes: (1) Bohrium access_key not set or invalid — '
                        'check BOHRIUM_ACCESS_KEY in .env; (2) job ID could not be resolved '
                        '— for ABACUS / dpdispatcher jobs, pass bohr_job_id explicitly '
                        '(from extra_info.bohr_job_id in the submit response).'
                    ),
                }
            if _sleep_until_stop(min(poll_interval, 10), stop_event):
                return {
                    'status': 'cancelled',
                    'job_id': current_job_id,
                    'bohr_job_id': bohr_job_id,
                    'message': 'User requested stop.',
                    'llm_decision_history': llm_decision_history,
                }
            continue

        # -- Still running: reset failure counter --
        failure_confirm_count = 0
        unknown_count = 0
        # 已达本次轮询上限且将返回 running（挂起恢复）时不 sleep，尽快返回；否则 sleep 后再下一轮
        if max_polls_per_call is None or (polls + 1) < max_polls:
            if _sleep_until_stop(poll_interval, stop_event):
                return {
                    'status': 'cancelled',
                    'job_id': current_job_id,
                    'bohr_job_id': bohr_job_id,
                    'message': 'User requested stop.',
                    'llm_decision_history': llm_decision_history,
                }
        polls += 1

    # ── Loop ended: either confirmed failure (break) or max_polls exceeded ──
    # If we exited because polls >= max_polls, job was still running.
    # Unified path: always return status="running" so the caller knows the job
    # is alive and can decide to re-poll or finish with task_completed=partial.
    if failure_confirm_count < _MAX_FAILURE_CONFIRMS:
        effective_interval = poll_interval
        if last_llm_decision and isinstance(
            last_llm_decision.get('suggested_poll_interval_seconds'), (int, float)
        ):
            try:
                effective_interval = max(
                    30,
                    min(
                        300,
                        int(last_llm_decision['suggested_poll_interval_seconds']),
                    ),
                )
            except (TypeError, ValueError):
                pass
        return {
            'status': 'running',
            'job_id': current_job_id,
            'bohr_job_id': bohr_job_id,
            'llm_decision': last_llm_decision,
            'llm_decision_history': llm_decision_history,
            'message': (
                f"Job {current_job_id} still running after {polls} poll(s) (max {max_polls}). "
                f"Re-call monitor_job with the same job_id in about {effective_interval} seconds "
                'to continue watching, or finish with task_completed=partial. '
                'Do NOT start unrelated work while the job is pending.'
            ),
            'poll_interval': effective_interval,
        }

    # ── Confirmed failed — read log tail for LLM diagnosis ──
    # Priority 1: download 'log' from Bohrium (works for both local and SSH sessions,
    # since all MCP-submitted jobs run on Bohrium regardless of agent session type).
    log_tail: str | None = None
    log_path: str | None = None

    if bohr_job_id:
        # 复用 _find_log_files_from_job 统一匹配日志文件
        fail_log_names = ['log']
        matched = _find_log_files_from_job(bohr_job_id, software, access_key=access_key)
        for p in matched:
            if p not in fail_log_names:
                fail_log_names.append(p)
        logger.info(
            'monitor_job failure log download: bohr_job_id=%s list=%s',
            bohr_job_id,
            fail_log_names,
        )
        combined_fail_text = ''
        fail_downloaded = []
        for log_name in fail_log_names:
            _tmp: Path | None = None
            try:
                safe_suffix = '_' + log_name.replace('/', '_').replace('.', '_')
                _tmp = Path(tempfile.mktemp(suffix=safe_suffix))
                download_job_file(log_name, bohr_job_id, _tmp, access_key=access_key)
                raw_text = _tmp.read_text(encoding='utf-8', errors='ignore')
                if raw_text and raw_text.strip():
                    text = (
                        raw_text[-_LOG_PER_FILE_MAX_CHARS:]
                        if len(raw_text) > _LOG_PER_FILE_MAX_CHARS
                        else raw_text
                    )
                    combined_fail_text += f'\n\n=== {log_name} (last {len(text)}/{len(raw_text)} chars) ===\n{text}'
                    fail_downloaded.append(log_name)
                    logger.info(
                        'monitor_job failure log: downloaded %s (%d/%d chars)',
                        log_name,
                        len(text),
                        len(raw_text),
                    )
                try:
                    _tmp.unlink()
                except OSError:
                    pass
            except Exception:
                logger.debug(
                    'monitor_job failure log: failed to download %s',
                    log_name,
                    exc_info=True,
                )
                if _tmp and _tmp.exists():
                    try:
                        _tmp.unlink()
                    except OSError:
                        pass
                continue
        if combined_fail_text:
            log_tail = combined_fail_text
            log_path = f'bohrium://jobs/{bohr_job_id}/[{",".join(fail_downloaded)}]'

    # Priority 2: fallback to local workspace or remote SSH node
    if log_tail is None:
        if is_ssh:
            log_path = _find_log_file_remote(session, workspace, software)
            log_tail = _read_log_tail_remote(session, log_path)
        else:
            log_path = _find_log_file_local(workspace, software)
            log_tail = _read_log_tail(log_path)

    log_hint = (
        log_path
        if log_path
        else 'not found — use execute_bash to search in the workspace'
    )
    return {
        'status': 'failed',
        'job_id': current_job_id,
        'bohr_job_id': bohr_job_id,
        'log_file': log_path,
        'log_file_is_remote': is_ssh,
        'log_tail': log_tail,
        'llm_decision': last_llm_decision,
        'llm_decision_history': llm_decision_history,
        'message': (
            f"Job {current_job_id} failed (confirmed after {failure_confirm_count} checks). "
            f"Log file: {log_hint}\n"
            "The last section of the log is included in 'log_tail'. "
            'Analyze it to identify the root cause, fix input files, re-submit via MCP, '
            'then call monitor_job with the new job_id. '
            'If you cannot identify the cause, call ask_human(mode="timeout") '
            'with the failure description and relevant log lines. '
            'On timeout with no human reply: abort the task '
            '(call finish with task_completed=false).'
        ),
    }
