"""matmaster/tools/builtin/write_tool.py

WriteTool — create/overwrite files via session.

CC Reference: tools/FileWriteTool/ (prompt.ts, FileWriteTool.ts)
CC name: Write
"""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from typing import Any, ClassVar

from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool


class WriteTool(BuiltinTool):
    """Write content to a file via session.

    CC name: Write (FileWriteTool)
    """

    name: ClassVar[str] = "Write"
    description: ClassVar[str] = "Write a file to the local filesystem."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "The absolute path to the file to write "
                    "(must be absolute, not relative)"
                ),
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
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

    def prompt(self, ctx=None) -> str:
        return (
            "Writes a file to the local filesystem.\n\n"
            "Usage:\n"
            "- This tool will overwrite the existing file if there is one at "
            "the provided path.\n"
            "- If this is an existing file, you MUST use the Read tool first "
            "to read the file's contents. This tool will fail if you did not "
            "read the file first.\n"
            "- Prefer the Edit tool for modifying existing files — it only sends "
            "the diff. Only use this tool to create new files or for complete rewrites."
        )

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        file_path = arguments.get("file_path", "")
        if not file_path:
            return ToolDecision(decision="deny", reason="file_path is required")
        if self._workdir is None:
            return ToolDecision(decision="deny", reason="workdir not set")

        try:
            resolved = PurePosixPath(posixpath.normpath(file_path))
            if not resolved.is_relative_to(self._workdir):
                return ToolDecision(
                    decision="deny",
                    reason=f"file_path '{file_path}' is outside workspace boundary",
                )
        except (TypeError, ValueError):
            return ToolDecision(decision="deny", reason=f"invalid file_path: '{file_path}'")

        if runner_state is not None and self._session is not None:
            normalized = posixpath.normpath(file_path)
            if self._session.path_exists(file_path):
                read_files = runner_state.get("read_files", set())
                if normalized not in read_files:
                    return ToolDecision(
                        decision="deny",
                        reason=f"File '{file_path}' must be read before overwrite",
                        guidance="Read the file first using Read.",
                    )
        return None

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()
        file_path: str = arguments.get("file_path", "")
        content: str = arguments.get("content", "")
        session.write_file(file_path, content)
        return f"File written successfully to: {file_path}"
