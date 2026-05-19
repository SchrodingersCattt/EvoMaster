"""Budget validators: token, turn, and duration checks.

Pure functions — accept measured values and budget, not EvidenceBundle.
"""

from __future__ import annotations

from typing import Any

from evaluation.core.schemas import TokenUsageRecord


def _last_turn_raw_total_tokens_for_budget(rec: TokenUsageRecord) -> int:
    """Last-round reported ``total_tokens`` for budgets (no cache subtraction)."""
    if rec.total_tokens > 0:
        return rec.total_tokens
    return max(0, rec.prompt_tokens + rec.completion_tokens)


def check_token_budget(*, measured_tokens: int, expected: Any) -> tuple[bool, str]:
    """Check last-turn token count against budget."""
    if isinstance(expected, dict):
        budget = int(expected.get("max", expected.get("budget", 999_999)))
    else:
        budget = int(expected)
    hit = measured_tokens <= budget
    return hit, f"last_turn_total_tokens={measured_tokens}, budget={budget}"


def check_turn_budget(*, total_steps: int, expected: Any) -> tuple[bool, str]:
    """Check that total agent steps do not exceed the turn budget."""
    if isinstance(expected, dict):
        budget = int(expected.get("max", expected.get("budget", 999)))
    else:
        budget = int(expected)
    hit = total_steps <= budget
    return hit, f"total_steps={total_steps}, budget={budget}"


def check_duration_budget(*, duration_ms: int, expected: Any) -> tuple[bool, str]:
    """Check that run duration does not exceed the duration budget."""
    if duration_ms <= 0:
        return False, "duration_ms not recorded"
    if isinstance(expected, dict):
        budget = int(expected.get("max", expected.get("budget", 86_400_000)))
    else:
        budget = int(expected)
    hit = duration_ms <= budget
    return hit, f"duration_ms={duration_ms}, budget={budget}"
