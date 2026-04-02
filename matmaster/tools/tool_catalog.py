"""ToolCatalog -- facade over ToolRegistry with BUILTIN_CLAIMS and BUILTIN_META.

Provides base+overlay structure with version tracking for lazy MCP tool
injection. get_tool() injects ResourceClaim, ToolPlane, effect_level, and
fast_path_eligible metadata for known builtin tools (per D-09).

Unknown tools (MCP/skill overlay) fall back to default empty claims
and CONTROL_PLANE placement.
"""

from __future__ import annotations

from typing import Any

from matmaster.tools.tool_compiler import BUILTIN_CLAIMS, BUILTIN_META, ToolCompiler
from matmaster.tools.tool_registry import Tool, ToolRegistry
from matmaster.types.tool_spec import ToolInstance
from matmaster.types.topology import RuntimeTopology


class ToolCatalog:
    """Facade over ToolRegistry with builtin claims and metadata injection.

    Per D-04: All operations delegate to internal ToolRegistry.
    ContextBuilder / SkillTool / MCP injection paths unchanged.

    Per D-09: get_tool() injects ResourceClaim, ToolPlane, effect_level,
    and fast_path_eligible from BUILTIN_CLAIMS and BUILTIN_META lookup
    tables for known builtin tools.

    version starts at 0 and increments on each register_overlay() call.
    Kernel compares version each turn to decide whether to refresh
    tool_definitions for the LLM.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        compiler: ToolCompiler | None = None,
        topology: RuntimeTopology | None = None,
    ) -> None:
        self._registry = registry
        self._compiler = compiler or ToolCompiler()
        self._topology = topology or RuntimeTopology(
            session_kind="local",
            control_root="/tmp/control",
            workspace_root="/tmp/workspace",
        )
        self._compiled_tools: dict[str, ToolInstance] = {}
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
        self._compiled_tools[tool.name] = self._compiler.compile(
            tool,
            self._topology,
            source=source,
        )
        self._version += 1

    def get_tool(self, tool_name: str) -> ToolInstance | None:
        """Look up tool and wrap as ToolInstance with claims/meta injection.

        For builtin tools listed in BUILTIN_CLAIMS and BUILTIN_META,
        injects correct ResourceClaim, ToolPlane, effect_level, and
        fast_path_eligible. Unknown tools get default empty claims.
        """
        cached = self._compiled_tools.get(tool_name)
        if cached is not None:
            return cached

        raw_tool = self._registry._tools.get(tool_name)
        if raw_tool is None:
            return None
        source = self._registry._sources.get(tool_name, "unknown")
        compiled = self._compiler.compile(
            raw_tool,
            self._topology,
            source=source,
        )
        self._compiled_tools[tool_name] = compiled
        return compiled

    def build_definitions(self) -> list[dict[str, Any]]:
        """Delegate to registry for OpenAI function calling format."""
        return self._registry.get_tool_definitions()

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, name: str) -> bool:
        return name in self._registry
