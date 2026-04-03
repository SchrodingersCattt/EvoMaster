"""Tests for ToolCatalog facade over ToolRegistry.

Verifies:
- Initial version == 0
- build_definitions() returns OpenAI function calling format
- register_overlay() increments version
- get_tool() returns ToolInstance
- get_tool() returns None for missing tool
- Multiple overlays accumulate version
- __contains__ and __len__ delegation
"""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.tool_spec import ToolInstance


# ── Helpers ──────────────────────────────────────────────


class _FakeTool:
    """Minimal Tool Protocol implementation for testing."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"fake tool {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    async def execute(self, arguments: dict[str, Any]) -> str:
        return f"executed {self._name}"


def _make_catalog(*base_tools: str) -> ToolCatalog:
    """Create a ToolCatalog with named base tools."""
    registry = ToolRegistry()
    for name in base_tools:
        registry.register(_FakeTool(name), source="builtin")
    return ToolCatalog(registry)


# ── Version ──────────────────────────────────────────────


class TestCatalogVersion:
    def test_initial_version_is_zero(self) -> None:
        """ToolCatalog starts with version 0."""
        catalog = _make_catalog()
        assert catalog.version == 0

    def test_register_overlay_increments_version(self) -> None:
        """register_overlay() increments version by 1."""
        catalog = _make_catalog()
        catalog.register_overlay(_FakeTool("mcp_tool"))
        assert catalog.version == 1

    def test_multiple_overlays_accumulate_version(self) -> None:
        """Each register_overlay() call increments version."""
        catalog = _make_catalog()
        catalog.register_overlay(_FakeTool("a"))
        catalog.register_overlay(_FakeTool("b"))
        catalog.register_overlay(_FakeTool("c"))
        assert catalog.version == 3


# ── build_definitions ─────────────────────────────────────


class TestCatalogBuildDefinitions:
    def test_returns_openai_function_format(self) -> None:
        """build_definitions() returns OpenAI function calling format."""
        catalog = _make_catalog("alpha", "beta")
        defs = catalog.build_definitions()
        assert len(defs) == 2
        names = {d["function"]["name"] for d in defs}
        assert names == {"alpha", "beta"}

    def test_includes_overlay_tools(self) -> None:
        """Overlay tools appear in build_definitions()."""
        catalog = _make_catalog("base")
        catalog.register_overlay(_FakeTool("overlay"))
        defs = catalog.build_definitions()
        names = {d["function"]["name"] for d in defs}
        assert "overlay" in names
        assert "base" in names


# ── get_tool ──────────────────────────────────────────────


class TestCatalogGetTool:
    def test_returns_tool_instance(self) -> None:
        """get_tool() returns a ToolInstance for registered tool."""
        catalog = _make_catalog("my_tool")
        instance = catalog.get_tool("my_tool")
        assert instance is not None
        assert isinstance(instance, ToolInstance)
        assert instance.tool_spec.tool_name == "my_tool"

    def test_returns_none_for_missing(self) -> None:
        """get_tool() returns None for unregistered tool."""
        catalog = _make_catalog()
        assert catalog.get_tool("nonexistent") is None

    def test_tool_instance_has_binding(self) -> None:
        """Returned ToolInstance has a valid ToolBinding."""
        catalog = _make_catalog("bound_tool")
        instance = catalog.get_tool("bound_tool")
        assert instance is not None
        assert instance.tool_binding is not None
        assert "bound_tool" in instance.tool_binding.binding_key

    def test_tool_instance_has_executor(self) -> None:
        """Returned ToolInstance has an executor callable."""
        catalog = _make_catalog("exec_tool")
        instance = catalog.get_tool("exec_tool")
        assert instance is not None
        assert callable(instance.tool_executor)


# ── Container protocol ────────────────────────────────────


class TestCatalogContainer:
    def test_contains_registered_tool(self) -> None:
        """__contains__ returns True for registered tool."""
        catalog = _make_catalog("present")
        assert "present" in catalog

    def test_not_contains_missing_tool(self) -> None:
        """__contains__ returns False for missing tool."""
        catalog = _make_catalog()
        assert "absent" not in catalog

    def test_len_reflects_tool_count(self) -> None:
        """__len__ returns number of registered tools."""
        catalog = _make_catalog("a", "b", "c")
        assert len(catalog) == 3

    def test_len_includes_overlays(self) -> None:
        """__len__ includes overlay tools."""
        catalog = _make_catalog("base")
        catalog.register_overlay(_FakeTool("overlay"))
        assert len(catalog) == 2

    def test_registry_property(self) -> None:
        """registry property exposes the internal ToolRegistry."""
        registry = ToolRegistry()
        catalog = ToolCatalog(registry)
        assert catalog.registry is registry


# ── Compiled definitions and exposed_to_model ─────────────


class TestCatalogCompiledDefinitions:
    def test_build_definitions_uses_compiled_instances(self) -> None:
        """build_definitions() uses compiled ToolInstance data, not raw registry."""
        catalog = _make_catalog("alpha")
        defs = catalog.build_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "alpha"

    def test_hidden_overlay_not_in_definitions(self) -> None:
        """Tool with exposed_to_model=False is excluded from build_definitions()."""

        class _HiddenTool:
            name = "hidden_overlay"
            description = "hidden"
            json_schema: dict[str, Any] = {"type": "object", "properties": {}}
            tool_runtime_meta: dict[str, Any] = {"exposed_to_model": False}

            async def execute(self, arguments):
                return "ok"

        catalog = _make_catalog("visible")
        catalog.register_overlay(_HiddenTool(), source="mcp")
        defs = catalog.build_definitions()
        names = {d["function"]["name"] for d in defs}
        assert "visible" in names
        assert "hidden_overlay" not in names


class TestMCPOverlayRuntimeMeta:
    def test_mcp_tool_respects_runtime_metadata_overrides(self) -> None:
        """Compiler uses tool_runtime_meta to override plane/effect_level."""
        from matmaster.tools.tool_compiler import ToolCompiler
        from matmaster.types.topology import RuntimeTopology, ToolPlane

        class _MetaTool:
            name = "mat_sg_build_bulk"
            description = "build bulk"
            json_schema: dict[str, Any] = {"type": "object", "properties": {}}
            tool_runtime_meta: dict[str, Any] = {
                "plane": "external_service",
                "effect_level": "external_effect",
            }

            async def execute(self, arguments):
                return "ok"

        topology = RuntimeTopology(
            session_kind="local",
            control_root="/tmp/ctrl",
            workspace_root="/tmp/ws",
            active_planes=frozenset(ToolPlane),
        )
        compiler = ToolCompiler()
        instance = compiler.compile(_MetaTool(), topology, source="mcp")
        assert instance.tool_binding.plane == ToolPlane.EXTERNAL_SERVICE
        assert instance.tool_spec.effect_level == "external_effect"
