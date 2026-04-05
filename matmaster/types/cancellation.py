"""Event-driven cancellation primitives."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable


class CancelledError(Exception):
    """Raised when an operation observes cancellation."""


class CancellationToken:
    """Read-only cancellation signal backed by a threading event."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._fired = False

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    async def wait_async(self, timeout: float | None = None) -> bool:
        if self._event.is_set():
            return True

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()

        def _resolve() -> None:
            def _safe_set() -> None:
                if not fut.done():
                    fut.set_result(True)

            try:
                loop.call_soon_threadsafe(_safe_set)
            except RuntimeError:
                pass

        self.on_cancel(_resolve)

        if timeout is not None:
            try:
                return await asyncio.wait_for(asyncio.ensure_future(fut), timeout)
            except asyncio.TimeoutError:
                return False

        return await fut

    def on_cancel(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._fired:
                should_call = True
            else:
                self._callbacks.append(callback)
                should_call = False

        if should_call:
            callback()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError("Operation cancelled")

    def _fire_callbacks(self) -> None:
        with self._lock:
            if self._fired:
                return
            self._fired = True
            callbacks = list(self._callbacks)
            self._callbacks.clear()

        for callback in callbacks:
            callback()


class CancellationController:
    """Holds authority to cancel a paired token."""

    def __init__(self) -> None:
        self.token = CancellationToken()

    def cancel(self) -> None:
        self.token._event.set()
        self.token._fire_callbacks()

    def child(self) -> CancellationController:
        child = CancellationController()
        self.token.on_cancel(child.cancel)
        return child
