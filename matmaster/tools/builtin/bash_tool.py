"""matmaster/tools/builtin/bash_tool.py

BashTool — execute bash commands via session.

CC Reference: tools/BashTool/ (toolName.ts, prompt.ts, BashTool.tsx)
CC name: Bash
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar

from matmaster.tools.bash_runner import run_bash_command
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool

# Pure `sleep N` pattern (integer or decimal seconds). Only commands matching
# this exact form are allowed to use the extended 1h timeout cap; anything
# compound (e.g. `sleep 3600 && foo`) gets clamped to the general 10m ceiling.
_PURE_SLEEP_RE = re.compile(r"\s*sleep\s+\d+(?:\.\d+)?\s*")
_GENERAL_TIMEOUT_CAP_MS = 600_000
_SLEEP_TIMEOUT_CAP_MS = 3_600_000


class BashTool(BuiltinTool):
    """Execute bash commands in the session shell.

    CC name: Bash (BashTool)
    """

    name: ClassVar[str] = "Bash"
    description: ClassVar[str] = (
        "Run a shell command in the session workspace and return its output."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600000,
                "description": (
                    "Optional timeout in milliseconds. "
                    "Default: 120000ms (2 minutes). "
                    "Max 600000ms (10 min) for general commands. "
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise active-voice summary of what this command "
                    "does. Keep simple commands short, and add enough context "
                    "for pipelines or obscure flags."
                ),
            },
        },
        "required": ["command"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="exclusive"),
        ResourceClaim(resource="session", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"shell.execute"})
    effect_level: ClassVar[str] = "local_mutation"
    max_result_chars: ClassVar[int] = 30_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str:
        workspace_root = ctx.workspace_root if ctx is not None else None
        if workspace_root is None and self._workdir is not None:
            workspace_root = str(self._workdir)

        workspace_note = "Each Bash call starts in the session workspace directory.\n"
        if workspace_root:
            workspace_note = (
                f"The session workspace directory for this run is `{workspace_root}`.\n"
                "Each Bash call starts in this directory.\n"
            )

        return (
            f"{workspace_note}"
            "Shell state does not persist between commands. "
            "Use dedicated tools instead of shell equivalents "
            "(Glob not find, Grep not grep, Read not cat, Edit not sed, Write not echo). "
            "**Turn economy:** each Bash call costs one turn. Combine related "
            "operations into a single command with `&&` or `;`, or write a "
            "self-contained script (heredoc or file) for multi-step workflows "
            "instead of issuing many small sequential calls."
        )

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        session = self._require_session()
        command: str = (arguments.get("command") or "").strip()
        if not command:
            return "Error: command is required and must not be empty."

        timeout_ms = int(arguments.get("timeout", 120_000))
        cap = (
            _SLEEP_TIMEOUT_CAP_MS
            if _PURE_SLEEP_RE.fullmatch(command)
            else _GENERAL_TIMEOUT_CAP_MS
        )
        timeout_s = min(timeout_ms, cap) / 1000

        run = run_bash_command(
            session=session,
            command=command,
            timeout_s=timeout_s,
            cancel_token=self._cancel_token_for_exec(),
        )
        if run.exit_code != 0:
            return ToolResult(status="error", content=run.observation)
        return run.observation
