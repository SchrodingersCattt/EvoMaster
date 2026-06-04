"""LLM 金额计费上报 HTTP 客户端（瘦客户端）。

只负责把一次 LLM 调用的 usage 事件 POST 给 matmaster-tools-server
（POST /api/v1/billing/usage），并按需返回当次定价金额。定价、用量流水、对账等
权威逻辑都在 tools-server 侧，这里不落本地账单表。

本模块只依赖 aiohttp + utils.env，不依赖 matmaster / src 业务，放在与 src 并列的
clients/ 顶层，供 src（线上 Worker）、matmaster.devshell、evaluation 共用，避免
matmaster 反向 import src（见 tests/matmaster/test_import_audit.py）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import aiohttp

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 5.0

# tools-server 接受的计费模式：platform 扣额度；byok/eval 仅记账（eval 额外定价）。
BillingMode = Literal["platform", "byok", "eval"]


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

    async def _post_usage(
        self,
        *,
        run_context: BillingRunContext,
        model: str,
        call_index: int,
        spawn_id: str | None,
        usage: dict[str, Any] | None,
        billing_mode: BillingMode,
        session: aiohttp.ClientSession | None,
    ) -> dict[str, Any] | None:
        """POST 一次 usage 事件，返回响应 ``data``（含 ``recorded`` 与定价金额），失败返回 None。

        定价字段见 tools-server ``UsageIngestData``：``total_amount_micro`` /
        ``total_amount_settle_micro`` / ``pricing_status`` 等。网络/服务异常在此吞掉
        并记 warning，避免影响调用方主链路。
        """
        if not usage:
            return None

        payload: dict[str, Any] = {
            "session_id": run_context.session_id,
            "task_id": run_context.task_id,
            "invocation_id": run_context.invocation_id,
            "spawn_id": spawn_id,
            "call_index": call_index,
            "model": model,
            "usage": usage,
        }
        # 仅在非默认（platform）时显式带上；tools-server 缺省即 platform，保持向后兼容。
        if billing_mode and billing_mode != "platform":
            payload["billing_mode"] = billing_mode
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
                        return None
                    data = await resp.json()
                    return (data or {}).get("data") or None
        except Exception:
            logger.warning(
                "billing usage ingest error session_id=%s call_index=%s",
                run_context.session_id,
                call_index,
                exc_info=True,
            )
            return None

    async def report_llm_usage(
        self,
        *,
        run_context: BillingRunContext,
        model: str,
        call_index: int,
        spawn_id: str | None,
        usage: dict[str, Any] | None,
        billing_mode: BillingMode = "platform",
        session: aiohttp.ClientSession | None = None,
    ) -> bool:
        """上报一次 LLM 调用 usage 事件。成功记账返回 True，其余返回 False。

        ``session`` 用于在一次 run 内复用连接池；None 时临时新建。
        ``billing_mode`` 为 'byok' / 'eval' 时 tools-server 仅记账不扣额度。
        """
        data = await self._post_usage(
            run_context=run_context,
            model=model,
            call_index=call_index,
            spawn_id=spawn_id,
            usage=usage,
            billing_mode=billing_mode,
            session=session,
        )
        return bool((data or {}).get("recorded"))

    async def price_llm_usage(
        self,
        *,
        run_context: BillingRunContext,
        model: str,
        call_index: int,
        spawn_id: str | None,
        usage: dict[str, Any] | None,
        billing_mode: BillingMode = "eval",
        session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any] | None:
        """上报一次 usage 并返回当次定价结果（含 ``total_amount_micro`` 等），失败返回 None。

        与 :meth:`report_llm_usage` 共用 POST，但返回完整响应 ``data`` 而非布尔，
        供评测侧按 call 攒成本明细。缺省 ``billing_mode='eval'``：记账并定价但不扣额度。
        """
        return await self._post_usage(
            run_context=run_context,
            model=model,
            call_index=call_index,
            spawn_id=spawn_id,
            usage=usage,
            billing_mode=billing_mode,
            session=session,
        )

    async def get_run_cost(
        self,
        invocation_id: str | None,
        *,
        timeout_seconds: float = 2.0,
        session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any] | None:
        """按 invocation_id 查本轮 run 全链路费用（best-effort）。

        失败/超时/无数据返回 None，绝不抛异常、不拖慢主链路。
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
