from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.tools.lazy_mcp import (
    LazyMCPConnector,
    LazyMCPTool,
    configure_mcp_manager,
)
from matmaster.tools.tool_registry import Tool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ToolExecutionContext
from matmaster.types.topology import ToolPlane


class FakeConnector:
    """Fake LazyMCPConnector for testing the new direct-call architecture."""

    def __init__(self, path_adaptor=None, *, session=None):
        self.workspace_path = "/fake/workspace"
        self.session = session
        self.ensure_calls: list[str] = []
        self._mock_conn = AsyncMock()
        self._mock_conn.call_tool.return_value = [MagicMock(text="result_text")]
        self._path_adaptor = path_adaptor

    async def ensure_connection(self, server_name: str) -> dict:
        self.ensure_calls.append(server_name)
        return {"connection": self._mock_conn, "path_adaptor": self._path_adaptor}


class SlowConnector(FakeConnector):
    """Connector whose call_tool waits indefinitely."""

    def __init__(self):
        super().__init__()

        async def _sleep_forever(*_args, **_kwargs):
            await asyncio.sleep(9999)

        self._mock_conn.call_tool = AsyncMock(side_effect=_sleep_forever)


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

    def test_protocol_properties_come_from_runtime_meta(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="t",
            remote_tool_name="t",
            description="d",
            input_schema={},
            connector=connector,
            runtime_meta={
                "plane": "external_service",
                "effect_level": "external_effect",
                "capabilities": ["materials.build"],
                "resource_claims": [
                    {"resource": "web", "mode": "counted", "max_concurrent": 2}
                ],
                "stop_mode": "best_effort",
                "max_result_chars": 123,
            },
        )

        assert tool.plane == ToolPlane.EXTERNAL_SERVICE
        assert tool.effect_level == "external_effect"
        assert tool.capabilities == frozenset({"materials.build"})
        assert tool.stop_mode == "best_effort"
        assert tool.max_result_chars == 123
        assert tool.describe(None) == "d"
        assert tool.prompt() is None


class TestLazyMCPToolExecution:
    async def test_do_call_returns_tool_result(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )
        result = await tool._do_call({"key": "val"})
        assert isinstance(result, ToolResult)
        assert result.status == "success"

    async def test_first_execute_connects(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="desc",
            input_schema={},
            connector=connector,
        )
        await tool.execute({"param": "value"})
        assert len(connector.ensure_calls) == 1
        assert connector.ensure_calls[0] == "mat_sg"
        connector._mock_conn.call_tool.assert_called_once_with(
            "build_bulk", {"param": "value"}
        )

    async def test_second_execute_reuses_connection(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_build_bulk",
            remote_tool_name="build_bulk",
            description="desc",
            input_schema={},
            connector=connector,
        )
        await tool.execute({"a": "1"})
        await tool.execute({"a": "2"})
        # Only connected once
        assert len(connector.ensure_calls) == 1
        # But called tool twice
        assert connector._mock_conn.call_tool.call_count == 2

    async def test_execute_returns_string_content(self):
        connector = FakeConnector()
        connector._mock_conn.call_tool.return_value = [MagicMock(text="hello world")]
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )
        result = await tool.execute({})
        assert isinstance(result, ToolResult)
        assert result.content == "hello world"
        assert result.status == "success"

    async def test_execute_returns_json_content(self):
        connector = FakeConnector()
        connector._mock_conn.call_tool.return_value = [MagicMock(text='{"key": "val"}')]
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )
        result = await tool.execute({})
        assert json.loads(result.content) == {"key": "val"}

    async def test_execute_error_from_call_tool(self):
        """MCPConnection.call_tool raises RuntimeError on isError=True."""
        connector = FakeConnector()
        connector._mock_conn.call_tool.side_effect = RuntimeError("remote failure")
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )
        result = await tool.execute({})
        assert result.status == "error"
        assert "remote failure" in result.content


class TestLazyMCPToolTimeout:
    def test_default_timeout(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )
        assert tool._timeout == 120.0

    def test_custom_timeout_via_constructor(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
            timeout=300.0,
        )
        assert tool._timeout == 300.0

    def test_runtime_meta_timeout_overrides_default(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
            runtime_meta={"timeout": 60},
        )
        assert tool._timeout == 60.0

    def test_runtime_meta_timeout_beats_constructor(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
            timeout=300.0,
            runtime_meta={"timeout": 45},
        )
        assert tool._timeout == 45.0


class TestLazyMCPToolExecuteWithContext:
    async def test_timeout_fires_on_hung_server(self):
        connector = SlowConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
            timeout=0.5,
        )
        exec_ctx = ToolExecutionContext()
        result = await tool.execute_with_context({}, exec_ctx)
        assert result.status == "error"
        assert "timed out" in result.content

    async def test_stop_event_before_call(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )
        stop = threading.Event()
        stop.set()
        exec_ctx = ToolExecutionContext(stop_event=stop)
        result = await tool.execute_with_context({}, exec_ctx)
        assert result.status == "cancelled"

    async def test_stop_event_during_call(self):
        connector = SlowConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
            timeout=30.0,
        )
        stop = threading.Event()
        exec_ctx = ToolExecutionContext(stop_event=stop)

        async def set_stop_later():
            await asyncio.sleep(0.2)
            stop.set()

        stopper = asyncio.create_task(set_stop_later())
        result = await tool.execute_with_context({}, exec_ctx)
        await stopper
        assert result.status == "cancelled"

    async def test_best_effort_cancel_message(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
            runtime_meta={"stop_mode": "best_effort"},
        )
        stop = threading.Event()
        stop.set()
        exec_ctx = ToolExecutionContext(stop_event=stop)
        result = await tool.execute_with_context({}, exec_ctx)
        assert "best-effort" in result.content

    async def test_cancellable_cancel_message(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
            runtime_meta={"stop_mode": "cancellable"},
        )
        stop = threading.Event()
        stop.set()
        exec_ctx = ToolExecutionContext(stop_event=stop)
        result = await tool.execute_with_context({}, exec_ctx)
        assert result.content == "Run cancelled."

    async def test_normal_execution_still_works(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )
        exec_ctx = ToolExecutionContext()
        result = await tool.execute_with_context({"key": "val"}, exec_ctx)
        assert result.status == "success"

    async def test_no_context_uses_timeout_only(self):
        connector = SlowConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
            timeout=0.5,
        )
        result = await tool.execute_with_context({}, None)
        assert result.status == "error"
        assert "timed out" in result.content

    async def test_race_timeout_and_stop_simultaneous(self):
        connector = SlowConnector()
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
            timeout=0.3,
        )
        stop = threading.Event()
        exec_ctx = ToolExecutionContext(stop_event=stop)

        async def set_stop_later():
            await asyncio.sleep(0.3)
            stop.set()

        stopper = asyncio.create_task(set_stop_later())
        result = await tool.execute_with_context({}, exec_ctx)
        await stopper
        assert result.status in ("error", "cancelled")


class TestLazyMCPToolFormatResult:
    """Test _format_result method that processes MCPConnection.call_tool output."""

    def _make_tool(self):
        connector = FakeConnector()
        return LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )

    def test_empty_content_list(self):
        tool = self._make_tool()
        assert tool._format_result([]) == ''

    def test_single_text_content(self):
        item = MagicMock(text="hello")
        tool = self._make_tool()
        assert tool._format_result([item]) == "hello"

    def test_single_json_string(self):
        item = MagicMock(text='{"a": 1}')
        tool = self._make_tool()
        result = tool._format_result([item])
        assert json.loads(result) == {"a": 1}

    def test_single_json_array(self):
        item = MagicMock(text='[1, 2, 3]')
        tool = self._make_tool()
        result = tool._format_result([item])
        assert json.loads(result) == [1, 2, 3]

    def test_invalid_json_returns_raw(self):
        item = MagicMock(text='{not valid json')
        tool = self._make_tool()
        assert tool._format_result([item]) == '{not valid json'

    def test_multiple_items_joined(self):
        items = [MagicMock(text="line1"), MagicMock(text="line2")]
        tool = self._make_tool()
        assert tool._format_result(items) == "line1\nline2"

    def test_dict_items_with_text_key(self):
        items = [{"text": "from dict"}]
        tool = self._make_tool()
        assert tool._format_result(items) == "from dict"

    def test_fallback_to_str(self):
        items = [42]
        tool = self._make_tool()
        assert tool._format_result(items) == "42"


class TestLazyMCPToolPathAdaptor:
    """Test path_adaptor integration in LazyMCPTool.execute."""

    async def test_path_adaptor_resolve_args_called(self):
        mock_adaptor = MagicMock()
        mock_adaptor.resolve_args.return_value = {"resolved": "path"}
        connector = FakeConnector(path_adaptor=mock_adaptor)
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_run",
            remote_tool_name="run",
            description="Run calculation",
            input_schema={"type": "object"},
            connector=connector,
        )
        await tool.execute({"input": "/local/file"})
        mock_adaptor.resolve_args.assert_called_once()
        # call_tool should receive resolved args
        connector._mock_conn.call_tool.assert_called_once_with(
            "run", {"resolved": "path"}
        )

    async def test_path_adaptor_receives_connector_session(self):
        mock_adaptor = MagicMock()
        mock_adaptor.resolve_args.return_value = {"resolved": "path"}
        fake_session = MagicMock()
        connector = FakeConnector(path_adaptor=mock_adaptor, session=fake_session)
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_run",
            remote_tool_name="run",
            description="Run calculation",
            input_schema={"type": "object"},
            connector=connector,
        )

        await tool.execute({"input": "/local/file"})

        assert mock_adaptor.resolve_args.call_args.kwargs["session"] is fake_session

    async def test_path_adaptor_failure_falls_back(self):
        mock_adaptor = MagicMock()
        mock_adaptor.resolve_args.side_effect = Exception("resolve failed")
        connector = FakeConnector(path_adaptor=mock_adaptor)
        tool = LazyMCPTool(
            server_name="mat_sg",
            tool_name="mat_sg_run",
            remote_tool_name="run",
            description="desc",
            input_schema={},
            connector=connector,
        )
        original_args = {"input": "/local/file"}
        await tool.execute(original_args)
        # Should fall back to original args
        connector._mock_conn.call_tool.assert_called_once_with("run", original_args)

    async def test_no_path_adaptor_passes_args_directly(self):
        connector = FakeConnector(path_adaptor=None)
        tool = LazyMCPTool(
            server_name="s",
            tool_name="s_t",
            remote_tool_name="t",
            description="",
            input_schema={},
            connector=connector,
        )
        args = {"param": "value"}
        await tool.execute(args)
        connector._mock_conn.call_tool.assert_called_once_with("t", args)


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
        assert (
            "build_bulk_structure_by_wyckoff" in manager.sync_tools_by_server["mat_sg"]
        )

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
        assert manager.tool_include_only["mat_sn"] == [
            "web-search",
            "search-papers-enhanced",
        ]
        assert manager.tool_include_only["bad_entry"] == []

    def test_empty_config_noop(self):
        manager = FakeMCPManager()
        configure_mcp_manager(manager, {})
        assert manager.path_adaptor_servers == set()
        assert manager.sync_tools_by_server == {}
        assert manager.tool_include_only == {}

    def test_path_adaptor_factory_uses_matmaster(self):
        """Verify factory uses matmaster.adaptors.calculation, not evomaster."""
        manager = FakeMCPManager()
        config = {
            "path_adaptor": "calculation",
            "calculation_servers": ["mat_sg"],
        }
        # The import is inside configure_mcp_manager's try block as a lazy import
        # from matmaster.adaptors.calculation. We patch at the source module.
        with patch(
            "matmaster.adaptors.calculation.get_calculation_path_adaptor"
        ) as mock_factory:
            mock_factory.return_value = MagicMock()
            configure_mcp_manager(manager, config)

        # Factory should be set (the actual import from matmaster.adaptors.calculation)
        assert manager.path_adaptor_factory is not None
        # Calling the factory should invoke get_calculation_path_adaptor
        manager.path_adaptor_factory()
        mock_factory.assert_called_once_with(config)


class TestLazyMCPConnector:
    def test_init_state(self):
        connector = LazyMCPConnector(
            mcp_server_config={
                "mat_sg": {"transport": "http", "url": "http://localhost"}
            },
            mcp_config={},
        )
        assert connector._manager is None
        assert connector._loop is None
        assert connector.workspace_path == ""

    def test_init_with_workspace_path(self):
        connector = LazyMCPConnector(
            mcp_server_config={},
            mcp_config={},
            workspace_path="/test/workspace",
        )
        assert connector.workspace_path == "/test/workspace"

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
        # Create a minimal fake manager
        fake_manager = MagicMock()
        fake_manager.connections = {}
        connector._manager = fake_manager
        connector._server_config = {}
        with pytest.raises(ValueError, match="not in config"):
            connector.connect_and_get_tool("nonexistent", "some_tool")

    def test_ensure_manager_uses_matmaster_mcp(self):
        """Verify _ensure_manager imports from matmaster.mcp.manager."""
        connector = LazyMCPConnector(
            mcp_server_config={"s": {"transport": "http", "url": "http://x"}},
            mcp_config={},
        )
        # Patch at the source module since it's a lazy import inside _ensure_manager
        with patch("matmaster.mcp.manager.MCPToolManager") as MockMgr:
            mock_instance = MagicMock()
            MockMgr.return_value = mock_instance
            manager = connector._ensure_manager()
            MockMgr.assert_called_once()
            assert manager is mock_instance


class TestNoEvoMasterImports:
    """Verify no evomaster imports remain in the module."""

    def test_no_evomaster_in_source(self):
        import inspect

        import matmaster.tools.lazy_mcp as mod

        source = inspect.getsource(mod)
        # Check there are no evomaster imports (code imports, not docstrings)
        lines = source.split('\n')
        import_lines = [
            line.strip()
            for line in lines
            if ('from evomaster' in line or 'import evomaster' in line)
            and not line.strip().startswith('#')
            and not line.strip().startswith('"')
            and not line.strip().startswith("'")
        ]
        assert import_lines == [], f"Found evomaster imports: {import_lines}"
