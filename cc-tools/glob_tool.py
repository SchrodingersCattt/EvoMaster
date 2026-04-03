"""Glob tool -- CC-style file pattern matching.

Uses Python's pathlib.glob for local execution, session.exec_bash for remote.
Returns paths sorted by modification time (newest first).
"""

from __future__ import annotations

import os
import posixpath
from pathlib import Path
from typing import Any, ClassVar

from .base import BuiltinTool

MAX_RESULTS = 100


class GlobTool(BuiltinTool):
    """Fast file pattern matching sorted by modification time."""

    name: ClassVar[str] = "Glob"
    description: ClassVar[str] = (
        "Fast file pattern matching tool that works with any codebase size.\n\n"
        '- Supports glob patterns like "**/*.py" or "src/**/*.ts"\n'
        "- Returns matching file paths sorted by modification time\n"
        "- Use when you need to find files by name patterns\n"
        f"- Results capped at {MAX_RESULTS} files"
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against",
            },
            "path": {
                "type": "string",
                "description": (
                    "The directory to search in. Defaults to working directory. "
                    "Must be a valid directory path if provided."
                ),
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        pattern: str = arguments["pattern"]
        search_path: str = arguments.get("path", "") or ""

        if self._session is not None:
            return self._execute_remote(pattern, search_path)
        return self._execute_local(pattern, search_path)

    def _execute_local(self, pattern: str, search_path: str) -> str:
        base = Path(search_path) if search_path else (self._workdir or Path.cwd())
        if not base.is_dir():
            return f"Error: {base} is not a directory"

        try:
            matches = list(base.glob(pattern))
        except ValueError as e:
            return f"Error: invalid glob pattern: {e}"

        # Filter to files only, sort by mtime desc
        files = [f for f in matches if f.is_file()]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        truncated = len(files) > MAX_RESULTS
        files = files[:MAX_RESULTS]

        if not files:
            return f"No files matching pattern '{pattern}' in {base}"

        # Relativize paths
        try:
            lines = [str(f.relative_to(base)) for f in files]
        except ValueError:
            lines = [str(f) for f in files]

        result = "\n".join(lines)
        if truncated:
            result += f"\n[Results truncated at {MAX_RESULTS} files]"
        return result

    def _execute_remote(self, pattern: str, search_path: str) -> str:
        session = self._require_session()
        workdir = str(self._workdir) if self._workdir else "/workspace"

        if not search_path or search_path == ".":
            safe_path = workdir
        elif search_path.startswith("/"):
            normalized = posixpath.normpath(search_path)
            safe_path = normalized if normalized.startswith(workdir) else workdir
        else:
            joined = posixpath.join(workdir, search_path)
            normalized = posixpath.normpath(joined)
            safe_path = normalized if normalized.startswith(workdir) else workdir

        command = (
            f'find "{safe_path}" -type f -name "{pattern}" '
            f"2>/dev/null | head -{MAX_RESULTS}"
        )
        result = session.exec_bash(command=command, timeout=30)
        output = result.get("output", "") or result.get("stdout", "")

        if not output.strip():
            return f"No files matching pattern '{pattern}' in {safe_path}"
        return output
