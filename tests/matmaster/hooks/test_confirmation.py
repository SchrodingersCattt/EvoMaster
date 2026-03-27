"""Tests for ConfirmationHook -- asyncio.Future based confirmation."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

from matmaster.core.hooks import HookAction
from matmaster.hooks.confirmation import ConfirmationHook
from matmaster.types.events import ConfirmationRequestEvent
from matmaster.types.messages import ToolCallData


@pytest.fixture
def bus() -> MagicMock:
    return MagicMock()


@pytest.fixture
def tc() -> ToolCallData:
    return ToolCallData(id="tc-1", name="bash", arguments={"cmd": "ls"})


class TestConfirmationHookNoLoop:
    """Without loop injection, ConfirmationHook always returns CONTINUE."""

    async def test_returns_continue_when_no_loop(self, bus, tc) -> None:
        hook = ConfirmationHook(bus=bus)
        result = await hook.pre_tool_call(tc)
        assert result == HookAction.CONTINUE
        bus.emit.assert_not_called()


class TestConfirmationHookWithLoop:
    """With loop injection, ConfirmationHook uses asyncio.Future."""

    async def test_returns_continue_when_tool_not_in_confirm_tools(self, bus, tc) -> None:
        hook = ConfirmationHook(bus=bus, confirm_tools={"dangerous_tool"})
        hook.set_loop(asyncio.get_event_loop())
        result = await hook.pre_tool_call(tc)
        assert result == HookAction.CONTINUE
        bus.emit.assert_not_called()

    async def test_emits_event_and_returns_continue_on_resolve(self, bus, tc) -> None:
        loop = asyncio.get_event_loop()
        hook = ConfirmationHook(bus=bus, timeout_sec=5)
        hook.set_loop(loop)

        async def resolve_soon():
            await asyncio.sleep(0.01)
            hook.resolve("yes")

        asyncio.ensure_future(resolve_soon())
        result = await hook.pre_tool_call(tc)

        assert result == HookAction.CONTINUE
        bus.emit.assert_called_once()
        emitted = bus.emit.call_args[0][0]
        assert isinstance(emitted, ConfirmationRequestEvent)
        assert emitted.timeout_seconds == 5

    async def test_returns_skip_on_cancel(self, bus, tc) -> None:
        loop = asyncio.get_event_loop()
        hook = ConfirmationHook(bus=bus, timeout_sec=5)
        hook.set_loop(loop)

        async def cancel_soon():
            await asyncio.sleep(0.01)
            hook.cancel()

        asyncio.ensure_future(cancel_soon())
        result = await hook.pre_tool_call(tc)

        assert result == HookAction.SKIP

    async def test_returns_skip_on_timeout(self, bus, tc) -> None:
        loop = asyncio.get_event_loop()
        hook = ConfirmationHook(bus=bus, timeout_sec=0.05)
        hook.set_loop(loop)
        result = await hook.pre_tool_call(tc)
        assert result == HookAction.SKIP

    async def test_pending_future_cleared_after_resolve(self, bus, tc) -> None:
        loop = asyncio.get_event_loop()
        hook = ConfirmationHook(bus=bus, timeout_sec=5)
        hook.set_loop(loop)

        async def resolve_soon():
            await asyncio.sleep(0.01)
            hook.resolve("ok")

        asyncio.ensure_future(resolve_soon())
        await hook.pre_tool_call(tc)
        assert hook._pending_future is None

    async def test_resolve_noop_when_no_pending(self, bus) -> None:
        hook = ConfirmationHook(bus=bus)
        hook.set_loop(asyncio.get_event_loop())
        hook.resolve("test")  # Should not raise

    async def test_cancel_noop_when_no_pending(self, bus) -> None:
        hook = ConfirmationHook(bus=bus)
        hook.set_loop(asyncio.get_event_loop())
        hook.cancel()  # Should not raise


class TestConfirmationHookCrossThread:
    """Cross-thread resolve/cancel via loop.call_soon_threadsafe."""

    async def test_resolve_from_thread(self, bus, tc) -> None:
        loop = asyncio.get_event_loop()
        hook = ConfirmationHook(bus=bus, timeout_sec=5)
        hook.set_loop(loop)

        def thread_resolve():
            time.sleep(0.02)
            hook.resolve("approved")

        t = threading.Thread(target=thread_resolve)
        t.start()
        result = await hook.pre_tool_call(tc)
        t.join()
        assert result == HookAction.CONTINUE

    async def test_cancel_from_thread(self, bus, tc) -> None:
        loop = asyncio.get_event_loop()
        hook = ConfirmationHook(bus=bus, timeout_sec=5)
        hook.set_loop(loop)

        def thread_cancel():
            time.sleep(0.02)
            hook.cancel()

        t = threading.Thread(target=thread_cancel)
        t.start()
        result = await hook.pre_tool_call(tc)
        t.join()
        assert result == HookAction.SKIP


class TestConfirmationHookAdapter:
    """Test ConfirmationHookAdapter bridges ReplyQueueLike to hook."""

    async def test_put_content_calls_resolve(self, bus) -> None:
        from src.services.stream_service import ConfirmationHookAdapter

        hook = ConfirmationHook(bus=bus)
        adapter = ConfirmationHookAdapter(hook)

        # Mock resolve to verify call
        hook.resolve = MagicMock()
        adapter.put_content("approved")
        hook.resolve.assert_called_once_with("approved")

    async def test_put_cancel_calls_cancel(self, bus) -> None:
        from src.services.stream_service import ConfirmationHookAdapter

        hook = ConfirmationHook(bus=bus)
        adapter = ConfirmationHookAdapter(hook)

        hook.cancel = MagicMock()
        adapter.put_cancel()
        hook.cancel.assert_called_once()

    async def test_get_raises_not_implemented(self, bus) -> None:
        from src.services.stream_service import ConfirmationHookAdapter

        hook = ConfirmationHook(bus=bus)
        adapter = ConfirmationHookAdapter(hook)

        with pytest.raises(NotImplementedError):
            adapter.get()
