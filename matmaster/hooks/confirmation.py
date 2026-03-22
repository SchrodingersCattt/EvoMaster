"""ConfirmationHook -- blocks tool execution pending user confirmation.

Wraps a ReplyQueueLike to intercept tool calls that require human approval.
Emits ConfirmationRequestEvent to the bus and blocks on reply_queue.get().
Returns SKIP if the user cancels or the queue times out, CONTINUE if approved.
"""

from __future__ import annotations

import logging
import queue
from typing import Protocol, runtime_checkable

from matmaster.bus.queue import MessageBus
from matmaster.engine.hooks import BaseHook, HookAction
from matmaster.engine.types import ToolCallData
from matmaster.types.events import ConfirmationRequestEvent

logger = logging.getLogger(__name__)


@runtime_checkable
class ReplyQueueLike(Protocol):
    """Confirmation reply queue abstraction.

    Same contract as src/services/agent_run_service.ReplyQueueLike.
    get() returns None to indicate cancellation; raises queue.Empty on timeout.
    """

    def put_content(self, content: str) -> None: ...

    def put_cancel(self) -> None: ...

    def get(self, timeout: float | None = None) -> str | None: ...


class ConfirmationHook(BaseHook):
    """Hook that blocks tool execution pending user confirmation.

    If reply_queue is None, confirmation is not available and all tools
    proceed. If confirm_tools is set, only those tools require confirmation.
    Otherwise all tools require confirmation.
    """

    def __init__(
        self,
        reply_queue: ReplyQueueLike | None,
        bus: MessageBus,
        *,
        timeout_sec: int = 20,
        confirm_tools: set[str] | None = None,
        source: str = "MatMaster",
    ) -> None:
        self._reply_queue = reply_queue
        self._bus = bus
        self._timeout_sec = timeout_sec
        self._confirm_tools = confirm_tools
        self._source = source

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Intercept tool call for confirmation.

        Returns CONTINUE if:
        - No reply_queue (confirmation not available)
        - Tool not in confirm_tools set (when confirm_tools is specified)
        - User approves (reply_queue.get() returns non-None string)

        Returns SKIP if:
        - User cancels (reply_queue.get() returns None)
        - Timeout (reply_queue.get() raises queue.Empty)
        """
        if self._reply_queue is None:
            return HookAction.CONTINUE

        if self._confirm_tools is not None and tool_call.name not in self._confirm_tools:
            return HookAction.CONTINUE

        # Emit confirmation request to bus
        self._bus.emit(
            ConfirmationRequestEvent(
                source=self._source,
                question=f"Confirm tool call: {tool_call.name}?",
                mode="timeout",
                timeout_seconds=self._timeout_sec,
            )
        )

        # Block on reply
        try:
            reply = self._reply_queue.get(timeout=self._timeout_sec)
        except queue.Empty:
            logger.info("Confirmation timed out for tool %s", tool_call.name)
            return HookAction.SKIP

        if reply is None:
            logger.info("User cancelled tool call %s", tool_call.name)
            return HookAction.SKIP

        return HookAction.CONTINUE
