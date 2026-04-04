from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from matmaster.mcp.manager import MCPToolManager


@dataclass
class _Event:
    name: str
    task_id: int
    loop_id: int


class _TracingConn:
    def __init__(
        self,
        server_name: str,
        *,
        startup_delay: float = 0.0,
        call_delay: float = 0.0,
        list_tools_release_event: asyncio.Event | None = None,
        list_tools_started_event: asyncio.Event | None = None,
        release_event: asyncio.Event | None = None,
        started_event: asyncio.Event | None = None,
    ) -> None:
        self.server_name = server_name
        self.startup_delay = startup_delay
        self.call_delay = call_delay
        self.list_tools_release_event = list_tools_release_event
        self.list_tools_started_event = list_tools_started_event
        self.release_event = release_event
        self.started_event = started_event
        self.events: list[_Event] = []
        self.enter_count = 0
        self.list_tools_count = 0

    def _record(self, name: str) -> None:
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        self.events.append(
            _Event(
                name=name,
                task_id=id(task) if task is not None else -1,
                loop_id=id(loop),
            )
        )

    async def __aenter__(self):
        self.enter_count += 1
        if self.startup_delay:
            await asyncio.sleep(self.startup_delay)
        self._record("enter")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._record("exit")

    async def list_tools(self) -> list[dict[str, Any]]:
        self.list_tools_count += 1
        self._record("list_tools")
        if self.list_tools_started_event is not None:
            self.list_tools_started_event.set()
        if self.list_tools_release_event is not None:
            await self.list_tools_release_event.wait()
        return [
            {
                "name": "remote_tool",
                "description": "Remote tool",
                "input_schema": {"type": "object"},
            }
        ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> list[dict[str, str]]:
        self._record(f"call_tool:{tool_name}")
        if self.started_event is not None:
            self.started_event.set()
        if self.release_event is not None:
            await self.release_event.wait()
        if self.call_delay:
            await asyncio.sleep(self.call_delay)
        return [{"text": f"{self.server_name}:{tool_name}:{arguments.get('value', '')}"}]


class TestManagerOwnerTaskLifecycle:
    async def test_add_server_and_call_tool_use_same_owner_task(self):
        manager = MCPToolManager()
        conn = _TracingConn("srv")

        with patch("matmaster.mcp.manager.create_connection", return_value=conn):
            await manager.add_server("srv", transport="http", url="http://srv")
            result = await manager.call_tool(
                "srv", "remote_tool", {"value": "payload"}
            )
            await manager.cleanup()

        assert result == [{"text": "srv:remote_tool:payload"}]
        labels = [event.name for event in conn.events]
        assert labels == ["enter", "list_tools", "call_tool:remote_tool", "exit"]
        task_ids = {event.task_id for event in conn.events}
        loop_ids = {event.loop_id for event in conn.events}
        assert len(task_ids) == 1
        assert len(loop_ids) == 1

    async def test_concurrent_first_use_shares_single_startup(self):
        manager = MCPToolManager()
        conn = _TracingConn("srv", startup_delay=0.05)

        with patch("matmaster.mcp.manager.create_connection", return_value=conn) as create:
            await asyncio.gather(
                manager.add_server("srv", transport="http", url="http://srv"),
                manager.add_server("srv", transport="http", url="http://srv"),
            )
            await manager.cleanup()

        assert create.call_count == 1
        assert conn.enter_count == 1
        assert conn.list_tools_count == 1

    async def test_different_servers_make_progress_independently(self):
        manager = MCPToolManager()
        slow_started = asyncio.Event()
        slow_release = asyncio.Event()
        slow_conn = _TracingConn(
            "slow",
            release_event=slow_release,
            started_event=slow_started,
        )
        fast_conn = _TracingConn("fast")

        with patch(
            "matmaster.mcp.manager.create_connection",
            side_effect=[slow_conn, fast_conn],
        ):
            await manager.add_server("slow", transport="http", url="http://slow")
            await manager.add_server("fast", transport="http", url="http://fast")

            slow_task = asyncio.create_task(
                manager.call_tool("slow", "remote_tool", {"value": "slow"})
            )
            await asyncio.wait_for(slow_started.wait(), timeout=1.0)

            fast_result = await asyncio.wait_for(
                manager.call_tool("fast", "remote_tool", {"value": "fast"}),
                timeout=1.0,
            )

            slow_release.set()
            slow_result = await asyncio.wait_for(slow_task, timeout=1.0)
            await manager.cleanup()

        assert fast_result == [{"text": "fast:remote_tool:fast"}]
        assert slow_result == [{"text": "slow:remote_tool:slow"}]

    async def test_cleanup_during_startup_still_exits_in_owner_task(self):
        manager = MCPToolManager()
        startup_started = asyncio.Event()
        conn = _TracingConn(
            "srv",
            list_tools_started_event=startup_started,
            list_tools_release_event=asyncio.Event(),
        )

        with patch("matmaster.mcp.manager.create_connection", return_value=conn):
            add_task = asyncio.create_task(
                manager.add_server("srv", transport="http", url="http://srv")
            )
            await asyncio.wait_for(startup_started.wait(), timeout=1.0)
            await manager.cleanup()

            with pytest.raises(asyncio.CancelledError):
                await add_task

        labels = [event.name for event in conn.events]
        assert labels == ["enter", "list_tools", "exit"]
        task_ids = {event.task_id for event in conn.events}
        assert len(task_ids) == 1

    async def test_cleanup_while_request_in_flight_waits_for_owner_task_exit(self):
        manager = MCPToolManager()
        call_started = asyncio.Event()
        call_release = asyncio.Event()
        conn = _TracingConn(
            "srv",
            started_event=call_started,
            release_event=call_release,
        )

        with patch("matmaster.mcp.manager.create_connection", return_value=conn):
            await manager.add_server("srv", transport="http", url="http://srv")
            call_task = asyncio.create_task(
                manager.call_tool("srv", "remote_tool", {"value": "payload"})
            )
            await asyncio.wait_for(call_started.wait(), timeout=1.0)

            cleanup_task = asyncio.create_task(manager.cleanup())
            await asyncio.sleep(0.05)
            assert not cleanup_task.done()

            call_release.set()
            result = await asyncio.wait_for(call_task, timeout=1.0)
            await asyncio.wait_for(cleanup_task, timeout=1.0)

        assert result == [{"text": "srv:remote_tool:payload"}]
        labels = [event.name for event in conn.events]
        assert labels == ["enter", "list_tools", "call_tool:remote_tool", "exit"]
