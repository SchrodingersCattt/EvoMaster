"""monitor_job — built-in tool for resilient remote calculation job lifecycle.

Runs entirely inside the agent backend process so it can import
evomaster.adaptors.calculation.job_service without shipping source code to
the remote Bohrium node.

Workflow
--------
1. Poll Bohrium OpenAPI until the job reaches a terminal state.
   Transient failures (network errors, API blips) are confirmed over
   ``_MAX_FAILURE_CONFIRMS`` consecutive checks before being treated as real.
2. On success: download result files via the NAS file-token API.
   - Local session  → write directly to ``workspace/calculation_results/``.
   - SSH session    → download to a temp dir on the backend, then SFTP-push
                      each file to the container's ``workspace/``, then clean up.
3. On confirmed failure: read the log tail and return it with the result so
   the LLM agent can diagnose the root cause and decide next steps.

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
import tempfile
import time
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from evomaster.adaptors.calculation.job_service import (
    download_job_file,
    get_file_token,
    get_job_results,
    iterate_job_files,
    query_job_status,
    terminate_job,
)
from evomaster.agent.session.ssh import SSHSession
from evomaster.config import ConfigManager
from evomaster.utils import LLMConfig, create_llm

from ..base import BaseTool, BaseToolParams

if TYPE_CHECKING:
    from evomaster.agent.session import BaseSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants for lifecycle states
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

# Number of consecutive failure/error status responses required before treating
# a job as truly failed.  Filters out transient network blips and API errors.
_MAX_FAILURE_CONFIRMS = 3

# Maximum characters to include in log_tail returned to the agent.
_LOG_TAIL_MAX_CHARS = 5000

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
_MONITOR_LLM_DECISION_PROMPT = """你是科学计算作业监控专家，分析材料计算、量子化学、分子动力学等任务的运行日志。

**任务目标**：{task_intent}

判断作业是否应该继续运行或立即终止，返回 JSON（不要 markdown 标记）：
{{
  "decision": "continue" | "terminate",
  "reason": "简洁的中文原因（<30字）",
  "severity": "low" | "medium" | "high",
  "confidence": 0.0-1.0
}}

关键判断点：
- 数值异常（NaN、Inf）→ terminate
- 致命错误（Fatal Error、Segmentation Fault）→ terminate  
- 死循环（长时间无进展）→ terminate
- 任务已完成（达到目标步数/时间）→ terminate（正常完成）
- 正常迭代收敛中 → continue
- 日志不完整或无法判断 → continue（保守策略）

注意：如果任务已经达到预期目标（如 MD 跑完指定时间、优化收敛），应判断为 terminate（正常完成），reason 中说明"任务已完成"。

"""
_DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS = 45


def _trim_text(text: str | None, max_chars: int) -> str:
    if not text:
        return ''
    if max_chars <= 0:
        return ''
    return text[-max_chars:] if len(text) > max_chars else text


def _load_test_injected_log(bohr_job_id: str | None) -> tuple[str | None, str | None]:
    """Load one-shot test injected log content for a specific job id.

    Intended for local integration tests only. If an injection file exists, this
    helper reads it and removes it by default (one-shot behavior).
    """
    bid = (bohr_job_id or '').strip()
    if not bid:
        return None, None
    inject_dir_raw = (os.environ.get('MONITOR_JOB_INJECT_DIR') or '').strip()
    inject_dir = (
        Path(inject_dir_raw)
        if inject_dir_raw
        else (Path(__file__).resolve().parents[4] / 'logs' / 'monitor_job_injections')
    )
    inject_path = inject_dir / f'{bid}.log.inject'
    if not inject_path.exists():
        return None, None
    try:
        injected = inject_path.read_text(encoding='utf-8', errors='ignore').strip()
    except Exception:
        return None, None
    if not injected:
        return None, None
    keep_file = (os.environ.get('MONITOR_JOB_INJECT_KEEP') or '').strip() == '1'
    if not keep_file:
        try:
            inject_path.unlink()
        except OSError:
            pass
    return injected, inject_path.as_posix()


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


@lru_cache(maxsize=4)
def _get_llm_by_alias(alias: str | None = None):
    config_mgr = ConfigManager(
        config_dir=_resolve_repo_root() / 'configs' / 'mat_master',
        config_file='config.yaml',
    )
    cfg = config_mgr.load()
    llm_section = cfg.llm if isinstance(cfg.llm, dict) else {}
    llm_alias = (alias or llm_section.get('default') or 'litellm').strip()
    llm_raw = llm_section.get(llm_alias)
    if not isinstance(llm_raw, dict):
        raise RuntimeError(f'LLM alias not found in config: {llm_alias}')
    llm_cfg = LLMConfig.model_validate(llm_raw)
    return create_llm(llm_cfg)


def _extract_json_object(raw: str) -> str | None:
    text = (raw or '').strip()
    if not text:
        return None
    if text.startswith('{') and text.endswith('}'):
        return text
    fenced_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, flags=re.DOTALL)
    if fenced_match:
        return fenced_match.group(1)
    brace_match = re.search(r'(\{.*\})', text, flags=re.DOTALL)
    if brace_match:
        return brace_match.group(1)
    return None


def _parse_llm_decision(raw: str | None) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        'decision': 'continue',
        'reason': 'LLM 无明确终止信号，继续监控。',
        'severity': 'low',
        'confidence': 0.5,
        'raw': raw or '',
        'parse_error': None,
    }
    text = (raw or '').strip()
    if not text:
        parsed['parse_error'] = 'empty_response'
        return parsed
    json_text = _extract_json_object(text)
    if not json_text:
        parsed['parse_error'] = 'json_not_found'
        return parsed
    try:
        payload = json.loads(json_text)
        if not isinstance(payload, dict):
            parsed['parse_error'] = 'json_not_object'
            return parsed
        decision = str(payload.get('decision') or 'continue').strip().lower()
        if decision not in {'continue', 'terminate'}:
            decision = 'continue'
        severity = str(payload.get('severity') or 'low').strip().lower()
        if severity not in {'low', 'medium', 'high'}:
            severity = 'low'
        confidence_raw = payload.get('confidence')
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        reason = str(payload.get('reason') or parsed['reason']).strip() or parsed['reason']
        parsed.update(
            {
                'decision': decision,
                'reason': reason,
                'severity': severity,
                'confidence': confidence,
                'parse_error': None,
            }
        )
        return parsed
    except Exception as exc:
        parsed['parse_error'] = str(exc)
        return parsed


def _build_monitor_snapshot(
    *,
    job_id: str,
    bohr_job_id: str | None,
    status: str,
    poll_index: int,
    software: str,
    log_tail: str | None,
    log_file: str | None,
) -> dict[str, Any]:
    return {
        'job_id': job_id,
        'bohr_job_id': bohr_job_id,
        'software': software,
        'status': status,
        'poll_index': poll_index,
        'log_file': log_file,
        'log_tail': log_tail or '',
        'log_tail_chars': len(log_tail or ''),
        'timestamp': int(time.time()),
    }


def _call_llm_decision(
    *,
    snapshot: dict[str, Any],
    task_intent: str | None = None,
    llm_alias: str | None = None,
    timeout_seconds: int = _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    llm = _get_llm_by_alias(llm_alias)
    user_payload = json.dumps(snapshot, ensure_ascii=False)
    # 格式化 prompt，填入任务目标
    intent_text = (task_intent or '').strip() or '未指定（通用监控）'
    system_prompt = _MONITOR_LLM_DECISION_PROMPT.format(task_intent=intent_text)
    response = llm._call(
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_payload},
        ],
        tools=None,
        timeout=timeout_seconds,
        temperature=0.0,
    )
    return _parse_llm_decision(response.content)


def _terminate_job_if_needed(
    *,
    bohr_job_id: str | None,
    access_key: str | None,
) -> dict[str, Any]:
    if not bohr_job_id:
        return {'attempted': False, 'success': False, 'reason': 'bohr_job_id_missing'}
    try:
        success, detail = terminate_job(bohr_job_id=bohr_job_id, access_key=access_key)
        return {'attempted': True, 'success': bool(success), 'detail': detail}
    except Exception as exc:
        return {'attempted': True, 'success': False, 'detail': str(exc)}


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
        # 尝试下载多个可能的日志文件并合并（扩展列表，覆盖更多场景）
        log_files = [
            'log',                    # Bohrium 调度日志
            'md_simulation.log',      # DPA MD 日志
            'relaxation.log',         # DPA 弛豫日志
            'output.log',             # 通用输出日志
            'OUTCAR',                 # VASP
            'OUT.ABACUS',             # ABACUS
            'log.lammps',             # LAMMPS
            'running_scf.log',        # ABACUS SCF
            'running_md.log',         # ABACUS MD
        ]
        combined_text = ''
        downloaded_files = []
        
        for log_name in log_files:
            tmp_path: Path | None = None
            try:
                tmp_path = Path(tempfile.mktemp(suffix=f'_{log_name.replace(".", "_")}'))
                download_job_file(log_name, bohr_job_id, tmp_path, access_key=access_key)
                # 读取完整文件内容（不截断）
                text = tmp_path.read_text(encoding='utf-8', errors='ignore')
                if text and text.strip():
                    combined_text += f'\n\n=== {log_name} ===\n{text}'
                    downloaded_files.append(log_name)
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            except Exception:
                # 某个日志文件不存在或下载失败，继续尝试其他文件
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
        log_tail=_trim_text(log_tail, _LOG_TAIL_MAX_CHARS),
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
    return {
        'status': status,
        'snapshot': snapshot,
        'decision': decision,
    }

# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------


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
    download_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: fetch results.txt
    results_txt_local = download_dir / 'result_0_results.txt'
    try:
        download_job_file(
            'results.txt', bohr_job_id, results_txt_local, access_key=access_key
        )
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
            return p[len(root_prefix) :].lstrip('/')
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
            path = download_job_file(
                _to_rel(rp), bohr_job_id, dest, access_key=access_key
            )
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


def _sftp_push_directory(
    session: BaseSession, local_dir: Path, remote_dir: str
) -> list[str]:
    """Upload all files in *local_dir* to *remote_dir* on the SSH node.

    Returns list of remote paths uploaded.
    """
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
            logger.warning(
                'monitor_job: SFTP push failed %s → %s: %s',
                local_file,
                remote_path,
                exc,
            )
    return pushed


# ---------------------------------------------------------------------------
# Progressive LLM decision schedule: 前密后疏（刚投递时频率高，随后逐渐拉长）
# ---------------------------------------------------------------------------

def _llm_decision_interval_at_poll(poll_index: int, base_interval: int) -> int:
    """Return the effective decision_check_interval at this poll (progressive schedule).

    - polls 0~9 (前 ~5 min): 每 2 次轮询做 1 次 LLM 决策
    - polls 10~29 (接下来 ~10 min): 每 5 次
    - polls 30+: 每 base_interval 次（默认 10，约 5 min）
    """
    if poll_index < 10:
        return 5
    if poll_index < 40:
        return 10
    return max(1, base_interval)


# ---------------------------------------------------------------------------
# Core lifecycle (backend-native version of run_lifecycle)
# ---------------------------------------------------------------------------


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
) -> dict[str, Any]:
    is_ssh = isinstance(session, SSHSession)

    current_job_id = job_id
    max_polls = 720
    unknown_count = 0
    max_unknown = 3
    failure_confirm_count = 0

    polls = 0
    llm_decision_history: list[dict[str, Any]] = []
    last_llm_decision: dict[str, Any] | None = None
    while polls < max_polls:
        status = str(
            query_job_status(
                current_job_id,
                bohr_job_id=bohr_job_id,
                software=None,
                access_key=access_key,
            )
        )

        if llm_decision_mode != 'off' and status not in TERMINAL_SUCCESS and status not in TERMINAL_FAILURE:
            effective_interval = _llm_decision_interval_at_poll(polls, max(1, decision_check_interval))
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
            time.sleep(min(poll_interval, 10))
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
            time.sleep(min(poll_interval, 10))
            continue

        # -- Still running: reset failure counter --
        failure_confirm_count = 0
        unknown_count = 0
        time.sleep(poll_interval)
        polls += 1

    # ── Loop ended: either confirmed failure (break) or max_polls exceeded (timeout) ──
    # If we exited because polls >= max_polls, job was still "Running" — treat as timeout, not failure.
    if failure_confirm_count < _MAX_FAILURE_CONFIRMS:
        return {
            'status': 'timeout',
            'job_id': current_job_id,
            'bohr_job_id': bohr_job_id,
            'llm_decision': last_llm_decision,
            'llm_decision_history': llm_decision_history,
            'message': (
                f"Job {current_job_id} still running after {polls} polls (max {max_polls}). "
                "Monitor timed out; job may still be running on Bohrium. "
                "Re-call monitor_job with the same job_id to continue, or check the job on Bohrium."
            ),
        }

    # ── Confirmed failed — read log tail for LLM diagnosis ──
    # Priority 1: download 'log' from Bohrium (works for both local and SSH sessions,
    # since all MCP-submitted jobs run on Bohrium regardless of agent session type).
    log_tail: str | None = None
    log_path: str | None = None

    if bohr_job_id:
        try:
            _tmp_log = Path(tempfile.mktemp(suffix='.log'))
            download_job_file('log', bohr_job_id, _tmp_log, access_key=access_key)
            log_tail = _read_log_tail(str(_tmp_log))
            log_path = f'bohrium://jobs/{bohr_job_id}/log'
            try:
                _tmp_log.unlink()
            except OSError:
                pass
        except Exception:
            pass

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


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


class MonitorJobParams(BaseToolParams):
    """Monitor a submitted remote calculation job (DPA, ABACUS, LAMMPS, CP2K, QE, ABINIT, ORCA,
    Gaussian, etc.) until it reaches a terminal state.

    Polls the Bohrium OpenAPI for job status and downloads result files on success.
    Transient API/network errors are confirmed over multiple checks before being
    treated as a real failure.

    On success: returns status + downloaded file paths.
    On failure: returns status='failed' + log_tail (last section of the job log).
    The agent should read log_tail to diagnose the root cause, fix input files,
    re-submit via MCP, and call monitor_job again with the new job_id.
    If the cause cannot be identified, call ask_human(mode="timeout").
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
        description=(
            'Workspace directory for result downloads. '
            'Defaults to the session workspace (session-isolated run directory). '
            'Only override if you need results in a specific path.'
        ),
    )
    bohr_job_id: str | None = Field(
        default=None,
        description=(
            'Explicit Bohrium OpenAPI job ID (from extra_info.bohr_job_id in submit response). '
            'Required for dpdispatcher-based jobs (ABACUS, etc.) whose MCP job_id contains a hex hash.'
        ),
    )
    poll_interval: int = Field(default=30, description='Seconds between status checks.')
    access_key: str | None = Field(
        default=None,
        description='Bohrium access key. Falls back to BOHRIUM_ACCESS_KEY env var.',
    )
    download_tag: str | None = Field(
        default=None,
        description='Folder tag for downloaded results (timestamp subfolder always added).',
    )
    llm_decision_mode: str = Field(
        default='auto_terminate',
        description=(
            "LLM 决策模式：off(关闭) | advise(仅给建议，不终止) | auto_terminate(判定异常时尝试终止 Bohrium 作业)"
        ),
    )
    llm_model_alias: str | None = Field(
        default=None,
        description='LLM 配置别名（来自 configs/mat_master/config.yaml 的 llm 节点），为空则用 default。',
    )
    llm_timeout_seconds: int = Field(
        default=_DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS,
        description='LLM 决策请求超时（秒）。',
    )
    decision_check_interval: int = Field(
        default=10,
        description=(
            'LLM 决策间隔（渐进式）：前 ~5 分钟每 2 次轮询一次，接下来 ~10 分钟每 5 次，之后每 N 次（本参数，默认 10）。'
        ),
    )
    task_intent: str | None = Field(
        default=None,
        description=(
            '任务目标/意图描述，用于帮助 LLM 判断任务是否完成或异常。'
            '**MANDATORY**: 直接传入用户的原始查询（ORIGINAL TASK）。'
            '例如：用户输入"石墨烯建模并进行5ps nvt md"，则传入 task_intent="石墨烯建模并进行5ps nvt md"。'
        ),
    )


class MonitorJobTool(BaseTool):
    """Built-in tool: monitor a remote Bohrium calculation job."""

    name: ClassVar[str] = 'monitor_job'
    params_class: ClassVar[type[BaseToolParams]] = MonitorJobParams

    def execute(
        self, session: BaseSession, args_json: str
    ) -> tuple[str, dict[str, Any]]:
        try:
            params = self.parse_params(args_json)
        except Exception as exc:
            return f'Parameter validation error: {exc}', {'error': str(exc)}

        assert isinstance(params, MonitorJobParams)

        # Resolve workspace: fall back to the session's configured workspace so that
        # downloads are isolated to the session's run directory, not the process CWD.
        workspace = params.workspace
        if not workspace or workspace == '.':
            if isinstance(session, SSHSession):
                workspace = session.config.working_dir or '/personal/workspace'
            else:
                workspace = getattr(session.config, 'workspace_path', None) or '.'

        # Inject access_key from session._bohrium_credentials if not explicitly provided
        access_key = params.access_key
        if not access_key:
            creds = getattr(session, '_bohrium_credentials', None)
            if isinstance(creds, dict):
                access_key = creds.get('access_key')
            if not access_key:
                access_key = os.environ.get('BOHRIUM_ACCESS_KEY')

        result = _run_lifecycle(
            job_id=params.job_id,
            software=params.software,
            workspace=workspace,
            session=session,
            poll_interval=params.poll_interval,
            bohr_job_id=params.bohr_job_id,
            download_tag=params.download_tag,
            access_key=access_key,
            task_intent=params.task_intent,
            llm_decision_mode=(params.llm_decision_mode or 'off').strip().lower(),
            llm_model_alias=params.llm_model_alias,
            llm_timeout_seconds=max(5, int(params.llm_timeout_seconds or _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS)),
            decision_check_interval=max(1, int(params.decision_check_interval or 1)),
        )

        output = json.dumps(result, indent=2, ensure_ascii=False)
        info = {
            'status': result.get('status'),
            'job_id': result.get('job_id'),
            'bohr_job_id': result.get('bohr_job_id'),
        }
        return output, info
