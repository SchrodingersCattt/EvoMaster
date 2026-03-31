"""Unit tests for Tool Protocol and ToolRegistry.

Tests cover: protocol conformance, register/execute, tool definitions,
override warnings, source tracking, empty registry, all_tools property.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from matmaster.tools.tool_registry import Tool, ToolRegistry
from matmaster.tools.tool_result import ToolResult

from .conftest import MockTool


class _ErrorStrTool:
    name = "error_tool"
    description = "returns error string"
    json_schema = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> str:
        return "Error: invalid input"


class _ExplicitToolResultTool:
    name = "explicit_tool_result"
    description = "returns explicit ToolResult"
    json_schema = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(status="success", content="Error: literal")


class TestToolProtocol:
    """Verify Tool Protocol isinstance checks with @runtime_checkable."""

    def test_tool_protocol_isinstance(self) -> None:
        """A class with name, description, json_schema, execute satisfies Tool."""
        tool = MockTool()
        assert isinstance(tool, Tool)


class TestToolRegistryBasic:
    """Register, execute, and definitions."""

    async def test_register_and_execute(self) -> None:
        """Register a tool, execute by name, returns tool's execute() result."""
        registry = ToolRegistry()
        tool = MockTool(name="greet", result="hello!")
        registry.register(tool, source="builtin")

        result = await registry.execute("greet", {})
        assert isinstance(result, ToolResult)
        assert result.content == "hello!"
        assert result.status == "success"

    async def test_register_unknown_tool_execute(self) -> None:
        """Execute non-existent tool returns error string with 'not found'."""
        registry = ToolRegistry()
        registry.register(MockTool(name="exists"), source="builtin")

        result = await registry.execute("missing_tool", {})
        assert result.status == "error"
        assert "not found" in result.content.lower()
        assert "exists" in result.content  # lists available tools

    def test_get_tool_definitions(self) -> None:
        """Returns list of dicts in OpenAI function calling format."""
        registry = ToolRegistry()
        tool = MockTool(name="calc", description="Calculator tool")
        registry.register(tool, source="builtin")

        defs = registry.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "calc"
        assert defs[0]["function"]["description"] == "Calculator tool"
        assert "parameters" in defs[0]["function"]

    async def test_empty_registry(self) -> None:
        """Empty registry: get_tool_definitions returns [], execute returns error."""
        registry = ToolRegistry()

        defs = registry.get_tool_definitions()
        assert defs == []

        result = await registry.execute("anything", {})
        assert result.status == "error"
        assert "not found" in result.content.lower()

    async def test_error_prefixed_string_normalizes_to_error(self) -> None:
        registry = ToolRegistry()
        registry.register(_ErrorStrTool(), source="test")

        result = await registry.execute("error_tool", {})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert result.content == "Error: invalid input"

    async def test_explicit_tool_result_preserves_success_status(self) -> None:
        registry = ToolRegistry()
        registry.register(_ExplicitToolResultTool(), source="test")

        result = await registry.execute("explicit_tool_result", {})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.content == "Error: literal"


class TestToolRegistryOverride:
    """Override and source tracking behavior."""

    async def test_override_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Register two tools with same name, second overwrites, warning logged."""
        registry = ToolRegistry()
        tool_a = MockTool(name="shared", result="first")
        tool_b = MockTool(name="shared", result="second")

        registry.register(tool_a, source="builtin")
        with caplog.at_level(logging.WARNING):
            registry.register(tool_b, source="mcp")

        # Second tool overwrites first
        result = await registry.execute("shared", {})
        assert result.content == "second"

        # Warning was logged mentioning the tool name
        assert any("shared" in record.message for record in caplog.records)

    def test_source_tracking(self) -> None:
        """Register tools with different sources, filter by source."""
        registry = ToolRegistry()
        registry.register(MockTool(name="builtin_tool"), source="builtin")
        registry.register(MockTool(name="mcp_tool"), source="mcp")
        registry.register(MockTool(name="skill_tool"), source="skill")

        builtin_tools = registry.get_tools_by_source("builtin")
        assert len(builtin_tools) == 1
        assert builtin_tools[0].name == "builtin_tool"

    async def test_register_order_builtin_mcp_skill(self) -> None:
        """Register builtin then MCP then skill with same name, final is skill."""
        registry = ToolRegistry()
        registry.register(MockTool(name="overlap", result="builtin"), source="builtin")
        registry.register(MockTool(name="overlap", result="mcp"), source="mcp")
        registry.register(MockTool(name="overlap", result="skill"), source="skill")

        result = await registry.execute("overlap", {})
        assert result.content == "skill"


class TestToolRegistryProperties:
    """Property accessors and dunder methods."""

    def test_all_tools_property(self) -> None:
        """registry.all_tools returns list of all registered Tool instances."""
        registry = ToolRegistry()
        registry.register(MockTool(name="a"), source="builtin")
        registry.register(MockTool(name="b"), source="mcp")

        tools = registry.all_tools
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"a", "b"}

    def test_len(self) -> None:
        """len(registry) returns number of registered tools."""
        registry = ToolRegistry()
        assert len(registry) == 0
        registry.register(MockTool(name="x"), source="builtin")
        assert len(registry) == 1

    def test_contains(self) -> None:
        """'tool_name' in registry checks membership."""
        registry = ToolRegistry()
        registry.register(MockTool(name="present"), source="builtin")
        assert "present" in registry
        assert "absent" not in registry
