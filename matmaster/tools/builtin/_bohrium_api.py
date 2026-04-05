"""Shared Bohrium API helpers for the builtin Bohrium tool."""

from __future__ import annotations

import logging
import os
from typing import Any, NamedTuple

import requests

logger = logging.getLogger(__name__)


def _use_sandbox() -> bool:
    return os.environ.get('BOHRIUM_USE_SANDBOX', '1').strip() == '1'


def _get(
    base_url: str,
    path: str,
    access_key: str,
    params: dict | None = None,
    timeout: int = 30,
) -> dict:
    url = f'{base_url}{path}'
    resp = requests.get(
        url,
        headers={'accessKey': access_key, 'Accept': 'application/json'},
        params=params or {},
        timeout=timeout,
    )
    if not getattr(resp, 'ok', True):
        _log_http_error('GET', url, resp)
    resp.raise_for_status()
    return resp.json()


def _post(
    base_url: str, path: str, access_key: str, payload: dict, timeout: int = 30
) -> dict:
    url = f'{base_url}{path}'
    resp = requests.post(
        url,
        headers={'accessKey': access_key, 'Content-Type': 'application/json'},
        json=payload,
        timeout=timeout,
    )
    if not getattr(resp, 'ok', True):
        _log_http_error('POST', url, resp)
    resp.raise_for_status()
    return resp.json()


def _mask_secret(secret: str) -> str:
    raw = (secret or '').strip()
    if not raw:
        return '(empty)'
    if len(raw) <= 4:
        return raw[0] + '...'
    return raw[:4] + '...'


def _compact_log_text(text: str, *, max_chars: int = 200) -> str:
    compact = ' '.join((text or '').split())
    if not compact:
        return '(empty)'
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + '...'


def _log_http_error(method: str, url: str, response: Any) -> None:
    logger.warning(
        'Bohrium HTTP error method=%s url=%s status=%s response_body=%s',
        method,
        url,
        getattr(response, 'status_code', 'unknown'),
        _compact_log_text(getattr(response, 'text', '')),
    )


_STATUS_MAP = {
    -10: 'Prepared',
    -2: 'Deleted',
    -1: 'Failed',
    0: 'Pending',
    1: 'Running',
    2: 'Finished',
    3: 'Scheduling',
    6: 'Unknown',
}
_SUCCESS_CODE = 2
_RUNNING_CODES = {-10, 0, 1, 3}
_FAILURE_CODES = {-2, -1}


class _ResolvedBohriumContext(NamedTuple):
    access_key: str
    project_id: int
    base_url: str
    source: str
