"""Bohrium stage helpers for agent_run_service.run_agent.

The actual Bohrium credential + SSH attach logic lives in
``src/services/agent_run_bohrium.py:BohriumSetupService``; this file
hosts only the run-time wiring around that service.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from matmaster.context.ports import UserInstructions
from matmaster.core.playground import ExecutionEnvironment, WorkspaceArchivalConfig
from matmaster.integration.fanout import RunEventFanout
from matmaster.integration.workspace_handler import WorkspaceHandler
from matmaster.types.figures import FigureUploadConfig
from src.dao.oss_io import upload_bytes_to_oss
from src.services.agent_run_bohrium import BohriumSetupService
from src.services.session_directory_service import normalize_remote_workspace_path
from src.services.user_turn_context_service import (
    load_user_instructions_from_session,
)


@dataclass(frozen=True)
class BohriumStageResult:
    """Return value of ``run_bohrium_stage``."""

    abort_result: Any | None
    bohrium_svc: BohriumSetupService
    environment: ExecutionEnvironment
    ssh_attached: bool
    user_instructions: UserInstructions
    workspace: str | None = None


def _build_workspace_upload_fn(
    archival_config: WorkspaceArchivalConfig | None,
) -> Callable[..., Any] | None:
    """Build workspace upload closure when archival is enabled.

    Lazy-imports oss_io to avoid hard oss2 dependency when archival
    is disabled.
    """
    if not archival_config or not archival_config.enabled:
        return None
    oss_prefix = (archival_config.oss_prefix or "").strip("/")

    def _do_upload(session_id: str, task_id: str, workspace_path: Path) -> None:
        from src.dao.oss_io import upload_dir_to_oss

        key_prefix = "/".join(part for part in (oss_prefix, session_id) if part)
        upload_dir_to_oss(workspace_path, key_prefix)

    return _do_upload


def _build_figure_upload_config(*, session_id: str, task_id: str) -> FigureUploadConfig:
    """Build the per-run figure upload contract injected into tool runtime state."""
    return FigureUploadConfig(
        session_id=session_id,
        task_id=task_id,
        asset_key_prefix="matmaster/chat_figures",
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
    environment: ExecutionEnvironment,
    run_started_at: float,
    bohrium_required: bool,
    workspace: str | None,
    bohrium_node_sku_id: int | None = None,
    invocation_id: str | None = None,
) -> BohriumStageResult:
    """Run Bohrium setup and physically rebind the execution environment.

    Bohrium evolves only the *physical* environment (session swap + execution
    workdir + bohrium snapshot) via ``with_execution`` / ``with_bohrium``; the
    per-run ``AgentRunRequest`` is assembled later by the service.
    """
    bohrium_svc = BohriumSetupService(
        sessions_service,
        event_sink=dispatch_from_thread,
    )
    effective_bohrium_required = bool(bohrium_required or workspace)
    bohrium_result = await bohrium_svc.run_setup(
        session_id=session_id,
        playground=playground,
        run_started_at=run_started_at,
        bohrium_required=effective_bohrium_required,
        workspace=workspace,
        bohrium_node_sku_id=bohrium_node_sku_id,
        invocation_id=invocation_id,
    )
    ssh_attached = bohrium_result.ssh_attached
    if bohrium_result.abort_result is not None:
        return BohriumStageResult(
            abort_result=bohrium_result.abort_result,
            bohrium_svc=bohrium_svc,
            environment=environment,
            ssh_attached=ssh_attached,
            user_instructions=load_user_instructions_from_session(None),
            workspace=None,
        )
    if bohrium_result.runtime_snapshot is not None:
        environment = environment.with_bohrium(bohrium_result.runtime_snapshot)
    stage_workspace: str | None = None
    if bohrium_result.execution_session is not None:
        execution_workdir = bohrium_result.execution_workdir or ""
        session_type = bohrium_result.session_type or "ssh"
        environment = environment.with_execution(
            session=bohrium_result.execution_session,
            session_type=session_type,
            execution_workdir=execution_workdir,
        )
        if ssh_attached:
            stage_workspace = normalize_remote_workspace_path(execution_workdir)
    _ui_session = (
        bohrium_result.execution_session if bohrium_result else None
    ) or environment.session
    user_instructions = load_user_instructions_from_session(_ui_session)

    fanout.add_handler(
        WorkspaceHandler(
            session_id=session_id,
            task_id=task_id,
            ssh_attached=ssh_attached,
            workspace_path=environment.workdir,
            upload_fn=_build_workspace_upload_fn(environment.archival),
        )
    )

    return BohriumStageResult(
        abort_result=None,
        bohrium_svc=bohrium_svc,
        environment=environment,
        ssh_attached=ssh_attached,
        user_instructions=user_instructions,
        workspace=stage_workspace,
    )
