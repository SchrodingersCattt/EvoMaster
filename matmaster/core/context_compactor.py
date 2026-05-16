"""Compatibility shim for the Phase 3 context compaction move.

The real implementation lives in `matmaster.context.compaction`.
This shim stays until Phase 4 removes legacy core import paths.
"""

from matmaster.context.compaction import (  # noqa: F401
    CURRENT_INPUT_CONTINUATION_INSTRUCTION,
    SUMMARY_SYSTEM_PROMPT,
    CompactionPlan,
    CompactionResult,
    ContextCompactor,
    estimate_tokens,
    parse_turns,
)
