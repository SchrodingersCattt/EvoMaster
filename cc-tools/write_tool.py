"""Write tool -- CC-style file writer.

Creates or overwrites files. Requires Read before overwriting existing files.
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Any, ClassVar

from .base import BuiltinTool


class WriteTool(BuiltinTool):
    """Write content to a file, creating or overwriting."""

    name: ClassVar[str] = "Write"
    description: ClassVar[str] = (
        "Writes a file to the local filesystem.\n\n"
        "Usage:\n"
        "- Will overwrite existing files at the provided path.\n"
        "- If this is an existing file, you MUST Read it first.\n"
        "- Prefer Edit for modifying existing files (sends only the diff).\n"
        "- Only use Write for creating new files or complete rewrites."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write (must be absolute)",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        tracker: Any | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._tracker = tracker

    def _execute(self, arguments: dict[str, Any]) -> str:
        file_path: str = arguments.get("file_path", "")
        content: str = arguments.get("content", "")

        if not file_path:
            return "Error: file_path is required"

        path = Path(file_path)
        is_existing = path.exists() if self._session is None else False

        # Read-Before-Modify check for existing files
        if is_existing and self._tracker is not None:
            if not self._tracker.has_been_read(posixpath.normpath(file_path)):
                return f"Error: file '{file_path}' must be read before overwriting"

        if self._session is not None:
            session = self._require_session()
            # Check existence via session
            if session.path_exists(file_path):
                if self._tracker is not None and not self._tracker.has_been_read(
                    posixpath.normpath(file_path)
                ):
                    return f"Error: file '{file_path}' must be read before overwriting"
                action = "updated"
            else:
                action = "created"
            session.write_file(file_path, content)
        else:
            action = "updated" if is_existing else "created"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

        line_count = content.count("\n") + 1
        return f"File {action} successfully at {file_path} ({line_count} lines)"
