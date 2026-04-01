"""LocalSession -- lightweight local session for builtin tool execution.

Replaces evomaster.agent.session.local.LocalSession with a minimal
implementation satisfying the 5-method interface used by builtin tools:
exec_bash, read_file, write_file, path_exists, is_file.
"""

from __future__ import annotations

import subprocess
import threading
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
        stop_event: threading.Event | Any | None = None,
    ) -> dict[str, Any]:
        """Execute a bash command via subprocess.

        Returns dict with: stdout, stderr, exit_code, working_dir, output.
        When ``stop_event`` is set during execution, the subprocess is killed
        and exit_code 130 is returned (matching evomaster BaseSession contract).
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

        if stop_event is not None:
            return self._exec_bash_with_stop(command, effective_timeout, stop_event)

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

    def _exec_bash_with_stop(
        self,
        command: str,
        timeout: int,
        stop_event: threading.Event,
    ) -> dict[str, Any]:
        """Execute with stop_event polling -- kill subprocess when stop is requested."""
        proc = subprocess.Popen(
            ["bash", "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self._workspace_path),
            text=True,
        )
        poll_interval = 0.1
        elapsed = 0.0
        while proc.poll() is None:
            if stop_event.is_set():
                proc.kill()
                proc.wait()
                return {
                    "stdout": "",
                    "stderr": "Cancelled by stop request",
                    "exit_code": 130,
                    "working_dir": str(self._workspace_path),
                    "output": "Cancelled by stop request",
                }
            stop_event.wait(poll_interval)
            elapsed += poll_interval
            if elapsed >= timeout:
                proc.kill()
                proc.wait()
                return {
                    "stdout": "",
                    "stderr": f"Command timeout after {timeout}s",
                    "exit_code": 124,
                    "working_dir": str(self._workspace_path),
                    "output": f"Command timeout after {timeout}s",
                }

        stdout = proc.stdout.read() if proc.stdout else ""
        stderr = proc.stderr.read() if proc.stderr else ""
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "working_dir": str(self._workspace_path),
            "output": stdout or stderr,
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
