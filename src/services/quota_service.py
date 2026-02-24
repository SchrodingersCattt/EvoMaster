"""配额服务：与 MatMaster 项目一致，调用 matmaster-tools-server 的 quota 接口。

- check_quota: 发送前检查剩余额度（GET /api/v1/quota/info）
- use_quota: 任务成功后扣减额度（POST /api/v1/quota/use）

异常不在此处捕获，由全局 error handler 统一处理。
"""

import logging
from typing import Any

import aiohttp

from src.utils.constant import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)


async def check_quota(user_id: str) -> int:
    """
    查询用户剩余配额。与 MatMaster services/quota.check_quota_service 一致。

    Args:
        user_id: 用户 ID（请求头 X-User-Id）

    Returns:
        剩余额度 remaining；响应中无有效 remaining 时返回 0。请求异常向上抛出。
    """
    url = f'{MATMASTER_TOOLS_SERVER.rstrip("/")}/api/v1/quota/info'
    headers = {'X-User-Id': user_id}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
            inner = (data or {}).get('data') or {}
            remaining = inner.get('remaining')
            if remaining is not None and isinstance(remaining, (int, float)):
                return max(0, int(remaining))
            return 0


async def use_quota(user_id: str) -> None:
    """
    扣减用户配额一次。与 MatMaster services/quota.use_quota_service 一致。

    Args:
        user_id: 用户 ID（请求头 X-User-Id，body 中也可带 user_id）
    """
    url = f'{MATMASTER_TOOLS_SERVER.rstrip("/")}/api/v1/quota/use'
    headers = {'X-User-Id': user_id}
    body = {'user_id': user_id}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            result = await resp.json()
            logger.info('use_quota ok: user_id=%s response=%s', user_id, result)
