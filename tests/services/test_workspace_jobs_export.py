from unittest.mock import MagicMock

from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
)
from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter


def _jobs() -> WorkspaceJobs:
    return WorkspaceJobs(
        workspace="/share/p",
        active_jobs=({"job_id": "a1", "status": "running"},),
        unhandled_terminal_jobs=({"id": 1, "job_id": "p1", "status": "failed"},),
    )


def _exporter(session, *, workdir="/share/p") -> WorkspaceJobsCsvExporter:
    return WorkspaceJobsCsvExporter(
        session=session,
        execution_workdir=workdir,
        session_id="sess 123",  # 含空格，验证 slug
        invocation_id="inv-456",
        task_id="task-789",
    )


def test_export_writes_csv_and_returns_metadata() -> None:
    session = MagicMock()
    result = _exporter(session).export(_jobs(), reason="row_limit")
    assert isinstance(result, WorkspaceJobsExport)
    assert result.path == (
        "/share/p/.matmaster/context/workspace_jobs/sess_123-inv-456.csv"
    )
    assert result.row_count == 2
    assert result.reason == "row_limit"
    args, kwargs = session.write_file.call_args
    assert args[0] == result.path
    assert kwargs == {"encoding": "utf-8"}
    assert args[1].splitlines()[0].startswith("group,job_id")


def test_export_uses_task_id_when_invocation_missing() -> None:
    session = MagicMock()
    exporter = WorkspaceJobsCsvExporter(
        session=session,
        execution_workdir="/share/p",
        session_id="s",
        invocation_id=None,
        task_id="task-789",
    )
    result = exporter.export(_jobs(), reason="char_limit")
    assert result.path.endswith("/s-task-789.csv")


def test_export_session_missing() -> None:
    result = _exporter(None).export(_jobs(), reason="row_limit")
    assert isinstance(result, WorkspaceJobsExportError)
    assert result.reason == "session_missing"
    assert result.rows == 2


def test_export_bad_target_path_when_workdir_empty() -> None:
    session = MagicMock()
    result = _exporter(session, workdir="").export(_jobs(), reason="row_limit")
    assert isinstance(result, WorkspaceJobsExportError)
    assert result.reason == "bad_target_path"
    session.write_file.assert_not_called()


def test_export_write_failed() -> None:
    session = MagicMock()
    session.write_file.side_effect = OSError("disk full")
    result = _exporter(session).export(_jobs(), reason="row_limit")
    assert isinstance(result, WorkspaceJobsExportError)
    assert result.reason == "write_failed"
