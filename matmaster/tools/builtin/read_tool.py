"""ReadTool -- read remote file content via session.

Returns line-numbered output (cat -n format). Supports line_range
for partial reads. Marks the file in ReadTracker after successful read.

Replaces EditorTool._view for file reading.
"""

from __future__ import annotations

import posixpath
from typing import Any, ClassVar

from evomaster.agent.tools.builtin.editor import MAX_OUTPUT_SIZE, maybe_truncate

from .base import BuiltinTool
from .read_tracker import ReadTracker


class ReadTool(BuiltinTool):
    """Read file content with line numbers (cat -n semantics)."""

    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read the contents of a file. Output will be line-numbered (cat -n format). "
        "Use line_range to read specific lines (1-indexed). "
        "Always read a file before attempting to edit or overwrite it."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
            "line_range": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": (
                    "Optional [start, end] line range (1-indexed). "
                    "Use -1 for end to read to end of file."
                ),
            },
        },
        "required": ["file_path"],
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
        line_range: list[int] | None = arguments.get("line_range")

        # Check file exists
        if not session.is_file(file_path):
            return f"Error: {file_path} is not a file"

        content: str = session.read_file(file_path)

        # Mark as read in tracker
        if self._tracker is not None:
            self._tracker.mark_read(posixpath.normpath(file_path))

        # Handle line_range
        init_line = 1
        if line_range is not None:
            if (
                len(line_range) != 2
                or not all(isinstance(i, int) for i in line_range)
            ):
                return "Error: line_range must be a list of two integers."

            lines = content.rstrip("\n").split("\n")
            n_lines = len(lines)
            start, end = line_range

            if start < 1 or start > n_lines:
                return f"Error: start line {start} is out of range [1, {n_lines}]."

            if end != -1:
                if end < start:
                    return f"Error: end line {end} should be >= start line {start}."
                if end > n_lines:
                    return f"Error: end line {end} exceeds file length {n_lines}."

            if end == -1:
                content = "\n".join(lines[start - 1 :])
            else:
                content = "\n".join(lines[start - 1 : end])
            init_line = start

        return self._format_with_line_numbers(content, file_path, init_line)

    @staticmethod
    def _format_with_line_numbers(
        content: str, descriptor: str, init_line: int = 1
    ) -> str:
        """Format content with line numbers in cat -n style."""
        content = maybe_truncate(content, max_size=MAX_OUTPUT_SIZE)
        numbered = "\n".join(
            f"{i + init_line:6}\t{line}"
            for i, line in enumerate(content.split("\n"))
        )
        return (
            f"Here's the result of running `cat -n` on {descriptor}:\n"
            f"{numbered}"
        )
