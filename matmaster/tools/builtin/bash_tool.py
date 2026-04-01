"""BashTool -- execute bash commands via session.

Reuses evomaster bash_safety for dangerous command detection.
Mirrors the evomaster BashTool behavior (proxy clear, is_input mode)
but satisfies the matmaster Tool Protocol directly.

Dual-path execute:
- matmaster LocalSession -> native asyncio.create_subprocess_exec
- evomaster / other sessions -> sync session.exec_bash (via base class)
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, ClassVar

from evomaster.agent.tools.builtin.bash_safety import is_dangerous_bash_command

from .base import BuiltinTool

# Proxy clear prefix -- injected before each new command to prevent
# platform-injected proxies from blocking curl/wget/git on remote nodes.
_PROXY_CLEAR_PREFIX = (
    'export http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= '
    'NO_PROXY= no_proxy= ftp_proxy= FTP_PROXY=; '
    'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY '
    'NO_PROXY no_proxy ftp_proxy FTP_PROXY WGETRC 2>/dev/null; '
)


class BashTool(BuiltinTool):
    """Execute bash commands in the session shell."""

    name: ClassVar[str] = 'execute_bash'
    description: ClassVar[str] = (
        'Execute a bash command in the session shell.\n\n'
        'Do not use bash for: cat/head/tail/sed/awk/find/ls/grep/rg/echo. '
        'Use read_file, edit_file, write_file, glob, grep instead.\n\n'
        'Paths: local/devshell cwd is the task workspace; do not assume /share exists. '
        'Bohrium SSH: shared storage is usually /share, not /workspace.'
    )
    json_schema: ClassVar[dict[str, Any]] = {
        'type': 'object',
        'properties': {
            'command': {
                'type': 'string',
                'description': (
                    'The bash command to execute. Prefer dedicated tools for file operations. '
                    'Local: cwd is the workspace (relative paths OK). '
                    'Bohrium SSH only: shared storage is often /share (not /workspace).'
                ),
            },
            'is_input': {
                'type': 'string',
                'enum': ['true', 'false'],
                'description': 'If true, the command is input to a running process.',
                'default': 'false',
            },
            'timeout': {
                'type': 'number',
                'description': 'Hard timeout in seconds. Use -1 for no limit.',
                'default': -1,
            },
        },
        'required': ['command'],
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        """Dual-path execute: native async for LocalSession, sync fallback otherwise.

        Overrides BuiltinTool.execute() to use asyncio.create_subprocess_exec
        when session is evomaster LocalSession, avoiding thread-pool overhead.
        For all other sessions, delegates to the base class async path (to_thread).
        """
        from evomaster.agent.session.local import LocalSession as _EvoLocal
        from matmaster.sessions.local import LocalSession as _MatLocal

        if isinstance(self._session, (_EvoLocal, _MatLocal)):
            try:
                return await self._execute_async(arguments)
            except Exception as e:
                self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
                return f"Error: {e}"

        return await super().execute(arguments)

    async def _execute_async(self, arguments: dict[str, Any]) -> str:
        """Native async subprocess execution for matmaster LocalSession.

        Uses asyncio.create_subprocess_exec instead of subprocess.run,
        eliminating thread-pool overhead for local command execution.
        """
        command: str = arguments.get('command', '').strip()
        is_input_str: str = arguments.get('is_input', 'false')
        is_input = is_input_str == 'true'
        timeout_val = arguments.get('timeout', -1)
        timeout = int(timeout_val) if timeout_val and float(timeout_val) > 0 else None

        # Interactive input not supported in local session
        if is_input:
            return "Interactive input is not supported in local session."

        # Block dangerous commands
        is_dangerous, reason = is_dangerous_bash_command(command)
        if is_dangerous:
            return f"Blocked: {reason}"

        # Inject proxy clear prefix for non-input commands on non-Windows
        if not is_input and command and sys.platform != 'win32':
            command = _PROXY_CLEAR_PREFIX + command

        wd = str(self._workdir) if self._workdir else None

        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=wd,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            obs = f"Command timeout after {timeout}s"
            if wd:
                obs += f"\n[Current working directory: {wd}]"
            obs += "\n[Command finished with exit code 124]"
            return obs

        stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        output = stdout
        if stderr:
            output = output + stderr if output else stderr

        obs = output
        if wd:
            obs += f"\n[Current working directory: {wd}]"
        if proc.returncode is not None:
            obs += f"\n[Command finished with exit code {proc.returncode}]"

        return obs

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        command: str = arguments.get('command', '').strip()
        is_input_str: str = arguments.get('is_input', 'false')
        is_input = is_input_str == 'true'
        timeout_val = arguments.get('timeout', -1)
        timeout = int(timeout_val) if timeout_val and float(timeout_val) > 0 else None

        # Block dangerous commands
        is_dangerous, reason = is_dangerous_bash_command(command)
        if is_dangerous:
            return f'Blocked: {reason}'

        # Inject proxy clear prefix for non-input commands on non-Windows
        if not is_input and command and sys.platform != 'win32':
            command = _PROXY_CLEAR_PREFIX + command

        result = session.exec_bash(
            command=command,
            timeout=timeout,
            is_input=is_input,
            stop_event=self._stop_event_for_exec(),
        )

        output = result.get('output', '') or result.get('stdout', '')
        exit_code = result.get('exit_code', -1)
        working_dir = result.get('working_dir', '')

        obs = output
        if working_dir:
            obs += f'\n[Current working directory: {working_dir}]'
        if exit_code != -1:
            obs += f'\n[Command finished with exit code {exit_code}]'

        return obs
