"""ReadTool -- read remote file content via session.

Returns line-numbered output (cat -n format). Supports offset/limit
for partial reads. Conditional mark_read via ReadTracker:
- Full-read within limit: mark_read
- Full-read overlimit (error + preview): no mark_read
- Ranged read (offset/limit): mark_read only if not char-truncated
"""

from __future__ import annotations

import posixpath
from typing import Any, ClassVar

from .base import BuiltinTool
from .read_tracker import ReadTracker

MAX_READ_LINES = 2000
MAX_READ_CHARS = 200_000
PREVIEW_LINES = 50


class ReadTool(BuiltinTool):
    """Read file content with line numbers (cat -n semantics)."""

    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read file contents with line numbers (cat -n format).\n\n"
        "Usage:\n"
        "- ALWAYS use read_file to read files. NEVER use cat/head/tail via execute_bash.\n"
        "- Files up to 2000 lines are returned in full. Larger files return an error with preview.\n"
        "- Use offset and limit to read specific portions of large files.\n"
        "- Always read a file before attempting to edit or overwrite it."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": (
                    "Line number to start reading from (1-indexed). Defaults to 1."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Number of lines to read. "
                    "Defaults to reading to end of file (up to 2000 lines)."
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

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        file_path: str = arguments.get("file_path", "")
        offset: int | None = arguments.get("offset")
        limit: int | None = arguments.get("limit")

        # --- parameter validation ---
        if offset is not None and offset < 1:
            return "Error: offset must be >= 1."
        if limit is not None and limit < 1:
            return "Error: limit must be >= 1."

        # --- file check ---
        if not session.is_file(file_path):
            return f"Error: {file_path} is not a file"

        # --- read content ---
        content: str = session.read_file(file_path)
        lines = content.splitlines()
        total = len(lines)

        ranged = offset is not None or limit is not None

        if ranged:
            return self._ranged_read(file_path, lines, total, offset, limit)
        else:
            return self._full_read(file_path, lines, total)

    # ------------------------------------------------------------------
    # Full-read mode
    # ------------------------------------------------------------------

    def _full_read(self, file_path: str, lines: list[str], total: int) -> str:
        if total <= MAX_READ_LINES:
            output = self._format_lines(lines, file_path, init_line=1)
            truncated, result = self._apply_char_limit(output)
            if not truncated:
                self._mark(file_path)
            return result

        # Overlimit: error + preview (no mark_read regardless)
        preview = lines[:PREVIEW_LINES]
        preview_text = self._format_lines(preview, file_path, init_line=1)
        _, result = self._apply_char_limit(
            f"Error: file has {total} lines, exceeds read limit "
            f"({MAX_READ_LINES} lines).\n"
            f"Use offset and limit to read portions, e.g. "
            f"offset=1, limit={MAX_READ_LINES} for the first {MAX_READ_LINES} lines.\n\n"
            f"Preview (first {PREVIEW_LINES} lines):\n"
            f"{preview_text}"
        )
        return result

    # ------------------------------------------------------------------
    # Ranged-read mode
    # ------------------------------------------------------------------

    def _ranged_read(
        self,
        file_path: str,
        lines: list[str],
        total: int,
        offset: int | None,
        limit: int | None,
    ) -> str:
        start = offset if offset is not None else 1

        if start < 1 or start > total:
            return f"Error: offset {start} is out of range [1, {total}]."

        remaining = total - start + 1
        requested = limit if limit is not None else remaining
        count = min(requested, remaining, MAX_READ_LINES)
        end = start + count - 1

        selected = lines[start - 1 : end]
        output = self._format_lines(selected, file_path, init_line=start)

        # Truncation notice
        if count < requested:
            output += (
                f"\n[Note: showing {count} of {remaining} remaining lines, "
                f"capped at {MAX_READ_LINES}. Use offset={end + 1} to continue reading.]"
            )

        truncated, result = self._apply_char_limit(output)
        if not truncated:
            self._mark(file_path)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mark(self, file_path: str) -> None:
        if self._tracker is not None:
            self._tracker.mark_read(posixpath.normpath(file_path))

    @staticmethod
    def _format_lines(lines: list[str], descriptor: str, init_line: int = 1) -> str:
        """Format lines with line numbers in cat -n style."""
        numbered = "\n".join(
            f"{i + init_line:6}\t{line}" for i, line in enumerate(lines)
        )
        return f"Here's the result of running `cat -n` on {descriptor}:\n" f"{numbered}"

    @staticmethod
    def _apply_char_limit(output: str) -> tuple[bool, str]:
        """Truncate output if it exceeds MAX_READ_CHARS.

        Returns (truncated, output) so callers can decide whether to mark_read.
        """
        if len(output) <= MAX_READ_CHARS:
            return False, output
        return True, (
            output[:MAX_READ_CHARS]
            + "\n[Output truncated at "
            + str(MAX_READ_CHARS)
            + " chars. Use offset/limit for smaller ranges.]"
        )
