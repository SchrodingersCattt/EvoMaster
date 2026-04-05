"""matmaster/tools/builtin/bash_tool.py

BashTool — execute bash commands via session.

CC Reference: tools/BashTool/ (toolName.ts, prompt.ts, BashTool.tsx)
CC name: Bash
"""

from __future__ import annotations

from typing import Any, ClassVar

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
    prompt_exposure: ClassVar[str] = "tool_description"
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
                    "Optional timeout in milliseconds (max 600000). "
                    "Default: 120000ms (2 minutes)."
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
            "Executes a given bash command and returns its output.\n\n"
            f"{workspace_note}\n"
            "Shell state does not persist between commands.\n\n"
            "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, "
            "`head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly "
            "instructed. Instead, use the appropriate dedicated tool:\n"
            " - File search: Use Glob (NOT find or ls)\n"
            " - Content search: Use Grep (NOT grep or rg)\n"
            " - Read files: Use Read (NOT cat/head/tail)\n"
            " - Edit files: Use Edit (NOT sed/awk)\n"
            " - Write files: Use Write (NOT echo >/cat <<EOF)\n\n"
            "# Instructions\n"
            " - Always quote file paths that contain spaces with double quotes\n"
            " - You may specify an optional timeout in milliseconds (max 600000ms / "
            "10 minutes). By default, your command will timeout after 120000ms.\n"
            " - When issuing multiple commands that are independent, make multiple "
            "Bash tool calls in a single message.\n"
            " - For git commands: prefer creating a new commit rather than amending."
        )

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        session = self._require_session()

        command: str = (arguments.get("command") or "").strip()
        if not command:
            return "Error: command is required and must not be empty."

        timeout_ms = arguments.get("timeout", 120_000)
        timeout_ms = min(int(timeout_ms), 600_000)  # cap at 10min
        timeout_s = timeout_ms / 1000  # float division preserves sub-second

        from matmaster.integration.runtime_bridge import build_service_env
        from matmaster.tools.script_env import inject_env

        env = build_service_env("bohrium", session=session)
        command = inject_env(command, env, session)

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
