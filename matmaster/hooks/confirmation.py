"""ConfirmationHook -- async confirmation gate for tool execution.

Accepts an async callable (get_reply) that produces user replies.
The service layer is responsible for constructing this callable,
e.g. by wrapping a blocking ReplyQueue in loop.run_in_executor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import BaseHook, HookAction
from matmaster.types.events import ConfirmationRequestEvent
from matmaster.types.messages import ToolCallData

logger = logging.getLogger(__name__)


class ConfirmationHook(BaseHook):
    """Gate selected tool calls until the user explicitly confirms them."""

    def __init__(
        self,
        bus: MessageBus,
        *,
        timeout_sec: float = 20,
        confirm_tools: set[str] | None = None,
        get_reply: Callable[[], Awaitable[str | None]],
        source: str = "MatMaster",
    ) -> None:
        self._bus = bus
        self._timeout_sec = timeout_sec
        self._confirm_tools = confirm_tools
        self._get_reply = get_reply
        self._source = source

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Wait asynchronously for user confirmation before running a tool."""

        if self._confirm_tools is not None and tool_call.name not in self._confirm_tools:
            return HookAction.CONTINUE

        await self._bus.emit(
            ConfirmationRequestEvent(
                source=self._source,
                question=f"Confirm tool call: {tool_call.name}?",
                mode="timeout",
                timeout_seconds=int(self._timeout_sec),
            )
        )

        try:
            reply = await asyncio.wait_for(self._get_reply(), timeout=self._timeout_sec)
        except asyncio.TimeoutError:
            logger.info("Confirmation timed out for tool %s", tool_call.name)
            return HookAction.SKIP

        if reply is None:
            logger.info("User cancelled tool call %s", tool_call.name)
            return HookAction.SKIP

        return HookAction.CONTINUE
