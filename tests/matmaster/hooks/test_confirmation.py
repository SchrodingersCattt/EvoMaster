"""Tests for ConfirmationHook."""

from __future__ import annotations

import queue
from unittest.mock import MagicMock

import pytest

from matmaster.engine.hooks import HookAction
from matmaster.engine.types import ToolCallData


class TestConfirmationHook:
    """ConfirmationHook pre_tool_call behavior."""

    def test_returns_continue_when_reply_queue_is_none(self) -> None:
        """No reply_queue means no confirmation possible -- always CONTINUE."""
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MagicMock()
        hook = ConfirmationHook(reply_queue=None, bus=bus)
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        assert hook.pre_tool_call(tc) == HookAction.CONTINUE
        bus.emit.assert_not_called()

    def test_returns_continue_when_tool_not_in_confirm_tools(self) -> None:
        """Tool not in confirm_tools set -- skip confirmation."""
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MagicMock()
        reply_queue = MagicMock()
        hook = ConfirmationHook(
            reply_queue=reply_queue,
            bus=bus,
            confirm_tools={"dangerous_tool"},
        )
        tc = ToolCallData(id="tc-1", name="safe_tool", arguments={})
        assert hook.pre_tool_call(tc) == HookAction.CONTINUE
        bus.emit.assert_not_called()

    def test_emits_confirmation_request_and_blocks(self) -> None:
        """Emits ConfirmationRequestEvent and blocks on reply_queue.get()."""
        from matmaster.hooks.confirmation import ConfirmationHook
        from matmaster.types.events import ConfirmationRequestEvent

        bus = MagicMock()
        reply_queue = MagicMock()
        reply_queue.get.return_value = "yes"  # user approved
        hook = ConfirmationHook(reply_queue=reply_queue, bus=bus, timeout_sec=10)
        tc = ToolCallData(id="tc-1", name="bash", arguments={"cmd": "rm -rf /"})
        result = hook.pre_tool_call(tc)

        assert result == HookAction.CONTINUE
        bus.emit.assert_called_once()
        emitted = bus.emit.call_args[0][0]
        assert isinstance(emitted, ConfirmationRequestEvent)
        reply_queue.get.assert_called_once_with(timeout=10)

    def test_returns_skip_when_user_cancels(self) -> None:
        """reply_queue.get() returns None -> user cancelled -> SKIP."""
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MagicMock()
        reply_queue = MagicMock()
        reply_queue.get.return_value = None  # user cancelled
        hook = ConfirmationHook(reply_queue=reply_queue, bus=bus)
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        result = hook.pre_tool_call(tc)

        assert result == HookAction.SKIP

    def test_returns_skip_on_queue_empty_timeout(self) -> None:
        """reply_queue.get() raises queue.Empty -> timeout -> SKIP."""
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MagicMock()
        reply_queue = MagicMock()
        reply_queue.get.side_effect = queue.Empty()
        hook = ConfirmationHook(reply_queue=reply_queue, bus=bus)
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        result = hook.pre_tool_call(tc)

        assert result == HookAction.SKIP
