"""LocalSession -- lightweight local session for builtin tool execution.

Replaces evomaster.agent.session.local.LocalSession with a minimal
implementation satisfying the 5-method interface used by builtin tools:
exec_bash, read_file, write_file, path_exists, is_file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class LocalSession:
    """Local session executing commands via subprocess.

    No connection lifecycle -- open()/close() are no-ops.
    Return format for exec_bash matches evomaster LocalSession exactly.
    """

    def __init__(self, workspace_path: Path, *, timeout: int = 300) -> None:
        self._workspace_path = Path(workspace_path)
        self._timeout = timeout

    def open(self) -> None:
        """No-op for local sessions."""

    def close(self) -> None:
        """No-op for local sessions."""

    def exec_bash(
        self,
        command: str,
        timeout: int | None = None,
        is_input: bool = False,
    ) -> dict[str, Any]:
        """Execute a bash command via subprocess.

        Returns dict with: stdout, stderr, exit_code, working_dir, output.
        """
        if is_input:
            return {
                "stdout": "",
                "stderr": "Interactive input is not supported in local session.",
                "exit_code": 1,
                "working_dir": str(self._workspace_path),
                "output": "Interactive input is not supported in local session.",
            }

        effective_timeout = timeout or self._timeout
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                cwd=str(self._workspace_path),
                timeout=effective_timeout,
            )
            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.returncode,
                "working_dir": str(self._workspace_path),
                "output": proc.stdout or proc.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Command timeout after {effective_timeout}s",
                "exit_code": 124,
                "working_dir": str(self._workspace_path),
                "output": f"Command timeout after {effective_timeout}s",
            }

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        """Read file content."""
        return Path(path).read_text(encoding=encoding)

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Write content to file, creating parent directories."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding=encoding)

    def path_exists(self, path: str) -> bool:
        """Check if path exists."""
        return Path(path).exists()

    def is_file(self, path: str) -> bool:
        """Check if path is a regular file."""
        return Path(path).is_file()
