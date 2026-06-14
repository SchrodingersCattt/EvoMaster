from __future__ import annotations

import logging
import re
from typing import Literal

from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
)
from matmaster.context.workspace_jobs_compute import (
    CSV_COLUMNS,
    build_csv_rows,
    build_csv_text,
)
from matmaster.types.session import Session

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(value: str) -> str:
    return _UNSAFE.sub("_", value)


class WorkspaceJobsCsvExporter:
    """把 workspace job 完整明细导出为当前 session 文件系统下的 CSV。"""

    def __init__(
        self,
        *,
        session: Session | None,
        execution_workdir: str,
        session_id: str,
        invocation_id: str | None,
        task_id: str | None,
    ) -> None:
        self._session = session
        self._execution_workdir = execution_workdir
        self._session_id = session_id
        self._invocation_id = invocation_id
        self._task_id = task_id

    def export(
        self, jobs: WorkspaceJobs, *, reason: Literal["row_limit", "char_limit"]
    ) -> WorkspaceJobsExport | WorkspaceJobsExportError:
        rows = build_csv_rows(
            jobs.active_jobs, jobs.pending_terminal_jobs, jobs.recent_terminal_jobs
        )
        row_count = len(rows)
        target = self._target_path()
        if self._session is None:
            return self._error("session_missing", row_count, target)
        if not self._under_workdir(target):
            return self._error("bad_target_path", row_count, target)
        try:
            text = build_csv_text(rows)
        except Exception:  # noqa: BLE001
            logger.warning(
                "workspace jobs csv serialize failed session_id=%s workspace=%s "
                "rows=%d target_path=%s",
                self._session_id, jobs.workspace, row_count, target, exc_info=True,
            )
            return self._error("serialize_failed", row_count, target)
        try:
            self._session.write_file(target, text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.warning(
                "workspace jobs csv write failed session_id=%s workspace=%s "
                "rows=%d target_path=%s",
                self._session_id, jobs.workspace, row_count, target, exc_info=True,
            )
            return self._error("write_failed", row_count, target)
        return WorkspaceJobsExport(
            path=target,
            format="csv",
            row_count=row_count,
            columns=CSV_COLUMNS,
            reason=reason,
        )

    def _target_path(self) -> str:
        suffix = self._invocation_id or self._task_id or "run"
        name = f"{_slug(self._session_id)}-{_slug(suffix)}.csv"
        base = self._execution_workdir.rstrip("/")
        return f"{base}/.matmaster/context/workspace_jobs/{name}"

    def _under_workdir(self, target: str) -> bool:
        base = self._execution_workdir.rstrip("/")
        return bool(base) and target.startswith(base + "/")

    @staticmethod
    def _error(
        reason: Literal[
            "session_missing", "bad_target_path", "write_failed", "serialize_failed"
        ],
        rows: int,
        target: str,
    ) -> WorkspaceJobsExportError:
        return WorkspaceJobsExportError(reason=reason, rows=rows, target_path=target)
