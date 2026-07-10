"""MatMaster 平台 quota HTTP 客户端。"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def fetch_quota_info(
    user_id: str, project_id: int | str | None = None
) -> dict[str, Any]:
    """GET /api/v1/quota/info，返回响应 data 字段；请求异常向上抛出。

    project_id 可选：传入时平台会附带 org_wallet_pass / org_wallet_available_fen
    （项目扣费能否兜底——org 钱包可用性是项目级的，脱离项目无意义），并把项目
    可用余额并入 available_micro。发送前闸口与 in-run 熔断预算都依赖它。
    """
    url = f'{MATMASTER_TOOLS_SERVER.rstrip("/")}/api/v1/quota/info'
    headers = {"X-User-Id": user_id}
    params = {"project_id": str(project_id)} if project_id not in (None, "") else None
    logger.info(
        "fetch_quota_info request: url=%s headers=%s params=%s", url, headers, params
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
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
