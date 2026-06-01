"""LLM 金额计费 dry-run 服务。"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any

from src.dao.billing_tables import (
    LLMUsageLedgerTable,
    ModelPriceCatalogTable,
    get_llm_usage_ledger_table,
    get_model_price_catalog_table,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_TOKENS_PER_PRICE_UNIT = Decimal("1000000")


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


@dataclass(frozen=True)
class StandardLLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def uncached_input_tokens(self) -> int:
        return max(
            0,
            self.input_tokens - self.cache_read_tokens - self.cache_write_tokens,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "uncached_input_tokens": self.uncached_input_tokens,
        }


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _nested_positive_int(data: dict[str, Any], *path: str) -> int:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return 0
        cur = cur.get(key)
    return _positive_int(cur)


def normalize_llm_usage(
    usage: dict[str, Any] | None,
    usage_vendor: dict[str, Any] | None = None,
) -> StandardLLMUsage:
    """统一 provider usage 为账单四类 token 口径。"""
    scalar = usage or {}
    vendor = usage_vendor or {}
    input_tokens = _positive_int(
        scalar.get("input_tokens", scalar.get("prompt_tokens"))
    )
    output_tokens = _positive_int(
        scalar.get("output_tokens", scalar.get("completion_tokens"))
    )
    cache_read_tokens = _positive_int(scalar.get("cache_read_tokens"))
    if cache_read_tokens == 0:
        cache_read_tokens = _nested_positive_int(
            vendor, "prompt_tokens_details", "cached_tokens"
        ) or _positive_int(vendor.get("cache_read_input_tokens"))
    cache_write_tokens = _positive_int(scalar.get("cache_write_tokens"))
    if cache_write_tokens == 0:
        cache_write_tokens = (
            _positive_int(vendor.get("cache_creation_input_tokens"))
            or _nested_positive_int(
                vendor, "cache_creation", "ephemeral_5m_input_tokens"
            )
            or _nested_positive_int(
                vendor, "cache_creation", "ephemeral_1h_input_tokens"
            )
            or _nested_positive_int(vendor, "cache_creation", "input_tokens")
        )
    return StandardLLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )


def _amount_micro(tokens: int, price_micro_per_million: int) -> int:
    amount = Decimal(tokens) * Decimal(price_micro_per_million) / _TOKENS_PER_PRICE_UNIT
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _stable_usage_hash(
    *,
    usage: dict[str, Any],
    usage_vendor: dict[str, Any] | None,
) -> str:
    payload = json.dumps(
        {"usage": usage, "usage_vendor": usage_vendor or {}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class BillingService:
    """写 dry-run LLM 用量金额流水。"""

    def __init__(
        self,
        *,
        price_table: ModelPriceCatalogTable | None = None,
        ledger_table: LLMUsageLedgerTable | None = None,
    ) -> None:
        self._price_table = price_table or get_model_price_catalog_table()
        self._ledger_table = ledger_table or get_llm_usage_ledger_table()

    async def record_llm_usage(
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
        """记录一次 LLM 调用。异常由调用方决定是否吞掉。"""
        if not usage and not usage_vendor:
            return False
        standard = normalize_llm_usage(usage, usage_vendor)
        if (
            standard.input_tokens == 0
            and standard.output_tokens == 0
            and standard.cache_read_tokens == 0
            and standard.cache_write_tokens == 0
        ):
            return False

        price = self._price_table.get_active_price(
            provider=model_identity.provider,
            model=model_identity.model,
            model_profile=model_identity.model_profile,
            model_route=model_identity.model_route,
        )
        pricing_status = "priced" if price else "missing_price"
        currency = str((price or {}).get("currency") or "CNY")
        price_version = (price or {}).get("version")
        input_price = int((price or {}).get("input_price_micro_per_million") or 0)
        output_price = int((price or {}).get("output_price_micro_per_million") or 0)
        cache_read_price = int(
            (price or {}).get("cache_read_price_micro_per_million") or 0
        )
        cache_write_price = int(
            (price or {}).get("cache_write_price_micro_per_million") or 0
        )

        input_amount = _amount_micro(standard.uncached_input_tokens, input_price)
        output_amount = _amount_micro(standard.output_tokens, output_price)
        cache_read_amount = _amount_micro(standard.cache_read_tokens, cache_read_price)
        cache_write_amount = _amount_micro(
            standard.cache_write_tokens, cache_write_price
        )
        total_amount = (
            input_amount + output_amount + cache_read_amount + cache_write_amount
        )
        standard_usage = standard.as_dict()
        usage_hash = _stable_usage_hash(
            usage=standard_usage,
            usage_vendor=usage_vendor,
        )
        idempotency_key = ":".join(
            (
                run_context.session_id,
                run_context.invocation_id or "-",
                spawn_id or "-",
                str(call_index),
                model_identity.provider,
                model_identity.model,
                usage_hash,
            )
        )
        return self._ledger_table.insert_usage(
            {
                "idempotency_key": idempotency_key,
                "billing_mode": billing_mode,
                "pricing_status": pricing_status,
                "user_id": run_context.user_id,
                "org_id": run_context.org_id,
                "project_id": run_context.project_id,
                "session_id": run_context.session_id,
                "task_id": run_context.task_id,
                "invocation_id": run_context.invocation_id,
                "spawn_id": spawn_id,
                "call_index": call_index,
                "call_kind": call_kind,
                "provider": model_identity.provider,
                "model": model_identity.model,
                "model_profile": model_identity.model_profile,
                "model_route": model_identity.model_route,
                "input_tokens": standard.input_tokens,
                "output_tokens": standard.output_tokens,
                "cache_read_tokens": standard.cache_read_tokens,
                "cache_write_tokens": standard.cache_write_tokens,
                "uncached_input_tokens": standard.uncached_input_tokens,
                "currency": currency,
                "price_version": price_version,
                "input_price_micro_per_million": input_price,
                "output_price_micro_per_million": output_price,
                "cache_read_price_micro_per_million": cache_read_price,
                "cache_write_price_micro_per_million": cache_write_price,
                "input_amount_micro": input_amount,
                "output_amount_micro": output_amount,
                "cache_read_amount_micro": cache_read_amount,
                "cache_write_amount_micro": cache_write_amount,
                "total_amount_micro": total_amount,
                "usage": standard_usage,
                "usage_vendor": usage_vendor,
            }
        )

    def summarize_for_reconciliation(
        self,
        *,
        start_at: str | None = None,
        end_at: str | None = None,
        billing_mode: str = "dry_run",
    ) -> list[dict[str, Any]]:
        return self._ledger_table.summarize_for_reconciliation(
            start_at=start_at,
            end_at=end_at,
            billing_mode=billing_mode,
        )


@lru_cache(maxsize=1)
def get_billing_service() -> BillingService:
    return BillingService()
