"""ToolCatalog -- facade over ToolRegistry with BUILTIN_CLAIMS and BUILTIN_META.

Provides base+overlay structure with version tracking for lazy MCP tool
injection. get_tool() injects ResourceClaim, ToolPlane, effect_level, and
fast_path_eligible metadata for known builtin tools (per D-09).

Unknown tools (MCP/skill overlay) fall back to default empty claims
and CONTROL_PLANE placement.
"""

from __future__ import annotations

from typing import Any

from matmaster.tools.tool_registry import Tool, ToolRegistry
from matmaster.types.tool_spec import ResourceClaim, ToolBinding, ToolInstance, ToolSpec
from matmaster.types.topology import ToolPlane

# spec section 8.2: builtin tool ResourceClaim declarations (per D-09)
BUILTIN_CLAIMS: dict[str, tuple[ResourceClaim, ...]] = {
    "execute_bash": (ResourceClaim(resource_id="session", mode="exclusive"),),
    "list_dir": (ResourceClaim(resource_id="session", mode="exclusive"),),
    "glob": (ResourceClaim(resource_id="session", mode="exclusive"),),
    "grep": (ResourceClaim(resource_id="session", mode="exclusive"),),
    "read_file": (ResourceClaim(resource_id="workspace", mode="shared_read"),),
    "write_file": (ResourceClaim(resource_id="workspace", mode="exclusive"),),
    "edit_file": (ResourceClaim(resource_id="workspace", mode="exclusive"),),
    "task_create": (ResourceClaim(resource_id="task-store", mode="exclusive"),),
    "task_get": (ResourceClaim(resource_id="task-store", mode="shared_read"),),
    "task_list": (ResourceClaim(resource_id="task-store", mode="shared_read"),),
    "task_update": (ResourceClaim(resource_id="task-store", mode="exclusive"),),
    "task_complete": (ResourceClaim(resource_id="task-store", mode="exclusive"),),
    "mm_web_search": (ResourceClaim(resource_id="web", mode="counted", limit=3),),
    "web_fetch": (ResourceClaim(resource_id="web", mode="counted", limit=3),),
    "spawn": (ResourceClaim(resource_id="spawn", mode="counted", limit=2),),
    "monitor_job": (
        ResourceClaim(resource_id="workspace", mode="exclusive"),
        ResourceClaim(resource_id="artifact-sync", mode="exclusive"),
    ),
}

# spec section 10: builtin tool ToolPlane + ToolSpec metadata
# (plane, effect_level, fast_path_eligible)
BUILTIN_META: dict[str, tuple[ToolPlane, str, bool]] = {
    "execute_bash": (ToolPlane.SESSION_SHELL, "local_mutation", False),
    "list_dir": (ToolPlane.SESSION_SHELL, "pure_read", False),
    "glob": (ToolPlane.SESSION_SHELL, "pure_read", False),
    "grep": (ToolPlane.SESSION_SHELL, "pure_read", False),
    "read_file": (ToolPlane.SESSION_FS, "pure_read", True),
    "write_file": (ToolPlane.SESSION_FS, "local_mutation", False),
    "edit_file": (ToolPlane.SESSION_FS, "local_mutation", False),
    "task_create": (ToolPlane.CONTROL_PLANE, "local_mutation", False),
    "task_get": (ToolPlane.CONTROL_PLANE, "pure_read", True),
    "task_list": (ToolPlane.CONTROL_PLANE, "pure_read", True),
    "task_update": (ToolPlane.CONTROL_PLANE, "local_mutation", False),
    "task_complete": (ToolPlane.CONTROL_PLANE, "local_mutation", False),
    "mm_web_search": (ToolPlane.EXTERNAL_SERVICE, "external_write", False),
    "web_fetch": (ToolPlane.EXTERNAL_SERVICE, "external_write", False),
    "spawn": (ToolPlane.CONTROL_PLANE, "local_mutation", False),
    "monitor_job": (ToolPlane.SESSION_FS, "external_write", False),
}


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
        """Look up tool and wrap as ToolInstance with claims/meta injection.

        For builtin tools listed in BUILTIN_CLAIMS and BUILTIN_META,
        injects correct ResourceClaim, ToolPlane, effect_level, and
        fast_path_eligible. Unknown tools get default empty claims.
        """
        raw_tool = self._registry._tools.get(tool_name)
        if raw_tool is None:
            return None
        source = self._registry._sources.get(tool_name, "unknown")
        claims = BUILTIN_CLAIMS.get(tool_name, ())
        meta = BUILTIN_META.get(tool_name)
        if meta is not None:
            plane, effect_level, fast_path = meta
        else:
            plane = ToolPlane.CONTROL_PLANE
            effect_level = "local_mutation"
            fast_path = False
        spec = ToolSpec(
            tool_name=raw_tool.name,
            description=raw_tool.description,
            args_schema=raw_tool.json_schema,
            source=source,
            effect_level=effect_level,
            fast_path_eligible=fast_path,
        )
        binding = ToolBinding(
            binding_key=f"{plane.value}:{raw_tool.name}",
            plane=plane,
            resource_claims=claims,
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
