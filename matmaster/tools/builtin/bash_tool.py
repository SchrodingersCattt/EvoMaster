"""matmaster/tools/builtin/bash_tool.py

BashTool — execute bash commands via session.

CC Reference: tools/BashTool/ (toolName.ts, prompt.ts, BashTool.tsx)
CC name: Bash
"""

from __future__ import annotations

import asyncio
import re
import shlex
from typing import Any, ClassVar

from pydantic import ValidationError

from matmaster.bohrium.runtime import get_runtime
from matmaster.tools.figure_artifacts import (
    build_figure_env,
    collect_figures_from_session,
)
from matmaster.tools.filesystem_semantics.shell_planner import plan_shell_command
from matmaster.tools.tool_result import ToolResult
from matmaster.types.figures import FigureUploadConfig
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
                "maximum": 3600000,
                "description": (
                    "Optional timeout in milliseconds. "
                    "Default: 120000ms (2 minutes). "
                    "Max 600000ms (10 min) for general commands. "
                    "Exception: pure `sleep N` commands "
                    "may set timeout up to 3600000ms (1 hour), for use as "
                    "polling intervals between long-time HPC job status checks. "
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
            "instead of issuing many small sequential calls. "
            "If a Bash command generates figures for the final answer, save them under "
            "`$ARTIFACT_DIR` and write `$MANIFEST_PATH` as JSON like "
            '`{"figures":[{"figure_id":"...","path":"relative/path.png","caption":"..."}]}`. '
            "`path` must be relative to `$ARTIFACT_DIR`; supported formats are png, jpg, "
            "jpeg, and webp; files not listed in the manifest are ignored."
        )

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        return self._execute_with_figure_support(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        try:
            figure_cfg: FigureUploadConfig | None = None
            tool_call_id: str | None = None
            if exec_ctx is not None:
                tool_call_id = exec_ctx.tool_call_id
                if exec_ctx.runner_state is not None:
                    raw_figure_cfg = exec_ctx.runner_state.get("figure_upload_config")
                    if isinstance(raw_figure_cfg, FigureUploadConfig):
                        figure_cfg = raw_figure_cfg
                    elif raw_figure_cfg is not None:
                        try:
                            figure_cfg = FigureUploadConfig.model_validate(
                                raw_figure_cfg
                            )
                        except ValidationError as exc:
                            self.logger.warning(
                                "Ignoring invalid figure_upload_config for %s: %s",
                                self.name,
                                exc,
                            )
                            figure_cfg = None

            return await asyncio.to_thread(
                self._execute_with_figure_support,
                arguments,
                figure_cfg,
                tool_call_id,
            )
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    def _execute_with_figure_support(
        self,
        arguments: dict[str, Any],
        figure_cfg: FigureUploadConfig | None = None,
        tool_call_id: str | None = None,
    ) -> str | ToolResult:
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
        timeout_ms = min(timeout_ms, cap)
        timeout_s = timeout_ms / 1000  # float division preserves sub-second

        from matmaster.tools.script_env import (
            prepare_inline_command,
            prepare_script_command,
        )

        runtime = get_runtime(session)
        env = runtime.build_env() if runtime is not None else {}
        artifact_dir: str | None = None
        manifest_path: str | None = None
        if figure_cfg is not None and tool_call_id and self._workdir is not None:
            artifact_dir, manifest_path = build_figure_env(
                str(self._workdir),
                tool_call_id,
            )
            session.exec_bash(f"mkdir -p {shlex.quote(artifact_dir)}")
            env = {
                **env,
                "ARTIFACT_DIR": artifact_dir,
                "MANIFEST_PATH": manifest_path,
            }
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

        if (
            figure_cfg is not None
            and tool_call_id is not None
            and artifact_dir is not None
            and manifest_path is not None
        ):
            collection = collect_figures_from_session(
                session=session,
                artifact_dir=artifact_dir,
                manifest_path=manifest_path,
                tool_call_id=tool_call_id,
                upload_config=figure_cfg,
            )
            if collection.figures or collection.failure_ids or collection.warnings:
                content = obs
                if collection.failure_ids:
                    content += (
                        "\n[Figure pipeline: "
                        f"{len(collection.failure_ids)} failed: "
                        + ", ".join(collection.failure_ids)
                        + "]"
                    )
                if collection.warnings:
                    content += (
                        "\n[Figure manifest ignored: "
                        + "; ".join(collection.warnings)
                        + "]"
                    )
                return ToolResult(
                    status="error" if exit_code != 0 else "success",
                    content=content,
                    payload={
                        "figures": [
                            fig.model_dump(mode="json") for fig in collection.figures
                        ]
                    },
                )

        if exit_code != 0:
            return ToolResult(status="error", content=obs)
        return obs
