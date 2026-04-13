"""调用 matmaster-tools-server 白名单接口（与 Nacos allowlist 规则名一致）。"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import httpx

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 与 matmaster-tools-server ``src/integrations/nacos.ADMIN_ALLOWLIST_RULE`` 一致
ALLOWLIST_RULE_ADMIN = 'admin'


def _allowlist_url(user_id: str) -> str:
    base = (MATMASTER_TOOLS_SERVER or '').strip().rstrip('/')
    uid = (user_id or '').strip()
    return f'{base}/api/v1/users/{quote(uid, safe="")}/is_in_allowlist'


def _fetch_is_in_admin_allowlist_uncached(user_id: str) -> bool:
    """请求 tools-server；失败或未命中时返回 False（fail-closed）。"""
    uid = (user_id or '').strip()
    if not uid:
        return False
    base = (MATMASTER_TOOLS_SERVER or '').strip()
    if not base:
        logger.debug('MATMASTER_TOOLS_SERVER 未配置，跳过 admin 白名单查询')
        return False
    url = _allowlist_url(uid)
    payload: dict[str, Any] = {'rules': [ALLOWLIST_RULE_ADMIN]}
    headers = {'X-User-Id': uid}
    try:
        with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            r = client.post(url, json=payload, headers=headers)
    except (httpx.HTTPError, OSError) as e:
        logger.warning(
            'tools-server allowlist 请求失败 user_id=%s error=%s',
            uid,
            e,
            exc_info=True,
        )
        return False
    if r.status_code == 401:
        logger.warning('tools-server allowlist 401（需 X-User-Id）user_id=%s', uid)
        return False
    if r.status_code >= 400:
        logger.warning(
            'tools-server allowlist HTTP %s user_id=%s body=%s',
            r.status_code,
            uid,
            (r.text or '')[:256],
        )
        return False
    try:
        body = r.json()
    except ValueError:
        logger.warning('tools-server allowlist 非 JSON user_id=%s', uid)
        return False
    # AllowlistCheckResponse: code 0 成功；1 配置/用户信息失败
    if body.get('code') != 0:
        return False
    data = body.get('data') or {}
    return bool(data.get('is_in_allowlist'))


def _cache_bucket() -> int:
    return int(time.time() // 60)


@lru_cache(maxsize=512)
def is_user_in_admin_allowlist_cached(user_id: str, bucket: int) -> bool:
    """按分钟桶缓存，减轻 stream 重连压力。"""
    return _fetch_is_in_admin_allowlist_uncached(user_id)


def is_user_in_admin_allowlist(user_id: str) -> bool:
    """当前用户是否在 tools-server Nacos ``allowlist.admin`` 中。"""
    uid = (user_id or '').strip()
    if not uid:
        return False
    return is_user_in_admin_allowlist_cached(uid, _cache_bucket())
