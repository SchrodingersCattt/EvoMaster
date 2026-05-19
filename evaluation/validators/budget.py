"""Budget validators: token, turn, and duration checks."""

from __future__ import annotations

from typing import Any

from evaluation.core.evidence import EvidenceBundle, TokenUsage
from evaluation.core.schemas import TokenUsageRecord


def _last_turn_raw_total_tokens_for_budget(rec: TokenUsageRecord) -> int:
    """Last-round reported ``total_tokens`` for budgets (no cache subtraction)."""
    if rec.total_tokens > 0:
        return rec.total_tokens
    tu = TokenUsage.from_usage_dict(
        {
            "prompt_tokens": rec.prompt_tokens,
            "completion_tokens": rec.completion_tokens,
            "total_tokens": rec.total_tokens,
            "cache_read_tokens": rec.cache_read_tokens,
        }
    )
    if tu.total_tokens > 0:
        return tu.total_tokens
    return max(0, tu.prompt_tokens + tu.completion_tokens)


def check_token_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    if evidence is None:
        return True, "no EvidenceBundle provided (skipped)"
    lt = evidence.token_usage_last_turn
    measured = lt.total_tokens
    if measured <= 0:
        tu = TokenUsage(
            prompt_tokens=lt.prompt_tokens,
            completion_tokens=lt.completion_tokens,
            total_tokens=lt.total_tokens,
            cache_read_tokens=lt.cache_read_tokens,
        )
        measured = (
            tu.total_tokens
            if tu.total_tokens > 0
            else max(0, tu.prompt_tokens + tu.completion_tokens)
        )
    if isinstance(expected, dict):
        budget = int(expected.get("max", expected.get("budget", 999_999)))
    else:
        budget = int(expected)
    hit = measured <= budget
    detail = f"last_turn_total_tokens={measured}, budget={budget}"
    return hit, detail


def check_turn_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    """Check that total agent steps (turns) do not exceed the turn budget."""
    if evidence is None:
        return True, "no EvidenceBundle provided (skipped)"
    actual = evidence.total_steps
    if isinstance(expected, dict):
        budget = int(expected.get("max", expected.get("budget", 999)))
    else:
        budget = int(expected)
    hit = actual <= budget
    return hit, f"total_steps={actual}, budget={budget}"


def check_duration_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    if evidence is None or evidence.duration_ms <= 0:
        return False, "duration_ms not recorded on evidence bundle"
    if isinstance(expected, dict):
        budget = int(expected.get("max", expected.get("budget", 86_400_000)))
    else:
        budget = int(expected)
    hit = evidence.duration_ms <= budget
    return hit, f"duration_ms={evidence.duration_ms}, budget={budget}"


def token_usage_record_from_evidence(evidence: EvidenceBundle) -> TokenUsageRecord:
    """Snapshot last LLM turn (raw total_tokens, no cache deduction)."""
    src = evidence.token_usage_last_turn
    raw_total = src.total_tokens
    return TokenUsageRecord(
        prompt_tokens=src.prompt_tokens,
        completion_tokens=src.completion_tokens,
        total_tokens=raw_total,
        cache_read_tokens=src.cache_read_tokens,
        total_tokens_effective=raw_total,
    )
