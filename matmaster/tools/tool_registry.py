"""Tool Protocol and ToolRegistry -- pure storage layer for the assembly layer.

Tool is the @runtime_checkable Protocol each tool must satisfy (name, description,
json_schema, execute). ToolRegistry provides flat-namespace registration with source
tags (builtin/mcp/skill) and same-name override with warning.

Upper-layer operations (execute dispatch, OpenAI definitions, stop_event injection)
are handled by ToolCatalog and FullToolRunner. Registry is consumed only as a storage
backend via ToolCatalog.registry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.tool_desc_ctx import ToolDescriptionContext
    from matmaster.types.tool_spec import ResourceClaim
    from matmaster.types.topology import ToolPlane

logger = logging.getLogger(__name__)

EffectLevel = Literal["none", "local_mutation", "external_effect"]


@runtime_checkable
class Tool(Protocol):
    """Tool interface: name, description, schema, execute.

    Every tool source (builtin, MCP, skill) wraps its tool into a class
    satisfying this Protocol. The kernel sees only this unified interface.
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    def describe(self, ctx: ToolDescriptionContext) -> str: ...

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None: ...

    @property
    def json_schema(self) -> dict[str, Any]: ...

    @property
    def resource_claims(self) -> tuple[ResourceClaim, ...]: ...

    @property
    def capabilities(self) -> frozenset[str]: ...

    @property
    def effect_level(self) -> EffectLevel: ...

    @property
    def fast_path_eligible(self) -> bool: ...

    @property
    def max_result_chars(self) -> int: ...

    @property
    def plane(self) -> ToolPlane: ...

    @property
    def state_mode(self) -> Literal["stateless", "persistent"]: ...

    @property
    def stop_mode(self) -> Literal["cancellable", "best_effort", "non_cancellable"]: ...

    @property
    def exposed_to_model(self) -> bool: ...

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult | None: ...


class ToolRegistry:
    """Pure storage: flat-namespace tool registry with source tracking.

    Registration order determines override: assemble() registers
    builtin -> MCP -> skill, so skill tools take final precedence.
    Same-name registration overwrites the previous entry with a warning log.

    Upper-layer operations (execute, definitions, stop_event) live in
    ToolCatalog and FullToolRunner -- not here.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._sources: dict[str, str] = {}

    def register(self, tool: Tool, *, source: str = "unknown") -> None:
        """Register a tool. Overwrites existing same-name tool with warning."""
        if tool.name in self._tools:
            old_source = self._sources[tool.name]
            logger.warning(
                "Tool '%s' overwritten: source '%s' -> '%s'",
                tool.name,
                old_source,
                source,
            )
        self._tools[tool.name] = tool
        self._sources[tool.name] = source

    @property
    def all_tools(self) -> list[Tool]:
        """Return all registered Tool instances."""
        return list(self._tools.values())

    def get_raw(self, name: str) -> Tool | None:
        """Return the registered tool instance by name, or None."""
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:  # type: ignore[override]
        return name in self._tools
