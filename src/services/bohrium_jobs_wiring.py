"""service 层把 bohrium_jobs DAO 包成 kernel 端口。"""

from __future__ import annotations

import asyncio
import logging

from matmaster.bohrium.status import to_ledger_status
from matmaster.context.ports import SessionJobs, SessionJobsQuery
from src.dao.bohrium_jobs_table import BohriumJobsTable

logger = logging.getLogger(__name__)

_FOREGROUND_POLL_BACKOFF_SECONDS = 30


class _BohriumJobLedger:
    def __init__(
        self,
        *,
        table: BohriumJobsTable,
        session_id: str,
        invocation_id: str | None,
        user_id: str,
        org_id: str,
        spawn_id: str | None = None,
    ) -> None:
        self._table = table
        self._session_id = session_id
        self._invocation_id = invocation_id
        self._user_id = user_id
        self._org_id = org_id
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
        self._table.insert_submitted(
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
        self._table.apply_poll(
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
        self._table.apply_kill(
            user_id=self._user_id,
            org_id=self._org_id,
            sandbox=bool(sandbox),
            job_id=str(job_id),
        )

    def mark_handled(self, *, job_id: str, sandbox: bool) -> None:
        self._require_identity()
        self._table.mark_handled(
            user_id=self._user_id,
            org_id=self._org_id,
            sandbox=bool(sandbox),
            job_id=str(job_id),
        )


class _RunSessionJobsPort:
    def __init__(self, *, table: BohriumJobsTable, user_id: str, org_id: str) -> None:
        self._table = table
        self._user_id = user_id
        self._org_id = org_id

    async def load_session_jobs(self, query: SessionJobsQuery) -> SessionJobs:
        if not (self._user_id and self._org_id):
            return SessionJobs.empty()
        try:
            active = await asyncio.to_thread(
                self._table.query_session_active,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
            )
            pending = await asyncio.to_thread(
                self._table.query_session_pending_terminal,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
                limit=5,
            )
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
        )


def build_bohrium_jobs_ports(
    *,
    session_id: str,
    invocation_id: str | None,
    user_id: str,
    org_id: str,
    spawn_id: str | None = None,
    table: BohriumJobsTable | None = None,
) -> tuple[_BohriumJobLedger, _RunSessionJobsPort]:
    """构造写 port 与读 port（共享同一个 DAO 实例）。"""
    table = table if table is not None else BohriumJobsTable()
    ledger = _BohriumJobLedger(
        table=table,
        session_id=session_id,
        invocation_id=invocation_id,
        user_id=user_id,
        org_id=org_id,
        spawn_id=spawn_id,
    )
    jobs = _RunSessionJobsPort(table=table, user_id=user_id, org_id=org_id)
    return ledger, jobs
