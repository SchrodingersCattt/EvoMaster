"""Tests for ToolCompiler and ToolCatalog compiler delegation."""

from __future__ import annotations

from typing import Any

from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_compiler import ToolCompiler
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import RuntimeTopology, ToolPlane


class _FakeTool:
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
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content=f"executed {self._name}")


def _make_topology() -> RuntimeTopology:
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=frozenset(ToolPlane),
    )


class TestToolCompiler:
    def test_compile_builtin_bash(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("execute_bash"),
            _make_topology(),
            source="builtin",
        )

        assert instance.tool_binding.plane == ToolPlane.SESSION_SHELL
        assert instance.tool_spec.effect_level == "local_mutation"
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource_id="session", mode="exclusive"),
        )

    def test_compile_builtin_read_file(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("read_file"),
            _make_topology(),
            source="builtin",
        )

        assert instance.tool_binding.plane == ToolPlane.SESSION_FS
        assert instance.tool_spec.effect_level == "pure_read"
        assert instance.tool_spec.fast_path_eligible is True
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource_id="workspace", mode="shared_read"),
        )

    def test_compile_builtin_web_search(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("mm_web_search"),
            _make_topology(),
            source="builtin",
        )

        assert instance.tool_binding.plane == ToolPlane.EXTERNAL_SERVICE
        assert instance.tool_spec.effect_level == "external_write"
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource_id="web", mode="counted", limit=3),
        )

    def test_compile_unknown_tool(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("custom_mcp_tool"),
            _make_topology(),
            source="mcp",
        )

        assert instance.tool_binding.plane == ToolPlane.CONTROL_PLANE
        assert instance.tool_spec.effect_level == "local_mutation"
        assert instance.tool_spec.fast_path_eligible is False
        assert instance.tool_binding.resource_claims == ()
        assert instance.tool_spec.source == "mcp"


class TestToolCatalogCompilerDelegation:
    def test_get_tool_uses_compiler(self) -> None:
        registry = ToolRegistry()
        tool = _FakeTool("execute_bash")
        registry.register(tool, source="builtin")

        topology = _make_topology()
        compiler = ToolCompiler()
        catalog = ToolCatalog(registry, compiler=compiler, topology=topology)

        instance = catalog.get_tool("execute_bash")
        expected = compiler.compile(tool, topology, source="builtin")

        assert instance is not None
        assert instance.tool_binding == expected.tool_binding
        assert instance.tool_spec == expected.tool_spec

    def test_register_overlay_uses_compiler(self) -> None:
        registry = ToolRegistry()
        topology = _make_topology()
        compiler = ToolCompiler()
        catalog = ToolCatalog(registry, compiler=compiler, topology=topology)

        overlay = _FakeTool("overlay_tool")
        catalog.register_overlay(overlay, source="mcp")
        instance = catalog.get_tool("overlay_tool")

        assert instance is not None
        assert instance.tool_spec.source == "mcp"
        assert instance.tool_binding.plane == ToolPlane.CONTROL_PLANE
        assert instance.tool_binding.binding_key == "control_plane:overlay_tool"
        assert instance.tool_binding.resource_claims == ()
