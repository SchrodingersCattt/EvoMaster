"""Unified Bohrium job status codes and classification."""

from __future__ import annotations

from dataclasses import dataclass

STATUS_MAP: dict[int, str] = {
    -10: "Prepared",
    -2: "Deleted",
    -1: "Failed",
    0: "Pending",
    1: "Running",
    2: "Finished",
    3: "Scheduling",
    4: "Stopping",
    5: "Stopped",
    6: "Terminating",
    7: "Killing",
    8: "Uploading",
    9: "Wait",
}

SUCCESS_CODE: int = 2
RUNNING_CODES: frozenset[int] = frozenset({-10, 0, 1, 3, 8, 9})
FAILURE_CODES: frozenset[int] = frozenset({-2, -1})


def status_name(code: int) -> str:
    """Return a human-readable name for a Bohrium status code."""
    return STATUS_MAP.get(code, f"Unknown({code})")


@dataclass(frozen=True)
class LedgerStatusDecision:
    """平台状态码归一化到 ledger 语义的结果。"""

    status: str
    is_terminal: bool


_LEDGER_RUNNING_CODES = frozenset({-10, 0, 1, 3, 8, 9})
_LEDGER_TERMINATING_CODES = frozenset({4, 6, 7})
_LEDGER_STOPPED_CODES = frozenset({-2, 5})
_LEDGER_FINISHED_CODE = 2
_LEDGER_FAILED_CODE = -1


def to_ledger_status(code: int) -> LedgerStatusDecision:
    """把 Bohrium 平台状态码映射为 ledger status。"""
    if code in _LEDGER_RUNNING_CODES:
        return LedgerStatusDecision("running", False)
    if code in _LEDGER_TERMINATING_CODES:
        return LedgerStatusDecision("terminating", False)
    if code == _LEDGER_FINISHED_CODE:
        return LedgerStatusDecision("finished", True)
    if code == _LEDGER_FAILED_CODE:
        return LedgerStatusDecision("failed", True)
    if code in _LEDGER_STOPPED_CODES:
        return LedgerStatusDecision("stopped", True)
    return LedgerStatusDecision("unknown", False)
