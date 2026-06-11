"""service 层把 bohrium_jobs DAO 包成 kernel 端口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from matmaster.bohrium.status import to_ledger_status
from matmaster.context.ports import SessionJobs, SessionJobsQuery
from src.dao.bohrium_jobs_table import BohriumJobsTable, get_bohrium_jobs_table
from src.services.bohrium_delivery_ack import DeliverySnapshot
from src.services.session_directory_service import (
    SessionDirectoryError,
    normalize_remote_workspace_path,
)

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
    ) -> None:
        self._table_ref = table_ref
        self._session_id = session_id
        self._invocation_id = invocation_id
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._spawn_id = spawn_id

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

    def record_kill(self, *, job_id: str, sandbox: bool) -> None:
        self._require_identity()
        self._table_ref.get().apply_kill(
            user_id=self._user_id,
            org_id=self._org_id,
            sandbox=bool(sandbox),
            job_id=str(job_id),
        )


class _RunSessionJobsPort:
    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        snapshot: DeliverySnapshot | None = None,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._snapshot = snapshot

    async def load_session_jobs(self, query: SessionJobsQuery) -> SessionJobs:
        if not (self._user_id and self._org_id):
            return SessionJobs.empty()
        try:
            table = self._table_ref.get()
            active_call = asyncio.to_thread(
                table.query_session_active,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
            )
            if self._snapshot is not None:
                # 本轮交付边界固定：compaction 再调时返回同一 snapshot 的 pending
                active = await active_call
                pending = self._snapshot.rows
                detail_limit: int | None = self._snapshot.detail_limit
            else:
                active, rows = await asyncio.gather(
                    active_call,
                    asyncio.to_thread(
                        table.query_session_pending_terminal,
                        user_id=self._user_id,
                        org_id=self._org_id,
                        session_id=query.session_id,
                        limit=5,
                    ),
                )
                pending = tuple(rows)
                detail_limit = None
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_session_jobs failed session_id=%s",
                query.session_id,
                exc_info=True,
            )
            return SessionJobs.empty()
        return SessionJobs(
            active_jobs=tuple(active),
            pending_terminal_jobs=tuple(pending),
            detail_limit=detail_limit,
        )


def build_bohrium_jobs_ports(
    *,
    session_id: str,
    invocation_id: str | None,
    user_id: str,
    org_id: str,
    workspace: str | None,
    spawn_id: str | None = None,
    delivery_snapshot: DeliverySnapshot | None = None,
    table: BohriumJobsTable | None = None,
    table_factory: Callable[[], BohriumJobsTable] = get_bohrium_jobs_table,
) -> tuple[_BohriumJobLedger | None, _RunSessionJobsPort]:
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
        )
        if normalized_workspace is not None
        else None
    )
    jobs = _RunSessionJobsPort(
        table_ref=table_ref,
        user_id=user_id,
        org_id=org_id,
        snapshot=delivery_snapshot,
    )
    return ledger, jobs
