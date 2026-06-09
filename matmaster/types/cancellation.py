"""内核级协作中止信号（cancellation signal）。

``CancellationToken`` / ``CancellationController`` 是一个**可携带原因（cause）的协作式
中止信号**：持有者 ``cancel()`` 置位，被中止方在自己的检查点（轮次、流式分片、串行
步骤之间）主动观测 ``is_cancelled`` 并优雅收尾——内核不强行打断调用栈，因此可以安全
地从旁路线程/任务发起中止。

``cause`` 是**自由字符串**，由触发方标注、本原语不解释其含义，仅透传给下游用于分流
（对外文案、成功/失败判定等）。缺省 cause 为 ``user``（调用方发起的常规取消）；其他
系统级中止（如成本熔断）由各自业务侧定义自己的 cause 常量，内核与本原语对此无感知。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable


class CancelledError(Exception):
    """被中止方观测到中止信号时抛出。"""


class CancellationToken:
    """只读中止信号（threading.Event 支撑），附带只读的 ``cancel_reason``。"""

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
        """中止原因（cause，首次取消为准）；未取消时为 None。

        自由文本，由触发方标注、本原语不解释其含义，仅透传给下游分流（对外文案、
        成功/失败判定等）。缺省为 ``user``（调用方发起的常规取消）；系统级中止的
        cause 常量由各业务侧自行定义，内核与本原语对此无感知。
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
        """置位中止信号。``reason`` 记录 cause（首次为准），缺省 ``user``。

        系统级中止（如成本熔断）由调用方传入自定义 cause；本原语只透传、不解释含义。
        """
        with self.token._lock:
            if self.token._reason is None:
                self.token._reason = reason
        self.token._event.set()
        self.token._fire_callbacks()

    def child(self) -> CancellationController:
        child = CancellationController()
        # 子 controller 继承父 cause（中止信号级联到下游），缺省回落 user。
        self.token.on_cancel(lambda: child.cancel(self.token._reason or "user"))
        return child
