"""Bohrium stage helpers extracted from agent_run_service.run_agent.

Phase 0 refactor (DESIGN.md §14 Phase 0a): move workspace upload
closure + figure upload config builder + Bohrium setup / context
threading out of ``run_agent`` so the orchestrator stays under the
800-line target.

The actual Bohrium credential + SSH attach logic lives in
``src/services/agent_run_bohrium.py:BohriumSetupService``; this file
hosts only the run-time wiring around that service.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from matmaster.integration.fanout import RunEventFanout
from matmaster.integration.workspace_handler import WorkspaceHandler
from matmaster.types.context import WorkspaceArchivalConfig
from matmaster.types.figures import FigureUploadConfig
from src.dao.oss_io import upload_bytes_to_oss
from src.services.agent_run_bohrium import BohriumSetupService
from src.services.agent_run_instructions import _USER_INSTRUCTIONS_PATH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BohriumStageResult:
    """Return value of ``run_bohrium_stage``."""

    abort_result: Any | None
    bohrium_svc: BohriumSetupService
    pg_ctx: Any
    ssh_attached: bool
    user_instructions: str | None


def _build_workspace_upload_fn(
    archival_config: WorkspaceArchivalConfig | None,
) -> Callable[..., Any] | None:
    """Build workspace upload closure when archival is enabled.

    Lazy-imports oss_io to avoid hard oss2 dependency when archival
    is disabled.
    """
    if not archival_config or not archival_config.enabled:
        return None
    oss_prefix = (archival_config.oss_prefix or '').strip('/')

    def _do_upload(session_id: str, task_id: str, workspace_path: Path) -> None:
        from src.dao.oss_io import upload_dir_to_oss

        key_prefix = '/'.join(part for part in (oss_prefix, session_id) if part)
        upload_dir_to_oss(workspace_path, key_prefix)

    return _do_upload


def _build_figure_upload_config(*, session_id: str, task_id: str) -> FigureUploadConfig:
    """Build the per-run figure upload contract injected into tool runtime state."""
    return FigureUploadConfig(
        session_id=session_id,
        task_id=task_id,
        asset_key_prefix='matmaster/chat_figures',
        upload_bytes=upload_bytes_to_oss,
    )


async def run_bohrium_stage(
    *,
    sessions_service: Any,
    fanout: RunEventFanout,
    dispatch_from_thread: Callable,
    session_id: str,
    task_id: str,
    playground: Any,
    pg_ctx: Any,
    run_started_at: float,
    bohrium_required: bool,
    remote_workdir: str | None,
) -> BohriumStageResult:
    """Run Bohrium setup and thread the resulting runtime context."""
    bohrium_svc = BohriumSetupService(
        sessions_service,
        event_sink=dispatch_from_thread,
    )
    effective_bohrium_required = bool(bohrium_required or remote_workdir)
    bohrium_result = await bohrium_svc.run_setup(
        session_id=session_id,
        playground=playground,
        run_started_at=run_started_at,
        bohrium_required=effective_bohrium_required,
        remote_workdir=remote_workdir,
    )
    ssh_attached = bohrium_result.ssh_attached
    if bohrium_result.abort_result is not None:
        return BohriumStageResult(
            abort_result=bohrium_result.abort_result,
            bohrium_svc=bohrium_svc,
            pg_ctx=pg_ctx,
            ssh_attached=ssh_attached,
            user_instructions=None,
        )
    bohrium_meta = (
        bohrium_result.runtime_snapshot.model_dump()
        if bohrium_result.runtime_snapshot is not None
        else {}
    )
    pg_ctx = pg_ctx.with_bohrium(bohrium_meta)
    if bohrium_result.execution_session is not None:
        execution_workdir = bohrium_result.execution_workdir or ''
        session_type = bohrium_result.session_type or 'ssh'
        pg_ctx = pg_ctx.with_execution(
            session=bohrium_result.execution_session,
            session_type=session_type,
            execution_workdir=execution_workdir,
        )
    user_instructions: str | None = None
    _ui_session = (
        bohrium_result.execution_session if bohrium_result else None
    ) or pg_ctx.session
    if _ui_session is not None:
        try:
            user_instructions = (
                _ui_session.read_file(_USER_INSTRUCTIONS_PATH).strip() or None
            )
        except Exception as _ui_err:
            logger.debug('read user instructions skipped: %s', _ui_err)

    fanout.add_handler(
        WorkspaceHandler(
            session_id=session_id,
            task_id=task_id,
            ssh_attached=ssh_attached,
            workspace_path=pg_ctx.workdir,
            upload_fn=_build_workspace_upload_fn(pg_ctx.archival),
        )
    )

    return BohriumStageResult(
        abort_result=None,
        bohrium_svc=bohrium_svc,
        pg_ctx=pg_ctx,
        ssh_attached=ssh_attached,
        user_instructions=user_instructions,
    )
