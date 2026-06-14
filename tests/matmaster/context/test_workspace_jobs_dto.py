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
    assert jobs.priority_samples == ()
    assert jobs.omitted_count is None
    assert jobs.snapshot_truncated is False


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
        pending_terminal=1,
        recent_terminal=0,
        by_status={"running": 2, "failed": 1},
        failed=1,
        stopped=0,
        lost=0,
    )
    err = WorkspaceJobsExportError(
        reason="write_failed", rows=3, target_path="/w/x.csv"
    )
    assert summary.total == 3
    assert summary.by_status["failed"] == 1
    assert err.reason == "write_failed"
