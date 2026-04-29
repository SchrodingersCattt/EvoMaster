"""飞书开放平台：tenant_access_token、回复消息。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FEISHU_API_BASE = 'https://open.feishu.cn/open-apis'

_token_cache: dict[str, tuple[str, float]] = {}


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    cache_key = app_id.strip()
    now = time.time()
    hit = _token_cache.get(cache_key)
    if hit and hit[1] > now:
        return hit[0]
    url = f'{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal'
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            url,
            json={'app_id': app_id.strip(), 'app_secret': app_secret.strip()},
        )
        resp.raise_for_status()
        data = resp.json()
    code = data.get('code')
    if code != 0:
        logger.warning('feishu tenant_access_token error: %s', data)
        raise RuntimeError(data.get('msg', 'tenant_access_token failed'))
    token = str(data.get('tenant_access_token') or '')
    expire = int(data.get('expire') or 7200)
    if not token:
        raise RuntimeError('empty tenant_access_token')
    _token_cache[cache_key] = (token, now + max(60, expire - 60))
    return token


def reply_text_message(
    message_id: str,
    text: str,
    *,
    tenant_token: str,
) -> dict[str, Any]:
    url = f'{FEISHU_API_BASE}/im/v1/messages/{message_id}/reply'
    headers = {
        'Authorization': f'Bearer {tenant_token}',
        'Content-Type': 'application/json; charset=utf-8',
    }
    body = {
        'msg_type': 'text',
        'content': json.dumps({'text': text}, ensure_ascii=False),
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, headers=headers, json=body)
        try:
            out = resp.json()
        except Exception:
            out = {'raw': resp.text}
        if resp.status_code >= 400:
            logger.warning(
                'feishu reply failed status=%s body=%s',
                resp.status_code,
                out,
            )
        return out
