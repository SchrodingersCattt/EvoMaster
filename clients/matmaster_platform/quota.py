"""MatMaster 平台 quota HTTP 客户端。"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def fetch_quota_info(user_id: str) -> dict[str, Any]:
    """GET /api/v1/quota/info，返回响应 data 字段；请求异常向上抛出。"""
    url = f'{MATMASTER_TOOLS_SERVER.rstrip("/")}/api/v1/quota/info'
    headers = {"X-User-Id": user_id}
    logger.info("fetch_quota_info request: url=%s headers=%s", url, headers)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()
            logger.info(
                "fetch_quota_info response: user_id=%s status=%s",
                user_id,
                resp.status,
            )
            data = (payload or {}).get("data") or {}
            return data if isinstance(data, dict) else {}


__all__ = ["fetch_quota_info"]
