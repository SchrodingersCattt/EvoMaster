"""Tests for event-driven cancellation primitives."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from matmaster.types.cancellation import (
    CancelledError,
    CancellationController,
)


class TestCancellationToken:
    def test_initial_state_not_cancelled(self) -> None:
        ctrl = CancellationController()
        assert ctrl.token.is_cancelled is False

    def test_cancel_sets_is_cancelled(self) -> None:
        ctrl = CancellationController()
        ctrl.cancel()
        assert ctrl.token.is_cancelled is True

    def test_wait_returns_true_when_cancelled(self) -> None:
        ctrl = CancellationController()
        ctrl.cancel()
        assert ctrl.token.wait(timeout=0.01) is True

    def test_wait_returns_false_on_timeout(self) -> None:
        ctrl = CancellationController()
        assert ctrl.token.wait(timeout=0.05) is False

    def test_wait_wakes_immediately_on_cancel(self) -> None:
        ctrl = CancellationController()
        t0 = time.monotonic()
        threading.Timer(0.1, ctrl.cancel).start()
        result = ctrl.token.wait(timeout=5.0)
        elapsed = time.monotonic() - t0
        assert result is True
        assert elapsed < 1.0

    def test_raise_if_cancelled(self) -> None:
        ctrl = CancellationController()
        ctrl.token.raise_if_cancelled()
        ctrl.cancel()
        with pytest.raises(CancelledError):
            ctrl.token.raise_if_cancelled()


class TestOnCancel:
    def test_callback_fires_on_cancel(self) -> None:
        ctrl = CancellationController()
        called: list[int] = []
        ctrl.token.on_cancel(lambda: called.append(1))
        assert called == []
        ctrl.cancel()
        assert called == [1]

    def test_callback_fires_immediately_if_already_cancelled(self) -> None:
        ctrl = CancellationController()
        ctrl.cancel()
        called: list[int] = []
        ctrl.token.on_cancel(lambda: called.append(1))
        assert called == [1]

    def test_callback_fires_at_most_once(self) -> None:
        ctrl = CancellationController()
        count: list[int] = []
        ctrl.token.on_cancel(lambda: count.append(1))
        ctrl.cancel()
        ctrl.cancel()
        assert len(count) == 1

    def test_multiple_callbacks(self) -> None:
        ctrl = CancellationController()
        results: list[str] = []
        ctrl.token.on_cancel(lambda: results.append("a"))
        ctrl.token.on_cancel(lambda: results.append("b"))
        ctrl.cancel()
        assert results == ["a", "b"]


class TestChild:
    def test_parent_cancel_cascades_to_child(self) -> None:
        parent = CancellationController()
        child = parent.child()
        assert child.token.is_cancelled is False
        parent.cancel()
        assert child.token.is_cancelled is True

    def test_child_cancel_does_not_affect_parent(self) -> None:
        parent = CancellationController()
        child = parent.child()
        child.cancel()
        assert child.token.is_cancelled is True
        assert parent.token.is_cancelled is False


class TestWaitAsync:
    @pytest.mark.asyncio
    async def test_returns_true_when_already_cancelled(self) -> None:
        ctrl = CancellationController()
        ctrl.cancel()
        assert await ctrl.token.wait_async(timeout=0.01) is True

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self) -> None:
        ctrl = CancellationController()
        assert await ctrl.token.wait_async(timeout=0.05) is False

    @pytest.mark.asyncio
    async def test_resolves_on_cancel_from_another_thread(self) -> None:
        ctrl = CancellationController()
        threading.Timer(0.1, ctrl.cancel).start()
        t0 = time.monotonic()
        result = await ctrl.token.wait_async(timeout=5.0)
        elapsed = time.monotonic() - t0
        assert result is True
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_task_cancel_does_not_leak_thread(self) -> None:
        ctrl = CancellationController()
        task = asyncio.create_task(ctrl.token.wait_async())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_race_pattern_no_thread_leak(self) -> None:
        ctrl = CancellationController()

        async def fast_work() -> str:
            await asyncio.sleep(0.05)
            return "done"

        call_task = asyncio.create_task(fast_work())
        stop_task = asyncio.create_task(ctrl.token.wait_async())
        done, pending = await asyncio.wait(
            {call_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert call_task in done
        assert call_task.result() == "done"

    @pytest.mark.asyncio
    async def test_token_cancel_after_waiter_timeout_no_invalid_state(self) -> None:
        ctrl = CancellationController()
        result = await ctrl.token.wait_async(timeout=0.05)
        assert result is False
        ctrl.cancel()

    @pytest.mark.asyncio
    async def test_token_cancel_after_task_cancel_no_invalid_state(self) -> None:
        ctrl = CancellationController()
        task = asyncio.create_task(ctrl.token.wait_async())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        ctrl.cancel()
