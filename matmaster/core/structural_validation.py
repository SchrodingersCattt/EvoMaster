"""Layer A: Stateless structural validation for tool calls.

StructuralValidation performs three sequential checks before a tool
can be dispatched:

1. **args_schema** -- validates tool_args against the ToolSpec's JSON Schema
   using jsonschema.validate(). Empty schema means no validation.

2. **plane activation** -- verifies the tool's ToolPlane is in the
   RuntimeTopology.active_planes set.

3. **session capabilities** -- if RuntimeTopology.session_capabilities is
   present, checks that the session supports required capabilities
   (e.g., shell_input for shell.execute tools).

Each check returns a deny ToolDecision on failure. If all pass,
returns allow.

This layer is stateless: it does not track execution history or
resource state. Those are handled by RunStateGuard (Layer B) and
ToolScheduler respectively.
"""

from __future__ import annotations

from typing import Any

import jsonschema

from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_spec import ToolInstance
from matmaster.types.topology import RuntimeTopology, ToolPlane


class StructuralValidation:
    """Layer A: stateless argument / topology validation.

    Designed as a simple class (no __init__ state) so it can be
    instantiated once and reused across turns.
    """

    def validate(
        self,
        runtime_topology: RuntimeTopology,
        tool_instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> ToolDecision:
        """Run all structural checks in order.

        Returns the first deny encountered, or allow if all pass.
        """
        # 1. args_schema validation
        schema = tool_instance.tool_spec.args_schema
        if schema:
            try:
                jsonschema.validate(instance=tool_args, schema=schema)
            except jsonschema.ValidationError as exc:
                return ToolDecision(
                    decision="deny",
                    reason=f"Invalid arguments: {exc.message}",
                )

        # 2. plane activation check
        plane = tool_instance.tool_binding.plane
        if plane not in runtime_topology.active_planes:
            return ToolDecision(
                decision="deny",
                reason=(
                    f"Plane '{plane.value}' is not active "
                    f"in current topology"
                ),
            )

        # 3. session capabilities matching
        caps = runtime_topology.session_capabilities
        if caps is not None:
            spec = tool_instance.tool_spec
            binding_plane = tool_instance.tool_binding.plane

            # shell.execute requires shell_input support
            if (
                binding_plane == ToolPlane.SESSION_SHELL
                and "shell.execute" in spec.capabilities
                and not caps.shell_input
            ):
                return ToolDecision(
                    decision="deny",
                    reason=(
                        "Session does not support required capability: "
                        "shell.execute"
                    ),
                )

        return ToolDecision(decision="allow")
