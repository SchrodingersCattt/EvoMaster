"""Async regression tests for ConfirmationHook."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import HookAction
from matmaster.types.events import ConfirmationRequestEvent
from matmaster.types.messages import ToolCallData
from src.services.stream_service import ConfirmationHookAdapter


def _tool_call(name: str = "execute_bash") -> ToolCallData:
    return ToolCallData(id=f"{name}-1", name=name, arguments={"command": "echo ok"})


class _ReplyingBus(MessageBus):
    """Emit the confirmation request, then immediately inject a reply."""

    def __init__(self, on_emit) -> None:
        super().__init__()
        self._on_emit = on_emit

    async def emit(self, event) -> None:
        await super().emit(event)
        self._on_emit()


class TestConfirmationHook:
    """ConfirmationHook async wait / resolve / cancel behavior."""

    @pytest.mark.asyncio
    async def test_without_loop_continues_without_emitting(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(bus=bus)

        result = await hook.pre_tool_call(_tool_call())

        assert result == HookAction.CONTINUE
        assert bus.pending == 0

    @pytest.mark.asyncio
    async def test_non_gated_tool_continues_without_emitting(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(bus=bus, confirm_tools={"execute_bash"})
        hook.set_loop(asyncio.get_running_loop())

        result = await hook.pre_tool_call(_tool_call("read_file"))

        assert result == HookAction.CONTINUE
        assert bus.pending == 0

    @pytest.mark.asyncio
    async def test_resolve_approved_reply_continues(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(bus=bus, timeout_sec=1.0)
        hook.set_loop(asyncio.get_running_loop())

        pending = asyncio.create_task(hook.pre_tool_call(_tool_call()))

        event = await bus.get(timeout=0.2)
        assert isinstance(event, ConfirmationRequestEvent)
        assert event.question == "Confirm tool call: execute_bash?"

        await asyncio.to_thread(hook.resolve, "approved")

        result = await pending
        assert result == HookAction.CONTINUE

    @pytest.mark.asyncio
    async def test_reply_during_emit_does_not_get_dropped(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        hook: ConfirmationHook | None = None

        def _reply_during_emit() -> None:
            assert hook is not None
            hook.resolve("approved")

        bus = _ReplyingBus(_reply_during_emit)
        hook = ConfirmationHook(bus=bus, timeout_sec=0.2)
        hook.set_loop(asyncio.get_running_loop())

        result = await hook.pre_tool_call(_tool_call())

        assert result == HookAction.CONTINUE

    @pytest.mark.asyncio
    async def test_reply_before_request_is_buffered(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(bus=bus, timeout_sec=0.2)
        hook.set_loop(asyncio.get_running_loop())

        hook.resolve("approved")

        result = await hook.pre_tool_call(_tool_call())

        assert result == HookAction.CONTINUE

    @pytest.mark.asyncio
    async def test_cancel_reply_returns_skip(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(bus=bus, timeout_sec=1.0)
        hook.set_loop(asyncio.get_running_loop())

        pending = asyncio.create_task(hook.pre_tool_call(_tool_call()))

        event = await bus.get(timeout=0.2)
        assert isinstance(event, ConfirmationRequestEvent)

        thread = threading.Thread(target=hook.cancel)
        thread.start()
        thread.join()

        result = await pending
        assert result == HookAction.SKIP

    @pytest.mark.asyncio
    async def test_timeout_returns_skip(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(bus=bus, timeout_sec=0.05)
        hook.set_loop(asyncio.get_running_loop())

        pending = asyncio.create_task(hook.pre_tool_call(_tool_call()))

        event = await bus.get(timeout=0.2)
        assert isinstance(event, ConfirmationRequestEvent)

        result = await pending
        assert result == HookAction.SKIP


class TestConfirmationHookAdapter:
    """Legacy adapter contract for stream_service callers."""

    def test_put_content_forwards_to_resolve(self) -> None:
        hook = MagicMock()
        adapter = ConfirmationHookAdapter(hook)

        adapter.put_content("approved")

        hook.resolve.assert_called_once_with("approved")

    def test_put_cancel_forwards_to_cancel(self) -> None:
        hook = MagicMock()
        adapter = ConfirmationHookAdapter(hook)

        adapter.put_cancel()

        hook.cancel.assert_called_once_with()

    def test_get_raises_not_implemented(self) -> None:
        hook = MagicMock()
        adapter = ConfirmationHookAdapter(hook)

        with pytest.raises(NotImplementedError):
            adapter.get(timeout=1.0)
