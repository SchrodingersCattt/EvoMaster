"""Gap 2 (27-01-02 / MCP-01): MCPToolManager instantiation, attributes, _build_tools, cleanup.

Behavioral contract:
- MCPToolManager can be instantiated with no arguments.
- All required attributes exist after instantiation with correct default types.
- _build_tools populates tools_by_server[name] as a dict mapping prefixed_name -> tool_info dict.
- tool_info dicts contain required keys: name, description, input_schema, remote_tool_name, connection.
- _build_tools applies tool_include_only whitelist filtering.
- cleanup clears connections, tools_by_server, _conn_ctxs.
- No evomaster imports in manager.py.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
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

    def test_path_adaptor_servers_is_empty_set(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert isinstance(m.path_adaptor_servers, set)
        assert len(m.path_adaptor_servers) == 0

    def test_path_adaptor_factory_is_none(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert m.path_adaptor_factory is None

    def test_sync_tools_by_server_is_empty_dict(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert isinstance(m.sync_tools_by_server, dict)

    def test_tool_include_only_is_empty_dict(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert isinstance(m.tool_include_only, dict)

    def test_loop_is_none(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        assert m.loop is None

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

    def test_has_path_adaptor_false_without_factory(self):
        m = self._make_manager()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["run"])
        m._build_tools("srv", conn, tools_info)
        tool_info = m.tools_by_server["srv"]["srv_run"]
        assert tool_info["has_path_adaptor"] is False

    def test_has_path_adaptor_true_when_configured(self):
        m = self._make_manager()
        m.path_adaptor_servers = {"srv"}
        m.path_adaptor_factory = lambda: MagicMock()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["run"])
        m._build_tools("srv", conn, tools_info)
        tool_info = m.tools_by_server["srv"]["srv_run"]
        assert tool_info["has_path_adaptor"] is True

    def test_no_mcp_tool_instances_in_tools_by_server(self):
        """tools_by_server stores plain dicts, not MCPTool instances."""
        m = self._make_manager()
        conn = self._fake_conn()
        tools_info = self._make_tools_info(["run"])
        m._build_tools("srv", conn, tools_info)
        tool_info = m.tools_by_server["srv"]["srv_run"]
        assert isinstance(tool_info, dict)


class TestMCPToolManagerCleanup:
    async def test_cleanup_clears_state(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        # Inject a fake connection context
        mock_conn_ctx = AsyncMock()
        mock_conn_ctx.__aexit__ = AsyncMock(return_value=None)
        m._conn_ctxs["fake_srv"] = mock_conn_ctx
        m.connections["fake_srv"] = MagicMock()
        m.tools_by_server["fake_srv"] = {"fake_srv_tool": {}}

        await m.cleanup()

        assert len(m.connections) == 0
        assert len(m.tools_by_server) == 0
        assert len(m._conn_ctxs) == 0

    async def test_cleanup_calls_aexit_on_connections(self):
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        mock_conn_ctx = AsyncMock()
        mock_conn_ctx.__aexit__ = AsyncMock(return_value=None)
        m._conn_ctxs["srv"] = mock_conn_ctx

        await m.cleanup()

        mock_conn_ctx.__aexit__.assert_called_once_with(None, None, None)

    async def test_cleanup_tolerates_aexit_error(self):
        """Cleanup should not raise even if a connection's __aexit__ fails."""
        from matmaster.mcp.manager import MCPToolManager

        m = MCPToolManager()
        mock_conn_ctx = AsyncMock()
        mock_conn_ctx.__aexit__ = AsyncMock(side_effect=RuntimeError("close failed"))
        m._conn_ctxs["bad_srv"] = mock_conn_ctx

        # Should not raise
        await m.cleanup()


class TestNoEvoMasterImportsInManager:
    def test_no_top_level_evomaster_imports(self):
        import matmaster.mcp.manager as mod

        module_file = Path(mod.__file__)
        source = module_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_evo = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and "evomaster" in node.module
            and node.col_offset == 0
        ]
        assert (
            top_level_evo == []
        ), f"Found {len(top_level_evo)} top-level evomaster imports in manager.py"

    def test_no_evomaster_import_lines_in_source(self):
        """manager.py must not contain any 'from evomaster' or 'import evomaster' lines (comments excluded)."""
        import matmaster.mcp.manager as mod

        source = inspect.getsource(mod)
        import_lines = [
            line.strip()
            for line in source.split('\n')
            if ('from evomaster' in line or 'import evomaster' in line)
            and not line.strip().startswith('#')
            and not line.strip().startswith('"')
            and not line.strip().startswith("'")
        ]
        assert (
            import_lines == []
        ), f"Found evomaster import statements in manager.py: {import_lines}"

    def test_no_reconnect_logic(self):
        import matmaster.mcp.manager as mod

        source = inspect.getsource(mod)
        assert (
            "reconnect" not in source.lower()
        ), "Reconnect logic found in manager.py (should be stripped per D-03)"

    def test_no_progress_callback(self):
        import matmaster.mcp.manager as mod

        source = inspect.getsource(mod)
        assert (
            "_progress_callback" not in source
        ), "Progress callback found in manager.py (should be stripped per D-03)"

    def test_no_register_tools(self):
        import matmaster.mcp.manager as mod

        source = inspect.getsource(mod)
        assert (
            "register_tools" not in source
        ), "register_tools found in manager.py (should be stripped per D-03)"
