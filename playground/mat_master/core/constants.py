"""Shared constants for MatMaster core modules.

Single source of truth for cross-module constants that must stay in sync
between ToolGuard, ResearchPlanner, and other core components.
"""

from __future__ import annotations

MANUSCRIPT_FAIL_MARKERS: tuple[str, ...] = (
    "overall: failed",
    "still tbd",
    "empty sections",
    "missing sections",
    "missing required elements",
    "unexpected sections",
    "not contiguous",
    "missing in references",
    "references not cited",
    "[fail]",
)
