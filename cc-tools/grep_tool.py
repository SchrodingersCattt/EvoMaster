"""Grep tool -- CC-style ripgrep wrapper with rich output modes.

Major upgrade from matmaster grep:
- output_mode: content / files_with_matches / count
- Context lines: -A / -B / -C
- multiline matching
- head_limit / offset pagination
- type-based file filtering
- Case insensitive search
"""

from __future__ import annotations

import posixpath
import subprocess
import shutil
from pathlib import Path
from typing import Any, ClassVar

from .base import BuiltinTool

DEFAULT_HEAD_LIMIT = 250


class GrepTool(BuiltinTool):
    """Powerful search tool built on ripgrep with multiple output modes."""

    name: ClassVar[str] = "Grep"
    description: ClassVar[str] = (
        "A powerful search tool built on ripgrep.\n\n"
        "Usage:\n"
        '- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")\n'
        '- Filter files with glob parameter (e.g., "*.py") or type parameter (e.g., "py")\n'
        '- Output modes: "content" shows matching lines, '
        '"files_with_matches" shows only file paths (default), '
        '"count" shows match counts\n'
        "- Use -A/-B/-C for context lines around matches (content mode only)\n"
        "- Use multiline=true for patterns spanning multiple lines\n"
        "- head_limit caps results (default 250, pass 0 for unlimited)"
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Defaults to working directory.",
            },
            "glob": {
                "type": "string",
                "description": 'Glob pattern to filter files (e.g. "*.py", "*.{ts,tsx}")',
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": (
                    "Output mode. Defaults to 'files_with_matches'. "
                    "'content' shows matching lines, 'count' shows match counts."
                ),
            },
            "-B": {
                "type": "number",
                "description": "Lines to show before each match (content mode only)",
            },
            "-A": {
                "type": "number",
                "description": "Lines to show after each match (content mode only)",
            },
            "-C": {
                "type": "number",
                "description": "Alias for context (lines before and after)",
            },
            "context": {
                "type": "number",
                "description": "Lines to show before and after each match (content mode only)",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers (content mode only). Defaults to true.",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search",
            },
            "type": {
                "type": "string",
                "description": 'File type filter (e.g., "py", "js", "rust")',
            },
            "head_limit": {
                "type": "number",
                "description": (
                    "Limit output to first N entries. "
                    "Defaults to 250. Pass 0 for unlimited."
                ),
            },
            "offset": {
                "type": "number",
                "description": "Skip first N entries before applying head_limit. Defaults to 0.",
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode for cross-line patterns. Default: false.",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        pattern: str = arguments["pattern"]
        search_path: str = arguments.get("path", "") or ""
        glob_filter: str = arguments.get("glob", "") or ""
        output_mode: str = arguments.get("output_mode", "files_with_matches")
        before: int | None = arguments.get("-B")
        after: int | None = arguments.get("-A")
        context_c: int | None = arguments.get("-C")
        context: int | None = arguments.get("context") or context_c
        show_numbers: bool = arguments.get("-n", True)
        case_insensitive: bool = arguments.get("-i", False)
        file_type: str = arguments.get("type", "") or ""
        head_limit: int | None = arguments.get("head_limit")
        offset_val: int = arguments.get("offset", 0)
        multiline: bool = arguments.get("multiline", False)

        if head_limit is None:
            head_limit = DEFAULT_HEAD_LIMIT

        # Determine search path
        if self._session is not None:
            return self._execute_remote(
                pattern, search_path, glob_filter, output_mode,
                before, after, context, show_numbers, case_insensitive,
                file_type, head_limit, offset_val, multiline,
            )
        return self._execute_local(
            pattern, search_path, glob_filter, output_mode,
            before, after, context, show_numbers, case_insensitive,
            file_type, head_limit, offset_val, multiline,
        )

    def _execute_local(
        self,
        pattern: str,
        search_path: str,
        glob_filter: str,
        output_mode: str,
        before: int | None,
        after: int | None,
        context: int | None,
        show_numbers: bool,
        case_insensitive: bool,
        file_type: str,
        head_limit: int,
        offset_val: int,
        multiline: bool,
    ) -> str:
        rg = shutil.which("rg")
        if rg is None:
            return self._fallback_grep(pattern, search_path, glob_filter, head_limit)

        base = Path(search_path) if search_path else (self._workdir or Path.cwd())
        args = [rg]

        # Output mode
        if output_mode == "files_with_matches":
            args.append("--files-with-matches")
        elif output_mode == "count":
            args.append("--count")
        # content mode: default rg behavior

        # Context lines (content mode only)
        if output_mode == "content":
            if context is not None:
                args.extend(["-C", str(context)])
            else:
                if before is not None:
                    args.extend(["-B", str(before)])
                if after is not None:
                    args.extend(["-A", str(after)])
            if show_numbers:
                args.append("-n")

        if case_insensitive:
            args.append("-i")

        if multiline:
            args.extend(["-U", "--multiline-dotall"])

        if glob_filter:
            args.extend(["--glob", glob_filter])

        if file_type:
            args.extend(["--type", file_type])

        # Exclude VCS directories
        args.extend(["--glob", "!.git"])

        args.append(pattern)
        args.append(str(base))

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return "Error: search timed out after 60s"
        except FileNotFoundError:
            return self._fallback_grep(pattern, str(base), glob_filter, head_limit)

        output = proc.stdout
        if not output.strip():
            return f"No matches for pattern '{pattern}'"

        # Apply offset and head_limit
        lines = output.strip().split("\n")
        if offset_val > 0:
            lines = lines[offset_val:]
        if head_limit > 0:
            applied_limit = len(lines) > head_limit
            lines = lines[:head_limit]
            if applied_limit:
                lines.append(f"[Results limited to {head_limit} entries]")

        return "\n".join(lines)

    def _execute_remote(
        self,
        pattern: str,
        search_path: str,
        glob_filter: str,
        output_mode: str,
        before: int | None,
        after: int | None,
        context: int | None,
        show_numbers: bool,
        case_insensitive: bool,
        file_type: str,
        head_limit: int,
        offset_val: int,
        multiline: bool,
    ) -> str:
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

        # Build rg command (prefer rg, fallback to grep)
        parts = ["rg"]

        if output_mode == "files_with_matches":
            parts.append("-l")
        elif output_mode == "count":
            parts.append("-c")

        if output_mode == "content":
            if context is not None:
                parts.extend(["-C", str(context)])
            else:
                if before is not None:
                    parts.extend(["-B", str(before)])
                if after is not None:
                    parts.extend(["-A", str(after)])
            if show_numbers:
                parts.append("-n")

        if case_insensitive:
            parts.append("-i")
        if multiline:
            parts.extend(["-U", "--multiline-dotall"])
        if glob_filter:
            parts.extend(["--glob", f'"{glob_filter}"'])
        if file_type:
            parts.extend(["--type", file_type])

        parts.append("--glob '!.git'")
        parts.append(f'"{pattern}"')
        parts.append(f'"{safe_path}"')

        limit = head_limit if head_limit > 0 else 10000
        command = " ".join(parts) + f" 2>/dev/null | tail -n +{offset_val + 1} | head -{limit}"

        result = session.exec_bash(command=command, timeout=60)
        output = result.get("output", "") or result.get("stdout", "")

        if not output.strip():
            return f"No matches for pattern '{pattern}' in {safe_path}"
        return output

    def _fallback_grep(
        self, pattern: str, path: str, include: str, limit: int
    ) -> str:
        """Fallback to system grep when rg is unavailable."""
        include_flag = f' --include="{include}"' if include else ""
        cap = limit if limit > 0 else 200
        command = f'grep -rn{include_flag} "{pattern}" "{path}" 2>/dev/null | head -{cap}'

        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired:
            return "Error: search timed out"

        if not proc.stdout.strip():
            return f"No matches for pattern '{pattern}'"
        return proc.stdout
