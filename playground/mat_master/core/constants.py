"""Shared constants for MatMaster core modules.

Single source of truth for cross-module constants that must stay in sync
between ToolGuard, ResearchPlanner, and other core components.
"""

MANUSCRIPT_FAIL_MARKERS: tuple[str, ...] = (
    'overall: failed',
    'still tbd',  # matches Check 4 output ("still TBD" lowercased)
    'placeholder sections',  # direct match for Check 4 header line
    'empty sections',
    'missing sections',
    'missing required elements',
    'unexpected sections',
    'not contiguous',
    'missing in references',
    'references not cited',
    '[fail]',
)
