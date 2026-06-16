from __future__ import annotations

from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
    WorkspaceJobsSummary,
)
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.workspace_jobs import WorkspaceJobsSource


def _summary() -> WorkspaceJobsSummary:
    return WorkspaceJobsSummary(
        total=2,
        active=1,
        pending_terminal=1,
        recent_terminal=0,
        by_status={"running": 1, "failed": 1},
        failed=1,
        stopped=0,
        lost=0,
    )


# ---- observation: inline 态 ----
def test_inline_renders_summary_and_columnar_details() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        active_jobs=({"job_id": "a1", "job_name": "n1", "status": "running"},),
        pending_terminal_jobs=({"job_id": "p1", "job_name": "n2", "status": "failed"},),
        mode="workspace_observation",
        summary=_summary(),
    )
    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.key == "workspace-jobs"
    assert section.tag == "workspace-jobs"
    assert section.order == SectionOrder.WORKSPACE_JOBS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
    lines = section.content.splitlines()
    assert lines[0] == "workspace /share/p"
    assert lines[1] == "mode workspace_observation"
    assert lines[2].startswith("summary {")
    assert lines[3] == "active job_id,job_name,status"
    assert lines[4] == "a1,n1,running"
    assert "pending_terminal job_id,job_name,status" in section.content
    assert "p1,n2,failed" in section.content


# ---- observation: compact 态 ----
def test_compact_renders_export_samples_omitted() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        summary=_summary(),
        export=WorkspaceJobsExport(
            path="/share/p/.matmaster/context/workspace_jobs/s-i.csv",
            format="csv",
            row_count=1020,
            columns=("group", "job_id"),
            reason="row_limit",
        ),
        priority_samples=({"job_id": "p1", "job_name": "n2", "status": "failed"},),
        omitted_count=1019,
    )
    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content
    assert "details_exported {" in content
    assert "read_hint " in content
    assert "action_hint " in content  # summary.failed=1
    assert "priority_samples job_id,job_name,status" in content
    assert "p1,n2,failed" in content
    assert "omitted_from_prompt {" in content
    assert "active job_id" not in content  # compact 不渲染 active 明细 block


# ---- observation: error 态 ----
def test_error_renders_export_error_not_details() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        summary=_summary(),
        export_error=WorkspaceJobsExportError(
            reason="write_failed", rows=1000, target_path="/share/p/x.csv"
        ),
    )
    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content
    assert "workspace_jobs_export_error {" in content
    assert "details_exported" not in content
    assert "do not assume omitted pending jobs were delivered" in content


def test_empty_jobs_render_nothing() -> None:
    assert WorkspaceJobsSource.from_jobs(WorkspaceJobs()).to_sections() == ()


# ---- delivery: 仍渲染为 turn instruction 文本，不在本 section ----
def _job(
    job_id: str,
    status: str = "finished",
    sandbox: bool = False,
    job_name: str | None = None,
) -> dict:
    row = {"job_id": job_id, "status": status, "sandbox": sandbox}
    if job_name is not None:
        row["job_name"] = job_name
    return row


def test_delivery_jobs_render_current_instruction_template_text() -> None:
    jobs = WorkspaceJobs(
        mode="session_workspace_delivery",
        pending_terminal_jobs=(
            _job("f1", "failed", job_name="relax-fail"),
            _job("s1", "stopped", job_name="relax-stop"),
            _job("l1", "lost", job_name="relax-lost"),
            _job("t9", "finished", job_name="relax-ok"),
        ),
        active_jobs=(_job("a1", "running", job_name="relax-running"),),
    )

    text = WorkspaceJobsSource.delivery_instruction_text(jobs)

    assert text.splitlines() == [
        "以下作业失败：",
        "job_id, job_name",
        "f1, relax-fail",
        "s1, relax-stop",
        "l1, relax-lost",
        "",
        "以下作业成功结束：",
        "job_id, job_name",
        "t9, relax-ok",
    ]
    assert "relax-running" not in text


def test_delivery_instruction_lists_all_pending_jobs() -> None:
    jobs = WorkspaceJobs(
        mode="session_workspace_delivery",
        pending_terminal_jobs=(
            _job("t1", "finished", job_name="one"),
            _job("t2", "failed", job_name="two"),
            _job("t3", "stopped", job_name="three"),
        ),
    )

    text = WorkspaceJobsSource.delivery_instruction_text(jobs)

    assert "t1, one" in text
    assert "t2, two" in text
    assert "t3, three" in text


def test_delivery_active_only_renders_no_instruction_or_section() -> None:
    jobs = WorkspaceJobs(
        mode="session_workspace_delivery",
        active_jobs=(_job("a1", "running"),),
    )

    assert WorkspaceJobsSource.delivery_instruction_text(jobs) == ""
    assert WorkspaceJobsSource.from_jobs(jobs).to_sections() == ()


def test_delivery_empty_jobs_render_no_sections() -> None:
    assert (
        WorkspaceJobsSource.delivery_instruction_text(
            WorkspaceJobs(mode="session_workspace_delivery")
        )
        == ""
    )
    assert (
        WorkspaceJobsSource.from_jobs(
            WorkspaceJobs(mode="session_workspace_delivery")
        ).to_sections()
        == ()
    )


def test_delivery_compact_renders_failed_samples_success_count_and_path() -> None:
    jobs = WorkspaceJobs(
        mode="session_workspace_delivery",
        summary=WorkspaceJobsSummary(
            total=982,
            active=0,
            pending_terminal=982,
            recent_terminal=0,
            by_status={"finished": 980, "failed": 2},
            failed=2,
            stopped=0,
            lost=0,
        ),
        priority_samples=(
            _job("f1", "failed", job_name="relax-fail"),
            _job("l3", "lost", job_name="relax-lost"),
        ),
        export=WorkspaceJobsExport(
            path="/share/p/.matmaster/context/workspace_jobs/s-i.csv",
            format="csv",
            row_count=982,
            columns=("group", "job_id"),
            reason="row_limit",
        ),
    )

    text = WorkspaceJobsSource.delivery_instruction_text(jobs)

    assert "以下作业失败：" in text
    assert "f1, relax-fail" in text
    assert "l3, relax-lost" in text
    assert "以下作业成功结束：共 980 个（详见导出文件）" in text
    assert "/share/p/.matmaster/context/workspace_jobs/s-i.csv" in text
    assert "Read 或 Bash" in text


def test_delivery_export_failure_renders_samples_and_warning_no_path() -> None:
    jobs = WorkspaceJobs(
        mode="session_workspace_delivery",
        summary=WorkspaceJobsSummary(
            total=600,
            active=0,
            pending_terminal=600,
            recent_terminal=0,
            by_status={"finished": 599, "failed": 1},
            failed=1,
            stopped=0,
            lost=0,
        ),
        priority_samples=(_job("f1", "failed", job_name="relax-fail"),),
        export_error=WorkspaceJobsExportError(
            reason="write_failed", rows=600, target_path="/share/p/x.csv"
        ),
    )

    text = WorkspaceJobsSource.delivery_instruction_text(jobs)

    assert "f1, relax-fail" in text
    assert "完整明细导出失败，被省略的作业未必已交付。" in text
    assert "/share/p/x.csv" not in text
    assert "已导出" not in text


def test_compact_truncated_renders_snapshot_hint() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        summary=_summary(),
        snapshot_truncated=True,
        export=WorkspaceJobsExport(
            path="/share/p/.matmaster/context/workspace_jobs/s-i.csv",
            format="csv",
            row_count=3000,
            columns=("group", "job_id"),
            reason="row_limit",
        ),
    )

    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    assert any("snapshot_truncated_hint" in line for line in lines)
