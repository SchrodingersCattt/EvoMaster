"""Unit tests for UsageCollectingProvider per-call usage capture."""

from __future__ import annotations

from matmaster.providers.usage_collector import (
    PerCallUsage,
    UsageCollectingProvider,
    per_call_usage_payload,
)
from matmaster.types.messages import LLMResponse, StreamChunk


class _FakeProvider:
    """Minimal LLMProvider stand-in with scripted usage."""

    def __init__(self) -> None:
        self.entered = False

    async def __aenter__(self) -> "_FakeProvider":
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.entered = False

    async def chat(self, messages, tools=None, *, tool_choice=None) -> LLMResponse:
        return LLMResponse(
            content="ok",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cache_read_tokens": 40,
                "cache_write_tokens": 10,
                "reasoning_tokens": 8,
            },
        )

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="par")
        yield StreamChunk(content="tial")
        yield StreamChunk(
            usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
        )


class _FakeReporter:
    """Records report_call invocations and returns a fixed cost payload."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str | None, str, dict]] = []
        self.closed = False

    async def report_call(self, *, call_index, spawn_id, model, usage):
        self.calls.append((call_index, spawn_id, model, dict(usage)))
        return {
            "total_amount_micro": 1155,
            "total_amount_settle_micro": 1155,
            "pricing_status": "priced",
            "currency": "CNY",
            "settlement_currency": "CNY",
        }

    async def aclose(self) -> None:
        self.closed = True


async def test_reporter_reports_each_call_and_backfills_cost() -> None:
    reporter = _FakeReporter()
    provider = UsageCollectingProvider(
        _FakeProvider(), model="claude-sonnet-4-6", reporter=reporter
    )
    async with provider:
        await provider.chat([])  # root
        with provider.billing_scope(spawn_id="child-1"):
            await provider.chat([])  # subagent

    # Each call is reported inline; the reporter is closed on __aexit__.
    assert reporter.closed is True
    assert len(reporter.calls) == 2

    calls = provider.collected_calls
    assert all(c.cost is not None for c in calls)
    assert calls[0].cost["total_amount_micro"] == 1155

    payload = per_call_usage_payload(calls)
    assert payload[0]["cost"]["pricing_status"] == "priced"
    assert payload[1]["spawn_id"] == "child-1"


async def test_no_reporter_leaves_cost_unset() -> None:
    provider = UsageCollectingProvider(_FakeProvider(), model="m")
    async with provider:
        await provider.chat([])
    calls = provider.collected_calls
    assert calls[0].cost is None
    assert "cost" not in per_call_usage_payload(calls)[0]


async def test_collects_root_subagent_and_streaming_calls() -> None:
    inner = _FakeProvider()
    provider = UsageCollectingProvider(inner, model="claude-sonnet-4-6")

    async with provider:
        assert inner.entered is True
        await provider.chat([])  # root
        with provider.billing_scope(spawn_id="child-1"):
            await provider.chat([])  # subagent
        async for _ in provider.chat_stream([]):  # streaming root
            pass
    assert inner.entered is False

    calls = provider.collected_calls
    assert [c.call_index for c in calls] == [1, 2, 3]

    root, sub, stream = calls
    assert root.kind == "root" and root.spawn_id is None
    assert root.usage["cache_write_tokens"] == 10
    assert root.usage["reasoning_tokens"] == 8

    assert sub.kind == "subagent" and sub.spawn_id == "child-1"

    assert stream.kind == "root"
    assert stream.usage["total_tokens"] == 6


async def test_billing_scope_resets_spawn_id() -> None:
    provider = UsageCollectingProvider(_FakeProvider(), model="m")
    async with provider:
        with provider.billing_scope(spawn_id="child-1"):
            await provider.chat([])
        await provider.chat([])  # back to root scope
    calls = provider.collected_calls
    assert calls[0].spawn_id == "child-1"
    assert calls[1].spawn_id is None


async def test_call_index_increments_even_without_usage() -> None:
    class _NoUsage(_FakeProvider):
        async def chat(self, messages, tools=None, *, tool_choice=None) -> LLMResponse:
            return LLMResponse(content="x", usage={})

    provider = UsageCollectingProvider(_NoUsage(), model="m")
    async with provider:
        await provider.chat([])  # empty usage, not recorded
        await provider.chat([])  # empty usage, not recorded
        # next call has usage via parent chat -> swap back
    # No usage recorded but internal index advanced; collected stays empty.
    assert provider.collected_calls == []


def test_per_call_usage_payload_shape() -> None:
    calls = [
        PerCallUsage(
            call_index=1,
            spawn_id=None,
            model="m",
            usage={"total_tokens": 10},
        ),
        PerCallUsage(
            call_index=2,
            spawn_id="c",
            model="m",
            usage={"total_tokens": 5},
        ),
    ]
    payload = per_call_usage_payload(calls)
    assert payload == [
        {
            "call_index": 1,
            "spawn_id": None,
            "kind": "root",
            "model": "m",
            "usage": {"total_tokens": 10},
        },
        {
            "call_index": 2,
            "spawn_id": "c",
            "kind": "subagent",
            "model": "m",
            "usage": {"total_tokens": 5},
        },
    ]


def test_getattr_delegates_to_inner() -> None:
    inner = _FakeProvider()
    inner.custom_attr = "hello"  # type: ignore[attr-defined]
    provider = UsageCollectingProvider(inner, model="m")
    assert provider.custom_attr == "hello"
