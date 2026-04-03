"""WriteTool -- create/overwrite files via session.

Read-Before-Modify for existing files is enforced via validate_input()
(input_validator path in ToolInstance). This preserves the path_exists
session capability check that Guard layer should not depend on.
New files (path_exists=False) are written without restriction.

When runner_state is None, read-before-modify enforcement is disabled
for backward compatibility during the migration.
"""

from __future__ import annotations

import posixpath
from typing import Any, ClassVar

from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool
from .read_tracker import ReadTracker


class WriteTool(BuiltinTool):
    """Write content to a file. Read-Before-Modify via validate_input for existing files."""

    name: ClassVar[str] = "write_file"
    description: ClassVar[str] = (
        "Write content to a file, creating parent directories as needed.\n\n"
        "Usage:\n"
        "- ALWAYS use write_file to create files. NEVER use echo/heredoc via execute_bash.\n"
        "- If the file exists, you MUST read it first using read_file.\n"
        "- Always provide the complete intended file content."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "The complete content to write to the file.",
            },
        },
        "required": ["file_path", "content"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.write"})
    effect_level: ClassVar[str] = "local_mutation"
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_FS

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        tracker: ReadTracker | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        # Temporary compatibility: accept tracker until Exp stops passing it.
        self._compat_tracker = tracker

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        from pathlib import PurePosixPath

        file_path = arguments.get("file_path", "")
        if not file_path:
            return ToolDecision(decision="deny", reason="file_path is required")
        if self._workdir is None:
            return ToolDecision(decision="deny", reason="workdir not set, cannot validate path")
        # Parent-child containment via PurePosixPath.is_relative_to (not string prefix)
        try:
            resolved = PurePosixPath(posixpath.normpath(file_path))
            if not resolved.is_relative_to(self._workdir):
                return ToolDecision(
                    decision="deny",
                    reason=f"file_path '{file_path}' is outside workspace boundary",
                )
        except (TypeError, ValueError):
            return ToolDecision(decision="deny", reason=f"invalid file_path: '{file_path}'")

        # Read-before-modify check for existing files
        if runner_state is not None and self._session is not None:
            normalized = posixpath.normpath(file_path)
            if self._session.path_exists(file_path):
                read_files = runner_state.get("read_files", set())
                if normalized not in read_files:
                    return ToolDecision(
                        decision="deny",
                        reason=f"File '{file_path}' must be read before overwrite",
                        guidance="Read the file first using read_file.",
                    )
        return None

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        file_path: str = arguments.get("file_path", "")
        content: str = arguments.get("content", "")

        session.write_file(file_path, content)
        return f"File written successfully to: {file_path}"
