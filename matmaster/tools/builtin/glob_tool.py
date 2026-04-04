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

# find-based excludes for VCS/build dirs
VCS_EXCLUDES = (
    '-not -path "*/.git/*" '
    '-not -path "*/node_modules/*" '
    '-not -path "*/__pycache__/*" '
    '-not -path "*/.svn/*"'
)


class GlobTool(BuiltinTool):
    """Search file paths by glob pattern within the workspace.

    CC name: Glob (GlobTool)

    Uses bash globstar (shopt -s globstar) for ** patterns, falls back to
    find -path for simple patterns. Results sorted by modification time.
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

        # Use bash globstar for ** support, sort by mtime (newest first),
        # exclude VCS dirs via grep -v, limit results
        command = (
            f"cd {shell_escape(safe_path)} && "
            f"shopt -s globstar nullglob && "
            f"printf '%s\\n' {shell_escape(pattern)} 2>/dev/null | "
            f"grep -v -E '/(\\.(git|svn)|node_modules|__pycache__)/' | "
            f"head -{MAX_GLOB_RESULTS} | "
            f"xargs -r ls -1t 2>/dev/null | "
            f"head -{MAX_GLOB_RESULTS}"
        )
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
