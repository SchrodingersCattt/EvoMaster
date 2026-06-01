"""LLM 金额计费上报客户端。

本仓库只负责在 Worker 完成 LLM 调用后，把一次调用的 usage 事件上报给
matmaster-tools-server（POST /api/v1/billing/usage）。定价、用量流水、对账等
权威逻辑都在 tools-server 侧，这里保持瘦客户端，不落本地账单表。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import aiohttp

from utils.env import MATMASTER_TOOLS_BILLING_BEARER, MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class BillingRunContext:
    session_id: str
    task_id: str | None
    invocation_id: str | None
    user_id: str | None
    org_id: str | None = None
    project_id: int | None = None


@dataclass(frozen=True)
class BillingModelIdentity:
    provider: str
    model: str
    model_profile: str | None
    model_route: str | None


class BillingService:
    """把一次 LLM 调用的 usage 事件上报给 tools-server 计费服务。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        bearer: str | None = None,
        timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = (base_url or MATMASTER_TOOLS_SERVER).rstrip("/")
        self._bearer = bearer if bearer is not None else MATMASTER_TOOLS_BILLING_BEARER
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._bearer:
            headers["Authorization"] = f"Bearer {self._bearer}"
        return headers

    async def report_llm_usage(
        self,
        *,
        run_context: BillingRunContext,
        model_identity: BillingModelIdentity,
        call_index: int,
        call_kind: str,
        spawn_id: str | None,
        usage: dict[str, Any] | None,
        usage_vendor: dict[str, Any] | None,
        billing_mode: str = "dry_run",
    ) -> bool:
        """上报一次 LLM 调用 usage 事件。成功记账返回 True，其余返回 False。

        网络/服务异常在此吞掉并记 warning，避免影响用户请求主链路。
        """
        if not usage and not usage_vendor:
            return False

        payload: dict[str, Any] = {
            "session_id": run_context.session_id,
            "task_id": run_context.task_id,
            "invocation_id": run_context.invocation_id,
            "spawn_id": spawn_id,
            "user_id": run_context.user_id,
            "org_id": run_context.org_id,
            "project_id": run_context.project_id,
            "call_index": call_index,
            "call_kind": call_kind,
            "provider": model_identity.provider,
            "model": model_identity.model,
            "model_profile": model_identity.model_profile,
            "model_route": model_identity.model_route,
            "usage": usage or {},
            "usage_vendor": usage_vendor,
            "billing_mode": billing_mode,
        }
        url = f"{self._base_url}/api/v1/billing/usage"
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url, headers=self._headers(), json=payload
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


@lru_cache(maxsize=1)
def get_billing_service() -> BillingService:
    return BillingService()
