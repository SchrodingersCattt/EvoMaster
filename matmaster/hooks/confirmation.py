"""ConfirmationHook -- async confirmation gate for tool execution.

Uses asyncio.Future for non-blocking waits. External threads push replies
through resolve()/cancel() via loop.call_soon_threadsafe().
"""

from __future__ import annotations

import asyncio
import logging
import threading

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import BaseHook, HookAction
from matmaster.types.events import ConfirmationRequestEvent
from matmaster.types.messages import ToolCallData

logger = logging.getLogger(__name__)
_NO_REPLY = object()


class ConfirmationHook(BaseHook):
    """Gate selected tool calls until the user explicitly confirms them."""

    def __init__(
        self,
        bus: MessageBus,
        *,
        timeout_sec: float = 20,
        confirm_tools: set[str] | None = None,
        source: str = "MatMaster",
    ) -> None:
        self._bus = bus
        self._timeout_sec = timeout_sec
        self._confirm_tools = confirm_tools
        self._source = source
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_future: asyncio.Future[str | None] | None = None
        self._buffered_reply: str | None | object = _NO_REPLY
        self._state_lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Inject the running loop so external threads can wake the hook safely."""

        self._loop = loop

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Wait asynchronously for user confirmation before running a tool."""

        if self._loop is None:
            return HookAction.CONTINUE

        if self._confirm_tools is not None and tool_call.name not in self._confirm_tools:
            return HookAction.CONTINUE

        future: asyncio.Future[str | None] = self._loop.create_future()
        with self._state_lock:
            buffered_reply = self._buffered_reply
            if buffered_reply is _NO_REPLY:
                self._pending_future = future
            else:
                self._buffered_reply = _NO_REPLY
        await self._bus.emit(
            ConfirmationRequestEvent(
                source=self._source,
                question=f"Confirm tool call: {tool_call.name}?",
                mode="timeout",
                timeout_seconds=int(self._timeout_sec),
            )
        )

        if buffered_reply is not _NO_REPLY:
            future.set_result(buffered_reply)

        try:
            reply = await asyncio.wait_for(future, timeout=self._timeout_sec)
        except asyncio.TimeoutError:
            logger.info("Confirmation timed out for tool %s", tool_call.name)
            return HookAction.SKIP
        finally:
            with self._state_lock:
                if self._pending_future is future:
                    self._pending_future = None

        if reply is None:
            logger.info("User cancelled tool call %s", tool_call.name)
            return HookAction.SKIP

        return HookAction.CONTINUE

    def resolve(self, reply: str) -> None:
        """Resolve the current pending confirmation from any thread."""

        self._deliver_reply(reply)

    def cancel(self) -> None:
        """Cancel the current pending confirmation from any thread."""

        self._deliver_reply(None)

    def _deliver_reply(self, reply: str | None) -> None:
        """Deliver a reply to the pending waiter or buffer it for the next one."""

        with self._state_lock:
            future = self._pending_future
            if future is None or future.done():
                self._buffered_reply = reply
                return

        if self._loop is None or self._loop.is_closed():
            with self._state_lock:
                self._buffered_reply = reply
            return

        def _safe_set_result() -> None:
            if not future.done():
                future.set_result(reply)

        self._loop.call_soon_threadsafe(_safe_set_result)
