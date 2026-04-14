from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.tools.lazy_mcp import LazyMCPConnector, LazyMCPTool


class _ActorConnector:
    def __init__(self, calculation_preflight=None, *, session=None):
        self.workspace_path = "/fake/workspace"
        self.session = session
        self.calculation_preflight = calculation_preflight
        self.call_tool = AsyncMock(return_value=[{"text": "actor_result"}])
        self.get_calculation_preflight = AsyncMock(return_value=calculation_preflight)


class _CancelableBlockingConn:
    def __init__(self) -> None:
        self.active_started = asyncio.Event()
        self.active_cancelled = asyncio.Event()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_CancelableBlockingConn":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def list_tools(self) -> list[dict[str, Any]]:
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
        self.calls.append((tool_name, dict(arguments)))
        if arguments.get("value") == "first":
            self.active_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.active_cancelled.set()
                raise
        return [{"text": arguments.get("value", "")}]


class TestLazyMCPToolActorRouting:
    async def test_execute_routes_via_connector_call_tool(self):
        connector = _ActorConnector()
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="Build bulk structure",
            input_schema={"type": "object", "properties": {}},
            connector=connector,
        )

        result = await tool.execute({"value": "payload"})

        assert "_connection" not in tool.__dict__
        connector.call_tool.assert_awaited_once_with(
            "mat_sg", "build_bulk", {"value": "payload"}
        )
        assert result.status == "success"
        assert result.content == "actor_result"

    async def test_calculation_preflight_comes_from_connector_lookup(self):
        preflight = MagicMock()
        preflight.prepare_call.return_value = {"value": "resolved"}
        connector = _ActorConnector(
            calculation_preflight=preflight, session=MagicMock()
        )
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_run",
            remote_tool_name="run",
            description="Run calculation",
            input_schema={"type": "object"},
            connector=connector,
        )

        await tool.execute({"value": "raw"})

        connector.get_calculation_preflight.assert_awaited_once_with("mat_sg")
        connector.call_tool.assert_awaited_once_with(
            "mat_sg", "run", {"value": "resolved"}
        )


class TestLazyMCPConnectorActorRouting:
    async def test_call_tool_runs_add_server_then_manager_call_tool(self):
        connector = LazyMCPConnector(
            mcp_server_config={"mat_sg": {"transport": "http", "url": "http://x"}},
            mcp_config={},
        )
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        fake_manager = MagicMock()
        fake_manager.connections = {}
        fake_manager.tools_by_server = {}
        fake_manager.calculation_preflight_factory = None
        fake_manager.calculation_preflight_servers = set()
        fake_manager.loop = loop
        fake_manager.cleanup = AsyncMock()

        async def _add_server(name: str, **_kwargs):
            fake_manager.connections[name] = object()

        fake_manager.add_server = AsyncMock(side_effect=_add_server)
        fake_manager.call_tool = AsyncMock(return_value=[{"text": "ok"}])
        connector._manager = fake_manager
        connector._loop = loop
        connector._loop_thread = thread

        try:
            result = await connector.call_tool(
                "mat_sg", "build_bulk", {"value": "payload"}
            )
        finally:
            await connector.cleanup()

        fake_manager.add_server.assert_awaited_once()
        fake_manager.call_tool.assert_awaited_once_with(
            "mat_sg", "build_bulk", {"value": "payload"}
        )
        assert result == [{"text": "ok"}]

    def test_connect_and_get_tool_uses_actor_startup_metadata(self):
        connector = LazyMCPConnector(
            mcp_server_config={"mat_sg": {"transport": "http", "url": "http://x"}},
            mcp_config={},
        )
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        fake_manager = MagicMock()
        fake_manager.connections = {}
        fake_manager.tools_by_server = {}
        fake_manager.calculation_preflight_factory = None
        fake_manager.calculation_preflight_servers = set()
        fake_manager.loop = loop
        fake_manager.cleanup = AsyncMock()

        async def _add_server(name: str, **_kwargs):
            fake_manager.connections[name] = object()
            fake_manager.tools_by_server[name] = {
                f"{name}_build_bulk": {
                    "name": f"{name}_build_bulk",
                    "remote_tool_name": "build_bulk",
                }
            }

        fake_manager.add_server = AsyncMock(side_effect=_add_server)
        connector._manager = fake_manager
        connector._loop = loop
        connector._loop_thread = thread

        try:
            tool_info = connector.connect_and_get_tool("mat_sg", "build_bulk")
        finally:
            asyncio.run(connector.cleanup())

        fake_manager.add_server.assert_awaited_once()
        assert tool_info["remote_tool_name"] == "build_bulk"

    async def test_cleanup_is_harmless_when_called_twice(self):
        connector = LazyMCPConnector(
            mcp_server_config={"mat_sg": {"transport": "http", "url": "http://x"}},
            mcp_config={},
        )
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        fake_manager = MagicMock()
        fake_manager.connections = {}
        fake_manager.tools_by_server = {}
        fake_manager.calculation_preflight_factory = None
        fake_manager.calculation_preflight_servers = set()
        fake_manager.loop = loop
        fake_manager.cleanup = AsyncMock()
        connector._manager = fake_manager
        connector._loop = loop
        connector._loop_thread = thread

        await connector.cleanup()
        await connector.cleanup()

        fake_manager.cleanup.assert_awaited_once()

    async def test_outer_timeout_cancels_active_request_and_releases_serial_slot(self):
        connector = LazyMCPConnector(
            mcp_server_config={"srv": {"transport": "http", "url": "http://srv"}},
            mcp_config={
                "mcp_concurrency": {
                    "servers": {
                        "srv": {
                            "mode": "serial",
                            "max_inflight": 1,
                            "max_pending_requests": 4,
                        }
                    }
                }
            },
        )
        conn = _CancelableBlockingConn()

        with patch("matmaster.mcp.manager.create_connection", return_value=conn):
            await connector.ensure_actor("srv")
            first_call = asyncio.create_task(
                connector.call_tool("srv", "remote_tool", {"value": "first"})
            )

            try:
                await asyncio.wait_for(conn.active_started.wait(), timeout=1.0)

                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(first_call, timeout=0.05)

                await asyncio.wait_for(conn.active_cancelled.wait(), timeout=1.0)

                second_result = await asyncio.wait_for(
                    connector.call_tool("srv", "remote_tool", {"value": "second"}),
                    timeout=1.0,
                )
            finally:
                await connector.cleanup()

        assert second_result == [{"text": "second"}]
        assert [arguments["value"] for _, arguments in conn.calls] == [
            "first",
            "second",
        ]
