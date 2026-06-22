from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
    WorkspaceJobsSummary,
)


def test_workspace_jobs_new_fields_default() -> None:
    jobs = WorkspaceJobs()
    assert jobs.mode is None
    assert jobs.summary is None
    assert jobs.export is None
    assert jobs.export_error is None
    assert jobs.active_jobs == ()
    assert jobs.unhandled_terminal_jobs == ()
    assert jobs.handled_recent_terminal_jobs == ()
    assert jobs.required_error is None
    assert jobs.preview_limit is None
    assert jobs.preview_rows == ()
    assert jobs.omitted_count is None
    assert jobs.required_truncated is False
    assert jobs.handled_recent_has_more is False
    assert jobs.handled_recent_unavailable is False


def test_export_metadata_constructs() -> None:
    export = WorkspaceJobsExport(
        path="/w/.matmaster/context/workspace_jobs/s-i.csv",
        format="csv",
        row_count=1020,
        columns=("group", "job_id"),
        reason="row_limit",
    )
    assert export.row_count == 1020
    assert export.reason == "row_limit"


def test_summary_and_error_construct() -> None:
    summary = WorkspaceJobsSummary(
        total=3,
        active=2,
        unhandled_terminal=1,
        handled_recent_terminal=0,
        by_status={"running": 2, "failed": 1},
        failed=1,
        stopped=0,
        lost=0,
        unhandled_action=1,
    )
    err = WorkspaceJobsExportError(
        reason="write_failed", rows=3, target_path="/w/x.csv"
    )
    assert summary.total == 3
    assert summary.unhandled_terminal == 1
    assert summary.by_status["failed"] == 1
    assert err.reason == "write_failed"
