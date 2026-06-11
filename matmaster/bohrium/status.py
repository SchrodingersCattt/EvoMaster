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


# ledger status 词汇表的唯一定义点；DAO 的 SQL 谓词由此插值生成。
# lost 不来自平台状态码映射：poller 连续失联超阈值时由 mark_poll_error 置位。
LEDGER_ACTIVE_STATUSES = ("submitted", "running", "terminating", "unknown")
LEDGER_TERMINAL_STATUSES = ("finished", "failed", "stopped", "lost")
LEDGER_FAILURE_STATUSES = ("failed", "stopped", "lost")

_LEDGER_TERMINATING_CODES = frozenset({4, 6, 7})
_LEDGER_STOPPED_CODES = frozenset({-2, 5})
_LEDGER_FAILED_CODE = -1


def to_ledger_status(code: int) -> LedgerStatusDecision:
    """把 Bohrium 平台状态码映射为 ledger status。"""
    if code in RUNNING_CODES:
        status = "running"
    elif code in _LEDGER_TERMINATING_CODES:
        status = "terminating"
    elif code == SUCCESS_CODE:
        status = "finished"
    elif code == _LEDGER_FAILED_CODE:
        status = "failed"
    elif code in _LEDGER_STOPPED_CODES:
        status = "stopped"
    else:
        status = "unknown"
    return LedgerStatusDecision(status, status in LEDGER_TERMINAL_STATUSES)
