"""LLM decision helpers and Bohrium job termination for monitor_job."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from ._constants import (
    _DEFAULT_MONITOR_LLM_TIMEOUT_SECONDS,
    _MONITOR_LLM_DECISION_PROMPT,
    REPO_ROOT,
)

logger = logging.getLogger(__name__)


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
        else (REPO_ROOT / 'logs' / 'monitor_job_injections')
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


@lru_cache(maxsize=4)
def _get_llm_by_alias(alias: str | None = None):
    # Lazy import: evomaster.config and evomaster.utils (not triggered at module load time)
    from evomaster.config import ConfigManager
    from evomaster.utils import LLMConfig, create_llm

    config_mgr = ConfigManager(
        config_dir=REPO_ROOT / 'configs' / 'mat_master',
        config_file='config.yaml',
    )
    cfg = config_mgr.load()
    llm_section = cfg.llm if isinstance(cfg.llm, dict) else {}
    llm_alias = (alias or llm_section.get('default') or 'opus').strip()
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
        'suggested_poll_interval_seconds': None,
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
        reason = (
            str(payload.get('reason') or parsed['reason']).strip() or parsed['reason']
        )
        suggested_raw = payload.get('suggested_poll_interval_seconds')
        try:
            suggested = int(float(suggested_raw)) if suggested_raw is not None else None
            if suggested is not None:
                suggested = max(30, min(300, suggested))
        except (TypeError, ValueError):
            suggested = None
        parsed.update(
            {
                'decision': decision,
                'reason': reason,
                'severity': severity,
                'confidence': confidence,
                'suggested_poll_interval_seconds': suggested,
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
        # Lazy import: matmaster.adaptors.calculation (not triggered at module load time)
        from matmaster.adaptors.calculation.job_service import terminate_job

        success, detail = terminate_job(bohr_job_id=bohr_job_id, access_key=access_key)
        return {'attempted': True, 'success': bool(success), 'detail': detail}
    except Exception as exc:
        return {'attempted': True, 'success': False, 'detail': str(exc)}
