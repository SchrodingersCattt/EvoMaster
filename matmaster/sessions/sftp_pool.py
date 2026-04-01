"""SFTP connection pool with semaphore-based concurrency control.

Manages a bounded pool of paramiko SFTPClient instances on a single
SSH transport. Supports lazy creation, health-check on release, and
generation-safe close_all for reconnection scenarios.
"""
from __future__ import annotations

import collections
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

try:
    import paramiko
except ImportError:  # pragma: no cover
    paramiko = None  # type: ignore[assignment]


class SFTPPool:
    """Bounded pool of SFTP clients sharing one SSH transport."""

    def __init__(self, transport: Any, max_size: int = 4) -> None:
        self._transport = transport
        self._max_size = max_size
        self._pool: collections.deque = collections.deque()
        self._created: int = 0
        self._semaphore = threading.Semaphore(max_size)
        self._lock = threading.Lock()

    def acquire(self) -> Any:
        """Acquire an SFTP client from the pool (blocking if exhausted)."""
        self._semaphore.acquire()
        with self._lock:
            if self._pool:
                return self._pool.popleft()
            self._created += 1
        try:
            client = self._transport.open_sftp_client()
            logger.debug("sftp_pool: created new client (total=%d)", self._created)
            return client
        except Exception:
            with self._lock:
                self._created -= 1
            self._semaphore.release()
            raise

    def release(self, sftp: Any) -> None:
        """Return an SFTP client to the pool (discards if unhealthy)."""
        try:
            sftp.stat('.')
        except Exception:
            try:
                sftp.close()
            except Exception:
                pass
            with self._lock:
                self._created -= 1
            self._semaphore.release()
            logger.debug("sftp_pool: discarded dead client (total=%d)", self._created)
            return
        with self._lock:
            self._pool.append(sftp)
        self._semaphore.release()

    def close_all(self) -> None:
        """Close all pooled clients. In-flight clients are not affected."""
        with self._lock:
            while self._pool:
                try:
                    self._pool.popleft().close()
                except Exception:
                    pass
            self._created = 0
        logger.debug("sftp_pool: close_all completed")
