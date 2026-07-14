"""Best-effort progress reporting for Bohrium Node acquisition."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

logger = logging.getLogger(__name__)

BohriumNodeStatus = Literal[
    "acquiring",
    "waiting",
    "creating",
    "restarting",
    "starting",
    "ready",
    "connecting",
    "connected",
    "paused",
    "destroyed",
    "failed",
]

NodeProgressReporter = Callable[[BohriumNodeStatus, int | None, str], None]


def report_node_progress(
    reporter: NodeProgressReporter | None,
    status: BohriumNodeStatus,
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
