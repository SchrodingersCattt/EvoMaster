"""Edit tool -- CC-style str_replace with replace_all support.

Differences from matmaster edit_file:
- Adds `replace_all` parameter for global replacement
- CC naming: old_string / new_string (vs old_str / new_str)
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Any, ClassVar

from .base import BuiltinTool

SNIPPET_LINES = 4
MAX_OUTPUT_SIZE = 16_000


def _maybe_truncate(content: str, max_size: int = MAX_OUTPUT_SIZE) -> str:
    if len(content) <= max_size:
        return content
    half = max_size // 2
    return (
        content[:half]
        + "\n<response clipped>\n"
        + content[-half:]
    )


class EditTool(BuiltinTool):
    """Exact string replacement in files with optional replace_all."""

    name: ClassVar[str] = "Edit"
    description: ClassVar[str] = (
        "Performs exact string replacements in files.\n\n"
        "Usage:\n"
        "- You must Read the file at least once before editing.\n"
        "- Preserve exact indentation from Read output (after the line number prefix).\n"
        "- The edit will FAIL if old_string is not unique in the file. "
        "Provide more surrounding context to make it unique, "
        "or use replace_all to change every instance.\n"
        "- Use replace_all for renaming variables across the file."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with (must be different from old_string)",
            },
            "replace_all": {
                "type": "boolean",
                "default": False,
                "description": "Replace all occurrences of old_string (default false)",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
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
        old_string: str = arguments.get("old_string", "")
        new_string: str = arguments.get("new_string", "")
        replace_all: bool = arguments.get("replace_all", False)

        # Read-Before-Modify check
        if self._tracker is not None and not self._tracker.has_been_read(
            posixpath.normpath(file_path)
        ):
            return f"Error: file '{file_path}' must be read before editing"

        if old_string == new_string:
            return "Error: old_string and new_string must be different."

        # Read content
        if self._session is not None:
            session = self._require_session()
            content: str = session.read_file(file_path)
        else:
            path = Path(file_path)
            if not path.is_file():
                return f"Error: {file_path} is not a file"
            content = path.read_text(errors="replace")

        # Find matches
        pattern = re.escape(old_string)
        matches = list(re.finditer(pattern, content))

        # Strip fallback if no exact match
        if not matches:
            old_stripped = old_string.strip()
            new_stripped = new_string.strip()
            pattern = re.escape(old_stripped)
            matches = list(re.finditer(pattern, content))
            if matches:
                if old_stripped == new_stripped:
                    return "Error: old_string and new_string are the same after stripping."
                old_string = old_stripped
                new_string = new_stripped
            else:
                return f"No replacement performed: old_string not found in {file_path}."

        if replace_all:
            # Replace all occurrences
            new_content = content.replace(old_string, new_string)
            count = len(matches)
            replacement_line = content.count("\n", 0, matches[0].start()) + 1
        else:
            # Single match required
            if len(matches) > 1:
                line_numbers = sorted(
                    {content.count("\n", 0, m.start()) + 1 for m in matches}
                )
                return (
                    f"No replacement performed. Multiple occurrences "
                    f"of old_string found at lines {line_numbers}. "
                    f"Provide more context to make it unique, or use replace_all=true."
                )
            match = matches[0]
            replacement_line = content.count("\n", 0, match.start()) + 1
            new_content = content[: match.start()] + new_string + content[match.end() :]
            count = 1

        # Write back
        if self._session is not None:
            session.write_file(file_path, new_content)
        else:
            Path(file_path).write_text(new_content)

        # Build context snippet
        start_line = max(0, replacement_line - SNIPPET_LINES)
        end_line = replacement_line + SNIPPET_LINES + new_string.count("\n") + 1
        snippet_lines = new_content.split("\n")[start_line : end_line + 1]
        snippet = _maybe_truncate("\n".join(snippet_lines))

        numbered = "\n".join(
            f"{i + start_line + 1:6}\t{line}"
            for i, line in enumerate(snippet.split("\n"))
        )

        msg = f"The file {file_path} has been edited. "
        if replace_all and count > 1:
            msg += f"({count} replacements made.) "
        msg += (
            f"Here's the result of running `cat -n` on a snippet:\n{numbered}"
        )
        return msg
