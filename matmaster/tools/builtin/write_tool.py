"""WriteTool -- create/overwrite files via session.

Enforces Read-Before-Modify protocol (D-02): existing files must be
read (tracked by ReadTracker) before they can be overwritten.
New files (path_exists=False) are written without restriction.

When tracker is None, protocol enforcement is disabled (backward compat).
"""

from __future__ import annotations

import posixpath
from typing import Any, ClassVar

from .base import BuiltinTool
from .read_tracker import ReadTracker


class WriteTool(BuiltinTool):
    """Write content to a file. Enforces Read-Before-Modify for existing files."""

    name: ClassVar[str] = "write_file"
    description: ClassVar[str] = (
        "Write content to a file. If the file exists, you MUST read it first "
        "using read_file. New files can be created directly. "
        "Always provide the complete file content."
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

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        file_path: str = arguments.get("file_path", "")
        content: str = arguments.get("content", "")

        # Read-Before-Modify check (D-02, D-03)
        if (
            self._tracker is not None
            and session.path_exists(file_path)
            and not self._tracker.has_been_read(posixpath.normpath(file_path))
        ):
            return f"Error: file '{file_path}' must be read before modify"

        session.write_file(file_path, content)
        return f"File written successfully to: {file_path}"
