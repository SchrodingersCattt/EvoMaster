"""ToolCatalog -- Phase 1 facade over ToolRegistry.

Provides base+overlay structure with version tracking for lazy MCP tool
injection. Phase 1 delegates all operations to the internal ToolRegistry
(per D-04: ContextBuilder / SkillTool / MCP injection paths unchanged).

Phase 2 (Plan 35) will make ToolCatalog the sole upper-layer consumer
after ToolRegistry is degraded to pure storage.
"""

from __future__ import annotations

from typing import Any

from matmaster.tools.tool_registry import Tool, ToolRegistry
from matmaster.types.tool_spec import ToolBinding, ToolInstance, ToolSpec
from matmaster.types.topology import ToolPlane


class ToolCatalog:
    """Phase 1: facade over ToolRegistry with base+overlay and version tracking.

    Per D-04: All operations delegate to internal ToolRegistry.
    ContextBuilder / SkillTool / MCP injection paths unchanged.

    version starts at 0 and increments on each register_overlay() call.
    Kernel compares version each turn to decide whether to refresh
    tool_definitions for the LLM.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._version: int = 0

    @property
    def version(self) -> int:
        """Current catalog version. Incremented on overlay registrations."""
        return self._version

    @property
    def registry(self) -> ToolRegistry:
        """Expose internal registry for backward-compatible access paths."""
        return self._registry

    def register_overlay(self, tool: Tool, *, source: str = "mcp") -> None:
        """Register a tool in the overlay layer (MCP/skill lazy injection).

        Increments version so Kernel can detect tool set changes.
        """
        self._registry.register(tool, source=source)
        self._version += 1

    def get_tool(self, tool_name: str) -> ToolInstance | None:
        """Look up tool and wrap as ToolInstance. Returns None if not found."""
        raw_tool = self._registry._tools.get(tool_name)
        if raw_tool is None:
            return None
        source = self._registry._sources.get(tool_name, "unknown")
        spec = ToolSpec(
            tool_name=raw_tool.name,
            description=raw_tool.description,
            args_schema=raw_tool.json_schema,
            source=source,
        )
        binding = ToolBinding(
            binding_key=f"{ToolPlane.CONTROL_PLANE.value}:{raw_tool.name}",
            plane=ToolPlane.CONTROL_PLANE,
        )
        return ToolInstance(
            tool_spec=spec,
            tool_binding=binding,
            tool_executor=raw_tool.execute,
        )

    def build_definitions(self) -> list[dict[str, Any]]:
        """Delegate to registry for OpenAI function calling format."""
        return self._registry.get_tool_definitions()

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, name: str) -> bool:
        return name in self._registry
