from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from matmaster.tools.tool_registry import Tool
from matmaster.tools.lazy_mcp import LazyMCPTool


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
        assert result == "hello world"

    def test_execute_serializes_dict_observation(self):
        connector = FakeConnector()
        connector._fake_tool.execute.return_value = ({"key": "val"}, {})
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
        )
        result = tool.execute({})
        assert json.loads(result) == {"key": "val"}
