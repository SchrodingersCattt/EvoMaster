"""GlobTool -- search file paths by glob pattern via session.

Uses session.exec_bash to run 'find' with -name filter within the workdir.
Enforces workdir boundary (path traversal blocked) and truncates output
via head -200 to prevent token explosion.
"""

from __future__ import annotations

import posixpath
from typing import Any, ClassVar

from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool


class GlobTool(BuiltinTool):
    """Search file paths by glob pattern within the workspace."""

    name: ClassVar[str] = 'glob'
    description: ClassVar[str] = (
        'Search for files matching a glob pattern within the workspace.\n\n'
        'Usage:\n'
        '- ALWAYS use glob for file search. NEVER use find/ls via execute_bash.\n'
        "- Supports patterns like '*.py', 'test_*.txt', '**/*.yaml'.\n"
        '- Returns matching file paths, up to 200 results.'
    )
    json_schema: ClassVar[dict[str, Any]] = {
        'type': 'object',
        'properties': {
            'pattern': {
                'type': 'string',
                'description': "Glob pattern to match file names (e.g. '*.py', 'test_*.txt').",
            },
            'path': {
                'type': 'string',
                'description': 'Directory to search in. Defaults to workspace root.',
            },
        },
        'required': ['pattern'],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource='session', mode='exclusive'),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({'workspace.search.path'})
    effect_level: ClassVar[str] = 'none'
    fast_path_eligible: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 8000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL

    def _resolve_safe_path(self, user_path: str) -> str:
        """Resolve user-provided path to a safe absolute path within workdir.

        Path traversal attempts (../, absolute paths outside workdir) are
        silently resolved back to workdir root.
        """
        workdir = str(self._workdir) if self._workdir else '/workspace'

        if not user_path or user_path == '.':
            return workdir

        # Absolute path: normalize and check containment
        if user_path.startswith('/'):
            normalized = posixpath.normpath(user_path)
            if normalized.startswith(workdir):
                return normalized
            # Traversal detected -- fall back to workdir
            return workdir

        # Relative path: join with workdir, normalize, check containment
        joined = posixpath.join(workdir, user_path)
        normalized = posixpath.normpath(joined)
        if normalized.startswith(workdir):
            return normalized
        # Traversal detected -- fall back to workdir
        return workdir

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        pattern: str = arguments['pattern']
        path: str = arguments.get('path', '.') or '.'
        safe_path = self._resolve_safe_path(path)

        command = (
            f'find "{safe_path}" -type f -name "{pattern}" 2>/dev/null | head -200'
        )
        result = session.exec_bash(
            command=command,
            timeout=30,
            stop_event=self._stop_event_for_exec(),
        )

        output = result.get('output', '') or result.get('stdout', '')

        if not output.strip():
            return f"No files matching pattern '{pattern}' found in {safe_path}"

        return output
