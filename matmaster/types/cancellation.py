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
        self._reason: str | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def cancel_reason(self) -> str | None:
        """取消原因（首次取消为准）：``user``=用户主动停；``cost_guard``=成本熔断等。

        让下游能区分「用户取消」与「系统因额度耗尽止损」，二者对外语义/文案不同。
        未取消时为 None。
        """
        return self._reason

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
                # Loop already closed; waiter abandoned, nothing to do.
                pass

        self.on_cancel(_resolve)

        if timeout is not None:
            try:
                return await asyncio.wait_for(fut, timeout)
            except TimeoutError:
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

    def cancel(self, reason: str = "user") -> None:
        """触发取消。``reason`` 记录取消原因（首次为准），默认 ``user``（用户主动停）。

        成本熔断等系统性中止应传专门 reason（如 ``cost_guard``），供下游分流文案/语义。
        """
        with self.token._lock:
            if self.token._reason is None:
                self.token._reason = reason
        self.token._event.set()
        self.token._fire_callbacks()

    def child(self) -> CancellationController:
        child = CancellationController()
        # 子 controller 继承父取消原因（如成本熔断级联到 subagent），缺省回落 user。
        self.token.on_cancel(lambda: child.cancel(self.token._reason or "user"))
        return child
