"""Gap 1 (27-01-01 / MCP-01): MCPConnection ABC + transport subclasses + create_connection factory.

Behavioral contract:
- MCPConnection ABC can be imported without triggering any evomaster imports.
- All three transport subclasses (Stdio/SSE/HTTP) are concrete importable types.
- create_connection factory returns the correct subclass for each transport string.
- create_connection raises ValueError for unknown transport or missing required args.
- The module contains no 'from evomaster' top-level imports.
"""

from __future__ import annotations

import ast
import inspect
from abc import ABC
from pathlib import Path

import pytest


class TestMCPConnectionImport:
    def test_mcpconnection_abc_importable(self):
        from matmaster.mcp.connection import MCPConnection
        assert MCPConnection is not None

    def test_mcpconnection_is_abstract(self):
        from matmaster.mcp.connection import MCPConnection
        assert issubclass(MCPConnection, ABC)

    def test_all_subclasses_importable(self):
        from matmaster.mcp.connection import (
            MCPConnectionHTTP,
            MCPConnectionSSE,
            MCPConnectionStdio,
        )
        assert MCPConnectionStdio is not None
        assert MCPConnectionSSE is not None
        assert MCPConnectionHTTP is not None

    def test_create_connection_importable(self):
        from matmaster.mcp.connection import create_connection
        assert callable(create_connection)

    def test_package_level_import(self):
        from matmaster.mcp import MCPConnection, create_connection
        assert MCPConnection is not None
        assert callable(create_connection)


class TestCreateConnectionFactory:
    def test_stdio_returns_stdio_subclass(self):
        from matmaster.mcp.connection import MCPConnectionStdio, create_connection
        conn = create_connection(transport="stdio", command="python")
        assert isinstance(conn, MCPConnectionStdio)

    def test_sse_returns_sse_subclass(self):
        from matmaster.mcp.connection import MCPConnectionSSE, create_connection
        conn = create_connection(transport="sse", url="http://localhost:8080/sse")
        assert isinstance(conn, MCPConnectionSSE)

    def test_http_returns_http_subclass(self):
        from matmaster.mcp.connection import MCPConnectionHTTP, create_connection
        conn = create_connection(transport="http", url="http://localhost:8080/mcp")
        assert isinstance(conn, MCPConnectionHTTP)

    def test_http_aliases_are_accepted(self):
        from matmaster.mcp.connection import MCPConnectionHTTP, create_connection
        for alias in ["streamable_http", "streamable-http"]:
            conn = create_connection(transport=alias, url="http://localhost:8080")
            assert isinstance(conn, MCPConnectionHTTP), f"alias '{alias}' failed"

    def test_transport_is_case_insensitive(self):
        from matmaster.mcp.connection import MCPConnectionStdio, create_connection
        conn = create_connection(transport="STDIO", command="python")
        assert isinstance(conn, MCPConnectionStdio)

    def test_unknown_transport_raises_value_error(self):
        from matmaster.mcp.connection import create_connection
        with pytest.raises(ValueError, match="Unsupported transport"):
            create_connection(transport="websocket", url="ws://localhost")

    def test_stdio_without_command_raises_value_error(self):
        from matmaster.mcp.connection import create_connection
        with pytest.raises(ValueError, match="Command is required"):
            create_connection(transport="stdio")

    def test_sse_without_url_raises_value_error(self):
        from matmaster.mcp.connection import create_connection
        with pytest.raises(ValueError, match="URL is required"):
            create_connection(transport="sse")

    def test_http_without_url_raises_value_error(self):
        from matmaster.mcp.connection import create_connection
        with pytest.raises(ValueError, match="URL is required"):
            create_connection(transport="http")


class TestMCPConnectionInterface:
    def test_has_list_tools_method(self):
        from matmaster.mcp.connection import MCPConnection
        assert hasattr(MCPConnection, "list_tools")

    def test_has_call_tool_method(self):
        from matmaster.mcp.connection import MCPConnection
        assert hasattr(MCPConnection, "call_tool")

    def test_has_aenter_aexit_methods(self):
        from matmaster.mcp.connection import MCPConnection
        assert hasattr(MCPConnection, "__aenter__")
        assert hasattr(MCPConnection, "__aexit__")

    def test_mcp_connect_timeout_constant_exists(self):
        from matmaster.mcp import connection
        assert hasattr(connection, "MCP_CONNECT_TIMEOUT")
        assert isinstance(connection.MCP_CONNECT_TIMEOUT, float)
        assert connection.MCP_CONNECT_TIMEOUT > 0


class TestNoEvoMasterImportsInConnection:
    def test_no_top_level_evomaster_imports(self):
        module_file = Path(
            __import__("matmaster.mcp.connection", fromlist=["connection"]).__file__
        )
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
        assert top_level_evo == [], (
            f"Found {len(top_level_evo)} top-level evomaster imports in connection.py"
        )

    def test_no_evomaster_string_in_source(self):
        import matmaster.mcp.connection as mod
        source = inspect.getsource(mod)
        assert "evomaster" not in source, "Found 'evomaster' in connection.py source"
