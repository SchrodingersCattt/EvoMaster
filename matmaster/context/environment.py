"""Runtime environment section for the system prompt.

Supplies the dynamic ``# Environment`` section: working directory and current
date, injected at runtime. Static properties of the execution image (OS,
shell, Python, preinstalled software) live in the Preinstalled environment
section of ``matmaster/exps/_base.toml``. Kept as a pure function (the date
is passed in) so it is deterministic and unit-testable, and so it stays
stable within a session for prompt caching.
"""

from __future__ import annotations

from datetime import datetime


def build_environment_section(*, execution_workdir: str, now: datetime) -> str:
    """Render the ``# Environment`` body for the system prompt.

    ``execution_workdir`` is where tools actually execute
    (``env.execution_workdir``). ``now`` must be timezone-aware; only the date
    (to the day) is rendered, so the section stays stable within a session and
    does not break prompt caching.
    """
    offset = now.strftime("%z")  # e.g. "+0800"
    tz_label = f"UTC{offset[:3]}:{offset[3:]}"
    date_str = now.strftime("%Y-%m-%d")
    return (
        "You have been invoked in the following environment:\n"
        f" - Working directory: {execution_workdir}\n"
        f" - Today is {date_str} ({tz_label})."
    )
