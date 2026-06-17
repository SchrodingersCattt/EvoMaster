"""service 层把 bohrium_jobs DAO 包成 kernel 端口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from matmaster.bohrium.status import to_ledger_status
from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
    WorkspaceJobsPort,
    WorkspaceJobsQuery,
)
from matmaster.context.workspace_jobs_compute import (
    PREVIEW_COLUMNS,
    PREVIEW_FIELD_CHAR_LIMIT,
    compute_inline_chars,
    compute_summary,
    select_delivery_preview_rows,
    select_observation_preview_rows,
    trim_preview_rows_to_char_limit,
)
from src.dao.bohrium_jobs_table import BohriumJobsTable, get_bohrium_jobs_table
from src.services.bohrium_delivery_ack import DeliverySnapshot
from src.services.session_directory_service import (
    SessionDirectoryError,
    normalize_remote_workspace_path,
)
from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter
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
    """delivery：只围绕本 session 的 snapshot.rows，只用 row 阈值。

    未超阈值返回含完整 unhandled_terminal_jobs 的 WorkspaceJobs；超阈值仅选
    action preview 并导出 CSV；导出失败时写 snapshot.export_failure。
    """

    def __init__(
        self,
        *,
        workspace: str,
        snapshot: DeliverySnapshot | None,
        exporter: WorkspaceJobsCsvExporter,
        prompt_preview_limit: int,
    ) -> None:
        self._workspace = workspace
        self._snapshot = snapshot
        self._exporter = exporter
        self._prompt_preview_limit = prompt_preview_limit

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        pending: tuple[dict[str, Any], ...] = (
            self._snapshot.rows if self._snapshot is not None else ()
        )
        summary = compute_summary((), pending, ())
        if len(pending) <= self._prompt_preview_limit:
            return WorkspaceJobs(
                workspace=self._workspace,
                unhandled_terminal_jobs=pending,
                summary=summary,
                mode="session_workspace_delivery",
            )
        preview_rows = select_delivery_preview_rows(
            pending, limit=self._prompt_preview_limit
        )
        export_input = WorkspaceJobs(
            workspace=self._workspace,
            unhandled_terminal_jobs=pending,
        )
        result = self._exporter.export(export_input, reason="row_limit")
        export: WorkspaceJobsExport | None
        export_error: WorkspaceJobsExportError | None
        if isinstance(result, WorkspaceJobsExportError):
            self._record_export_failure(result)
            export, export_error = None, result
        else:
            export, export_error = result, None
        return WorkspaceJobs(
            workspace=self._workspace,
            summary=summary,
            preview_limit=self._prompt_preview_limit,
            preview_rows=preview_rows,
            export=export,
            export_error=export_error,
            mode="session_workspace_delivery",
        )

    def _record_export_failure(self, err: WorkspaceJobsExportError) -> None:
        if self._snapshot is None:
            return
        self._snapshot.export_failure.update(err.as_meta())


class _WorkspaceObservationJobsPort:
    """observation：跨 session required/reference 三 bucket，row+char 双阈值。"""

    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        workspace: str,
        exporter: WorkspaceJobsCsvExporter,
        snapshot: DeliverySnapshot | None,
        required_fetch_limit: int,
        handled_recent_limit: int,
        prompt_preview_limit: int,
        char_limit: int,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._exporter = exporter
        self._snapshot = snapshot
        self._required_fetch_limit = required_fetch_limit
        self._handled_recent_limit = handled_recent_limit
        self._prompt_preview_limit = prompt_preview_limit
        self._char_limit = char_limit

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        try:
            table = self._table_ref.get()
        except Exception as exc:  # noqa: BLE001
            return self._required_unavailable(exc)
        active_res, unhandled_res, handled_res = await asyncio.gather(
            asyncio.to_thread(
                table.query_workspace_active,
                user_id=self._user_id,
                org_id=self._org_id,
                workspace=self._workspace,
                limit=self._required_fetch_limit + 1,
            ),
            asyncio.to_thread(
                table.query_workspace_unhandled_terminal,
                user_id=self._user_id,
                org_id=self._org_id,
                workspace=self._workspace,
                limit=self._required_fetch_limit + 1,
            ),
            asyncio.to_thread(
                table.query_workspace_handled_recent_terminal,
                user_id=self._user_id,
                org_id=self._org_id,
                workspace=self._workspace,
                limit=self._handled_recent_limit + 1,
            ),
            return_exceptions=True,
        )
        if isinstance(active_res, BaseException):
            return self._required_unavailable(active_res)
        if isinstance(unhandled_res, BaseException):
            return self._required_unavailable(unhandled_res)
        active_raw, unhandled_raw = active_res, unhandled_res
        if isinstance(handled_res, BaseException):
            logger.warning(
                "load_workspace_jobs(observation handled_recent) failed workspace=%s",
                self._workspace,
                exc_info=handled_res,
            )
            handled_recent_raw: list[dict[str, Any]] = []
            handled_recent_unavailable = True
        else:
            handled_recent_raw = handled_res
            handled_recent_unavailable = False

        active = tuple(active_raw[: self._required_fetch_limit])
        unhandled = tuple(unhandled_raw[: self._required_fetch_limit])
        handled_recent = tuple(handled_recent_raw[: self._handled_recent_limit])
        active_truncated = len(active_raw) > self._required_fetch_limit
        unhandled_truncated = len(unhandled_raw) > self._required_fetch_limit
        required_truncated = active_truncated or unhandled_truncated
        handled_recent_has_more = len(handled_recent_raw) > self._handled_recent_limit
        if required_truncated:
            self._write_required_block(
                reason="required_truncated",
                active_truncated=active_truncated,
                unhandled_terminal_truncated=unhandled_truncated,
            )

        summary = compute_summary(active, unhandled, handled_recent)
        full = WorkspaceJobs(
            workspace=self._workspace,
            active_jobs=active,
            unhandled_terminal_jobs=unhandled,
            handled_recent_terminal_jobs=handled_recent,
            summary=summary,
            mode="workspace_observation",
            required_truncated=required_truncated,
            handled_recent_has_more=handled_recent_has_more,
            handled_recent_unavailable=handled_recent_unavailable,
        )
        snapshot_total = len(active) + len(unhandled) + len(handled_recent)
        if (
            snapshot_total <= self._prompt_preview_limit
            and compute_inline_chars(full) <= self._char_limit
        ):
            return full
        preview_rows = select_observation_preview_rows(
            active=active,
            unhandled_terminal=unhandled,
            handled_recent_terminal=handled_recent,
            limit=self._prompt_preview_limit,
        )
        preview_rows = trim_preview_rows_to_char_limit(
            preview_rows,
            columns=PREVIEW_COLUMNS,
            char_limit=self._char_limit,
        )
        reason = (
            "row_limit" if snapshot_total > self._prompt_preview_limit else "char_limit"
        )
        result = self._exporter.export(full, reason=reason)
        export: WorkspaceJobsExport | None
        export_error: WorkspaceJobsExportError | None
        if isinstance(result, WorkspaceJobsExportError):
            self._record_export_failure(result)
            export, export_error, omitted = None, result, None
        else:
            export, export_error, omitted = (
                result,
                None,
                snapshot_total - len(preview_rows),
            )
        return WorkspaceJobs(
            workspace=self._workspace,
            summary=summary,
            preview_limit=self._prompt_preview_limit,
            preview_rows=preview_rows,
            export=export,
            export_error=export_error,
            omitted_count=omitted,
            mode="workspace_observation",
            required_truncated=required_truncated,
            handled_recent_has_more=handled_recent_has_more,
            handled_recent_unavailable=handled_recent_unavailable,
        )

    def _required_unavailable(self, exc: BaseException) -> WorkspaceJobs:
        logger.warning(
            "load_workspace_jobs(observation required) failed workspace=%s",
            self._workspace,
            exc_info=exc,
        )
        self._write_required_block(reason="query_failed")
        return WorkspaceJobs(
            workspace=self._workspace,
            mode="workspace_observation",
            required_error={"reason": "query_failed"},
        )

    def _write_required_block(self, *, reason: str, **extra: Any) -> None:
        if self._snapshot is not None:
            self._snapshot.required_block.update({"reason": reason, **extra})

    def _record_export_failure(self, err: WorkspaceJobsExportError) -> None:
        if self._snapshot is None:
            return
        self._snapshot.export_failure.update(err.as_meta())


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
    exporter: WorkspaceJobsCsvExporter,
    job_context_mode: str = "session_workspace_delivery",
    spawn_id: str | None = None,
    delivery_snapshot: DeliverySnapshot | None = None,
    table: BohriumJobsTable | None = None,
    table_factory: Callable[[], BohriumJobsTable] = get_bohrium_jobs_table,
) -> tuple[_BohriumJobLedger | None, WorkspaceJobsPort]:
    """构造写 port 与读 port（共享同一个 DAO 实例）。"""
    table_ref = _BohriumJobsTableRef(table=table, table_factory=table_factory)
    normalized_workspace = _normalize_ledger_workspace(workspace)
    required_fetch_limit = env_int("BOHRIUM_WORKSPACE_JOBS_REQUIRED_FETCH_LIMIT", 2000)
    handled_recent_limit = env_int("BOHRIUM_WORKSPACE_JOBS_HANDLED_RECENT_LIMIT", 20)
    prompt_preview_limit = env_int("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", 50)
    char_limit = min(prompt_preview_limit * PREVIEW_FIELD_CHAR_LIMIT, 24000)
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
            exporter=exporter,
            snapshot=delivery_snapshot,
            required_fetch_limit=required_fetch_limit,
            handled_recent_limit=handled_recent_limit,
            prompt_preview_limit=prompt_preview_limit,
            char_limit=char_limit,
        )
    elif job_context_mode == "session_workspace_delivery":
        jobs = _SessionWorkspaceDeliveryJobsPort(
            workspace=normalized_workspace,
            snapshot=delivery_snapshot,
            exporter=exporter,
            prompt_preview_limit=prompt_preview_limit,
        )
    else:
        jobs = _EmptyWorkspaceJobsPort()
    return ledger, jobs
