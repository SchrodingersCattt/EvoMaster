"""配额服务：调用 matmaster-tools-server 的 quota 接口。

- check_quota_status: 发送前查询余额（GET /api/v1/quota/info）。
  计价化后只读金额额度 ``credit_remaining``（元）与 ``credit_reset_at``
  （下次额度刷新日期）；旧的次数 ``remaining`` 字段已不再使用。

扣费由 billing usage 上报在 tools-server 侧按金额实时完成，evo 不再做按次扣减
（已移除 use_quota）；模型级次数限制并入金额额度（已移除 check_model_quota）。

异常不在此处捕获，由调用方/全局 error handler 统一处理。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass(frozen=True)
class QuotaStatus:
    """发送前配额状态。

    remaining: 剩余金额额度（元）。
    reset_at: 下次额度刷新日期（ISO，如 ``2026-06-09``），无则 None。
    """

    remaining: float
    reset_at: str | None = None


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


async def check_quota_status(user_id: str) -> QuotaStatus:
    """查询用户剩余金额额度 + 下次刷新时间。

    只读 ``credit_remaining``（金额，元）；缺失或非法时按 0 处理（视为额度耗尽）。
    请求异常向上抛出。
    """
    url = f'{MATMASTER_TOOLS_SERVER.rstrip("/")}/api/v1/quota/info'
    headers = {"X-User-Id": user_id}
    logger.info("check_quota_status request: url=%s headers=%s", url, headers)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
            inner = (data or {}).get("data") or {}
            credit = _coerce_number(inner.get("credit_remaining"))
            remaining = max(0.0, credit) if credit is not None else 0.0
            reset_at = inner.get("credit_reset_at")
            reset_at = reset_at if isinstance(reset_at, str) and reset_at else None
            logger.info(
                "check_quota_status response: user_id=%s status=%s "
                "remaining=%s reset_at=%s",
                user_id,
                resp.status,
                remaining,
                reset_at,
            )
            return QuotaStatus(remaining=remaining, reset_at=reset_at)
