"""LocalSession -- lightweight local session for builtin tool execution.

Replaces evomaster.agent.session.local.LocalSession with a minimal
implementation satisfying the 5-method interface used by builtin tools:
exec_bash, read_file, write_file, path_exists, is_file.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from matmaster.types.cancellation import CancellationToken
from matmaster.types.topology import SessionCapabilities


class LocalSession:
    """Local session executing commands via subprocess.

    No connection lifecycle -- open()/close() are no-ops.
    Return format for exec_bash matches evomaster LocalSession exactly.
    """

    def __init__(
        self, workspace_path: Path | str, *, timeout: int = 300, encoding: str = "utf-8"
    ) -> None:
        self._workspace_path = Path(workspace_path)
        self._timeout = timeout
        self._encoding = encoding
        self._is_open: bool = False

    @property
    def is_open(self) -> bool:
        """Whether the session has been opened."""
        return self._is_open

    @property
    def capabilities(self) -> SessionCapabilities:
        """Local session capabilities for ToolRunner/CapabilityPolicy."""
        return SessionCapabilities(
            shell_persistence="stateless",
            shell_input=False,
            file_ops="native",
            upload_support=False,
            exec_cancel=True,
        )

    def open(self) -> None:
        """Mark session as open."""
        self._is_open = True

    def close(self) -> None:
        """Mark session as closed."""
        self._is_open = False

    def exec_bash(
        self,
        command: str,
        timeout: int | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> dict[str, Any]:
        """Execute a bash command via subprocess.

        Returns dict with: stdout, stderr, exit_code, working_dir, output.
        When ``cancel_token`` is cancelled during execution, the subprocess is killed
        and exit_code 130 is returned (matching evomaster BaseSession contract).
        """
        effective_timeout = timeout or self._timeout

        if cancel_token is not None:
            return self._exec_bash_with_token(command, effective_timeout, cancel_token)

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

    def _exec_bash_with_token(
        self,
        command: str,
        timeout: int,
        cancel_token: CancellationToken,
    ) -> dict[str, Any]:
        """Execute with cancel token callbacks -- kill subprocess when cancelled."""
        if cancel_token.is_cancelled:
            return {
                "stdout": "",
                "stderr": "Cancelled before command start.",
                "exit_code": 130,
                "working_dir": str(self._workspace_path),
                "output": "Cancelled before command start.",
            }

        proc = subprocess.Popen(
            ["bash", "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self._workspace_path),
            text=True,
            start_new_session=True,
        )

        def _kill_group() -> None:
            if proc.poll() is not None:
                return
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass

        cancel_token.on_cancel(_kill_group)

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group()
            proc.communicate()
            return {
                "stdout": "",
                "stderr": f"Command timeout after {timeout}s",
                "exit_code": 124,
                "working_dir": str(self._workspace_path),
                "output": f"Command timeout after {timeout}s",
            }

        if cancel_token.is_cancelled:
            output = stdout
            if output:
                output = output.rstrip("\n") + "\nCommand cancelled."
            else:
                output = "Command cancelled."
            return {
                "stdout": stdout,
                "stderr": "Command cancelled.",
                "exit_code": 130,
                "working_dir": str(self._workspace_path),
                "output": output,
            }

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
