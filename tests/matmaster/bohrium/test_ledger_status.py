from __future__ import annotations

import pytest

from matmaster.bohrium.status import LedgerStatusDecision, to_ledger_status


@pytest.mark.parametrize(
    "code,status,is_terminal",
    [
        (-10, "running", False),
        (0, "running", False),
        (1, "running", False),
        (3, "running", False),
        (8, "running", False),
        (9, "running", False),
        (4, "terminating", False),
        (6, "terminating", False),
        (7, "terminating", False),
        (2, "finished", True),
        (-1, "failed", True),
        (-2, "stopped", True),
        (5, "stopped", True),
        (999, "unknown", False),
        (-999, "unknown", False),
    ],
)
def test_to_ledger_status_maps_platform_codes(
    code: int, status: str, is_terminal: bool
) -> None:
    decision = to_ledger_status(code)
    assert isinstance(decision, LedgerStatusDecision)
    assert decision.status == status
    assert decision.is_terminal is is_terminal


def test_terminating_and_unknown_keep_polling() -> None:
    assert to_ledger_status(7).is_terminal is False
    assert to_ledger_status(123).is_terminal is False
