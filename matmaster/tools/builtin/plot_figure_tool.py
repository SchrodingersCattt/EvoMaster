"""matmaster/tools/builtin/plot_figure_tool.py

PlotFigure — generate or publish one figure and attach it to the response.
Single model-visible figure-publishing entry point. Two modes:
- with command: run the command, then collect output_path.
- without command: publish an already-existing image at output_path.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from matmaster.tools.figure_artifacts import (
    collect_declared_figure,
    resolve_workspace_output_path,
)
from matmaster.tools.tool_result import ToolResult
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool

_PLOT_TIMEOUT_CAP_MS = 600_000
_DEFAULT_TIMEOUT_MS = 120_000


class PlotFigure(BuiltinTool):
    name: ClassVar[str] = "PlotFigure"
    description: ClassVar[str] = (
        "Generate or publish one figure and attach it to the response."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Optional shell command to generate the figure. "
                    "Omit this when output_path already exists."
                ),
            },
            "output_path": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Path to the image to attach. Absolute, or relative to "
                    "the session workspace."
                ),
            },
            "caption": {
                "type": "string",
                "minLength": 1,
                "description": "Caption shown with the figure in the response.",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600000,
                "description": (
                    "Optional timeout in milliseconds for command execution. "
                    "Used only when command is provided. "
                    "Default 120000 (2 min), max 600000 (10 min)."
                ),
            },
        },
        "required": ["output_path", "caption"],
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
        return (
            "Use PlotFigure for any figure that should appear in the final answer.\n\n"
            "If you need to create the image now, provide command, output_path, and "
            "caption. If the image already exists from Bash, Bohrium, a skill, or a "
            "previous command, omit command and provide output_path and caption.\n\n"
            "Bash output is never shown as an answer image by itself. To show an "
            "existing image, publish it with PlotFigure. Write one figure per "
            "PlotFigure call; call it again for additional figures. After a "
            "successful call, reference the figure with the returned "
            "[[fig:<figure_id>]] marker."
        )

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        output_path = arguments.get("output_path") or ""
        if not output_path.strip():
            return ToolDecision(decision="deny", reason="output_path is required")
        caption = arguments.get("caption") or ""
        if not caption.strip():
            return ToolDecision(decision="deny", reason="caption is required")
        if "command" in arguments:
            command = arguments.get("command") or ""
            if not command.strip():
                return ToolDecision(
                    decision="deny",
                    reason="command, when provided, must not be empty",
                )
        if self._workdir is None:
            return ToolDecision(decision="deny", reason="workdir not set")
        if (
            resolve_workspace_output_path(
                raw_path=output_path, workdir=str(self._workdir)
            )
            is None
        ):
            return ToolDecision(
                decision="deny",
                reason=f"output_path '{output_path}' is outside workspace boundary",
            )
        return None

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
                figure_cfg = self._resolve_figure_cfg(exec_ctx.runner_state)
            return await asyncio.to_thread(
                self._run, arguments, figure_cfg, tool_call_id
            )
        except Exception as exc:
            self.logger.error("Tool %s failed: %s", self.name, exc, exc_info=True)
            return f"Error: {exc}"

    def _resolve_figure_cfg(
        self, runner_state: ToolRunnerState | None
    ) -> FigureUploadConfig | None:
        if runner_state is None:
            return None
        raw = runner_state.get("figure_upload_config")
        if isinstance(raw, FigureUploadConfig):
            return raw
        if raw is None:
            return None
        try:
            return FigureUploadConfig.model_validate(raw)
        except Exception:
            self.logger.warning("Ignoring invalid figure_upload_config for %s", self.name)
            return None

    def _run(
        self,
        arguments: dict[str, Any],
        figure_cfg: FigureUploadConfig | None,
        tool_call_id: str | None,
    ) -> str | ToolResult:
        session = self._require_session()
        if figure_cfg is None:
            return ToolResult(
                status="error",
                content="Figure attachment failed: figure upload is not configured for this run.",
            )
        if not tool_call_id:
            return ToolResult(
                status="error",
                content="Figure attachment failed: missing tool_call_id for this run.",
            )

        output_path: str = arguments["output_path"]
        caption: str = arguments["caption"]
        command: str = (arguments.get("command") or "").strip()
        workdir = str(self._workdir)

        observation = ""
        exit_code = 0
        if command:
            from matmaster.tools.bash_runner import run_bash_command

            timeout_ms = int(arguments.get("timeout", _DEFAULT_TIMEOUT_MS))
            timeout_s = min(timeout_ms, _PLOT_TIMEOUT_CAP_MS) / 1000
            run = run_bash_command(
                session=session,
                command=command,
                timeout_s=timeout_s,
                cancel_token=self._cancel_token_for_exec(),
            )
            observation = run.observation
            exit_code = run.exit_code

        declared = collect_declared_figure(
            session=session,
            workdir=workdir,
            output_path=output_path,
            caption=caption,
            tool_call_id=tool_call_id,
            upload_config=figure_cfg,
        )
        return self._assemble_result(
            observation=observation,
            exit_code=exit_code,
            has_command=bool(command),
            declared=declared,
            output_path=output_path,
            caption=caption,
        )

    def _assemble_result(
        self,
        *,
        observation: str,
        exit_code: int,
        has_command: bool,
        declared: Any,
        output_path: str,
        caption: str,
    ) -> ToolResult:
        if declared.figure is not None:
            success_block = (
                "Figure attached:\n"
                f"- figure_id: {declared.figure_id}\n"
                f"- path: {output_path}\n"
                f"- caption: {caption}\n"
                f"Use [[fig:{declared.figure_id}]] when referring to this figure."
            )
            content = (
                f"{observation}\n{success_block}" if has_command else success_block
            )
            status = "error" if (has_command and exit_code != 0) else "success"
            return ToolResult(
                status=status,
                content=content,
                payload={"figures": [declared.figure.model_dump(mode="json")]},
            )

        failure_block = (
            f"Figure attachment failed: {declared.failure_reason}\n"
            f"{declared.guidance or ''}"
        ).rstrip()
        content = (
            f"{observation}\n{failure_block}" if has_command else failure_block
        )
        return ToolResult(status="error", content=content)

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        return ToolResult(
            status="error",
            content=(
                "PlotFigure requires execution context "
                "(figure upload config and tool_call_id)."
            ),
        )
