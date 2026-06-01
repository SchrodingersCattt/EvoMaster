import pytest

from src.services.billing_service import (
    BillingModelIdentity,
    BillingRunContext,
    BillingService,
    normalize_llm_usage,
)


class FakePriceTable:
    def __init__(self, price):
        self.price = price

    def get_active_price(self, **_kwargs):
        return self.price


class FakeLedgerTable:
    def __init__(self):
        self.rows = []

    def insert_usage(self, row):
        self.rows.append(row)
        return True

    def summarize_for_reconciliation(self, **_kwargs):
        return []


def test_normalize_llm_usage_extracts_four_token_classes():
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "cache_read_tokens": 300,
    }
    vendor = {
        "cache_creation_input_tokens": 100,
    }

    standard = normalize_llm_usage(usage, vendor)

    assert standard.input_tokens == 1000
    assert standard.output_tokens == 200
    assert standard.cache_read_tokens == 300
    assert standard.cache_write_tokens == 100
    assert standard.uncached_input_tokens == 600


@pytest.mark.asyncio
async def test_record_llm_usage_writes_priced_dry_run_ledger_row():
    ledger = FakeLedgerTable()
    service = BillingService(
        price_table=FakePriceTable(
            {
                "currency": "CNY",
                "version": "2026-06-01",
                "input_price_micro_per_million": 1_000_000,
                "output_price_micro_per_million": 2_000_000,
                "cache_read_price_micro_per_million": 100_000,
                "cache_write_price_micro_per_million": 1_250_000,
            }
        ),
        ledger_table=ledger,
    )

    ok = await service.record_llm_usage(
        run_context=BillingRunContext(
            session_id="s1",
            task_id="t1",
            invocation_id="i1",
            user_id="u1",
        ),
        model_identity=BillingModelIdentity(
            provider="openai",
            model="claude-sonnet-4-6",
            model_profile="sonnet",
            model_route="claude-sonnet-4-6",
        ),
        call_index=1,
        call_kind="chat_stream",
        spawn_id=None,
        usage={
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "cache_read_tokens": 300,
            "cache_write_tokens": 100,
        },
        usage_vendor={"cache_creation_input_tokens": 100},
    )

    assert ok is True
    row = ledger.rows[0]
    assert row["pricing_status"] == "priced"
    assert row["price_version"] == "2026-06-01"
    assert row["uncached_input_tokens"] == 600
    assert row["input_amount_micro"] == 600
    assert row["output_amount_micro"] == 400
    assert row["cache_read_amount_micro"] == 30
    assert row["cache_write_amount_micro"] == 125
    assert row["total_amount_micro"] == 1155
    assert row["idempotency_key"].startswith("s1:i1:-:1:openai:")


@pytest.mark.asyncio
async def test_record_llm_usage_allows_missing_price_for_dry_run():
    ledger = FakeLedgerTable()
    service = BillingService(
        price_table=FakePriceTable(None),
        ledger_table=ledger,
    )

    await service.record_llm_usage(
        run_context=BillingRunContext(
            session_id="s1",
            task_id=None,
            invocation_id=None,
            user_id=None,
        ),
        model_identity=BillingModelIdentity(
            provider="openai",
            model="unknown",
            model_profile=None,
            model_route=None,
        ),
        call_index=1,
        call_kind="chat",
        spawn_id="child",
        usage={"prompt_tokens": 10},
        usage_vendor=None,
    )

    row = ledger.rows[0]
    assert row["pricing_status"] == "missing_price"
    assert row["total_amount_micro"] == 0
    assert row["price_version"] is None
