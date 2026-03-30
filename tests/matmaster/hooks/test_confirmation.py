"""Async regression tests for ConfirmationHook."""

from __future__ import annotations

import asyncio

import pytest

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import HookAction
from matmaster.types.events import ConfirmationRequestEvent
from matmaster.types.messages import ToolCallData


def _tool_call(name: str = "execute_bash") -> ToolCallData:
    return ToolCallData(id=f"{name}-1", name=name, arguments={"command": "echo ok"})


async def _immediate_reply(reply: str | None = "approved") -> str | None:
    return reply


class TestConfirmationHook:
    """ConfirmationHook async get_reply behavior."""

    @pytest.mark.asyncio
    async def test_non_gated_tool_continues_without_emitting(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            confirm_tools={"execute_bash"},
            get_reply=_immediate_reply,
        )

        result = await hook.pre_tool_call(_tool_call("read_file"))

        assert result == HookAction.CONTINUE
        assert bus.pending == 0

    @pytest.mark.asyncio
    async def test_approved_reply_continues(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            get_reply=lambda: _immediate_reply("approved"),
        )

        result = await hook.pre_tool_call(_tool_call())

        assert result == HookAction.CONTINUE

    @pytest.mark.asyncio
    async def test_none_reply_skips(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            get_reply=lambda: _immediate_reply(None),
        )

        result = await hook.pre_tool_call(_tool_call())

        assert result == HookAction.SKIP

    @pytest.mark.asyncio
    async def test_timeout_returns_skip(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        async def _hang_forever() -> str | None:
            await asyncio.sleep(999)
            return "never"

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            timeout_sec=0.05,
            get_reply=_hang_forever,
        )

        result = await hook.pre_tool_call(_tool_call())

        assert result == HookAction.SKIP

    @pytest.mark.asyncio
    async def test_confirm_tools_filter_skips_get_reply(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        called = False

        async def _should_not_be_called() -> str | None:
            nonlocal called
            called = True
            return "approved"

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            confirm_tools={"execute_bash"},
            get_reply=_should_not_be_called,
        )

        result = await hook.pre_tool_call(_tool_call("read_file"))

        assert result == HookAction.CONTINUE
        assert not called

    @pytest.mark.asyncio
    async def test_emits_confirmation_request_event(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            get_reply=lambda: _immediate_reply("approved"),
        )

        await hook.pre_tool_call(_tool_call())

        event = await bus.get(timeout=0.1)
        assert isinstance(event, ConfirmationRequestEvent)
        assert event.question == "Confirm tool call: execute_bash?"
