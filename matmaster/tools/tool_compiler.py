"""ToolCompiler -- compile Tool definitions into ToolInstance bindings."""

from __future__ import annotations

from matmaster.tools.tool_registry import Tool
from matmaster.types.tool_spec import ResourceClaim, ToolBinding, ToolInstance, ToolSpec
from matmaster.types.topology import RuntimeTopology, ToolPlane

BUILTIN_CLAIMS: dict[str, tuple[ResourceClaim, ...]] = {
    "execute_bash": (ResourceClaim(resource="session", mode="exclusive"),),
    "list_dir": (ResourceClaim(resource="session", mode="exclusive"),),
    "glob": (ResourceClaim(resource="session", mode="exclusive"),),
    "grep": (ResourceClaim(resource="session", mode="exclusive"),),
    "read_file": (ResourceClaim(resource="workspace", mode="shared_read"),),
    "write_file": (ResourceClaim(resource="workspace", mode="exclusive"),),
    "edit_file": (ResourceClaim(resource="workspace", mode="exclusive"),),
    "task_create": (ResourceClaim(resource="task-store", mode="exclusive"),),
    "task_get": (ResourceClaim(resource="task-store", mode="shared_read"),),
    "task_list": (ResourceClaim(resource="task-store", mode="shared_read"),),
    "task_update": (ResourceClaim(resource="task-store", mode="exclusive"),),
    "task_complete": (ResourceClaim(resource="task-store", mode="exclusive"),),
    "mm_web_search": (ResourceClaim(resource="web", mode="counted", max_concurrent=3),),
    "web_fetch": (ResourceClaim(resource="web", mode="counted", max_concurrent=3),),
    "spawn": (ResourceClaim(resource="spawn", mode="counted", max_concurrent=2),),
    "monitor_job": (
        ResourceClaim(resource="workspace", mode="exclusive"),
        ResourceClaim(resource="artifact-sync", mode="exclusive"),
    ),
}

BUILTIN_META: dict[str, tuple[ToolPlane, str, bool, int]] = {
    "execute_bash": (ToolPlane.SESSION_SHELL, "local_mutation", False, 12000),
    "list_dir": (ToolPlane.SESSION_SHELL, "none", True, 8000),
    "glob": (ToolPlane.SESSION_SHELL, "none", True, 8000),
    "grep": (ToolPlane.SESSION_SHELL, "none", True, 8000),
    "read_file": (ToolPlane.SESSION_FS, "none", True, 12000),
    "write_file": (ToolPlane.SESSION_FS, "local_mutation", False, 0),
    "edit_file": (ToolPlane.SESSION_FS, "local_mutation", False, 0),
    "task_create": (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
    "task_get": (ToolPlane.CONTROL_PLANE, "none", True, 0),
    "task_list": (ToolPlane.CONTROL_PLANE, "none", True, 0),
    "task_update": (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
    "task_complete": (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
    "mm_web_search": (ToolPlane.EXTERNAL_SERVICE, "external_effect", False, 0),
    "web_fetch": (ToolPlane.EXTERNAL_SERVICE, "external_effect", False, 16000),
    "spawn": (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
    "monitor_job": (ToolPlane.SESSION_FS, "external_effect", False, 0),
}

BUILTIN_STOP_MODES: dict[str, tuple[str, str]] = {
    # tool_name: (state_mode, stop_mode)
    "execute_bash": ("stateless", "cancellable"),
    "read_file": ("stateless", "cancellable"),
    "write_file": ("stateless", "cancellable"),
    "edit_file": ("stateless", "cancellable"),
    "list_dir": ("stateless", "cancellable"),
    "glob": ("stateless", "cancellable"),
    "grep": ("stateless", "cancellable"),
    "task_create": ("stateless", "cancellable"),
    "task_get": ("stateless", "cancellable"),
    "task_list": ("stateless", "cancellable"),
    "task_update": ("stateless", "cancellable"),
    "task_complete": ("stateless", "cancellable"),
    "mm_web_search": ("stateless", "best_effort"),
    "web_fetch": ("stateless", "best_effort"),
    "spawn": ("persistent", "non_cancellable"),
    "monitor_job": ("persistent", "best_effort"),
}


class ToolCompiler:
    """Compile a Tool plus topology metadata into a ToolInstance."""

    def compile(
        self,
        tool: Tool,
        topology: RuntimeTopology,
        *,
        source: str = "unknown",
    ) -> ToolInstance:
        """Compile a tool into its bound runtime representation.

        The current builtin rules are topology-independent, but the topology is
        part of the API so future compilers can specialize bindings by session.
        """
        claims = BUILTIN_CLAIMS.get(tool.name, ())

        # Topology-dependent binding relaxation (spec 8.2)
        if (
            topology.session_kind == "local"
            and topology.session_capabilities is not None
            and topology.session_capabilities.shell_persistence == "stateless"
            and tool.name in ("list_dir", "glob", "grep")
        ):
            claims = (ResourceClaim(resource="session", mode="shared_read"),)
        plane, effect_level, fast_path, max_result_chars = BUILTIN_META.get(
            tool.name,
            (ToolPlane.CONTROL_PLANE, "local_mutation", False, 0),
        )
        spec = ToolSpec(
            tool_name=tool.name,
            description=tool.description,
            args_schema=tool.json_schema,
            source=source,
            effect_level=effect_level,
            fast_path_eligible=fast_path,
            max_result_chars=max_result_chars,
        )
        state_mode, stop_mode = BUILTIN_STOP_MODES.get(
            tool.name, ("stateless", "cancellable")
        )
        binding = ToolBinding(
            binding_key=f"{plane.value}:{tool.name}",
            plane=plane,
            resource_claims=claims,
            state_mode=state_mode,
            stop_mode=stop_mode,
        )
        validator = None
        if hasattr(tool, "validate_input") and callable(tool.validate_input):
            validator = tool.validate_input

        return ToolInstance(
            tool_spec=spec,
            tool_binding=binding,
            tool_executor=tool.execute,
            input_validator=validator,
        )
