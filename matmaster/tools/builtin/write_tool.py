"""WriteTool -- create/overwrite files via session.

Read-Before-Modify for existing files is enforced via validate_input()
(input_validator path in ToolInstance). This preserves the path_exists
session capability check that Guard layer should not depend on.
New files (path_exists=False) are written without restriction.

When tracker is None, protocol enforcement is disabled (backward compat).
"""

from __future__ import annotations

import posixpath
from typing import Any, ClassVar

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

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        tracker: ReadTracker | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._tracker = tracker

    async def validate_input(self, arguments: dict[str, Any]) -> Any:
        """Read-Before-Modify check for existing files (input_validator path).

        Returns ToolDecision(deny) if existing file not yet read.
        Returns None if check passes (no objection).

        Uses session.path_exists to distinguish new vs existing files,
        which is why this check lives in validate_input (needs session)
        rather than in Guard layer.
        """
        from matmaster.types.tool_decision import ToolDecision

        file_path = arguments.get("file_path", "")
        if not file_path:
            return None

        if self._tracker is None:
            return None

        if self._session is None:
            return None

        normalized = posixpath.normpath(file_path)
        if self._session.path_exists(file_path) and not self._tracker.has_been_read(
            normalized
        ):
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
