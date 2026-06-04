"""UsageReporter 实现：把每次 LLM 调用 usage 上报 tools-server 并取回当次金额。

实现 ``matmaster.types.usage_reporter.UsageReporter`` 窄端口，供
``UsageCollectingProvider`` 在评测 run 内实时上报使用。一次 run 内复用一个
aiohttp 连接池（``aclose()`` 释放），上报失败/无定价时返回 None，不阻塞 run。
"""

from __future__ import annotations

from typing import Any

import aiohttp

from clients.billing.client import BillingRunContext, BillingService


class BillingUsageReporter:
    """把每次 usage 以 ``billing_mode`` 上报 tools-server，返回当次定价 data。"""

    def __init__(
        self,
        *,
        billing_service: BillingService,
        run_context: BillingRunContext,
        billing_mode: str = "eval",
    ) -> None:
        self._billing_service = billing_service
        self._run_context = run_context
        self._billing_mode = billing_mode
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def report_call(
        self,
        *,
        call_index: int,
        spawn_id: str | None,
        model: str,
        usage: dict[str, Any],
    ) -> dict[str, Any] | None:
        session = await self._ensure_session()
        return await self._billing_service.price_llm_usage(
            run_context=self._run_context,
            model=model,
            call_index=call_index,
            spawn_id=spawn_id,
            usage=usage,
            billing_mode=self._billing_mode,
            session=session,
        )

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
