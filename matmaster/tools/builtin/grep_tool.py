"""GrepTool -- search file content by regex via session.

Uses session.exec_bash to run 'grep -rn' within the workdir.
Enforces workdir boundary (path traversal blocked) and truncates output
via head -200 to prevent token explosion. Supports optional --include
filter for file type restriction.
"""

from __future__ import annotations

import posixpath
from typing import Any, ClassVar

from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool


class GrepTool(BuiltinTool):
    """Search file content by regex pattern within the workspace."""

    name: ClassVar[str] = 'grep'
    description: ClassVar[str] = (
        'Search file content for a regex pattern within the workspace.\n\n'
        'Usage:\n'
        '- ALWAYS use grep for content search. NEVER use grep/rg via execute_bash.\n'
        "- Supports regex syntax (e.g. 'import os', 'def foo.*:').\n"
        "- Use include to filter by file type (e.g. '*.py')."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        'type': 'object',
        'properties': {
            'pattern': {
                'type': 'string',
                'description': 'Regex pattern to search for in file content.',
            },
            'path': {
                'type': 'string',
                'description': 'Directory to search in. Defaults to workspace root.',
            },
            'include': {
                'type': 'string',
                'description': "File glob filter (e.g. '*.py') to restrict search scope.",
            },
        },
        'required': ['pattern'],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource='session', mode='exclusive'),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({'workspace.search.content'})
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
        include: str = arguments.get('include', '') or ''
        safe_path = self._resolve_safe_path(path)

        include_flag = f' --include="{include}"' if include else ''
        command = (
            f'grep -rn{include_flag} "{pattern}" "{safe_path}" 2>/dev/null | head -200'
        )
        result = session.exec_bash(
            command=command,
            timeout=30,
            cancel_token=self._cancel_token_for_exec(),
        )

        output = result.get('output', '') or result.get('stdout', '')

        if not output.strip():
            return f"No matches for pattern '{pattern}' in {safe_path}"

        return output
