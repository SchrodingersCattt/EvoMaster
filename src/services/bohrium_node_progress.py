"""Best-effort progress reporting for Bohrium Node acquisition."""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

NodeProgressReporter = Callable[[str, int | None, str], None]


def report_node_progress(
    reporter: NodeProgressReporter | None,
    status: str,
    node_id: int | None,
    message: str,
) -> None:
    """Report UI progress without allowing display failures to abort setup."""
    if reporter is None:
        return
    try:
        reporter(status, node_id, message)
    except Exception:
        logger.warning(
            "Bohrium node progress reporter failed status=%s node_id=%s",
            status,
            node_id,
            exc_info=True,
        )
