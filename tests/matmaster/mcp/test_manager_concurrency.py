from __future__ import annotations

import asyncio
from typing import Any

import pytest
from unittest.mock import patch

from matmaster.mcp.manager import (
    MCPToolManager,
    ManagedConnBackpressure,
    ManagedConnClosing,
    ManagedConnDead,
    _ManagedConn,
)


class _BlockingConn:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.exited = False

    async def __aenter__(self) -> "_BlockingConn":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited = True

    async def list_tools(self) -> list[dict[str, Any]]:
        return []

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> list[dict[str, str]]:
        self.calls.append((tool_name, dict(arguments)))
        self.started.set()
        await self.release.wait()
        return [{"text": arguments.get("value", "")}]


class _StartupBlockingConn:
    def __init__(self) -> None:
        self.list_tools_started = asyncio.Event()
        self.release_startup = asyncio.Event()

    async def __aenter__(self) -> "_StartupBlockingConn":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def list_tools(self) -> list[dict[str, Any]]:
        self.list_tools_started.set()
        await self.release_startup.wait()
        return [
            {
                "name": "remote_tool",
                "description": "Remote tool",
                "input_schema": {"type": "object"},
            }
        ]

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> list[dict[str, str]]:
        return [{"text": arguments.get("value", "")}]


async def _spin_event_loop(turns: int = 5) -> None:
    for _ in range(turns):
        await asyncio.sleep(0)


class TestManagedConnConcurrency:
    async def test_call_tool_raises_backpressure_when_pending_queue_is_full(self):
        conn = _BlockingConn()
        managed = _ManagedConn(conn, max_inflight=1, max_pending_requests=1)

        first_task: asyncio.Task[Any] | None = None
        second_task: asyncio.Task[Any] | None = None
        try:
            await managed.wait_ready(timeout=1.0)

            first_task = asyncio.create_task(
                managed.call_tool("remote_tool", {"value": "first"})
            )
            await asyncio.wait_for(conn.started.wait(), timeout=1.0)

            second_task = asyncio.create_task(
                managed.call_tool("remote_tool", {"value": "second"})
            )
            await _spin_event_loop()

            with pytest.raises(ManagedConnBackpressure):
                await asyncio.wait_for(
                    managed.call_tool("remote_tool", {"value": "third"}),
                    timeout=0.2,
                )
        finally:
            conn.release.set()
            if first_task is not None or second_task is not None:
                await asyncio.gather(
                    *[task for task in (first_task, second_task) if task is not None],
                    return_exceptions=True,
                )
            await managed.close(timeout=1.0)

    async def test_pending_request_stays_in_queue_until_capacity_is_available(self):
        conn = _BlockingConn()
        managed = _ManagedConn(conn, max_inflight=1, max_pending_requests=1)

        first_task: asyncio.Task[Any] | None = None
        second_task: asyncio.Task[Any] | None = None
        try:
            await managed.wait_ready(timeout=1.0)

            first_task = asyncio.create_task(
                managed.call_tool("remote_tool", {"value": "first"})
            )
            await asyncio.wait_for(conn.started.wait(), timeout=1.0)

            second_task = asyncio.create_task(
                managed.call_tool("remote_tool", {"value": "second"})
            )
            await _spin_event_loop()

            assert len(managed._active_tasks) == 1
            assert managed._requests.qsize() == 1
            assert [arguments["value"] for _, arguments in conn.calls] == ["first"]
        finally:
            conn.release.set()
            if first_task is not None or second_task is not None:
                await asyncio.gather(
                    *[task for task in (first_task, second_task) if task is not None],
                    return_exceptions=True,
                )
            await managed.close(timeout=1.0)

    async def test_cancelled_request_is_dropped_before_real_execution(self):
        conn = _BlockingConn()
        managed = _ManagedConn(conn, max_inflight=1, max_pending_requests=4)

        first_task: asyncio.Task[Any] | None = None
        second_task: asyncio.Task[Any] | None = None
        try:
            await managed.wait_ready(timeout=1.0)

            first_task = asyncio.create_task(
                managed.call_tool("remote_tool", {"value": "first"})
            )
            await asyncio.wait_for(conn.started.wait(), timeout=1.0)

            second_task = asyncio.create_task(
                managed.call_tool("remote_tool", {"value": "second"})
            )
            await _spin_event_loop()

            second_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second_task

            conn.release.set()
            await asyncio.wait_for(first_task, timeout=1.0)
            await asyncio.sleep(0)

            assert [arguments["value"] for _, arguments in conn.calls] == ["first"]
        finally:
            conn.release.set()
            if first_task is not None:
                await asyncio.gather(first_task, return_exceptions=True)
            await managed.close(timeout=1.0)

    async def test_call_tool_raises_managed_conn_closing_after_cleanup_starts(self):
        conn = _BlockingConn()
        managed = _ManagedConn(conn, max_inflight=1, max_pending_requests=1)

        await managed.wait_ready(timeout=1.0)

        close_task = asyncio.create_task(managed.close(timeout=1.0))
        try:
            await _spin_event_loop()
            assert managed._closing is True
            with pytest.raises(ManagedConnClosing):
                await managed.call_tool("remote_tool", {"value": "late"})
        finally:
            conn.release.set()
            await asyncio.wait_for(close_task, timeout=1.0)

    async def test_forced_close_converts_active_caller_cancel_to_managed_conn_closing(
        self,
    ):
        conn = _BlockingConn()
        managed = _ManagedConn(conn, max_inflight=1, max_pending_requests=4)

        active_task: asyncio.Task[Any] | None = None
        close_task: asyncio.Task[None] | None = None
        try:
            await managed.wait_ready(timeout=1.0)

            active_task = asyncio.create_task(
                managed.call_tool("remote_tool", {"value": "first"})
            )
            await asyncio.wait_for(conn.started.wait(), timeout=1.0)

            close_task = asyncio.create_task(managed.close(timeout=0.01))

            with pytest.raises(ManagedConnClosing):
                await asyncio.wait_for(active_task, timeout=1.0)

            await asyncio.wait_for(close_task, timeout=1.0)
        finally:
            conn.release.set()
            if active_task is not None or close_task is not None:
                await asyncio.gather(
                    *[task for task in (active_task, close_task) if task is not None],
                    return_exceptions=True,
                )

    async def test_owner_task_crash_fails_future_with_managed_conn_dead(self):
        conn = _BlockingConn()
        managed = _ManagedConn(conn, max_inflight=1, max_pending_requests=4)

        first_task: asyncio.Task[Any] | None = None
        second_task: asyncio.Task[Any] | None = None
        try:
            await managed.wait_ready(timeout=1.0)

            first_task = asyncio.create_task(
                managed.call_tool("remote_tool", {"value": "first"})
            )
            await asyncio.wait_for(conn.started.wait(), timeout=1.0)

            second_task = asyncio.create_task(
                managed.call_tool("remote_tool", {"value": "queued"})
            )
            await _spin_event_loop()

            managed._task.cancel()
            await managed._task
            assert isinstance(managed._fatal_error, asyncio.CancelledError)

            with pytest.raises(ManagedConnDead):
                await second_task

            with pytest.raises(ManagedConnDead):
                await managed.call_tool("remote_tool", {"value": "late"})
        finally:
            conn.release.set()
            if first_task is not None or second_task is not None:
                await asyncio.gather(
                    *[task for task in (first_task, second_task) if task is not None],
                    return_exceptions=True,
                )


class TestManagerStartupConcurrency:
    async def test_call_tool_waiting_for_startup_returns_managed_conn_closing_on_cleanup(
        self,
    ):
        manager = MCPToolManager()
        conn = _StartupBlockingConn()

        with patch("matmaster.mcp.manager.create_connection", return_value=conn):
            add_task = asyncio.create_task(
                manager.add_server("srv", transport="http", url="http://srv")
            )
            await asyncio.wait_for(conn.list_tools_started.wait(), timeout=1.0)

            call_task = asyncio.create_task(
                manager.call_tool("srv", "remote_tool", {"value": "payload"})
            )
            await _spin_event_loop()

            await manager.cleanup()

            with pytest.raises(ManagedConnClosing):
                await asyncio.wait_for(call_task, timeout=1.0)

            with pytest.raises(asyncio.CancelledError):
                await add_task
