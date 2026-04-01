"""EditTool -- str_replace editing via session.

Performs exact string replacement with unique-match enforcement.
Enforces Read-Before-Modify protocol (D-02) via ReadTracker.
No insert/undo_edit commands (D-01: str_replace only).
Editor helpers inlined (originally from evomaster).

Strip retry: if old_str not found verbatim but old_str.strip() matches
uniquely, the stripped version is used automatically.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any, ClassVar

from .base import BuiltinTool

# ---- Editor helpers (inlined from evomaster.agent.tools.builtin.editor) ----
SNIPPET_LINES = 4
MAX_OUTPUT_SIZE = 16000
_TEXT_FILE_TRUNCATED_NOTICE = (
    '<response clipped><NOTE>Due to the max output limit, only part of this file has been shown to you. '
    'You should retry this tool after you have searched inside the file with `grep -n` in order to find '
    'the line numbers of what you are looking for.</NOTE>'
)


def maybe_truncate(
    content: str,
    max_size: int = MAX_OUTPUT_SIZE,
    notice: str = _TEXT_FILE_TRUNCATED_NOTICE,
) -> str:
    """Truncate content in the middle if it exceeds max_size."""
    if len(content) <= max_size:
        return content
    half = max_size // 2
    return content[:half] + '\n' + notice + '\n' + content[-half:]
# ---- End Editor helpers ----
from .read_tracker import ReadTracker


class EditTool(BuiltinTool):
    """Edit a file using str_replace with unique-match enforcement."""

    name: ClassVar[str] = "edit_file"
    description: ClassVar[str] = (
        "Edit a file by replacing an exact string match (str_replace).\n\n"
        "Usage:\n"
        "- ALWAYS use edit_file for edits. NEVER use sed/awk via execute_bash.\n"
        "- old_str must match exactly one location. Include 3-5 lines of context.\n"
        "- You must read the file first using read_file."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit.",
            },
            "old_str": {
                "type": "string",
                "description": "The exact string to find and replace. Must be unique in the file.",
            },
            "new_str": {
                "type": "string",
                "description": "The replacement string.",
            },
        },
        "required": ["file_path", "old_str", "new_str"],
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
        old_str: str = arguments.get("old_str", "")
        new_str: str = arguments.get("new_str", "")

        # Read-Before-Modify check (D-02, D-03)
        if self._tracker is not None and not self._tracker.has_been_read(
            posixpath.normpath(file_path)
        ):
            return f"Error: file '{file_path}' must be read before modify"

        # No-op check
        if old_str == new_str:
            return (
                "Error: No replacement was performed. "
                "`old_str` and `new_str` must be different."
            )

        content: str = session.read_file(file_path)

        # Find matches
        pattern = re.escape(old_str)
        matches = list(re.finditer(pattern, content))

        # Strip fallback if no exact match
        if not matches:
            old_str_stripped = old_str.strip()
            new_str_stripped = new_str.strip()
            pattern = re.escape(old_str_stripped)
            matches = list(re.finditer(pattern, content))

            if matches:
                if old_str_stripped == new_str_stripped:
                    return (
                        "Error: No replacement was performed. "
                        "`old_str` and `new_str` must be different "
                        "(after stripping whitespace)."
                    )
                old_str = old_str_stripped
                new_str = new_str_stripped
            else:
                return (
                    f"No replacement was performed, old_str "
                    f"did not appear verbatim in {file_path}."
                )

        # Multiple matches
        if len(matches) > 1:
            line_numbers = sorted(
                {content.count("\n", 0, m.start()) + 1 for m in matches}
            )
            return (
                f"No replacement was performed. Multiple occurrences "
                f"of old_str in lines {line_numbers}. "
                f"Please ensure it is unique."
            )

        # Single match -- perform replacement
        match = matches[0]
        replacement_line = content.count("\n", 0, match.start()) + 1
        new_content = content[: match.start()] + new_str + content[match.end() :]

        session.write_file(file_path, new_content)

        # Build context snippet
        start_line = max(0, replacement_line - SNIPPET_LINES)
        end_line = replacement_line + SNIPPET_LINES + new_str.count("\n") + 1
        snippet = "\n".join(new_content.split("\n")[start_line : end_line + 1])
        snippet = maybe_truncate(snippet, max_size=MAX_OUTPUT_SIZE)

        # Format with line numbers
        numbered = "\n".join(
            f"{i + start_line + 1:6}\t{line}"
            for i, line in enumerate(snippet.split("\n"))
        )
        return (
            f"The file {file_path} has been edited. "
            f"Here's the result of running `cat -n` on a snippet of {file_path}:\n"
            f"{numbered}"
        )
