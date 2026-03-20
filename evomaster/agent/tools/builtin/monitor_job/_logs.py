"""Log discovery and one-shot monitor+LLM decision for monitor_job."""

from __future__ import annotations

import fnmatch
import logging
import tempfile
from pathlib import Path
from typing import Any

from evomaster.adaptors.calculation.job_service import (
    download_job_file,
    iterate_job_files,
    query_job_status,
)
from evomaster.agent.session import BaseSession

from ._constants import (
    _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS,
    _LOG_PER_FILE_MAX_CHARS,
    _LOG_TAIL_MAX_CHARS,
    LOG_PATTERNS,
)
from ._llm import (
    _build_monitor_snapshot,
    _call_llm_decision,
    _load_test_injected_log,
)

logger = logging.getLogger(__name__)


def _find_log_files_from_job(
    bohr_job_id: str,
    software: str,
    access_key: str | None = None,
) -> list[str]:
    """用 iterate_job_files 递归列出 job 全部文件，按 LOG_PATTERNS 通配符匹配日志文件路径。"""
    patterns = LOG_PATTERNS.get(software.lower(), ['*.log', '*.out', '*.json'])
    logger.info(
        '_find_log_files_from_job: bohr_job_id=%s software=%s patterns=%s',
        bohr_job_id,
        software,
        patterns,
    )

    # 递归列出所有文件（iterate_job_files 只返回一级，需要对子目录再次调用）
    all_files: list[dict] = []
    max_depth = 2  # 只遍历两级目录
    try:
        dirs_to_visit: list[tuple[str | None, int]] = [(None, 0)]
        while dirs_to_visit:
            prefix, depth = dirs_to_visit.pop()
            entries = iterate_job_files(
                bohr_job_id, prefix=prefix, access_key=access_key
            )
            for entry in entries:
                if entry.get('isDir'):
                    if depth < max_depth:
                        dir_path = entry.get('path', '')
                        if dir_path:
                            dirs_to_visit.append((dir_path, depth + 1))
                else:
                    all_files.append(entry)
    except Exception:
        logger.warning(
            '_find_log_files_from_job: iterate_job_files failed for %s',
            bohr_job_id,
            exc_info=True,
        )
        return []
    logger.info(
        '_find_log_files_from_job: found %d files (recursive) for %s: %s',
        len(all_files),
        bohr_job_id,
        [f.get('path', '') for f in all_files],
    )
    matched = []
    for f in all_files:
        path = f.get('path', '')
        basename = path.rsplit('/', 1)[-1] if '/' in path else path
        for pat in patterns:
            if fnmatch.fnmatch(basename, pat):
                matched.append(path)
                break
    logger.info(
        '_find_log_files_from_job: matched %d log files: %s',
        len(matched),
        matched,
    )
    return matched


def run_monitor_decision_once(
    *,
    job_id: str,
    software: str,
    bohr_job_id: str | None,
    access_key: str | None,
    poll_index: int = 0,
    task_intent: str | None = None,
    llm_model_alias: str | None = None,
    llm_timeout_seconds: int = _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS,
    enable_llm_decision: bool = True,
    status_override: str | None = None,
) -> dict[str, Any]:
    status = (
        str(status_override)
        if status_override is not None
        else str(
            query_job_status(
                job_id,
                bohr_job_id=bohr_job_id,
                software=None,
                access_key=access_key,
            )
        )
    )
    log_tail: str | None = None
    log_file: str | None = None
    if bohr_job_id:
        combined_text = ''
        downloaded_files = []

        # 1) 始终先下载 Bohrium 调度日志（固定文件名 'log'）
        log_names_to_download = ['log']
        # 2) 用 iterate_job_files + LOG_PATTERNS 通配符匹配日志文件
        matched_paths = _find_log_files_from_job(
            bohr_job_id, software, access_key=access_key
        )
        for p in matched_paths:
            if p not in log_names_to_download:
                log_names_to_download.append(p)
        logger.info(
            'monitor_log_download: bohr_job_id=%s final download list=%s',
            bohr_job_id,
            log_names_to_download,
        )

        for log_name in log_names_to_download:
            tmp_path: Path | None = None
            try:
                safe_suffix = '_' + log_name.replace('/', '_').replace('.', '_')
                tmp_path = Path(tempfile.mktemp(suffix=safe_suffix))
                download_job_file(
                    log_name, bohr_job_id, tmp_path, access_key=access_key
                )
                raw_text = tmp_path.read_text(encoding='utf-8', errors='ignore')
                if raw_text and raw_text.strip():
                    text = (
                        raw_text[-_LOG_PER_FILE_MAX_CHARS:]
                        if len(raw_text) > _LOG_PER_FILE_MAX_CHARS
                        else raw_text
                    )
                    combined_text += f'\n\n=== {log_name} (last {len(text)}/{len(raw_text)} chars) ===\n{text}'
                    downloaded_files.append(log_name)
                    logger.info(
                        'monitor_log_download: downloaded %s (%d/%d chars)',
                        log_name,
                        len(text),
                        len(raw_text),
                    )
                else:
                    logger.debug(
                        'monitor_log_download: %s downloaded but empty',
                        log_name,
                    )
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            except Exception:
                logger.debug(
                    'monitor_log_download: failed to download %s',
                    log_name,
                    exc_info=True,
                )
                if tmp_path and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                continue

        if combined_text:
            log_tail = combined_text
            log_file = f'bohrium://jobs/{bohr_job_id}/[{",".join(downloaded_files)}]'

    # Optional one-shot local test hook: append injected log chunk before LLM decision.
    injected_log, injected_path = _load_test_injected_log(bohr_job_id)
    if injected_log:
        prefix = '\n\n=== injected_test_log ===\n'
        log_tail = (log_tail or '') + prefix + injected_log
        if log_file:
            log_file = f'{log_file},injected:{injected_path}'
        else:
            log_file = f'injected:{injected_path}'

    # 在这里统一截断到最大长度（只保留最后 _LOG_TAIL_MAX_CHARS 个字符）
    snapshot = _build_monitor_snapshot(
        job_id=job_id,
        bohr_job_id=bohr_job_id,
        status=status,
        poll_index=poll_index,
        software=software,
        log_tail=log_tail or '',
        log_file=log_file,
    )
    decision: dict[str, Any] | None = None
    if enable_llm_decision:
        decision = _call_llm_decision(
            snapshot=snapshot,
            task_intent=task_intent,
            llm_alias=llm_model_alias,
            timeout_seconds=llm_timeout_seconds,
        )
        logger.info(
            'monitor_llm_decision: bohr_job_id=%s poll=%d decision=%s reason=%s confidence=%s',
            bohr_job_id,
            poll_index,
            decision.get('decision'),
            decision.get('reason'),
            decision.get('confidence'),
        )
    return {
        'status': status,
        'snapshot': snapshot,
        'decision': decision,
    }


def _read_log_tail(log_path: str | None) -> str | None:
    """Return the last ``_LOG_TAIL_MAX_CHARS`` characters of a local log file."""
    if not log_path:
        return None
    try:
        with open(log_path, errors='ignore') as f:
            content = f.read()
        return (
            content[-_LOG_TAIL_MAX_CHARS:]
            if len(content) > _LOG_TAIL_MAX_CHARS
            else content
        )
    except OSError:
        return None


def _read_log_tail_remote(session: BaseSession, log_path: str | None) -> str | None:
    """Return the last ``_LOG_TAIL_MAX_CHARS`` characters of a remote log file."""
    if not log_path:
        return None
    try:
        content = session._env.read_file_content(log_path)
        if not isinstance(content, str):
            content = str(content)
        return (
            content[-_LOG_TAIL_MAX_CHARS:]
            if len(content) > _LOG_TAIL_MAX_CHARS
            else content
        )
    except Exception:
        return None


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


def _find_log_file_remote(
    session: BaseSession, workspace: str, software: str
) -> str | None:
    """Return path of the most-recently-modified log on the remote node, or None."""
    from evomaster.agent.session.ssh import SSHSession

    try:
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
