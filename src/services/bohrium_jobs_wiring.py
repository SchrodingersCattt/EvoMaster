"""service 层把 bohrium_jobs DAO 包成 kernel 端口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from matmaster.bohrium.status import to_ledger_status
from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsPort,
    WorkspaceJobsQuery,
)
from src.dao.bohrium_jobs_table import BohriumJobsTable, get_bohrium_jobs_table
from src.services.bohrium_delivery_ack import DeliverySnapshot
from src.services.session_directory_service import (
    SessionDirectoryError,
    normalize_remote_workspace_path,
)
from src.utils.constant import env_int

logger = logging.getLogger(__name__)

_FOREGROUND_POLL_BACKOFF_SECONDS = 30


def _normalize_ledger_workspace(workspace: str | None) -> str | None:
    if workspace is None:
        return None
    try:
        return normalize_remote_workspace_path(workspace)
    except SessionDirectoryError as exc:
        raise ValueError(f"bohrium ledger workspace invalid: {workspace!r}") from exc


class _BohriumJobsTableRef:
    def __init__(
        self,
        *,
        table: BohriumJobsTable | None,
        table_factory: Callable[[], BohriumJobsTable],
    ) -> None:
        self._table = table
        self._table_factory = table_factory

    def get(self) -> BohriumJobsTable:
        if self._table is None:
            self._table = self._table_factory()
        return self._table


class _BohriumJobLedger:
    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        session_id: str,
        invocation_id: str | None,
        user_id: str,
        org_id: str,
        workspace: str,
        spawn_id: str | None = None,
        observed_terminal: set[tuple[bool, str]] | None = None,
    ) -> None:
        self._table_ref = table_ref
        self._session_id = session_id
        self._invocation_id = invocation_id
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._spawn_id = spawn_id
        self._observed_terminal = observed_terminal

    def _require_identity(self) -> None:
        missing = [
            name
            for name, val in (
                ("session_id", self._session_id),
                ("user_id", self._user_id),
                ("org_id", self._org_id),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                f"bohrium ledger missing identity fields: {', '.join(missing)}"
            )

    def record_submit(
        self,
        *,
        job_id: str,
        job_name: str | None,
        project_id: int,
        sandbox: bool,
        input_dir: str,
    ) -> None:
        self._require_identity()
        self._table_ref.get().insert_submitted(
            session_id=self._session_id,
            invocation_id=self._invocation_id,
            spawn_id=self._spawn_id,
            user_id=self._user_id,
            org_id=self._org_id,
            job_id=str(job_id),
            job_name=job_name,
            project_id=int(project_id),
            sandbox=bool(sandbox),
            input_dir=str(input_dir),
            workspace=self._workspace,
        )

    def record_poll(
        self,
        *,
        job_id: str,
        sandbox: bool,
        status_code: int,
    ) -> None:
        self._require_identity()
        decision = to_ledger_status(int(status_code))
        self._table_ref.get().apply_poll(
            user_id=self._user_id,
            org_id=self._org_id,
            sandbox=bool(sandbox),
            job_id=str(job_id),
            status=decision.status,
            is_terminal=decision.is_terminal,
            backoff_seconds=_FOREGROUND_POLL_BACKOFF_SECONDS,
        )
        if decision.is_terminal and self._observed_terminal is not None:
            self._observed_terminal.add((bool(sandbox), str(job_id)))

    def record_kill(self, *, job_id: str, sandbox: bool) -> None:
        self._require_identity()
        self._table_ref.get().apply_kill(
            user_id=self._user_id,
            org_id=self._org_id,
            sandbox=bool(sandbox),
            job_id=str(job_id),
        )


class _SessionWorkspaceDeliveryJobsPort:
    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        workspace: str,
        snapshot: DeliverySnapshot | None = None,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._snapshot = snapshot

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        try:
            table = self._table_ref.get()
            active = await asyncio.to_thread(
                table.query_session_active,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
                workspace=self._workspace,
            )
            if self._snapshot is not None:
                pending: tuple[dict[str, Any], ...] = self._snapshot.rows
                detail_limit: int | None = self._snapshot.detail_limit
            else:
                pending = ()
                detail_limit = None
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs(delivery) failed session_id=%s workspace=%s",
                query.session_id,
                self._workspace,
                exc_info=True,
            )
            return WorkspaceJobs.empty()
        return WorkspaceJobs(
            workspace=self._workspace,
            active_jobs=tuple(active),
            pending_terminal_jobs=tuple(pending),
            detail_limit=detail_limit,
        )


class _WorkspaceObservationJobsPort:
    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        workspace: str,
        detail_limit: int,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._detail_limit = detail_limit

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        try:
            table = self._table_ref.get()
            active, pending, recent = await asyncio.gather(
                asyncio.to_thread(
                    table.query_workspace_active,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                ),
                asyncio.to_thread(
                    table.query_workspace_pending_terminal,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                    limit=self._detail_limit,
                ),
                asyncio.to_thread(
                    table.query_workspace_recent_terminal,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                    limit=self._detail_limit,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs(observation) failed workspace=%s",
                self._workspace,
                exc_info=True,
            )
            return WorkspaceJobs.empty()
        return WorkspaceJobs(
            workspace=self._workspace,
            active_jobs=tuple(active),
            pending_terminal_jobs=tuple(pending),
            recent_terminal_jobs=tuple(recent),
            detail_limit=self._detail_limit,
        )


class _EmptyWorkspaceJobsPort:
    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        return WorkspaceJobs.empty()


def build_bohrium_jobs_ports(
    *,
    session_id: str,
    invocation_id: str | None,
    user_id: str,
    org_id: str,
    workspace: str | None,
    job_context_mode: str = "session_workspace_delivery",
    spawn_id: str | None = None,
    delivery_snapshot: DeliverySnapshot | None = None,
    table: BohriumJobsTable | None = None,
    table_factory: Callable[[], BohriumJobsTable] = get_bohrium_jobs_table,
) -> tuple[_BohriumJobLedger | None, WorkspaceJobsPort]:
    """构造写 port 与读 port（共享同一个 DAO 实例）。"""
    table_ref = _BohriumJobsTableRef(table=table, table_factory=table_factory)
    normalized_workspace = _normalize_ledger_workspace(workspace)
    ledger = (
        _BohriumJobLedger(
            table_ref=table_ref,
            session_id=session_id,
            invocation_id=invocation_id,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            spawn_id=spawn_id,
            observed_terminal=(
                delivery_snapshot.observed_terminal
                if delivery_snapshot is not None
                else None
            ),
        )
        if normalized_workspace is not None
        else None
    )
    if normalized_workspace is None or not (user_id and org_id):
        jobs: WorkspaceJobsPort = _EmptyWorkspaceJobsPort()
    elif job_context_mode == "workspace_observation":
        jobs = _WorkspaceObservationJobsPort(
            table_ref=table_ref,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20),
        )
    elif job_context_mode == "session_workspace_delivery":
        jobs = _SessionWorkspaceDeliveryJobsPort(
            table_ref=table_ref,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            snapshot=delivery_snapshot,
        )
    else:
        jobs = _EmptyWorkspaceJobsPort()
    return ledger, jobs
