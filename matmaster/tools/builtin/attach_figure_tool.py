"""matmaster/tools/builtin/attach_figure_tool.py

AttachFigure -- publish one or more existing workspace images as answer figures.

Publish-only: it uploads images that already exist on disk and returns a
figure_id per image so the model can reference them with [[fig:<figure_id>]].
It runs no commands and generates no images -- generate with Bash first, then
attach. Batch publishing is all-or-nothing via two phases: Phase A validates and
content-hashes the whole batch (uploading nothing); Phase B uploads the whole
batch only if Phase A fully passed. Any failure returns status="error" with no
payload.figures, which the downstream ResponseFiguresAccumulator relies on.
"""

from __future__ import annotations

import asyncio
import posixpath
from typing import Any, ClassVar

from matmaster.tools.figure_artifacts import (
    PreparedFigure,
    prepare_declared_figure,
    publish_prepared_figure,
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

# Per-call batch cap. The two-phase design holds every validated image's bytes
# in memory across Phase A before any upload (Phase B), so peak memory is
# bounded by _MAX_FIGURES_PER_CALL x _MAX_FIGURE_BYTES (~200 MB at 20 x 10 MB).
# Lower this one constant to tighten the bound. Realistic answers attach 1-6.
_MAX_FIGURES_PER_CALL = 20


class AttachFigure(BuiltinTool):
    name: ClassVar[str] = "AttachFigure"
    description: ClassVar[str] = (
        "Publish one or more existing workspace images as answer figures."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "figures": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX_FIGURES_PER_CALL,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "output_path": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Absolute path to an existing image inside the "
                                "workspace."
                            ),
                        },
                        "caption": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Caption shown with the figure in the response."
                            ),
                        },
                    },
                    "required": ["output_path", "caption"],
                },
            }
        },
        "required": ["figures"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="shared_read"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.read"})
    effect_level: ClassVar[str] = "external_effect"
    max_result_chars: ClassVar[int] = 30_000
    plane: ClassVar[ToolPlane] = ToolPlane.EXTERNAL_SERVICE

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str:
        workspace_root = ctx.workspace_root if ctx is not None else None
        if workspace_root is None and self._workdir is not None:
            workspace_root = str(self._workdir)

        root_note = ""
        if workspace_root:
            root_note = (
                f"The session workspace directory for this run is `{workspace_root}`. "
                "Pass each output_path as an absolute path inside this directory.\n\n"
            )

        return (
            f"{root_note}"
            "Use AttachFigure to publish images that already exist in the workspace "
            "so they appear in your answer. Two situations: (1) you just generated a "
            "new image (e.g. with Bash) and want to show it; (2) an image already "
            "exists and you want to reference it. AttachFigure never creates images "
            "-- generate them with Bash first.\n\n"
            "Pass a `figures` array; each item needs an absolute `output_path` and a "
            "`caption`. Publish several images in one call by listing several items. "
            "Publishing is all-or-nothing: if any path is rejected, nothing is "
            "published -- fix the reported path and resend the whole batch. After a "
            "successful call, reference each image with its returned "
            "[[fig:<figure_id>]] marker."
        )

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        figures = arguments.get("figures")
        if not isinstance(figures, list) or not figures:
            return ToolDecision(
                decision="deny", reason="figures must be a non-empty array"
            )
        if len(figures) > _MAX_FIGURES_PER_CALL:
            return ToolDecision(
                decision="deny",
                reason=(
                    f"too many figures in one call: {len(figures)} "
                    f"(max {_MAX_FIGURES_PER_CALL})"
                ),
            )
        if self._workdir is None:
            return ToolDecision(decision="deny", reason="workdir not set")
        workdir = str(self._workdir)

        seen_resolved: set[str] = set()
        for item in figures:
            if not isinstance(item, dict):
                return ToolDecision(
                    decision="deny", reason="each figure must be an object"
                )
            output_path = (item.get("output_path") or "").strip()
            caption = (item.get("caption") or "").strip()
            if not output_path:
                return ToolDecision(decision="deny", reason="output_path is required")
            if not caption:
                return ToolDecision(decision="deny", reason="caption is required")
            if not posixpath.isabs(output_path):
                return ToolDecision(
                    decision="deny",
                    reason=f"output_path '{output_path}' must be an absolute path",
                )
            resolved = resolve_workspace_output_path(
                raw_path=output_path, workdir=workdir
            )
            if resolved is None:
                return ToolDecision(
                    decision="deny",
                    reason=f"output_path '{output_path}' is outside workspace boundary",
                )
            if resolved in seen_resolved:
                return ToolDecision(
                    decision="deny",
                    reason=f"duplicate output_path '{output_path}' in one call",
                )
            seen_resolved.add(resolved)
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
            self.logger.warning(
                "Ignoring invalid figure_upload_config for %s", self.name
            )
            return None

    def _run(
        self,
        arguments: dict[str, Any],
        figure_cfg: FigureUploadConfig | None,
        tool_call_id: str | None,
    ) -> ToolResult:
        session = self._require_session()
        if figure_cfg is None:
            return ToolResult(
                status="error",
                content=(
                    "Figure attachment failed: figure upload is not configured "
                    "for this run."
                ),
            )
        if not tool_call_id:
            return ToolResult(
                status="error",
                content="Figure attachment failed: missing tool_call_id for this run.",
            )

        workdir = str(self._workdir)
        figures: list[dict[str, Any]] = arguments["figures"]

        # Phase A -- validate + hash the whole batch; upload nothing.
        prepared: list[PreparedFigure] = []
        for item in figures:
            result = prepare_declared_figure(
                session=session,
                workdir=workdir,
                output_path=item["output_path"],
                caption=item["caption"],
            )
            if result.prepared is None:
                return ToolResult(
                    status="error",
                    content=self._failure_block(
                        output_path=item["output_path"],
                        reason=result.failure_reason or "unknown",
                        guidance=result.guidance,
                    ),
                )
            prepared.append(result.prepared)

        by_id: dict[str, str] = {}
        for p in prepared:
            if p.figure_id in by_id:
                return ToolResult(
                    status="error",
                    content=(
                        f"Figure attachment failed: duplicate figure_id "
                        f"'{p.figure_id}'\n"
                        f"Both '{by_id[p.figure_id]}' and '{p.output_path}' resolve "
                        "to identical image contents. Declare each distinct image once."
                    ),
                )
            by_id[p.figure_id] = p.output_path

        # Phase B -- upload the whole batch.
        descriptors = []
        for p in prepared:
            pub = publish_prepared_figure(
                prepared=p,
                upload_config=figure_cfg,
                tool_call_id=tool_call_id,
            )
            if pub.figure is None:
                return ToolResult(
                    status="error",
                    content=self._failure_block(
                        output_path=p.output_path,
                        reason=pub.failure_reason or "upload_failed",
                        guidance=pub.guidance,
                    ),
                )
            descriptors.append(pub.figure)

        lines = ["Figures attached:"]
        for d in descriptors:
            lines.append(
                f"- [[fig:{d.figure_id}]] path={d.remote_path} caption={d.caption}"
            )
        return ToolResult(
            status="success",
            content="\n".join(lines),
            payload={"figures": [d.model_dump(mode="json") for d in descriptors]},
        )

    @staticmethod
    def _failure_block(
        *, output_path: str, reason: str, guidance: str | None
    ) -> str:
        return (
            f"Figure attachment failed for {output_path}: {reason}\n{guidance or ''}"
        ).rstrip()

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        return ToolResult(
            status="error",
            content=(
                "AttachFigure requires execution context "
                "(figure upload config and tool_call_id)."
            ),
        )
