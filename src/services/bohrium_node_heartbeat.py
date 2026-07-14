"""Run-owned heartbeat for one fenced Bohrium Node invocation lease."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.bohrium_node_lifecycle import (
        BohriumNodeLeaseManager,
        NodeLease,
    )

logger = logging.getLogger(__name__)


class NodeLeaseHeartbeat:
    """Renew a lease until its owning run finishes or loses the fence."""

    def __init__(
        self,
        manager: BohriumNodeLeaseManager,
        lease: NodeLease,
        *,
        interval_seconds: float = 30.0,
    ) -> None:
        self._manager = manager
        self._lease = lease
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"bohrium-node-lease-{self._lease.invocation_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self._interval_seconds + 1.0))

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                if not self._manager.heartbeat(self._lease):
                    logger.warning(
                        "Bohrium node lease heartbeat fenced invocation_id=%s",
                        self._lease.invocation_id,
                    )
                    return
            except Exception:
                logger.warning(
                    "Bohrium node lease heartbeat failed invocation_id=%s",
                    self._lease.invocation_id,
                    exc_info=True,
                )
