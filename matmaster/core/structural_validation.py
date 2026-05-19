"""Layer A: Stateless structural validation for tool calls.

StructuralValidation performs three sequential checks before a tool
can be dispatched:

1. **args_schema** -- validates tool_args against the ToolSpec's JSON Schema
   using jsonschema.validate(). Empty schema means no validation.

2. **plane activation** -- verifies the tool's ToolPlane is in the
   RuntimeTopology.active_planes set.

3. **workspace path normalization** -- for workspace-bound tools, resolves
   file path arguments against RuntimeTopology.workspace_root and rejects
   paths outside the workspace boundary.

Each check returns a deny ToolDecision on failure. If all pass,
returns allow.

This layer is stateless: it does not track execution history or
resource state. Those are handled by CapabilityPolicy and ToolScheduler
respectively.
"""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from typing import Any

import jsonschema

from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_spec import ToolInstance
from matmaster.types.topology import PathAccessOperation, RuntimeTopology, ToolPlane

_PATH_KEYS = ("file_path", "path")


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
                reason=(f"Plane '{plane.value}' is not active " f"in current topology"),
            )

        # 3. Path normalization for workspace-bound tools only.
        # shell_input is reserved for future interactive shell features;
        # stateless one-shot shell execution remains valid with shell_input=False.
        _WORKSPACE_PLANES = {ToolPlane.SESSION_FS, ToolPlane.SESSION_SHELL}
        modified_args: dict[str, Any] | None = None
        if plane in _WORKSPACE_PLANES:
            try:
                modified_args = self._normalize_path_args(
                    runtime_topology,
                    tool_instance,
                    tool_args,
                )
            except ValueError as exc:
                return ToolDecision(
                    decision="deny",
                    reason=f"Path {exc}",
                )

        return ToolDecision(decision="allow", modified_args=modified_args)

    @staticmethod
    def _operation_for_tool(tool_instance: ToolInstance) -> PathAccessOperation:
        """Infer path access operation from the tool's declared capabilities."""
        capabilities = tool_instance.tool_spec.capabilities
        if "workspace.write" in capabilities:
            return "write"
        if any(cap.startswith("workspace.search") for cap in capabilities):
            return "search"
        return "read"

    @staticmethod
    def _allowed_roots_for_operation(
        runtime_topology: RuntimeTopology,
        operation: PathAccessOperation,
    ) -> list[PurePosixPath]:
        roots: list[PurePosixPath] = [
            PurePosixPath(posixpath.normpath(runtime_topology.workspace_root))
        ]
        seen = {str(roots[0])}
        for root in runtime_topology.path_access_roots:
            if operation not in root.permissions:
                continue
            normalized = posixpath.normpath(root.root)
            if not normalized or normalized == "." or normalized in seen:
                continue
            roots.append(PurePosixPath(normalized))
            seen.add(normalized)
        return roots

    @staticmethod
    def _normalize_access_path(
        runtime_topology: RuntimeTopology,
        raw_path: str,
        operation: PathAccessOperation,
    ) -> str:
        """Resolve and validate a path against workspace plus access roots.

        Relative paths are still scoped strictly to ``workspace_root``. Extra
        access roots only apply to absolute paths that the runtime has already
        exposed to the model, such as SkillTool-rendered directories.
        """
        workspace_root = PurePosixPath(
            posixpath.normpath(runtime_topology.workspace_root)
        )
        if raw_path.startswith("/"):
            normalized = PurePosixPath(posixpath.normpath(raw_path))
            allowed_roots = StructuralValidation._allowed_roots_for_operation(
                runtime_topology,
                operation,
            )
        else:
            normalized = PurePosixPath(
                posixpath.normpath(
                    posixpath.join(runtime_topology.workspace_root, raw_path)
                )
            )
            allowed_roots = [workspace_root]

        if not any(normalized.is_relative_to(root) for root in allowed_roots):
            raise ValueError("outside workspace boundary")
        return str(normalized)

    @staticmethod
    def _normalize_path_args(
        runtime_topology: RuntimeTopology,
        tool_instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Normalize file_path / path keys in tool_args.

        Returns a new dict with normalized paths, or None if no path keys
        are present or no normalization was needed. Raises ValueError if
        any path escapes the workspace boundary.
        """
        updated: dict[str, Any] = {}
        operation = StructuralValidation._operation_for_tool(tool_instance)
        for key in _PATH_KEYS:
            raw = tool_args.get(key)
            if raw is not None and isinstance(raw, str):
                resolved = StructuralValidation._normalize_access_path(
                    runtime_topology,
                    raw,
                    operation,
                )
                if resolved != raw:
                    updated[key] = resolved
        if not updated:
            return None
        return {**tool_args, **updated}
