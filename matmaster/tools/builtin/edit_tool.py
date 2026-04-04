"""matmaster/tools/builtin/edit_tool.py

EditTool — str_replace editing via session.

CC Reference: tools/FileEditTool/ (constants.ts, types.ts, prompt.ts)
CC name: Edit
"""

from __future__ import annotations

import posixpath
import re
from typing import Any, ClassVar

from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool

SNIPPET_LINES = 4
MAX_OUTPUT_SIZE = 16_000


class EditTool(BuiltinTool):
    """Edit a file using str_replace with unique-match enforcement.

    CC name: Edit (FileEditTool)
    """

    name: ClassVar[str] = "Edit"
    description: ClassVar[str] = "Performs exact string replacements in files."
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
                "description": (
                    "The text to replace it with " "(must be different from old_string)"
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "Replace all occurrences of old_string (default false)"
                ),
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.write"})
    effect_level: ClassVar[str] = "local_mutation"
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_FS

    def prompt(self, ctx=None) -> str:
        return (
            "Performs exact string replacements in files.\n\n"
            "Usage:\n"
            "- You must use your `Read` tool at least once in the conversation "
            "before editing. This tool will error if you attempt an edit without "
            "reading the file.\n"
            "- ALWAYS prefer editing existing files in the codebase. NEVER write "
            "new files unless explicitly required.\n"
            "- The edit will FAIL if `old_string` is not unique in the file. "
            "Either provide a larger string with more surrounding context to make "
            "it unique or use `replace_all` to change every instance of `old_string`.\n"
            "- Use `replace_all` for replacing and renaming strings across the file."
        )

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        old_str = arguments.get("old_string", "")
        new_str = arguments.get("new_string", "")
        if not old_str:
            return ToolDecision(decision="deny", reason="old_string must not be empty")
        if old_str == new_str:
            return ToolDecision(
                decision="deny",
                reason="old_string and new_string are identical, no edit needed",
            )
        if runner_state is not None:
            read_files = runner_state.get("read_files", set())
            path = posixpath.normpath(arguments.get("file_path", ""))
            if path and path not in read_files:
                return ToolDecision(
                    decision="deny",
                    reason=f"File '{path}' must be read before editing",
                    guidance="Read the file first using Read.",
                )
        return None

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        file_path: str = arguments.get("file_path", "")
        old_str: str = arguments.get("old_string", "")
        new_str: str = arguments.get("new_string", "")
        replace_all: bool = arguments.get("replace_all", False)

        if old_str == new_str:
            return "Error: No replacement was performed. `old_string` and `new_string` must be different."

        content: str = session.read_file(file_path)
        pattern = re.escape(old_str)
        matches = list(re.finditer(pattern, content))

        if not matches:
            return (
                f"No replacement was performed, old_string "
                f"did not appear verbatim in {file_path}."
            )

        if replace_all:
            new_content = content.replace(old_str, new_str)
            count = len(matches)
            session.write_file(file_path, new_content)
            return f"Replaced {count} occurrence(s) in {file_path}."

        if len(matches) > 1:
            line_numbers = sorted(
                {content.count("\n", 0, m.start()) + 1 for m in matches}
            )
            return (
                f"No replacement was performed. Multiple occurrences "
                f"of old_string in lines {line_numbers}. "
                f"Please ensure it is unique or use replace_all=true."
            )

        match = matches[0]
        replacement_line = content.count("\n", 0, match.start()) + 1
        new_content = content[: match.start()] + new_str + content[match.end() :]

        session.write_file(file_path, new_content)

        start_line = max(0, replacement_line - SNIPPET_LINES)
        end_line = replacement_line + SNIPPET_LINES + new_str.count("\n") + 1
        snippet = "\n".join(new_content.split("\n")[start_line : end_line + 1])
        if len(snippet) > MAX_OUTPUT_SIZE:
            half = MAX_OUTPUT_SIZE // 2
            snippet = snippet[:half] + "\n...\n" + snippet[-half:]

        numbered = "\n".join(
            f"{i + start_line + 1:6}\t{line}"
            for i, line in enumerate(snippet.split("\n"))
        )
        return (
            f"The file {file_path} has been edited. "
            f"Here's the result of running `cat -n` on a snippet of {file_path}:\n"
            f"{numbered}"
        )
