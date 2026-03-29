"""AssistantStateHook -- emits AssistantStateEvent for persistence.

Emits the full assistant message state (including tool_calls) before each
LLM call so that the last assistant response can be persisted by downstream
handlers. Only emits when the last message is an AssistantMessage with
tool_calls (indicating a tool-use turn that needs persistence).
"""

from __future__ import annotations

import logging

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import BaseHook
from matmaster.types.messages import AssistantMessage, Message
from matmaster.types.events import AssistantStateEvent

logger = logging.getLogger(__name__)


class AssistantStateHook(BaseHook):
    """Hook that emits AssistantStateEvent before each LLM call.

    Only emits when the last AssistantMessage in the conversation
    has tool_calls (meaning the LLM requested tool invocations in
    the previous turn). This allows downstream handlers to persist
    the full assistant state including tool call details.

    """

    def __init__(self, bus: MessageBus, *, source: str = "MatMaster") -> None:
        self._bus = bus
        self._source = source

    async def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        """Emit AssistantStateEvent if last AssistantMessage has tool_calls."""
        last_assistant = self._find_last_assistant(messages)
        if last_assistant is None:
            return

        if not last_assistant.tool_calls:
            return

        await self._bus.emit(
            AssistantStateEvent(
                source=self._source,
                # Persist the full assistant state so history replay can retain
                # reasoning_content in tool-use turns.
                state=last_assistant.model_dump(mode="json"),
            )
        )

    @staticmethod
    def _find_last_assistant(messages: list[Message]) -> AssistantMessage | None:
        """Find the last AssistantMessage in the message list."""
        for msg in reversed(messages):
            if isinstance(msg, AssistantMessage):
                return msg
        return None
