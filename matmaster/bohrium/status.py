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


def status_name(code: int) -> str:
    """Return a human-readable name for a Bohrium status code."""
    return STATUS_MAP.get(code, f"Unknown({code})")
