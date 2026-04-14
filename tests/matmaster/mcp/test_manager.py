"""Gap 2 (27-01-02 / MCP-01): MCPToolManager instantiation, attributes, _build_tools, cleanup.

Behavioral contract:
- MCPToolManager can be instantiated with no arguments.
- All required attributes exist after instantiation with correct default types.
- _build_tools populates tools_by_server[name] as a dict mapping prefixed_name -> tool_info dict.
- tool_info dicts contain required keys: name, description, input_schema, remote_tool_name, connection.
- _build_tools applies tool_include_only whitelist filtering.
- cleanup clears connections, tools_by_server, _managed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock


class TestMCPToolManagerInstantiation:
    def test_instantiates_with_no_args(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert m is not None

    def test_connections_is_empty_dict(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert isinstance(m.connections, dict)
        assert len(m.connections) == 0

    def test_tools_by_server_is_empty_dict(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert isinstance(m.tools_by_server, dict)
        assert len(m.tools_by_server) == 0

    def test_calculation_preflight_servers_is_empty_set(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert isinstance(m.calculation_preflight_servers, set)
        assert len(m.calculation_preflight_servers) == 0

    def test_calculation_preflight_factory_is_none(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert m.calculation_preflight_factory is None

    def test_sync_tools_by_server_is_empty_dict(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert isinstance(m.sync_tools_by_server, dict)

    def test_tool_include_only_is_empty_dict(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert isinstance(m.tool_include_only, dict)

    def test_manager_has_empty_concurrency_policy_maps(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert isinstance(m.concurrency_defaults_by_transport, dict)
        assert m.concurrency_defaults_by_transport == {}
        assert isinstance(m.concurrency_by_server, dict)
        assert m.concurrency_by_server == {}
        assert isinstance(m._server_transports, dict)
        assert m._server_transports == {}

    def test_loop_is_none(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert m.loop is None

    def test_default_stdio_policy_is_serial(self):
        from matmaster.mcp.manager import MCPConcurrencyPolicy

        policy = MCPConcurrencyPolicy.default_for_transport("stdio")
        assert policy.mode == "serial"
        assert policy.max_inflight == 1
        assert policy.max_pending_requests == 16

    def test_default_non_stdio_policy_is_multiplex(self):
        from matmaster.mcp.manager import MCPConcurrencyPolicy

        policy = MCPConcurrencyPolicy.default_for_transport("sse")
        assert policy.mode == "multiplex"
        assert policy.max_inflight == 4
        assert policy.max_pending_requests == 64

    def test_has_add_server_method(self):
        from matmaster.mcp.manager import MCPToolManager

        assert hasattr(MCPToolManager, "add_server")
        assert asyncio.iscoroutinefunction(MCPToolManager.add_server)

    def test_has_build_tools_method(self):
        from matmaster.mcp.manager import MCPToolManager

        assert hasattr(MCPToolManager, "_build_tools")

    def test_has_cleanup_method(self):
        from matmaster.mcp.manager import MCPToolManager

        assert hasattr(MCPToolManager, "cleanup")
        assert asyncio.iscoroutinefunction(MCPToolManager.cleanup)


class TestMCPToolManagerBuildTools:
    """Verify _build_tools produces correct dict structure without real MCP connection."""

    def _make_manager(self):
        from matmaster.mcp.manager import MCPToolManager

        return MCPToolManager()

    def _fake_conn(self):
        return MagicMock()

    def _make_tools_info(self, names):
        return [
            {"name": n, "description": f"desc_{n}", "input_schema": {"type": "object"}}
            for n in names
        ]

    def test_build_tools_populates_tools_by_server(self):
        m = self._make_manager()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["build_bulk", "relax"])
        m._build_tools("mat_sg", conn, tools_info)
        assert "mat_sg" in m.tools_by_server
        assert len(m.tools_by_server["mat_sg"]) == 2

    def test_build_tools_prefixes_tool_names(self):
        m = self._make_manager()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["build_bulk"])
        m._build_tools("mat_sg", conn, tools_info)
        assert "mat_sg_build_bulk" in m.tools_by_server["mat_sg"]

    def test_tool_info_dict_has_required_keys(self):
        m = self._make_manager()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["build_bulk"])
        m._build_tools("mat_sg", conn, tools_info)
        tool_info = m.tools_by_server["mat_sg"]["mat_sg_build_bulk"]
        assert "name" in tool_info
        assert "description" in tool_info
        assert "input_schema" in tool_info
        assert "remote_tool_name" in tool_info
        assert "connection" in tool_info

    def test_remote_tool_name_is_original_unprefixed(self):
        m = self._make_manager()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["build_bulk"])
        m._build_tools("mat_sg", conn, tools_info)
        tool_info = m.tools_by_server["mat_sg"]["mat_sg_build_bulk"]
        assert tool_info["remote_tool_name"] == "build_bulk"

    def test_connection_reference_stored_in_dict(self):
        m = self._make_manager()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["run"])
        m._build_tools("srv", conn, tools_info)
        tool_info = m.tools_by_server["srv"]["srv_run"]
        assert tool_info["connection"] is conn

    def test_tool_include_only_filters_tools(self):
        m = self._make_manager()
        m.tool_include_only = {"mat_sg": ["build_bulk"]}
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["build_bulk", "relax", "phonon"])
        m._build_tools("mat_sg", conn, tools_info)
        server_tools = m.tools_by_server["mat_sg"]
        assert "mat_sg_build_bulk" in server_tools
        assert "mat_sg_relax" not in server_tools
        assert "mat_sg_phonon" not in server_tools

    def test_global_dedup_skips_seen_tool(self):
        m = self._make_manager()
        conn1 = self._fake_conn()
        conn2 = self._fake_conn()
        tools_info = self._make_tools_info(["build_bulk"])
        m._build_tools("srv", conn1, tools_info)
        # Add a second server with same tool name
        m._build_tools(
            "srv2",
            conn2,
            [{"name": "srv_build_bulk", "description": "d", "input_schema": {}}],
        )
        # The first server's tool stays
        assert "srv_build_bulk" in m.tools_by_server["srv"]

    def test_has_calculation_preflight_false_without_factory(self):
        m = self._make_manager()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["run"])
        m._build_tools("srv", conn, tools_info)
        tool_info = m.tools_by_server["srv"]["srv_run"]
        assert tool_info["has_calculation_preflight"] is False

    def test_has_calculation_preflight_true_when_configured(self):
        m = self._make_manager()
        m.calculation_preflight_servers = {"srv"}
        m.calculation_preflight_factory = lambda: MagicMock()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["run"])
        m._build_tools("srv", conn, tools_info)
        tool_info = m.tools_by_server["srv"]["srv_run"]
        assert tool_info["has_calculation_preflight"] is True

    def test_no_mcp_tool_instances_in_tools_by_server(self):
        """tools_by_server stores plain dicts, not MCPTool instances."""
        m = self._make_manager()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["run"])
        m._build_tools("srv", conn, tools_info)
        tool_info = m.tools_by_server["srv"]["srv_run"]
        assert isinstance(tool_info, dict)


class _FakeConnCtx:
    """Minimal MCPConnection stand-in for cleanup tests."""

    def __init__(self, *, hang: bool = False, fail: bool = False):
        self._hang = hang
        self._fail = fail
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        self.session = MagicMock()
        self.session.initialize = AsyncMock()
        await self.session.initialize()
        return self

    async def __aexit__(self, *args):
        if self._hang:
            await asyncio.get_event_loop().create_future()  # block forever
        if self._fail:
            raise RuntimeError("close failed")
        self.exited = True

    async def list_tools(self):
        return []


def _make_managed(m, name, *, hang=False, fail=False):
    """Helper: create a _ManagedConn and register it in the manager."""
    from matmaster.mcp.manager import _ManagedConn

    ctx = _FakeConnCtx(hang=hang, fail=fail)
    managed = _ManagedConn(ctx)
    m._managed[name] = managed
    m.connections[name] = ctx
    return ctx, managed


class TestMCPToolManagerCleanup:
    async def test_cleanup_clears_state(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        ctx, managed = _make_managed(m, "fake_srv")
        await managed.wait_ready(timeout=2)
        m.tools_by_server["fake_srv"] = {"fake_srv_tool": {}}
        m._server_transports["fake_srv"] = "stdio"

        await m.cleanup()

        assert len(m.connections) == 0
        assert len(m.tools_by_server) == 0
        assert len(m._managed) == 0
        assert len(m._server_transports) == 0
        assert ctx.exited

    async def test_cleanup_exits_connection_context(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        ctx, managed = _make_managed(m, "srv")
        await managed.wait_ready(timeout=2)

        await m.cleanup()

        assert ctx.exited

    async def test_cleanup_tolerates_aexit_error(self):
        """Cleanup should not raise even if a connection's __aexit__ fails."""
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        _ctx, managed = _make_managed(m, "bad_srv", fail=True)
        await managed.wait_ready(timeout=2)

        # Should not raise
        await m.cleanup()

    async def test_cleanup_per_connection_timeout_isolation(self):
        """One hung connection must not prevent later connections from being attempted."""
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        _hung_ctx, hung_managed = _make_managed(m, "hung_srv", hang=True)
        await hung_managed.wait_ready(timeout=2)
        fast_ctx, fast_managed = _make_managed(m, "fast_srv")
        await fast_managed.wait_ready(timeout=2)

        await m.cleanup()

        # fast_srv must have been cleaned up despite hung_srv timing out
        assert fast_ctx.exited
        assert len(m._managed) == 0
