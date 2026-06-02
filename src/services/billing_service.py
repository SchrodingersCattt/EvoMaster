"""LLM 金额计费上报客户端。

本仓库只负责在 Worker 完成 LLM 调用后，把一次调用的 usage 事件上报给
matmaster-tools-server（POST /api/v1/billing/usage）。定价、用量流水、对账等
权威逻辑都在 tools-server 侧，这里保持瘦客户端，不落本地账单表。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import aiohttp

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class BillingRunContext:
    session_id: str
    task_id: str | None
    invocation_id: str | None


class BillingService:
    """把一次 LLM 调用的 usage 事件上报给 tools-server 计费服务。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = (base_url or MATMASTER_TOOLS_SERVER).rstrip("/")
        self._timeout_seconds = timeout_seconds

    @asynccontextmanager
    async def _session(
        self, session: aiohttp.ClientSession | None
    ) -> AsyncIterator[aiohttp.ClientSession]:
        """复用传入 session（一次 run 内共享连接池），否则临时建一个。"""
        if session is not None:
            yield session
        else:
            async with aiohttp.ClientSession() as owned:
                yield owned

    async def report_llm_usage(
        self,
        *,
        run_context: BillingRunContext,
        model: str,
        call_index: int,
        spawn_id: str | None,
        usage: dict[str, Any] | None,
        session: aiohttp.ClientSession | None = None,
    ) -> bool:
        """上报一次 LLM 调用 usage 事件。成功记账返回 True，其余返回 False。

        ``session`` 用于在一次 run 内复用连接池；None 时临时新建。
        网络/服务异常在此吞掉并记 warning，避免影响用户请求主链路。
        """
        if not usage:
            return False

        payload: dict[str, Any] = {
            "session_id": run_context.session_id,
            "task_id": run_context.task_id,
            "invocation_id": run_context.invocation_id,
            "spawn_id": spawn_id,
            "call_index": call_index,
            "model": model,
            "usage": usage,
        }
        url = f"{self._base_url}/api/v1/billing/usage"
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with self._session(session) as http:
                async with http.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=timeout,
                ) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.warning(
                            "billing usage ingest failed status=%s session_id=%s "
                            "call_index=%s body=%s",
                            resp.status,
                            run_context.session_id,
                            call_index,
                            body[:500],
                        )
                        return False
                    data = await resp.json()
                    return bool((data or {}).get("data", {}).get("recorded"))
        except Exception:
            logger.warning(
                "billing usage ingest error session_id=%s call_index=%s",
                run_context.session_id,
                call_index,
                exc_info=True,
            )
            return False

    async def get_run_cost(
        self,
        invocation_id: str | None,
        *,
        timeout_seconds: float = 2.0,
        session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any] | None:
        """按 invocation_id 查本轮 run 全链路费用（best-effort）。

        供飞书完成卡片展示费用用。失败/超时/无数据返回 None，绝不抛异常、
        不拖慢完成卡片主链路。
        """
        if not invocation_id:
            return None
        url = f"{self._base_url}/api/v1/billing/usage/summary"
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        try:
            async with self._session(session) as http:
                async with http.get(
                    url, params={"invocation_id": invocation_id}, timeout=timeout
                ) as resp:
                    if resp.status >= 400:
                        return None
                    data = (await resp.json() or {}).get("data")
                    return data or None
        except Exception:
            logger.warning(
                "billing run cost query error invocation_id=%s",
                invocation_id,
                exc_info=True,
            )
            return None


@lru_cache(maxsize=1)
def get_billing_service() -> BillingService:
    return BillingService()
