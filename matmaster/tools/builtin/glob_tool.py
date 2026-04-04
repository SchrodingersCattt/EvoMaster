"""matmaster/tools/builtin/glob_tool.py

GlobTool — search file paths by glob pattern via session.

CC Reference: tools/GlobTool/ (prompt.ts, GlobTool.ts)
CC name: Glob
"""

from __future__ import annotations

from typing import Any, ClassVar

from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from ._path_safety import resolve_safe_path, shell_escape
from .base import BuiltinTool

MAX_GLOB_RESULTS = 200

# find-based excludes for VCS/build dirs — portable across macOS/Linux
VCS_EXCLUDES = (
    r'-not -path "*/.git/*" '
    r'-not -path "*/node_modules/*" '
    r'-not -path "*/__pycache__/*" '
    r'-not -path "*/.svn/*"'
)


class GlobTool(BuiltinTool):
    """Search file paths by glob pattern within the workspace.

    CC name: Glob (GlobTool)

    Uses ``find`` with ``-path`` / ``-name`` matching for portable glob
    expansion (no bash globstar dependency). Results sorted by modification
    time (newest first).
    """

    name: ClassVar[str] = "Glob"
    description: ClassVar[str] = (
        "- Fast file pattern matching tool that works with any codebase size\n"
        "- Supports glob patterns like \"**/*.js\" or \"src/**/*.ts\"\n"
        "- Returns matching file paths sorted by modification time\n"
        "- Use this tool when you need to find files by name patterns\n"
        "- When you are doing an open ended search that may require multiple "
        "rounds of globbing and grepping, use the Agent tool instead"
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
                    "The directory to search in. If not specified, the current "
                    "working directory will be used."
                ),
            },
        },
        "required": ["pattern"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="session", mode="shared_read"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.search.path"})
    effect_level: ClassVar[str] = "none"
    fast_path_eligible: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 8_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        pattern: str = arguments.get("pattern", "")
        if not pattern:
            return "Error: pattern is required and must not be empty."

        path: str = arguments.get("path", "") or ""
        workdir = str(self._workdir) if self._workdir else "/workspace"
        safe_path = resolve_safe_path(path, workdir)

        command = self._build_find_command(pattern, safe_path)
        result = session.exec_bash(
            command=command,
            timeout=30,
            cancel_token=self._cancel_token_for_exec(),
        )

        output = result.get("output", "") or result.get("stdout", "")

        if not output.strip():
            return f"No files matching pattern '{pattern}' found in {safe_path}"

        lines = output.strip().splitlines()
        if len(lines) >= MAX_GLOB_RESULTS:
            output += (
                f"\n(Results truncated at {MAX_GLOB_RESULTS}. "
                "Consider using a more specific path or pattern.)"
            )

        return output

    @staticmethod
    def _build_find_command(pattern: str, safe_path: str) -> str:
        """Convert a glob pattern into a portable ``find`` command.

        Translates common glob idioms into ``find -path`` / ``-name``
        expressions that work on both macOS (BSD find) and Linux (GNU find)
        without requiring bash globstar.

        Handles three categories:
        * Recursive (contains ``**``): ``**/*.py``, ``src/**/*.ts``
        * Path pattern (contains ``/`` but no ``**``): ``src/*.py``
        * Simple name pattern (no ``/``): ``*.py``
        """
        escaped_path = shell_escape(safe_path)

        if "**" in pattern:
            # --- recursive pattern ---
            # Split on the first '**' to get prefix dir and suffix.
            # e.g. "src/**/*.ts" -> prefix="src", rest="*.ts"
            # e.g. "**/*.py"    -> prefix="", rest="*.py"
            before, _, after = pattern.partition("**")
            # Strip trailing / from prefix and leading / from suffix
            prefix_dir = before.rstrip("/")
            suffix = after.lstrip("/")

            if prefix_dir:
                # Search from subdirectory: cd into safe_path/prefix_dir
                search_root = f"{escaped_path}/{shell_escape(prefix_dir)}"
            else:
                search_root = escaped_path

            if suffix and "/" not in suffix:
                # Simple filename after **: use -name for clarity
                find_expr = f"-name {shell_escape(suffix)}"
            elif suffix:
                # Deeper suffix like "bar/*.ts": use -path with wildcard prefix
                find_expr = f"-path {shell_escape('./*/' + suffix)}"
            else:
                # Bare "**" or "src/**" — match all files
                find_expr = ""

        elif "/" in pattern:
            # --- path pattern without ** ---
            # e.g. "src/*.py" -> search from safe_path, -path './src/*.py'
            search_root = escaped_path
            find_expr = f"-path {shell_escape('./' + pattern)}"

        else:
            # --- simple name pattern: *.py ---
            # Non-recursive: only top-level files
            search_root = escaped_path
            find_expr = f"-maxdepth 1 -name {shell_escape(pattern)}"

        parts = [
            f"find {search_root} {find_expr} -type f".rstrip(),
            VCS_EXCLUDES,
            "2>/dev/null",
        ]
        find_cmd = " ".join(parts)

        # Sort by mtime (newest first) and limit results.
        # xargs ls -1td is portable; -r (--no-run-if-empty) is a GNU
        # extension but harmless on macOS where xargs already skips empty.
        return (
            f"{find_cmd} | "
            f"head -{MAX_GLOB_RESULTS} | "
            f"xargs ls -1td 2>/dev/null | "
            f"head -{MAX_GLOB_RESULTS}"
        )
