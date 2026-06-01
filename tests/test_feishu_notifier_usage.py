"""Tests for token-usage row rendering in the Feishu notifier."""

from __future__ import annotations

from src.utils.feishu_notifier import format_usage_rows


def _val(rows: list[tuple[str, str]], label: str) -> str:
    return next(v for k, v in rows if k == label)


def test_none_and_empty_return_no_rows() -> None:
    assert format_usage_rows(None) == []
    assert format_usage_rows({}) == []


def test_all_zero_usage_returns_no_rows() -> None:
    assert (
        format_usage_rows(
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        == []
    )


def test_full_summary_renders_total_and_breakdown() -> None:
    rows = format_usage_rows(
        {
            "num_turns": 3,
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
            "cache_read_tokens": 800,
            "reasoning_tokens": 50,
        }
    )
    labels = [k for k, _ in rows]
    assert labels == ["Token 消耗", "LLM 轮数"]

    total_val = _val(rows, "Token 消耗")
    assert "1,200" in total_val
    assert "输入 1,000" in total_val
    assert "输出 200" in total_val
    assert "缓存命中 800 (80.0%)" in total_val
    assert "推理 50" in total_val
    assert _val(rows, "LLM 轮数") == "3"


def test_total_falls_back_to_prompt_plus_completion() -> None:
    rows = format_usage_rows({"prompt_tokens": 1000, "completion_tokens": 200})
    assert "1,200" in _val(rows, "Token 消耗")


def test_cache_hit_rate_divides_by_prompt() -> None:
    rows = format_usage_rows(
        {"prompt_tokens": 200, "completion_tokens": 10, "cache_read_tokens": 50}
    )
    # 50 / 200 = 25.0%
    assert "缓存命中 50 (25.0%)" in _val(rows, "Token 消耗")


def test_cache_hit_without_prompt_shows_count_only() -> None:
    rows = format_usage_rows(
        {"prompt_tokens": 0, "completion_tokens": 100, "cache_read_tokens": 50}
    )
    val = _val(rows, "Token 消耗")
    assert "缓存命中 50" in val
    # 无 prompt 时不算百分比，也不应出现半角括号（百分比）
    assert "(" not in val


def test_cache_write_rendered_when_present() -> None:
    rows = format_usage_rows(
        {"prompt_tokens": 100, "completion_tokens": 10, "cache_write_tokens": 30}
    )
    assert "缓存写入 30" in _val(rows, "Token 消耗")


def test_aggregate_usage_rows_include_cache_write_and_reasoning_from_scalar() -> None:
    rows = format_usage_rows(
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "cache_write_tokens": 30,
            "reasoning_tokens": 7,
        }
    )

    text = "\n".join(value for _, value in rows)
    assert "缓存写入 30" in text
    assert "推理 7" in text


def test_last_turn_usage_adds_row() -> None:
    rows = format_usage_rows(
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "last_turn_usage": {"prompt_tokens": 40, "completion_tokens": 6},
        }
    )
    last_turn = _val(rows, "末轮 Token")
    assert "46" in last_turn  # 40 + 6
    assert "输入 40" in last_turn
    assert "输出 6" in last_turn


def test_zero_num_turns_omits_turns_row() -> None:
    rows = format_usage_rows(
        {"prompt_tokens": 100, "completion_tokens": 10, "num_turns": 0}
    )
    assert all(k != "LLM 轮数" for k, _ in rows)
