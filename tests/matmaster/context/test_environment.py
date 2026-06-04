"""Tests for the runtime environment section builder.

Covers: all fields rendered, date rendered to the day only, tz label derived
from now's tzinfo, and pure-function behaviour (output follows the now arg).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from matmaster.context.environment import (
    EXECUTION_OS,
    EXECUTION_PLATFORM,
    EXECUTION_SHELL,
    build_environment_section,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_section_contains_all_fields() -> None:
    section = build_environment_section(
        execution_workdir="/share/projects/run1",
        now=datetime(2026, 6, 3, 14, 13, tzinfo=SHANGHAI),
    )
    assert "You have been invoked in the following environment:" in section
    assert " - Working directory: /share/projects/run1" in section
    assert f" - Platform: {EXECUTION_PLATFORM}" in section
    assert f" - Shell: {EXECUTION_SHELL}" in section
    assert f" - OS Version: {EXECUTION_OS}" in section


def test_date_rendered_to_the_day_with_tz_label() -> None:
    section = build_environment_section(
        execution_workdir="/share",
        now=datetime(2026, 6, 3, 14, 13, 59, tzinfo=SHANGHAI),
    )
    # Only the date (to the day) is rendered -- no time component, so the
    # section stays stable within a session and does not break prompt caching.
    assert " - Today is 2026-06-03 (UTC+08:00)." in section
    assert "14:13" not in section


def test_date_follows_now_argument() -> None:
    """Pure function: a different now yields a different date."""
    section = build_environment_section(
        execution_workdir="/share",
        now=datetime(2025, 1, 1, tzinfo=SHANGHAI),
    )
    assert "2025-01-01" in section


def test_tz_label_follows_now_timezone() -> None:
    """The tz label is derived from now's tzinfo, not hardcoded."""
    section = build_environment_section(
        execution_workdir="/share",
        now=datetime(2026, 6, 3, tzinfo=ZoneInfo("UTC")),
    )
    assert "(UTC+00:00)" in section
