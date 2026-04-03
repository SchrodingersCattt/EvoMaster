"""ToolScheduler -- resource-aware concurrent tool scheduling.

Uses per-resource read-write locks (exclusive/shared_read) and semaphores
(counted) to manage concurrent tool access to shared resources.

_RWLock implements a classic readers-writer lock using only asyncio
primitives (Lock + Condition + counters). No third-party lock libraries.

SchedulerTicket is the acquire receipt -- holds which resources were
acquired and in what mode, enabling targeted release.

ToolScheduler is the public interface:
  acquire(claims, timeout) -> SchedulerTicket | None
  release(ticket) -> None
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from matmaster.types.tool_spec import ResourceClaim

logger = logging.getLogger(__name__)


class _RWLock:
    """Classic readers-writer lock using asyncio primitives.

    - shared_read: multiple readers allowed concurrently
    - exclusive: single writer, blocks all readers and other writers
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)
        self._readers: int = 0
        self._writer: bool = False

    async def acquire_read(self, timeout: float) -> bool:
        """Acquire shared read access. Returns False on timeout."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout

        async with self._lock:
            while self._writer:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                except TimeoutError:
                    return False
            self._readers += 1
            return True

    async def release_read(self) -> None:
        """Release shared read access."""
        async with self._lock:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    async def acquire_write(self, timeout: float) -> bool:
        """Acquire exclusive write access. Returns False on timeout."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout

        async with self._lock:
            while self._writer or self._readers > 0:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                except TimeoutError:
                    return False
            self._writer = True
            return True

    async def release_write(self) -> None:
        """Release exclusive write access."""
        async with self._lock:
            self._writer = False
            self._cond.notify_all()


@dataclass
class SchedulerTicket:
    """Acquire receipt -- tracks which resources were locked and how.

    resource_locks is a list of (resource, mode) tuples, recorded
    in acquisition order. Release iterates in reverse order.
    """

    resource_locks: list[tuple[str, str]] = field(default_factory=list)


class ToolScheduler:
    """Resource-aware tool scheduler using RWLock and Semaphore.

    Manages per-resource concurrency:
    - exclusive: single access via _RWLock write lock
    - shared_read: concurrent reads via _RWLock read lock
    - counted: semaphore-limited concurrency via asyncio.Semaphore
    """

    def __init__(self, default_timeout: float = 60.0) -> None:
        self._default_timeout = default_timeout
        self._rw_locks: dict[str, _RWLock] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def _get_rw_lock(self, resource: str) -> _RWLock:
        """Get or lazily create a _RWLock for the given resource."""
        if resource not in self._rw_locks:
            self._rw_locks[resource] = _RWLock()
        return self._rw_locks[resource]

    def _get_semaphore(self, resource: str, max_concurrent: int) -> asyncio.Semaphore:
        """Get or lazily create a Semaphore for the given resource.

        If max_concurrent is <= 0, defensively defaults to 1 with a warning.
        Once created, the semaphore for a resource is reused (max_concurrent
        from first creation wins).
        """
        if resource not in self._semaphores:
            if max_concurrent <= 0:
                logger.warning(
                    "counted resource %r has max_concurrent=%r, defaulting to 1",
                    resource,
                    max_concurrent,
                )
                max_concurrent = 1
            self._semaphores[resource] = asyncio.Semaphore(max_concurrent)
        return self._semaphores[resource]

    async def acquire(
        self,
        claims: tuple[ResourceClaim, ...],
        timeout: float | None = None,
    ) -> SchedulerTicket | None:
        """Acquire all claimed resources. Returns SchedulerTicket on success, None on timeout.

        Claims are acquired sequentially. If any claim fails (timeout),
        all previously acquired claims are rolled back and None is returned.
        """
        if timeout is None:
            timeout = self._default_timeout

        acquired: list[tuple[str, str]] = []  # (resource, mode)

        for claim in claims:
            ok = await self._acquire_single(claim, timeout)
            if not ok:
                # Rollback already acquired
                await self._release_acquired(acquired)
                return None
            acquired.append((claim.resource, claim.mode))

        return SchedulerTicket(resource_locks=acquired)

    async def release(self, ticket: SchedulerTicket) -> None:
        """Release all resources held by the ticket, in reverse acquisition order."""
        for resource, mode in reversed(ticket.resource_locks):
            await self._release_single(resource, mode)

    async def _acquire_single(self, claim: ResourceClaim, timeout: float) -> bool:
        """Acquire a single resource claim. Returns True on success."""
        if claim.mode == "exclusive":
            return await self._get_rw_lock(claim.resource).acquire_write(timeout)
        elif claim.mode == "shared_read":
            return await self._get_rw_lock(claim.resource).acquire_read(timeout)
        elif claim.mode == "counted":
            sem = self._get_semaphore(claim.resource, claim.max_concurrent or 1)
            try:
                await asyncio.wait_for(sem.acquire(), timeout=timeout)
                return True
            except TimeoutError:
                return False
        else:
            logger.error("Unknown claim mode: %r", claim.mode)
            return False

    async def _release_single(self, resource: str, mode: str) -> None:
        """Release a single resource by id and mode."""
        if mode == "exclusive":
            await self._rw_locks[resource].release_write()
        elif mode == "shared_read":
            await self._rw_locks[resource].release_read()
        elif mode == "counted":
            self._semaphores[resource].release()
        else:
            logger.error("Unknown release mode: %r for resource %r", mode, resource)

    async def _release_acquired(self, acquired: list[tuple[str, str]]) -> None:
        """Rollback: release resources in reverse order."""
        for resource, mode in reversed(acquired):
            await self._release_single(resource, mode)
