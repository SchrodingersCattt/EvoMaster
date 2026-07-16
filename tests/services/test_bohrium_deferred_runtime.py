from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from matmaster.bohrium.types import BohriumRuntimeSnapshot
from matmaster.types.bohrium_node_approval import BohriumNodeStartDecision
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

    def acquire(_cancelled, _policy, _idle_timeout) -> BohriumNodeBinding:
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

    def acquire(_cancelled, _policy, _idle_timeout) -> BohriumNodeBinding:
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

    def acquire(cancelled, _policy, _idle_timeout) -> BohriumNodeBinding:
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


@pytest.mark.asyncio
async def test_approval_is_single_flight_and_selects_lifecycle() -> None:
    expected = _binding()
    approval_calls = 0
    acquired_with = None

    class Gate:
        async def review(self, request):
            nonlocal approval_calls
            approval_calls += 1
            await asyncio.sleep(0.01)
            assert request.trigger_reason == "tool:Bash"
            return BohriumNodeStartDecision(
                review_outcome="approved",
                lifecycle_policy="idle_timeout",
                idle_timeout_seconds=1800,
            )

    def acquire(_cancelled, policy, idle_timeout) -> BohriumNodeBinding:
        nonlocal acquired_with
        acquired_with = (policy, idle_timeout)
        return expected

    coordinator = BohriumNodeRuntimeCoordinator(
        acquire,
        approval_gate=Gate(),
        request_id="node-review-1",
    )

    first, second = await asyncio.gather(
        coordinator.ensure_ready(reason="tool:Bash"),
        coordinator.ensure_ready(reason="tool:Read"),
    )

    assert first is expected
    assert second is expected
    assert approval_calls == 1
    assert acquired_with == ("idle_timeout", 1800)


@pytest.mark.asyncio
async def test_rejected_approval_is_cached_without_acquiring() -> None:
    acquire = MagicMock(return_value=_binding())

    class RejectingGate:
        calls = 0

        async def review(self, _request):
            self.calls += 1
            return BohriumNodeStartDecision(review_outcome="rejected")

    rejecting_gate = RejectingGate()
    coordinator = BohriumNodeRuntimeCoordinator(
        acquire,
        approval_gate=rejecting_gate,
    )

    with pytest.raises(RuntimeError, match="rejected by the user"):
        await coordinator.ensure_ready(reason="tool:Bash")
    with pytest.raises(RuntimeError, match="rejected by the user"):
        await coordinator.ensure_ready(reason="tool:Read")

    assert rejecting_gate.calls == 1
    acquire.assert_not_called()
