from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

from matmaster.tools.lazy_mcp import LazyMCPConnector, LazyMCPTool


class _ActorConnector:
    def __init__(self, path_adaptor=None, *, session=None):
        self.workspace_path = "/fake/workspace"
        self.session = session
        self.path_adaptor = path_adaptor
        self.call_tool = AsyncMock(return_value=[{"text": "actor_result"}])
        self.get_path_adaptor = AsyncMock(return_value=path_adaptor)


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

    async def test_path_adaptor_comes_from_connector_lookup(self):
        adaptor = MagicMock()
        adaptor.resolve_args.return_value = {"value": "resolved"}
        connector = _ActorConnector(path_adaptor=adaptor, session=MagicMock())
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_run",
            remote_tool_name="run",
            description="Run calculation",
            input_schema={"type": "object"},
            connector=connector,
        )

        await tool.execute({"value": "raw"})

        connector.get_path_adaptor.assert_awaited_once_with("mat_sg")
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
        fake_manager.path_adaptor_factory = None
        fake_manager.path_adaptor_servers = set()
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
        fake_manager.path_adaptor_factory = None
        fake_manager.path_adaptor_servers = set()
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
        fake_manager.path_adaptor_factory = None
        fake_manager.path_adaptor_servers = set()
        fake_manager.loop = loop
        fake_manager.cleanup = AsyncMock()
        connector._manager = fake_manager
        connector._loop = loop
        connector._loop_thread = thread

        await connector.cleanup()
        await connector.cleanup()

        fake_manager.cleanup.assert_awaited_once()
