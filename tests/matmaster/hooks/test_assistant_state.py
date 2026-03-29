"""Tests for AssistantStateHook."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from matmaster.types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCallData,
    UserMessage,
)


class TestAssistantStateHook:
    """AssistantStateHook pre_llm_call behavior."""

    async def test_emits_assistant_state_event_with_tool_calls(self) -> None:
        """pre_llm_call emits AssistantStateEvent with last AssistantMessage that has tool_calls."""
        from matmaster.hooks.assistant_state import AssistantStateHook
        from matmaster.types.events import AssistantStateEvent

        bus = MagicMock(emit=AsyncMock())
        hook = AssistantStateHook(bus=bus, source="MatMaster")
        tc = ToolCallData(id="tc-1", name="bash", arguments={"cmd": "ls"})
        messages: list[Message] = [
            SystemMessage(content="system"),
            UserMessage(content="task"),
            AssistantMessage(content="I will run a command", tool_calls=[tc]),
        ]
        await hook.pre_llm_call(messages, turn=2)

        bus.emit.assert_called_once()
        emitted = bus.emit.call_args[0][0]
        assert isinstance(emitted, AssistantStateEvent)
        assert emitted.source == "MatMaster"
        # state should contain the assistant message dict
        assert emitted.state["role"] == "assistant"
        assert emitted.state["content"] == "I will run a command"

    async def test_emits_reasoning_content_in_assistant_state(self) -> None:
        """pre_llm_call preserves reasoning_content for tool-use turns."""
        from matmaster.hooks.assistant_state import AssistantStateHook
        from matmaster.types.events import AssistantStateEvent

        bus = MagicMock(emit=AsyncMock())
        hook = AssistantStateHook(bus=bus, source="MatMaster")
        tc = ToolCallData(id="tc-1", name="bash", arguments={"cmd": "ls"})
        messages: list[Message] = [
            SystemMessage(content="system"),
            UserMessage(content="task"),
            AssistantMessage(
                content="I will run a command",
                tool_calls=[tc],
                reasoning_content="Need to inspect the workspace first.",
            ),
        ]

        await hook.pre_llm_call(messages, turn=2)

        bus.emit.assert_called_once()
        emitted = bus.emit.call_args[0][0]
        assert isinstance(emitted, AssistantStateEvent)
        assert emitted.state["reasoning_content"] == "Need to inspect the workspace first."

    async def test_does_nothing_when_no_assistant_message(self) -> None:
        """pre_llm_call does nothing on first turn (no AssistantMessage)."""
        from matmaster.hooks.assistant_state import AssistantStateHook

        bus = MagicMock(emit=AsyncMock())
        hook = AssistantStateHook(bus=bus)
        messages: list[Message] = [
            SystemMessage(content="system"),
            UserMessage(content="task"),
        ]
        await hook.pre_llm_call(messages, turn=1)

        bus.emit.assert_not_called()

    async def test_does_nothing_when_last_assistant_has_no_tool_calls(self) -> None:
        """pre_llm_call does nothing when last AssistantMessage has no tool_calls."""
        from matmaster.hooks.assistant_state import AssistantStateHook

        bus = MagicMock(emit=AsyncMock())
        hook = AssistantStateHook(bus=bus)
        messages: list[Message] = [
            SystemMessage(content="system"),
            UserMessage(content="task"),
            AssistantMessage(content="Just a text response"),
        ]
        await hook.pre_llm_call(messages, turn=2)

        bus.emit.assert_not_called()
