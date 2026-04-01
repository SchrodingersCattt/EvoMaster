"""ListDirTool -- list directory contents via session.

Uses session.exec_bash to run 'ls -la' on the target path.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .base import BuiltinTool


class ListDirTool(BuiltinTool):
    """List directory contents via session shell."""

    name: ClassVar[str] = 'list_dir'
    description: ClassVar[str] = (
        'List files and directories at the specified path (ls -la format).\n\n'
        'Usage:\n'
        '- Use for quick directory overview before navigating the workspace.\n'
        '- Returns file permissions, sizes, and timestamps.'
    )
    json_schema: ClassVar[dict[str, Any]] = {
        'type': 'object',
        'properties': {
            'path': {
                'type': 'string',
                'description': 'Directory path to list contents of. Defaults to current directory.',
            },
        },
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        path = arguments.get('path', '.') or '.'

        result = session.exec_bash(
            command=f'ls -la "{path}"',
            timeout=10,
            stop_event=self._stop_event_for_exec(),
        )

        output = result.get('output', '')
        exit_code = result.get('exit_code', -1)

        if exit_code != 0:
            return f"Error listing directory '{path}': {output}"

        return output
