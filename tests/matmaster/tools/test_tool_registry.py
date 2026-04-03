"""Unit tests for Tool Protocol and ToolRegistry (pure storage layer).

Tests cover: protocol conformance, register, all_tools property,
override warnings, source tracking, empty registry, __len__, __contains__.

Note: execute(), get_tool_definitions(), get_tools_by_source() were removed
in Phase 35-03 (ToolRegistry demoted to pure storage). Those operations
now live in ToolCatalog and FullToolRunner.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from matmaster.tools.tool_registry import Tool, ToolRegistry

from .conftest import MockTool


class TestToolProtocol:
    """Verify Tool Protocol isinstance checks with @runtime_checkable."""

    def test_tool_protocol_isinstance(self) -> None:
        """A class with name, description, json_schema, execute satisfies Tool."""
        tool = MockTool()
        assert isinstance(tool, Tool)


class TestToolRegistryBasic:
    """Register and storage operations."""

    def test_register_and_all_tools(self) -> None:
        """Register a tool, all_tools returns it."""
        registry = ToolRegistry()
        tool = MockTool(name="greet", result="hello!")
        registry.register(tool, source="builtin")

        assert len(registry) == 1
        assert registry.all_tools[0].name == "greet"

    def test_empty_registry(self) -> None:
        """Empty registry: all_tools returns [], len returns 0."""
        registry = ToolRegistry()
        assert registry.all_tools == []
        assert len(registry) == 0


class TestToolRegistryOverride:
    """Override and source tracking behavior."""

    def test_override_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Register two tools with same name, second overwrites, warning logged."""
        registry = ToolRegistry()
        tool_a = MockTool(name="shared", result="first")
        tool_b = MockTool(name="shared", result="second")

        registry.register(tool_a, source="builtin")
        with caplog.at_level(logging.WARNING):
            registry.register(tool_b, source="mcp")

        # Second tool overwrites first
        assert len(registry) == 1
        assert registry.all_tools[0] is tool_b

        # Warning was logged mentioning the tool name
        assert any("shared" in record.message for record in caplog.records)

    def test_register_order_builtin_mcp_skill(self) -> None:
        """Register builtin then MCP then skill with same name, final is skill."""
        registry = ToolRegistry()
        registry.register(MockTool(name="overlap", result="builtin"), source="builtin")
        registry.register(MockTool(name="overlap", result="mcp"), source="mcp")
        t_skill = MockTool(name="overlap", result="skill")
        registry.register(t_skill, source="skill")

        assert len(registry) == 1
        assert registry.all_tools[0] is t_skill


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
