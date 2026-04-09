"""Unified Bohrium job status codes and classification."""

from __future__ import annotations

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
TERMINAL_CODES: frozenset[int] = frozenset({-2, -1, 2, 4, 5, 6, 7})


def status_name(code: int) -> str:
    """Return a human-readable name for a Bohrium status code."""
    return STATUS_MAP.get(code, f"Unknown({code})")


def classify_status(code: int) -> str:
    """Classify a status code into a lifecycle category."""
    if code == SUCCESS_CODE:
        return "finished"
    if code in FAILURE_CODES:
        return "failed"
    if code in RUNNING_CODES:
        return "running"
    if code in TERMINAL_CODES:
        return "terminal"
    return "unknown"
