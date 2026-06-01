"""Admin-only endpoints for LLM billing dry-run reconciliation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.services.billing_service import get_billing_service
from src.services.tools_server_allowlist import is_user_in_admin_allowlist
from src.services.user_service import UserService
from src.utils.exceptions import ForbiddenErrorResponse

router = APIRouter(tags=["Admin Billing"])


def _require_admin(user_id: str = Depends(UserService.require_user_id)) -> str:
    if not is_user_in_admin_allowlist(user_id):
        raise ForbiddenErrorResponse(msg="Admin access required")
    return user_id


class BillingReconciliationResponse(BaseModel):
    billing_mode: str
    rows: list[dict[str, Any]]


@router.get(
    "/llm-usage/reconciliation",
    response_model=BillingReconciliationResponse,
    summary="汇总 LLM dry-run 计费流水（admin）",
    operation_id="adminSummarizeLLMBillingReconciliation",
)
def summarize_llm_usage_reconciliation(
    _admin: str = Depends(_require_admin),
    since: datetime | None = Query(None, description="起始时间 (ISO 8601)"),
    until: datetime | None = Query(None, description="截止时间 (ISO 8601)"),
    billing_mode: str = Query("dry_run", description="账单模式: dry_run/charge"),
) -> BillingReconciliationResponse:
    rows = get_billing_service().summarize_for_reconciliation(
        start_at=since.isoformat(sep=" ") if since else None,
        end_at=until.isoformat(sep=" ") if until else None,
        billing_mode=billing_mode,
    )
    return BillingReconciliationResponse(billing_mode=billing_mode, rows=rows)
