"""ToolCompiler -- compile Tool definitions into ToolInstance bindings."""

from __future__ import annotations

from matmaster.tools.tool_registry import Tool
from matmaster.types.tool_spec import (
    ResourceClaim,
    ToolBinding,
    ToolInstance,
    ToolSpec,
)
from matmaster.types.topology import RuntimeTopology, ToolPlane


class ToolCompiler:
    """Compile a Tool into ToolInstance using self-describing metadata."""

    def compile(
        self,
        tool: Tool,
        topology: RuntimeTopology,
        *,
        source: str = "unknown",
    ) -> ToolInstance:
        claims = tool.resource_claims
        plane = tool.plane

        if not isinstance(plane, ToolPlane):
            plane = ToolPlane(plane)

        # Relax claims for local stateless shell-backed read/search tools.
        if (
            topology.session_kind == "local"
            and topology.session_capabilities is not None
            and topology.session_capabilities.shell_persistence == "stateless"
            and tool.name in ("list_dir", "glob", "grep")
        ):
            claims = (ResourceClaim(resource="session", mode="shared_read"),)

        spec = ToolSpec(
            tool_name=tool.name,
            description=tool.description,
            args_schema=tool.json_schema,
            source=source,
            capabilities=tool.capabilities,
            effect_level=tool.effect_level,
            fast_path_eligible=tool.fast_path_eligible,
            max_result_chars=tool.max_result_chars,
            exposed_to_model=tool.exposed_to_model,
        )
        binding = ToolBinding(
            binding_key=f"{plane.value}:{tool.name}",
            plane=plane,
            resource_claims=claims,
            state_mode=tool.state_mode,
            stop_mode=tool.stop_mode,
        )

        if hasattr(tool, "execute_with_context"):
            tool_executor = tool.execute_with_context
        else:
            _execute = tool.execute

            async def tool_executor(args, exec_ctx):  # type: ignore[misc]
                return await _execute(args)

        validator = None
        if hasattr(tool, "validate_input") and callable(tool.validate_input):
            validator = tool.validate_input

        return ToolInstance(
            tool_spec=spec,
            tool_binding=binding,
            tool_executor=tool_executor,
            input_validator=validator,
        )
