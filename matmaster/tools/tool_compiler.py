"""ToolCompiler -- compile Tool definitions into ToolInstance bindings."""

from __future__ import annotations

from matmaster.tools.tool_registry import Tool
from matmaster.types.tool_spec import (
    ResourceClaim,
    ToolBinding,
    ToolExecutionContext,
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
        claims = tuple(getattr(tool, "resource_claims", ()))
        capabilities = frozenset(getattr(tool, "capabilities", frozenset()))
        effect_level = getattr(tool, "effect_level", "local_mutation")
        fast_path = getattr(tool, "fast_path_eligible", False)
        max_result_chars = getattr(tool, "max_result_chars", 0)
        exposed_to_model = getattr(tool, "exposed_to_model", True)
        plane = getattr(tool, "plane", ToolPlane.CONTROL_PLANE)
        state_mode = getattr(tool, "state_mode", "stateless")
        stop_mode = getattr(tool, "stop_mode", "cancellable")

        if not isinstance(plane, ToolPlane):
            plane = ToolPlane(plane)

        # Keep the local+stateless relaxation for shell-backed read/search tools.
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
            capabilities=capabilities,
            effect_level=effect_level,
            fast_path_eligible=fast_path,
            max_result_chars=max_result_chars,
            exposed_to_model=exposed_to_model,
        )
        binding = ToolBinding(
            binding_key=f"{plane.value}:{tool.name}",
            plane=plane,
            resource_claims=claims,
            state_mode=state_mode,
            stop_mode=stop_mode,
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
