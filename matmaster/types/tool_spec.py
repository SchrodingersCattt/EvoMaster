"""Tool Runtime v2 spec types -- ToolSpec, ResourceClaim, ToolBinding, ToolInstance.

ToolSpec describes a tool's static properties (name, schema, capabilities,
effect level, etc.) independent of runtime binding.

ResourceClaim declares a tool's resource requirements (exclusive lock,
shared read, counted semaphore).

ToolBinding binds a tool to a specific execution plane with resource
claims, state mode, and stop mode.

ToolInstance combines ToolSpec + ToolBinding + executor callable into
a frozen dataclass -- the unit that ToolCatalog stores and ToolRunner
consumes.

All Pydantic models are frozen=True. ToolInstance is a frozen dataclass.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from matmaster.types.topology import ToolPlane


class ToolSpec(BaseModel):
    """Static tool specification -- describes what a tool is and can do.

    Independent of execution binding. Multiple ToolBindings can reference
    the same ToolSpec (e.g., bash on local vs ssh).
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str
    description: str = ""
    args_schema: dict[str, Any] = Field(default_factory=dict)
    source: str = "unknown"  # "builtin" | "mcp" | "skill" | "unknown"
    capabilities: frozenset[str] = frozenset()
    effect_level: str = "local_mutation"  # "pure_read" | "local_mutation" | "external_write"
    exposed_to_model: bool = True
    fast_path_eligible: bool = False
    max_result_chars: int = 0
    usage_hint: str = ""


class ResourceClaim(BaseModel):
    """Declares a tool's resource requirement.

    - exclusive: tool needs sole access (e.g., shell for interactive command)
    - shared_read: concurrent reads allowed (e.g., file read)
    - counted: up to `limit` concurrent users (e.g., API rate limiting)
    """

    model_config = ConfigDict(frozen=True)

    resource_id: str
    mode: Literal["exclusive", "shared_read", "counted"]
    limit: int | None = None  # only meaningful when mode="counted"


class ToolBinding(BaseModel):
    """Binds a tool to an execution plane with runtime constraints.

    binding_key format: "{plane}:{tool_name}" -- unique within a ToolCatalog.
    """

    model_config = ConfigDict(frozen=True)

    binding_key: str  # "{plane}:{tool_name}"
    plane: ToolPlane
    resource_claims: tuple[ResourceClaim, ...] = ()
    state_mode: str = "stateless"  # "stateless" | "turn_scoped" | "session_scoped"
    stop_mode: str = "immediate"  # "immediate" | "graceful" | "detached"


# Import ToolResult for the executor type signature
from matmaster.tools.tool_result import ToolResult  # noqa: E402


@dataclass(frozen=True)
class ToolInstance:
    """Frozen unit combining spec + binding + executor.

    This is what ToolCatalog stores and ToolRunner consumes.
    The executor is an async callable: dict[str, Any] -> Awaitable[ToolResult].
    """

    tool_spec: ToolSpec
    tool_binding: ToolBinding
    tool_executor: Callable[[dict[str, Any]], Awaitable[ToolResult]]
