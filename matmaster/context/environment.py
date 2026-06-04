"""Runtime environment section for the system prompt.

Supplies the dynamic ``# Environment`` section. Working directory and current
date are injected at runtime; platform/shell/OS are fixed constants describing
the execution image (Bohrium lifsea8 node) where the agent's tool commands run.
Kept as a pure function (the date is passed in) so it is deterministic and
unit-testable, and so it stays stable within a session for prompt caching.
"""

from __future__ import annotations

from datetime import datetime

# Fixed properties of the execution image (Bohrium lifsea8). The agent's
# Bash/tool commands run on this Linux node, not on the host running the agent
# loop, so these describe the node -- not the developer's local machine.
EXECUTION_PLATFORM = "linux"
EXECUTION_SHELL = "/bin/bash"
EXECUTION_OS = "Ubuntu 24.04.2 LTS; kernel 5.10.134-18.0.10.lifsea8.x86_64"


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
        f" - Platform: {EXECUTION_PLATFORM}\n"
        f" - Shell: {EXECUTION_SHELL}\n"
        f" - OS Version: {EXECUTION_OS}\n"
        f" - Today is {date_str} ({tz_label})."
    )
