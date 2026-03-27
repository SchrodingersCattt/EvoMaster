"""ConfirmationHook -- blocks tool execution pending user confirmation.

Uses asyncio.Future for non-blocking wait. External threads push replies
via resolve()/cancel() using loop.call_soon_threadsafe().
"""

from __future__ import annotations

import asyncio
import logging

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import BaseHook, HookAction
from matmaster.types.events import ConfirmationRequestEvent
from matmaster.types.messages import ToolCallData

logger = logging.getLogger(__name__)


class ConfirmationHook(BaseHook):
    """Hook that blocks tool execution pending user confirmation.

    Uses asyncio.Future + wait_for for non-blocking async wait.
    External threads call resolve(reply)/cancel() to push replies
    via loop.call_soon_threadsafe.

    If no loop is injected (pre-Kernel setup), all tools proceed.
    If confirm_tools is set, only those tools require confirmation.
    """

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

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Inject the bridge event loop. Called by Kernel after loop creation."""
        self._loop = loop

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Intercept tool call for confirmation using asyncio.Future.

        Returns CONTINUE if no loop, tool not in confirm_tools, or user approves.
        Returns SKIP on cancel or timeout.
        """
        if self._loop is None:
            return HookAction.CONTINUE

        if self._confirm_tools is not None and tool_call.name not in self._confirm_tools:
            return HookAction.CONTINUE

        self._bus.emit(
            ConfirmationRequestEvent(
                source=self._source,
                question=f"Confirm tool call: {tool_call.name}?",
                mode="timeout",
                timeout_seconds=int(self._timeout_sec),
            )
        )

        future: asyncio.Future[str | None] = self._loop.create_future()
        self._pending_future = future
        try:
            reply = await asyncio.wait_for(future, timeout=self._timeout_sec)
        except asyncio.TimeoutError:
            logger.info("Confirmation timed out for tool %s", tool_call.name)
            return HookAction.SKIP
        finally:
            self._pending_future = None

        if reply is None:
            logger.info("User cancelled tool call %s", tool_call.name)
            return HookAction.SKIP

        return HookAction.CONTINUE

    def resolve(self, reply: str) -> None:
        """Thread-safe: resolve pending confirmation with user reply.

        Uses atomic swap of _pending_future to avoid race condition
        where two concurrent resolve() calls both pass the done() check.
        (Addresses Review P2: resolve/cancel race)
        """
        # Atomic swap: grab and clear reference in one step
        future = self._pending_future
        self._pending_future = None  # prevent second caller from reaching set_result

        if future is None or future.done():
            return
        if self._loop is None or self._loop.is_closed():
            return

        def _safe_set_result():
            if not future.done():
                future.set_result(reply)

        self._loop.call_soon_threadsafe(_safe_set_result)

    def cancel(self) -> None:
        """Thread-safe: cancel pending confirmation.

        Sets Future result to None, which pre_tool_call interprets as SKIP.
        Uses same atomic swap pattern as resolve().
        (Addresses Review P2: resolve/cancel race)
        """
        future = self._pending_future
        self._pending_future = None

        if future is None or future.done():
            return
        if self._loop is None or self._loop.is_closed():
            return

        def _safe_set_result():
            if not future.done():
                future.set_result(None)

        self._loop.call_soon_threadsafe(_safe_set_result)
