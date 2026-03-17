"""Tool callback pipeline and MatMaster callback implementations.

All before/after tool hooks live here so that ``MatMasterAgent._step()``
stays free of inline hook logic.
"""

from .after import MatToolCallbacksAfter
from .base import MatToolCallbacksBase
from .before import MatToolCallbacksBefore
from .constants import is_error_artifact_url
from .pipeline import AfterToolCallback, BeforeToolCallback, ToolCallbackPipeline


class MatToolCallbacks(
    MatToolCallbacksBefore, MatToolCallbacksAfter, MatToolCallbacksBase
):
    """Concrete MAT callback rules.

    All before/after tool hooks are registered here so that ``_step()``
    stays free of inline hook logic.
    """


__all__ = [
    'AfterToolCallback',
    'BeforeToolCallback',
    'MatToolCallbacks',
    'ToolCallbackPipeline',
    'is_error_artifact_url',
]
