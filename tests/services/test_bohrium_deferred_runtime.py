from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from matmaster.bohrium.types import BohriumRuntimeSnapshot
from matmaster.types.runtime_ports import BohriumNodeBinding
from src.services.bohrium_deferred_runtime import BohriumNodeRuntimeCoordinator


def _binding() -> BohriumNodeBinding:
    return BohriumNodeBinding(
        session=MagicMock(),
        execution_workdir="/share/case",
        snapshot=BohriumRuntimeSnapshot(
            session_type="ssh",
            execution_workdir="/share/case",
            node_id=42,
            ssh_attached=True,
        ),
    )


def test_concurrent_acquire_is_single_flight() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0
    expected = _binding()

    def acquire(_cancelled) -> BohriumNodeBinding:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2)
        return expected

    coordinator = BohriumNodeRuntimeCoordinator(acquire)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(coordinator.ensure_ready_sync, reason="tool:Bash")
        assert started.wait(timeout=1)
        second = pool.submit(coordinator.ensure_ready_sync, reason="tool:Read")
        release.set()

    assert first.result() is expected
    assert second.result() is expected
    assert calls == 1
    assert coordinator.first_reason == "tool:Bash"


def test_failed_acquire_is_cached_for_the_run() -> None:
    calls = 0

    def acquire(_cancelled) -> BohriumNodeBinding:
        nonlocal calls
        calls += 1
        raise ValueError("provider unavailable")

    coordinator = BohriumNodeRuntimeCoordinator(acquire)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        coordinator.ensure_ready_sync(reason="tool:Bash")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        coordinator.ensure_ready_sync(reason="tool:Read")

    assert calls == 1


@pytest.mark.asyncio
async def test_close_fences_cold_acquisition() -> None:
    acquire = MagicMock(return_value=_binding())
    coordinator = BohriumNodeRuntimeCoordinator(acquire)

    await coordinator.close()

    with pytest.raises(RuntimeError, match="closed"):
        coordinator.ensure_ready_sync(reason="tool:Bash")
    acquire.assert_not_called()


@pytest.mark.asyncio
async def test_close_cancels_inflight_setup_before_cleanup_can_run() -> None:
    started = threading.Event()
    cancel_poll = threading.Event()

    def acquire(cancelled) -> BohriumNodeBinding:
        started.set()
        while not cancelled():
            cancel_poll.wait(timeout=0.01)
        raise RuntimeError("cancelled")

    coordinator = BohriumNodeRuntimeCoordinator(acquire)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(coordinator.ensure_ready_sync, reason="tool:Bash")
        assert started.wait(timeout=1)
        close_task = asyncio.create_task(coordinator.close())
        await asyncio.sleep(0.01)
        await close_task

    with pytest.raises(RuntimeError, match="cancelled"):
        future.result()
    assert coordinator.acquired is False
