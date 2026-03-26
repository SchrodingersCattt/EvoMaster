from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from matmaster.tools.tool_result import ToolResult
from matmaster.tools.tool_registry import Tool
from matmaster.tools.lazy_mcp import LazyMCPTool, configure_mcp_manager


class FakeConnector:
    """Fake LazyMCPConnector for testing."""

    def __init__(self):
        self.session = MagicMock()
        self.connect_calls: list[tuple[str, str]] = []
        self._fake_tool = MagicMock()
        self._fake_tool.execute.return_value = ("result_text", {"success": True})

    def connect_and_get_tool(self, server_name: str, remote_tool_name: str):
        self.connect_calls.append((server_name, remote_tool_name))
        return self._fake_tool


class TestLazyMCPToolProtocol:
    def test_satisfies_tool_protocol(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="Build bulk structure",
            input_schema={"type": "object", "properties": {}},
            connector=connector,
        )
        assert isinstance(tool, Tool)

    def test_properties(self):
        connector = FakeConnector()
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="Build bulk structure",
            input_schema=schema,
            connector=connector,
        )
        assert tool.name == "mat_sg_build_bulk"
        assert tool.description == "Build bulk structure"
        assert tool.json_schema == schema


class TestLazyMCPToolExecution:
    def test_first_execute_connects(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="desc",
            input_schema={},
            connector=connector,
        )
        result = tool.execute({"param": "value"})
        assert len(connector.connect_calls) == 1
        assert connector.connect_calls[0] == ("mat_sg", "build_bulk")
        connector._fake_tool.execute.assert_called_once()

    def test_second_execute_reuses_connection(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="desc",
            input_schema={},
            connector=connector,
        )
        tool.execute({"a": "1"})
        tool.execute({"a": "2"})
        # Only connected once
        assert len(connector.connect_calls) == 1
        # But executed twice
        assert connector._fake_tool.execute.call_count == 2

    def test_execute_returns_string(self):
        connector = FakeConnector()
        connector._fake_tool.execute.return_value = ("hello world", {})
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
        )
        result = tool.execute({})
        assert isinstance(result, ToolResult)
        assert result.content == "hello world"
        assert result.status == "success"
        assert result.info == {}

    def test_execute_serializes_dict_observation(self):
        connector = FakeConnector()
        connector._fake_tool.execute.return_value = ({"key": "val"}, {})
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
        )
        result = tool.execute({})
        assert json.loads(result.content) == {"key": "val"}

    def test_execute_returns_tool_result_with_info(self):
        connector = FakeConnector()
        connector._fake_tool.execute.return_value = ("result", {"saved_path": "/tmp/x"})
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )

        result = tool.execute({})
        assert result.status == "success"
        assert result.info == {"saved_path": "/tmp/x"}

    def test_execute_error_prefixed_observation_is_error_without_info_key(self):
        connector = FakeConnector()
        connector._fake_tool.execute.return_value = ("Error: remote failure", {})
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )

        result = tool.execute({})
        assert result.status == "error"
        assert result.content == "Error: remote failure"


class FakeMCPManager:
    """Minimal MCPToolManager mock for configure_mcp_manager tests."""

    def __init__(self):
        self.path_adaptor_servers: set = set()
        self.path_adaptor_factory = None
        self.sync_tools_by_server: dict = {}
        self.tool_include_only: dict = {}


class TestConfigureMCPManager:
    def test_sets_path_adaptor_servers_from_explicit_list(self):
        manager = FakeMCPManager()
        config = {
            "path_adaptor": "calculation",
            "calculation_servers": ["mat_sg", "mat_dpa"],
        }
        configure_mcp_manager(manager, config)
        assert manager.path_adaptor_servers == {"mat_sg", "mat_dpa"}

    def test_path_adaptor_servers_fallback_to_all_servers(self):
        """When calculation_servers is absent, fallback to all_server_names."""
        manager = FakeMCPManager()
        config = {"path_adaptor": "calculation"}
        configure_mcp_manager(
            manager, config, all_server_names={"mat_sg", "mat_sn", "mat_doc"}
        )
        assert manager.path_adaptor_servers == {"mat_sg", "mat_sn", "mat_doc"}

    def test_sync_tools_only_inside_calculation_branch(self):
        """sync_tools_by_server is only set when path_adaptor == calculation."""
        manager = FakeMCPManager()
        config = {
            "path_adaptor": "calculation",
            "calculation_executors": {
                "mat_sg": {"sync_tools": ["build_bulk_structure_by_wyckoff"]},
            },
        }
        configure_mcp_manager(manager, config)
        assert "build_bulk_structure_by_wyckoff" in manager.sync_tools_by_server["mat_sg"]

    def test_sync_tools_not_set_without_calculation(self):
        """Without path_adaptor=calculation, sync_tools_by_server stays empty."""
        manager = FakeMCPManager()
        config = {
            "calculation_executors": {
                "mat_sg": {"sync_tools": ["build_bulk_structure_by_wyckoff"]},
            },
        }
        configure_mcp_manager(manager, config)
        assert manager.sync_tools_by_server == {}

    def test_sets_tool_include_only(self):
        manager = FakeMCPManager()
        config = {
            "tool_include_only": {
                "mat_sn": ["web-search", "search-papers-enhanced"],
                "bad_entry": "not_a_list",
            }
        }
        configure_mcp_manager(manager, config)
        assert manager.tool_include_only["mat_sn"] == ["web-search", "search-papers-enhanced"]
        assert manager.tool_include_only["bad_entry"] == []

    def test_empty_config_noop(self):
        manager = FakeMCPManager()
        configure_mcp_manager(manager, {})
        assert manager.path_adaptor_servers == set()
        assert manager.sync_tools_by_server == {}
        assert manager.tool_include_only == {}


from unittest.mock import AsyncMock, patch, MagicMock

try:
    from matmaster.tools.lazy_mcp import LazyMCPConnector
    _has_lazy_mcp_connector = True
except ImportError:
    _has_lazy_mcp_connector = False


@pytest.mark.skipif(not _has_lazy_mcp_connector, reason="LazyMCPConnector not yet implemented")
class TestLazyMCPConnector:
    def test_init_state(self):
        connector = LazyMCPConnector(
            mcp_server_config={"mat_sg": {"transport": "http", "url": "http://localhost"}},
            mcp_config={},
        )
        assert connector._manager is None
        assert connector._loop is None

    def test_cleanup_noop_when_not_connected(self):
        """Cleanup on a fresh connector should not raise."""
        connector = LazyMCPConnector(
            mcp_server_config={},
            mcp_config={},
        )
        connector.cleanup()  # Should not raise

    def test_missing_server_raises(self):
        connector = LazyMCPConnector(
            mcp_server_config={},
            mcp_config={},
        )
        # Force _ensure_manager to not actually create event loops
        # Create a minimal fake manager
        fake_manager = MagicMock()
        fake_manager.connections = {}
        connector._manager = fake_manager
        connector._server_config = {}
        with pytest.raises(ValueError, match="not in config"):
            connector.connect_and_get_tool("nonexistent", "some_tool")
