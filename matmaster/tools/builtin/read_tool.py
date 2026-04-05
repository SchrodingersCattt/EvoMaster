"""matmaster/tools/builtin/read_tool.py

ReadTool — read file content via session with line numbers (cat -n).

CC Reference: tools/FileReadTool/FileReadTool.ts + prompt.ts
"""

from __future__ import annotations

import asyncio
import posixpath
from typing import Any, ClassVar

from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool

MAX_READ_LINES = 2000
MAX_READ_CHARS = 200_000
PREVIEW_LINES = 50


class ReadTool(BuiltinTool):
    """Read file content with line numbers (cat -n semantics).

    CC name: Read (FileReadTool)
    """

    name: ClassVar[str] = "Read"
    description: ClassVar[str] = "Read a file from the local filesystem."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": ("The absolute path to the file to read"),
            },
            "offset": {
                "type": "integer",
                "description": (
                    "The line number to start reading from (0-indexed). "
                    "Only provide if the file is too large to read at once."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "The number of lines to read. Only provide if the "
                    "file is too large to read at once."
                ),
            },
        },
        "required": ["file_path"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="shared_read"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.read"})
    effect_level: ClassVar[str] = "none"
    fast_path_eligible: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 12_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_FS

    def prompt(self, ctx=None) -> str:
        return (
            "Reads a file from the local filesystem. You can access any "
            "file directly by using this tool.\n"
            "Assume this tool is able to read all files on the machine. "
            "If the User provides a path to a file assume that path is valid.\n\n"
            "Usage:\n"
            "- The file_path parameter must be an absolute path, not a relative path\n"
            f"- By default, it reads up to {MAX_READ_LINES} lines starting from "
            "the beginning of the file\n"
            "- When you already know which part of the file you need, only read "
            "that part. This can be important for larger files.\n"
            "- Results are returned using cat -n format, with line numbers starting at 1\n"
            "- This tool can only read files, not directories. To read a directory, "
            "use an ls command via the Bash tool.\n"
            "- If you read a file that exists but has empty contents you will "
            "receive a system reminder warning in place of file contents."
        )

    # -- Core execution --

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self._execute_internal(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> ToolResult:
        try:
            result = await asyncio.to_thread(self._execute_internal, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return ToolResult(status="error", content=f"Error: {e}")

        if exec_ctx is not None and exec_ctx.runner_state is not None:
            if result.meta.get("mark_read"):
                path = posixpath.normpath(arguments.get("file_path", ""))
                read_files = set(exec_ctx.runner_state.get("read_files", set()))
                read_files.add(path)
                exec_ctx.runner_state.set("read_files", read_files)

        return result

    def _execute_internal(self, arguments: dict[str, Any]) -> ToolResult:
        session = self._require_session()

        file_path: str = arguments.get("file_path", "")
        offset: int | None = arguments.get("offset")
        limit: int | None = arguments.get("limit")

        if not session.is_file(file_path):
            return ToolResult(
                status="error",
                content=f"Error: {file_path} is not a file or does not exist",
            )

        content: str = session.read_file(file_path)
        lines = content.splitlines()
        total = len(lines)

        ranged = offset is not None or limit is not None

        if ranged:
            return self._ranged_read(file_path, lines, total, offset, limit)
        else:
            return self._full_read(file_path, lines, total)

    # -- Full-read mode --

    def _full_read(self, file_path: str, lines: list[str], total: int) -> ToolResult:
        if total == 0:
            return ToolResult(
                content=(
                    f"<system-reminder>Warning: the file {file_path} exists "
                    "but the contents are empty.</system-reminder>"
                ),
                meta={"mark_read": True},
            )

        if total <= MAX_READ_LINES:
            output = self._format_lines(lines, file_path, init_line=1)
            _truncated, result = self._apply_char_limit(output)
            return ToolResult(content=result, meta={"mark_read": True})

        preview = lines[:PREVIEW_LINES]
        preview_text = self._format_lines(preview, file_path, init_line=1)
        _truncated, result = self._apply_char_limit(
            f"Error: file has {total} lines, exceeds read limit "
            f"({MAX_READ_LINES} lines).\n"
            f"Use offset and limit to read portions, e.g. "
            f"offset=0, limit={MAX_READ_LINES} for the first {MAX_READ_LINES} lines.\n\n"
            f"Preview (first {PREVIEW_LINES} lines):\n"
            f"{preview_text}"
        )
        return ToolResult(status="error", content=result)

    # -- Ranged-read mode --

    def _ranged_read(
        self,
        file_path: str,
        lines: list[str],
        total: int,
        offset: int | None,
        limit: int | None,
    ) -> ToolResult:
        start = offset if offset is not None else 0

        if start < 0 or start >= total:
            return ToolResult(
                status="error",
                content=f"Error: offset {start} is out of range [0, {total - 1}].",
            )

        remaining = total - start
        requested = limit if limit is not None else remaining
        count = min(requested, remaining, MAX_READ_LINES)
        end = start + count

        selected = lines[start:end]
        output = self._format_lines(selected, file_path, init_line=start + 1)

        if count < requested:
            output += (
                f"\n[Note: showing {count} of {remaining} remaining lines, "
                f"capped at {MAX_READ_LINES}. Use offset={end} to continue reading.]"
            )

        _truncated, result = self._apply_char_limit(output)
        return ToolResult(content=result, meta={"mark_read": True})

    # -- Helpers --

    @staticmethod
    def _format_lines(lines: list[str], descriptor: str, init_line: int = 1) -> str:
        numbered = "\n".join(
            f"{i + init_line:6}\t{line}" for i, line in enumerate(lines)
        )
        return f"Here's the result of running `cat -n` on {descriptor}:\n{numbered}"

    @staticmethod
    def _apply_char_limit(output: str) -> tuple[bool, str]:
        if len(output) <= MAX_READ_CHARS:
            return False, output
        return True, (
            output[:MAX_READ_CHARS]
            + "\n[Output truncated at "
            + str(MAX_READ_CHARS)
            + " chars. Use offset/limit for smaller ranges.]"
        )
