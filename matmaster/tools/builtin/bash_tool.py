"""BashTool -- execute bash commands via session.

Reuses evomaster bash_safety for dangerous command detection.
Mirrors the evomaster BashTool behavior (proxy clear, is_input mode)
but satisfies the matmaster Tool Protocol directly.
"""

from __future__ import annotations

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

    name: ClassVar[str] = "execute_bash"
    description: ClassVar[str] = (
        "Execute a bash command in the terminal within a persistent shell session."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "is_input": {
                "type": "string",
                "enum": ["true", "false"],
                "description": "If true, the command is input to a running process.",
                "default": "false",
            },
            "timeout": {
                "type": "number",
                "description": "Hard timeout in seconds for command execution.",
                "default": -1,
            },
        },
        "required": ["command"],
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        command: str = arguments.get("command", "").strip()
        is_input_str: str = arguments.get("is_input", "false")
        is_input = is_input_str == "true"
        timeout_val = arguments.get("timeout", -1)
        timeout = int(timeout_val) if timeout_val and float(timeout_val) > 0 else None

        # Block dangerous commands
        is_dangerous, reason = is_dangerous_bash_command(command)
        if is_dangerous:
            return f"Blocked: {reason}"

        # Inject proxy clear prefix for non-input commands on non-Windows
        if not is_input and command and sys.platform != "win32":
            command = _PROXY_CLEAR_PREFIX + command

        result = session.exec_bash(
            command=command,
            timeout=timeout,
            is_input=is_input,
        )

        output = result.get("output", "") or result.get("stdout", "")
        exit_code = result.get("exit_code", -1)
        working_dir = result.get("working_dir", "")

        obs = output
        if working_dir:
            obs += f"\n[Current working directory: {working_dir}]"
        if exit_code != -1:
            obs += f"\n[Command finished with exit code {exit_code}]"

        return obs
