from __future__ import annotations

from matmaster.bohrium.status import (
    RUNNING_CODES,
    STATUS_MAP,
    status_name,
)


def test_status_name_known_code() -> None:
    assert status_name(1) == "Running"
    assert status_name(2) == "Finished"
    assert status_name(-1) == "Failed"


def test_status_name_unknown_code() -> None:
    assert status_name(999) == "Unknown(999)"


def test_uploading_and_wait_are_running() -> None:
    """Regression: status 8 (Uploading) and 9 (Wait) must be treated as running."""
    assert 8 in RUNNING_CODES
    assert 9 in RUNNING_CODES


def test_status_map_superset() -> None:
    """Verify STATUS_MAP contains all codes from both old implementations."""
    old_jobs_codes = {-1, -2, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9}
    old_api_codes = {-10, -2, -1, 0, 1, 2, 3, 6}
    expected = old_jobs_codes | old_api_codes
    assert expected <= set(STATUS_MAP.keys())
