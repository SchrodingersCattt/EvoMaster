"""Baseline CLI: prefer last embedded usage when present."""

from __future__ import annotations

from evaluation.scripts.baseline.run_claude_cli_baseline_tasks import (
    _build_usage,
    _extract_last_turn_usage_raw,
)


def test_extract_last_turn_from_messages() -> None:
    cli = {
        "usage": {"input_tokens": 999},
        "messages": [
            {"role": "user", "usage": {}},
            {
                "role": "assistant",
                "usage": {
                    "input_tokens": 1,
                    "cache_creation_input_tokens": 2,
                    "cache_read_input_tokens": 3,
                    "output_tokens": 4,
                },
            },
        ],
    }
    raw = _extract_last_turn_usage_raw(cli)
    assert raw is not None
    assert raw.get("input_tokens") == 1


def test_build_usage_prefers_last_message_over_top_level() -> None:
    cli = {
        "usage": {
            "input_tokens": 100,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 50,
        },
        "messages": [
            {
                "role": "assistant",
                "usage": {
                    "input_tokens": 5,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 1,
                },
            },
        ],
    }
    u = _build_usage(cli)
    assert u["input_tokens"] == 5
    assert u["prompt_tokens"] == 5
    assert u["completion_tokens"] == 1
