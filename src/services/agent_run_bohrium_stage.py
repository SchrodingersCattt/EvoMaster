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

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from matmaster.core.playground import WorkspaceArchivalConfig
from matmaster.integration.fanout import RunEventFanout
from matmaster.integration.workspace_handler import WorkspaceHandler
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.runtime_ports import BohriumRuntimeSnapshot
from src.dao.oss_io import upload_bytes_to_oss
from src.services.agent_run_bohrium import BohriumSetupService
from src.services.user_turn_context_service import (
    UserInstructionsInfo,
    load_user_instructions_from_session,
)


@dataclass(frozen=True)
class BohriumStageResult:
    """Return value of ``run_bohrium_stage``."""

    abort_result: Any | None
    bohrium_svc: BohriumSetupService
    pg_ctx: Any
    ssh_attached: bool
    user_instructions: UserInstructionsInfo


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


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


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
            user_instructions=load_user_instructions_from_session(None),
        )
    runtime_snapshot = getattr(bohrium_result, "runtime_snapshot", None)
    if runtime_snapshot is not None:
        ssh_attached_snapshot = _optional_bool(
            getattr(runtime_snapshot, "ssh_attached", None)
        )
        ssh_attached_value = (
            ssh_attached_snapshot
            if ssh_attached_snapshot is not None
            else bool(getattr(bohrium_result, "ssh_attached", False))
        )
        snapshot = BohriumRuntimeSnapshot(
            ssh_attached=ssh_attached_value,
            node_id=_optional_int(getattr(runtime_snapshot, "node_id", None)),
            remote_project_root=_optional_str(
                getattr(runtime_snapshot, "remote_project_root", None)
            ),
            remote_workspace_root=_optional_str(
                getattr(runtime_snapshot, "remote_workspace_root", None)
            ),
        )
        if (
            snapshot.ssh_attached
            or snapshot.node_id is not None
            or snapshot.remote_project_root is not None
            or snapshot.remote_workspace_root is not None
        ):
            pg_ctx = pg_ctx.with_bohrium(snapshot)
    if bohrium_result.execution_session is not None:
        execution_workdir = bohrium_result.execution_workdir or ''
        session_type = bohrium_result.session_type or 'ssh'
        pg_ctx = pg_ctx.with_execution(
            session=bohrium_result.execution_session,
            session_type=session_type,
            execution_workdir=execution_workdir,
        )
    _ui_session = (
        bohrium_result.execution_session if bohrium_result else None
    ) or pg_ctx.session
    user_instructions = load_user_instructions_from_session(_ui_session)

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
