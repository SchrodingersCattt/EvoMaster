"""Tests for ToolScheduler -- resource-aware tool scheduling.

Tests cover:
- Exclusive mode: mutual exclusion on same resource
- SharedRead mode: concurrent reads allowed, exclusive blocked during read
- Counted mode: semaphore-based concurrency control
- Timeout behavior: acquire returns None on timeout
- MultiResource: multiple claims in single acquire
- CountedMaxConcurrentZero: defensive handling when max_concurrent <= 0
"""

from __future__ import annotations

import asyncio

import pytest

from matmaster.core.tool_scheduler import SchedulerTicket, ToolScheduler
from matmaster.types.tool_spec import ResourceClaim


class TestExclusive:
    """Exclusive mode claims provide mutual exclusion."""

    async def test_second_exclusive_times_out_while_first_held(self) -> None:
        """Two exclusive claims on same resource: second times out while first held."""
        scheduler = ToolScheduler()
        claim = ResourceClaim(resource="shell", mode="exclusive")

        ticket1 = await scheduler.acquire((claim,), timeout=1.0)
        assert ticket1 is not None

        # Second acquire should time out
        ticket2 = await scheduler.acquire((claim,), timeout=0.1)
        assert ticket2 is None

    async def test_second_exclusive_succeeds_after_release(self) -> None:
        """After first release, second exclusive acquire succeeds."""
        scheduler = ToolScheduler()
        claim = ResourceClaim(resource="shell", mode="exclusive")

        ticket1 = await scheduler.acquire((claim,), timeout=1.0)
        assert ticket1 is not None

        await scheduler.release(ticket1)

        ticket2 = await scheduler.acquire((claim,), timeout=1.0)
        assert ticket2 is not None
        await scheduler.release(ticket2)


class TestSharedRead:
    """SharedRead mode allows concurrent readers but blocks exclusive."""

    async def test_concurrent_shared_reads_succeed(self) -> None:
        """Two shared_read claims on same resource both succeed concurrently."""
        scheduler = ToolScheduler()
        claim = ResourceClaim(resource="file:data.csv", mode="shared_read")

        async def acquire_shared() -> SchedulerTicket | None:
            return await scheduler.acquire((claim,), timeout=1.0)

        t1, t2 = await asyncio.gather(acquire_shared(), acquire_shared())
        assert t1 is not None
        assert t2 is not None
        await scheduler.release(t1)
        await scheduler.release(t2)

    async def test_exclusive_blocked_while_shared_read_held(self) -> None:
        """Exclusive acquire times out while shared_read is held."""
        scheduler = ToolScheduler()
        read_claim = ResourceClaim(resource="file:data.csv", mode="shared_read")
        write_claim = ResourceClaim(resource="file:data.csv", mode="exclusive")

        ticket_read = await scheduler.acquire((read_claim,), timeout=1.0)
        assert ticket_read is not None

        # Exclusive should time out while shared_read held
        ticket_write = await scheduler.acquire((write_claim,), timeout=0.1)
        assert ticket_write is None

    async def test_exclusive_succeeds_after_all_shared_reads_released(self) -> None:
        """Exclusive acquire succeeds after all shared_read tickets released."""
        scheduler = ToolScheduler()
        read_claim = ResourceClaim(resource="file:data.csv", mode="shared_read")
        write_claim = ResourceClaim(resource="file:data.csv", mode="exclusive")

        t1 = await scheduler.acquire((read_claim,), timeout=1.0)
        t2 = await scheduler.acquire((read_claim,), timeout=1.0)
        assert t1 is not None
        assert t2 is not None

        await scheduler.release(t1)
        await scheduler.release(t2)

        ticket_write = await scheduler.acquire((write_claim,), timeout=1.0)
        assert ticket_write is not None
        await scheduler.release(ticket_write)


class TestCounted:
    """Counted mode uses semaphore for concurrency control."""

    async def test_within_limit_succeeds(self) -> None:
        """First N acquires within limit succeed."""
        scheduler = ToolScheduler()
        claim = ResourceClaim(resource="api:openai", mode="counted", max_concurrent=2)

        t1 = await scheduler.acquire((claim,), timeout=1.0)
        t2 = await scheduler.acquire((claim,), timeout=1.0)
        assert t1 is not None
        assert t2 is not None

        # Third should time out
        t3 = await scheduler.acquire((claim,), timeout=0.1)
        assert t3 is None

        await scheduler.release(t1)
        await scheduler.release(t2)

    async def test_release_allows_next_acquire(self) -> None:
        """After release, blocked acquire can succeed."""
        scheduler = ToolScheduler()
        claim = ResourceClaim(resource="api:openai", mode="counted", max_concurrent=2)

        t1 = await scheduler.acquire((claim,), timeout=1.0)
        t2 = await scheduler.acquire((claim,), timeout=1.0)
        assert t1 is not None
        assert t2 is not None

        # Release one
        await scheduler.release(t1)

        # Now third should succeed
        t3 = await scheduler.acquire((claim,), timeout=1.0)
        assert t3 is not None

        await scheduler.release(t2)
        await scheduler.release(t3)


class TestTimeout:
    """Timeout behavior for acquire."""

    async def test_exclusive_acquire_timeout(self) -> None:
        """Exclusive acquire with short timeout returns None."""
        scheduler = ToolScheduler()
        claim = ResourceClaim(resource="shell", mode="exclusive")

        ticket1 = await scheduler.acquire((claim,), timeout=1.0)
        assert ticket1 is not None

        ticket2 = await scheduler.acquire((claim,), timeout=0.05)
        assert ticket2 is None

        await scheduler.release(ticket1)


class TestMultiResource:
    """Multiple resource claims in a single acquire."""

    async def test_multi_resource_acquire(self) -> None:
        """Acquire with claims on two different resources returns ticket with both."""
        scheduler = ToolScheduler()
        claims = (
            ResourceClaim(resource="shell", mode="exclusive"),
            ResourceClaim(resource="file:data.csv", mode="shared_read"),
        )

        ticket = await scheduler.acquire(claims, timeout=1.0)
        assert ticket is not None
        assert len(ticket.resource_locks) == 2

        # Verify resources in ticket
        resources = {res for res, _ in ticket.resource_locks}
        assert "shell" in resources
        assert "file:data.csv" in resources

        await scheduler.release(ticket)


class TestCountedMaxConcurrentZero:
    """Defensive handling of counted mode with max_concurrent <= 0."""

    async def test_max_concurrent_zero_treated_as_one(self) -> None:
        """counted mode with max_concurrent=0 is defensively treated as 1."""
        scheduler = ToolScheduler()
        claim = ResourceClaim(resource="api:test", mode="counted", max_concurrent=0)

        # First acquire should succeed (treated as max_concurrent=1)
        t1 = await scheduler.acquire((claim,), timeout=1.0)
        assert t1 is not None

        # Second should time out (only 1 slot)
        t2 = await scheduler.acquire((claim,), timeout=0.1)
        assert t2 is None

        await scheduler.release(t1)


class TestStatelessSchedulingBoundary:
    """Lock the stateless scheduling boundary per D-10 / ASCH-01 defer.

    ToolScheduler is intentionally generic over ResourceClaim. It does NOT
    inspect SessionCapabilities, shell_persistence, or any session-level
    attribute. All session-aware binding decisions live in ToolCompiler.

    This class exists to prevent regression: if a future phase needs
    persistent-shell scheduling, it must be a new feature, not a silent
    change to ToolScheduler internals.
    """

    def test_scheduler_does_not_import_session_capabilities(self) -> None:
        """ToolScheduler module must not import SessionCapabilities."""
        import ast
        import inspect

        from matmaster.core import tool_scheduler

        source = inspect.getsource(tool_scheduler)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.names:
                    for alias in node.names:
                        assert alias.name != "SessionCapabilities", (
                            "ToolScheduler must remain capability-agnostic; "
                            "SessionCapabilities belongs in ToolCompiler"
                        )

    def test_scheduler_does_not_reference_shell_persistence(self) -> None:
        """ToolScheduler source must not contain shell_persistence references."""
        import inspect

        from matmaster.core import tool_scheduler

        source = inspect.getsource(tool_scheduler)
        assert "shell_persistence" not in source, (
            "ToolScheduler must not inspect shell_persistence; "
            "that logic belongs in ToolCompiler"
        )

    def test_scheduler_api_is_claim_generic(self) -> None:
        """ToolScheduler.acquire() accepts any ResourceClaim tuple, no session args."""
        import inspect

        sig = inspect.signature(ToolScheduler.acquire)
        param_names = set(sig.parameters.keys())
        # Only self, claims, timeout -- no session/capabilities/topology
        assert "session" not in param_names
        assert "capabilities" not in param_names
        assert "topology" not in param_names
        assert "shell_persistence" not in param_names
