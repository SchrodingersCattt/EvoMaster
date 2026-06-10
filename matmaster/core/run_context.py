"""Agent run boundary context -- the environment + request composition.

The runtime boundary is modeled as two owned halves and a thin composition:

  * :class:`~matmaster.core.playground.ExecutionEnvironment` -- the physical
    execution substrate produced by ``Playground.prepare()``.
  * :class:`AgentRunRequest` -- the per-run runtime ingredients the service
    layer resolves (llm provider/config, turn input, user instructions,
    active skills, interaction bridge, runtime capability ports).
  * :class:`AgentRunContext` -- ``environment + request``, the single object
    Exp consumes. Exp reads ``ctx.environment.<physical>`` and
    ``ctx.request.<runtime>``; its method arity stays single-argument.

The physical / runtime boundary is a *type* boundary: the service assembles
one ``AgentRunRequest`` and composes it with the environment exactly once.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from matmaster.context.ports import UserInstructions
from matmaster.context.sources.turn_input import TurnInput
from matmaster.core.playground import ExecutionEnvironment
from matmaster.types.runtime_ports import AgentRunPorts

# Opaque per-event payload replayed to rebuild the Bohrium job registry.
BohriumRebuildEvent = dict[str, Any]


class AgentRunRequest(BaseModel):
    """Per-run runtime ingredients assembled by the service layer.

    Built once, near the end of request setup, from values the service
    resolves across its stages (model capability, the enriched turn input,
    user instructions, replayed skills, the interaction bridge, and the narrow
    runtime capability ports). Frozen: assembled once, never mutated.
    """

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    # llm_provider is intentionally ``Any`` so duck-typed mocks and partially
    # instantiated providers bypass Pydantic's strict isinstance check.
    llm_provider: Any = None
    llm_config: Any = None
    llm_model: str | None = None
    llm_model_profile: str | None = None
    llm_model_route: str | None = None
    supports_vision: bool = False
    vision_detail: Literal["low", "high", "auto"] | None = None
    context_limit: int | None = Field(default=None, gt=0)
    invocation_id: str | None = None
    interaction_bridge: Any = Field(default=None, repr=False, exclude=True)
    turn_input: TurnInput | None = None
    user_instructions: UserInstructions | None = None
    active_skills: frozenset[str] = Field(default_factory=frozenset)
    bohrium_rebuild_events: tuple[BohriumRebuildEvent, ...] = Field(
        default_factory=tuple
    )
    ports: AgentRunPorts = Field(
        default_factory=AgentRunPorts,
        repr=False,
        exclude=True,
    )


class AgentRunContext(BaseModel):
    """Composition of physical environment + runtime request.

    The single argument Exp.assemble / build_runtime / runtime_scope /
    run_stream consume.
    """

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    environment: ExecutionEnvironment
    request: AgentRunRequest = Field(default_factory=AgentRunRequest)
