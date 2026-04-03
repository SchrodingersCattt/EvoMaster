"""Bash tool -- CC-style command execution with run_in_background support.

Differences from matmaster execute_bash:
- Adds `run_in_background` for async background task execution
- Adds `description` for human-readable command labeling
- Timeout in milliseconds (CC convention) vs seconds (MM convention)
- Adds `dangerouslyDisableSandbox` flag (no-op in Python, for schema compat)
"""

from __future__ import annotations

import asyncio
import re
import sys
import uuid
from typing import Any, ClassVar

from .base import BuiltinTool, ToolResult

# ---- Safety checks (from matmaster) ----
_BLOCKED_FIRST_TOKENS = frozenset({"env", "set", "printenv"})

_DANGEROUS_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"rm\s+-rf\s+/",
        r"rm\s+-rf\s+\.\.",
        r":\s*\(\s*\)\s*\{[^}]*\|\s*:.*\}",
        r"mkfs\.?\s",
        r"dd\s+if=.*of=/dev",
        r"\bchmod\s+[0-7]{3,4}\s+/",
        r">\s*/dev/sd",
    ]
]

_PROXY_CLEAR_PREFIX = (
    "export http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= "
    "NO_PROXY= no_proxy= ftp_proxy= FTP_PROXY=; "
    "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY "
    "NO_PROXY no_proxy ftp_proxy FTP_PROXY WGETRC 2>/dev/null; "
)


def _is_dangerous(command: str) -> tuple[bool, str]:
    raw = command.strip()
    if not raw:
        return False, ""
    first = raw.split(None, 1)[0].lower()
    if first in _BLOCKED_FIRST_TOKENS:
        return True, f"'{first}' is blocked for security."
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(command):
            return True, "Command contains potentially destructive operations."
    return False, ""


# ---- Background task store ----
_background_tasks: dict[str, asyncio.Task[str]] = {}


class BashTool(BuiltinTool):
    """Execute bash commands with background execution support."""

    name: ClassVar[str] = "Bash"
    description: ClassVar[str] = (
        "Executes a bash command and returns its output.\n\n"
        "Working directory persists between commands, shell state does not.\n\n"
        "IMPORTANT: Avoid using this for file operations. Use dedicated tools:\n"
        "- File search: Glob (not find/ls)\n"
        "- Content search: Grep (not grep/rg)\n"
        "- Read files: Read (not cat/head/tail)\n"
        "- Edit files: Edit (not sed/awk)\n"
        "- Write files: Write (not echo/cat heredoc)\n\n"
        "Use run_in_background=true for long-running commands."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute",
            },
            "timeout": {
                "type": "number",
                "description": "Optional timeout in milliseconds (max 600000)",
            },
            "run_in_background": {
                "type": "boolean",
                "description": (
                    "Run command in background. "
                    "You will be notified when it completes."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does. "
                    'E.g., "Install dependencies", "Run test suite"'
                ),
            },
            "dangerouslyDisableSandbox": {
                "type": "boolean",
                "description": "Override sandbox mode (use with caution).",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Override to support run_in_background and native async."""
        run_bg = arguments.get("run_in_background", False)

        if run_bg:
            return await self._execute_background(arguments)

        try:
            return await self._execute_foreground(arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    async def _execute_foreground(self, arguments: dict[str, Any]) -> str:
        command: str = arguments.get("command", "").strip()
        timeout_ms = arguments.get("timeout")
        timeout = timeout_ms / 1000.0 if timeout_ms and timeout_ms > 0 else None

        dangerous, reason = _is_dangerous(command)
        if dangerous:
            return f"Blocked: {reason}"

        if command and sys.platform != "win32":
            command = _PROXY_CLEAR_PREFIX + command

        wd = str(self._workdir) if self._workdir else None

        # Try native async for local execution
        if self._session is None or self._is_local_session():
            return await self._run_subprocess(command, wd, timeout)

        # Fall back to session.exec_bash via thread
        return await asyncio.to_thread(self._run_session, command, timeout)

    async def _execute_background(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Spawn a background task and return immediately with task ID."""
        task_id = str(uuid.uuid4())[:8]

        async def _bg_runner() -> str:
            return await self._execute_foreground(arguments)

        task = asyncio.create_task(_bg_runner())
        _background_tasks[task_id] = task

        desc = arguments.get("description", arguments.get("command", "")[:60])
        return ToolResult.ok(
            f"Background task started: {desc}",
            task_id=task_id,
            status="running",
        )

    async def _run_subprocess(
        self, command: str, cwd: str | None, timeout: float | None
    ) -> str:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            obs = f"Command timed out after {timeout:.0f}s"
            if cwd:
                obs += f"\n[CWD: {cwd}]"
            obs += "\n[Exit code: 124]"
            return obs

        stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        output = stdout
        if stderr:
            output = (output + stderr) if output else stderr

        obs = output
        if cwd:
            obs += f"\n[CWD: {cwd}]"
        if proc.returncode is not None:
            obs += f"\n[Exit code: {proc.returncode}]"
        return obs

    def _run_session(self, command: str, timeout: float | None) -> str:
        session = self._require_session()
        result = session.exec_bash(
            command=command,
            timeout=int(timeout) if timeout else None,
        )
        output = result.get("output", "") or result.get("stdout", "")
        exit_code = result.get("exit_code", -1)
        working_dir = result.get("working_dir", "")
        obs = output
        if working_dir:
            obs += f"\n[CWD: {working_dir}]"
        if exit_code != -1:
            obs += f"\n[Exit code: {exit_code}]"
        return obs

    def _is_local_session(self) -> bool:
        try:
            from matmaster.sessions.local import LocalSession
            return isinstance(self._session, LocalSession)
        except ImportError:
            return False

    def _execute(self, arguments: dict[str, Any]) -> str:
        """Sync fallback (not normally used -- execute() handles async)."""
        return self._run_session(
            arguments.get("command", ""),
            arguments.get("timeout"),
        )

    @classmethod
    def get_background_task(cls, task_id: str) -> asyncio.Task[str] | None:
        """Retrieve a background task by ID."""
        return _background_tasks.get(task_id)

    @classmethod
    def pop_background_task(cls, task_id: str) -> asyncio.Task[str] | None:
        """Remove and return a completed background task."""
        return _background_tasks.pop(task_id, None)
