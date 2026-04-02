"""Layer C: Capability policy evaluation for tool calls.

CapabilityPolicy is a @runtime_checkable Protocol that evaluates
whether a tool call is permitted based on effect_level constraints
and fine-grained capability matching.

DefaultCapabilityPolicy implements Phase 1 policy:
1. effect_level="external_write" requires EXTERNAL_SERVICE plane active
2. Capability matching against SessionCapabilities (artifact.download,
   shell.execute, etc.)

This layer complements StructuralValidation (Layer A):
- Layer A checks topology-level enablement (is the plane active?)
- Layer C checks policy-level constraints (does the session support
  the specific capability the tool needs?)

Per D-07: Phase 1 does not migrate tool-internal safety checks.
Those remain inside the tools themselves until future phases.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_spec import ToolInstance
from matmaster.types.topology import RuntimeTopology, ToolPlane


@runtime_checkable
class CapabilityPolicy(Protocol):
    """Protocol for tool capability policy evaluation.

    Implementations evaluate whether a tool call should be allowed
    based on effect level, capabilities, and session state.
    """

    def evaluate(
        self,
        runtime_topology: RuntimeTopology,
        tool_instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> ToolDecision: ...


class DefaultCapabilityPolicy:
    """Phase 1 CapabilityPolicy: effect_level + capability matching.

    Per D-07: Phase 1 only handles effect_level constraints and
    plane/capability matching. Tool-internal safety checks are not
    migrated in this phase.
    """

    def evaluate(
        self,
        runtime_topology: RuntimeTopology,
        tool_instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> ToolDecision:
        """Evaluate tool call against capability policy.

        Returns deny with guidance on policy violation, allow otherwise.
        """
        spec = tool_instance.tool_spec

        # 1. effect_level constraint
        if spec.effect_level == "external_write":
            if ToolPlane.EXTERNAL_SERVICE not in runtime_topology.active_planes:
                return ToolDecision(
                    decision="deny",
                    reason=(
                        "External effect tools are not allowed "
                        "in current topology"
                    ),
                    guidance=(
                        "This tool makes external service calls. "
                        "Ensure the session topology permits "
                        "external access."
                    ),
                )

        # 2. Fine-grained capability matching
        caps = runtime_topology.session_capabilities
        if caps is not None:
            capabilities = spec.capabilities

            if "artifact.download" in capabilities and not caps.upload_support:
                return ToolDecision(
                    decision="deny",
                    reason=(
                        "Session does not support required capability: "
                        "artifact.download"
                    ),
                    guidance=(
                        "Session does not support artifact "
                        "upload/download"
                    ),
                )

            if "shell.execute" in capabilities and not caps.shell_input:
                return ToolDecision(
                    decision="deny",
                    reason=(
                        "Session does not support required capability: "
                        "shell.execute"
                    ),
                    guidance=(
                        "Session does not support interactive "
                        "shell input"
                    ),
                )

        return ToolDecision(decision="allow")
