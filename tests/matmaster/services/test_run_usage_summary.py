"""Tests for run token-usage summary extraction from RunResultEvent."""

from __future__ import annotations

from matmaster.types.events import FinishDetail, RunResultEvent
from src.services.agent_run_service import _build_run_usage_summary


def _event(**kwargs: object) -> RunResultEvent:
    return RunResultEvent(source="agent", **kwargs)


def test_no_usage_returns_none() -> None:
    assert _build_run_usage_summary(_event(status="completed")) is None


def test_scalar_usage_computes_total() -> None:
    s = _build_run_usage_summary(
        _event(
            num_turns=2,
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "cache_read_tokens": 30,
            },
        )
    )
    assert s is not None
    assert s["num_turns"] == 2
    assert s["prompt_tokens"] == 100
    assert s["completion_tokens"] == 20
    assert s["total_tokens"] == 120
    assert s["cache_read_tokens"] == 30


def test_zero_optional_fields_are_omitted() -> None:
    s = _build_run_usage_summary(
        _event(usage={"prompt_tokens": 100, "completion_tokens": 20})
    )
    assert s is not None
    assert "cache_read_tokens" not in s
    assert "cache_write_tokens" not in s
    assert "reasoning_tokens" not in s
    assert "last_turn_usage" not in s


def test_vendor_fallback_openai_nested_cache() -> None:
    s = _build_run_usage_summary(
        _event(
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            usage_vendor_by_turn=[{"prompt_tokens_details": {"cached_tokens": 40}}],
        )
    )
    assert s is not None
    assert s["cache_read_tokens"] == 40


def test_vendor_fallback_anthropic_top_level_cache() -> None:
    s = _build_run_usage_summary(
        _event(
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            usage_vendor_by_turn=[{"cache_read_input_tokens": 25}],
        )
    )
    assert s is not None
    assert s["cache_read_tokens"] == 25


def test_scalar_cache_read_takes_precedence_over_vendor() -> None:
    s = _build_run_usage_summary(
        _event(
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "cache_read_tokens": 5,
            },
            usage_vendor_by_turn=[{"cache_read_input_tokens": 99}],
        )
    )
    assert s is not None
    assert s["cache_read_tokens"] == 5


def test_vendor_reasoning_and_cache_write() -> None:
    s = _build_run_usage_summary(
        _event(
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            usage_vendor_by_turn=[
                {
                    "cache_creation_input_tokens": 15,
                    "completion_tokens_details": {"reasoning_tokens": 10},
                }
            ],
        )
    )
    assert s is not None
    assert s["cache_write_tokens"] == 15
    assert s["reasoning_tokens"] == 10


def test_vendor_sum_across_turns() -> None:
    s = _build_run_usage_summary(
        _event(
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            usage_vendor_by_turn=[
                {"cache_read_input_tokens": 10},
                {"cache_read_input_tokens": 7},
            ],
        )
    )
    assert s is not None
    assert s["cache_read_tokens"] == 17


def test_last_turn_usage_from_finish_detail() -> None:
    fd = FinishDetail(
        kind="unknown",
        message="x",
        last_turn_usage={"prompt_tokens": 7, "completion_tokens": 3},
    )
    s = _build_run_usage_summary(
        _event(
            status="failed",
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            finish_detail=fd,
        )
    )
    assert s is not None
    assert s["last_turn_usage"] == {"prompt_tokens": 7, "completion_tokens": 3}


def test_only_last_turn_usage_still_builds_summary() -> None:
    fd = FinishDetail(
        kind="unknown",
        message="x",
        last_turn_usage={"prompt_tokens": 5, "completion_tokens": 1},
    )
    s = _build_run_usage_summary(_event(status="failed", finish_detail=fd))
    assert s is not None
    assert s["prompt_tokens"] == 0
    assert s["last_turn_usage"] == {"prompt_tokens": 5, "completion_tokens": 1}
