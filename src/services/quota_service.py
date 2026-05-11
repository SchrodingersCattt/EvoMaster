"""配额服务：调用 matmaster-tools-server 的 quota 接口。

- check_quota: 发送前检查剩余额度（GET /api/v1/quota/info）
- use_quota: 任务成功后扣减额度（POST /api/v1/quota/use）
- check_model_quota: 检查模型级剩余配额（复用 /api/v1/quota/info?model_key=...）

异常不在此处捕获，由全局 error handler 统一处理。
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def check_quota(user_id: str, model_key: str | None = None) -> int:
    """
    查询用户剩余配额。

    Args:
        user_id: 用户 ID（请求头 X-User-Id）
        model_key: 可选模型路由 key，传入时额外返回模型级配额

    Returns:
        剩余额度 remaining；响应中无有效 remaining 时返回 0。请求异常向上抛出。
    """
    url = f'{MATMASTER_TOOLS_SERVER.rstrip("/")}/api/v1/quota/info'
    headers = {"X-User-Id": user_id}
    params = {}
    if model_key:
        params["model_key"] = model_key
    logger.info(
        "check_quota request: url=%s headers=%s params=%s", url, headers, params
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
            inner = (data or {}).get("data") or {}
            remaining = inner.get("remaining")
            if remaining is not None and isinstance(remaining, (int, float)):
                remaining = max(0, int(remaining))
            else:
                remaining = 0
            logger.info(
                "check_quota response: user_id=%s status=%s data=%s remaining=%s",
                user_id,
                resp.status,
                data,
                remaining,
            )
            return remaining


async def check_model_quota(user_id: str, model_key: str) -> int:
    """检查模型级剩余配额。返回 remaining（-1 表示无限制）。"""
    url = f'{MATMASTER_TOOLS_SERVER.rstrip("/")}/api/v1/quota/info'
    headers = {"X-User-Id": user_id}
    params = {"model_key": model_key}
    logger.info(
        "check_model_quota request: user_id=%s model_key=%s", user_id, model_key
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
            inner = (data or {}).get("data") or {}
            model_quota = inner.get("model_quota") or {}
            remaining = model_quota.get("remaining")
            if remaining is not None and isinstance(remaining, (int, float)):
                return int(remaining)
            return -1


async def use_quota(user_id: str, model_key: str | None = None) -> None:
    """
    扣减用户配额一次，可选同时扣减模型级配额。

    Args:
        user_id: 用户 ID
        model_key: 可选模型路由 key，传入时一并扣减模型配额
    """
    url = f'{MATMASTER_TOOLS_SERVER.rstrip("/")}/api/v1/quota/use'
    headers = {"X-User-Id": user_id}
    body: dict[str, Any] = {"user_id": user_id}
    if model_key:
        body["model_key"] = model_key
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            result = await resp.json()
            logger.info(
                "use_quota ok: user_id=%s model_key=%s response=%s",
                user_id,
                model_key,
                result,
            )
