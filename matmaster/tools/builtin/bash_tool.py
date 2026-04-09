"""matmaster/tools/builtin/bash_tool.py

BashTool — execute bash commands via session.

CC Reference: tools/BashTool/ (toolName.ts, prompt.ts, BashTool.tsx)
CC name: Bash
"""

from __future__ import annotations

from typing import Any, ClassVar

from matmaster.bohrium.runtime import get_runtime
from matmaster.tools.filesystem_semantics.shell_planner import plan_shell_command
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool


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
                "maximum": 3600000,
                "description": (
                    "Optional timeout in milliseconds. "
                    "Default: 120000ms (2 minutes). "
                    "Max 600000ms (10 min) for general commands. "
                    "Exception: pure `sleep N` commands "
                    "may set timeout up to 3600000ms (1 hour), for use as "
                    "polling intervals between HPC job status checks. "
                    "Compound commands like `sleep 3600 && ...` are NOT "
                    "eligible for the higher cap. "
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

    def describe(self, ctx: ToolDescriptionContext | None = None) -> str:
        return self.prompt(ctx)

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
            "(Glob not find, Grep not grep, Read not cat, Edit not sed, Write not echo)."
        )

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        session = self._require_session()

        command: str = (arguments.get("command") or "").strip()
        if not command:
            return "Error: command is required and must not be empty."

        timeout_ms = arguments.get("timeout", 120_000)
        timeout_ms = min(int(timeout_ms), 3_600_000)  # cap at 1h (sleep only)
        timeout_s = timeout_ms / 1000  # float division preserves sub-second

        from matmaster.tools.script_env import (
            prepare_inline_command,
            prepare_script_command,
        )

        runtime = get_runtime(session)
        env = runtime.build_env() if runtime is not None else {}
        plan = plan_shell_command(command)
        if plan.mode == "script":
            command = prepare_script_command(
                command,
                env,
                session,
                shell_path="bash",
            )
        else:
            command = prepare_inline_command(command, env, session)

        result = session.exec_bash(
            command=command,
            timeout=timeout_s,
            cancel_token=self._cancel_token_for_exec(),
        )

        output = result.get("output", "") or result.get("stdout", "")
        exit_code = result.get("exit_code", 0)
        working_dir = result.get("working_dir", "")

        obs = output
        if working_dir:
            obs += f"\n[Session working directory: {working_dir}]"
        obs += f"\n[Command finished with exit code {exit_code}]"

        if exit_code != 0:
            return ToolResult(status="error", content=obs)
        return obs
