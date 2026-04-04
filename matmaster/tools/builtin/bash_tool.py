"""BashTool -- execute bash commands via session.

Pure execution layer: receives commands, executes them, returns results.
Bash safety checks (dangerous command patterns, env credential scanning)
are handled by DefaultCapabilityPolicy in the constraint model (Phase 35-01).

Dual-path execute:
- matmaster LocalSession -> native asyncio.create_subprocess_exec
- other sessions -> sync session.exec_bash (via base class)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any, ClassVar

from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

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
    description: ClassVar[str] = 'Execute a bash command in the session shell.'
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
            'timeout': {
                'type': 'number',
                'description': 'Hard timeout in seconds. Use -1 for no limit.',
                'default': -1,
            },
        },
        'required': ['command'],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="session", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"shell.execute"})
    effect_level: ClassVar[str] = "local_mutation"
    max_result_chars: ClassVar[int] = 12000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL

    def prompt(self, ctx: Any | None = None) -> str | None:
        return (
            'Do not use bash for: cat/head/tail/sed/awk/find/ls/grep/rg/echo. '
            'Use read_file, edit_file, write_file, glob, grep instead.\n\n'
            'Paths: local/devshell cwd is the task workspace; do not assume /share exists. '
            'Bohrium SSH: shared storage is usually /share, not /workspace.'
        )

    async def execute(self, arguments: dict[str, Any]) -> str:
        """Dual-path execute: native async for matmaster LocalSession, sync fallback otherwise."""
        from matmaster.sessions.local import LocalSession as _MatLocal

        if isinstance(self._session, _MatLocal):
            try:
                return await self._execute_async(arguments)
            except Exception as e:
                self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
                return f"Error: {e}"

        return await super().execute(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: Any,
    ) -> str:
        """Context-aware execution entry point.

        Captures the cancel_token from ToolExecutionContext for cancellation
        support, then delegates to the standard execute() path.
        """
        if exec_ctx is not None and hasattr(exec_ctx, "cancel_token"):
            self._cancel_token = exec_ctx.cancel_token
        return await self.execute(arguments)

    async def _execute_async(self, arguments: dict[str, Any]) -> str:
        """Native async subprocess execution for matmaster LocalSession.

        Uses asyncio.create_subprocess_exec instead of subprocess.run,
        eliminating thread-pool overhead for local command execution.
        """
        command: str = arguments.get('command', '').strip()
        timeout_val = arguments.get('timeout', -1)
        timeout = int(timeout_val) if timeout_val and float(timeout_val) > 0 else None

        # Inject proxy clear prefix on non-Windows
        if command and sys.platform != 'win32':
            command = _PROXY_CLEAR_PREFIX + command

        wd = str(self._workdir) if self._workdir else None

        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=wd,
            start_new_session=True,
        )
        cancel_token = getattr(self, "_cancel_token", None)

        def _kill_group() -> None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

        if cancel_token and cancel_token.is_cancelled:
            _kill_group()
            await proc.wait()
            obs = "Command cancelled."
            if wd:
                obs += f"\n[Current working directory: {wd}]"
            obs += "\n[Command finished with exit code 130]"
            return obs

        if cancel_token:
            cancel_token.on_cancel(_kill_group)

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
        if cancel_token and cancel_token.is_cancelled:
            obs = "Command cancelled."
            if wd:
                obs += f"\n[Current working directory: {wd}]"
            obs += "\n[Command finished with exit code 130]"
            return obs
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
        timeout_val = arguments.get('timeout', -1)
        timeout = int(timeout_val) if timeout_val and float(timeout_val) > 0 else None

        # Inject proxy clear prefix on non-Windows
        if command and sys.platform != 'win32':
            command = _PROXY_CLEAR_PREFIX + command

        result = session.exec_bash(
            command=command,
            timeout=timeout,
            cancel_token=self._cancel_token_for_exec(),
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
