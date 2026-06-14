from __future__ import annotations

import json

from matmaster.context.ports import WorkspaceJobs
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.workspace_jobs import WorkspaceJobsSource


def test_workspace_jobs_empty_returns_no_sections() -> None:
    assert WorkspaceJobsSource.from_jobs(WorkspaceJobs.empty()).to_sections() == ()


def test_workspace_jobs_mode_defaults_to_observation() -> None:
    assert WorkspaceJobs().mode == "observation"
    assert WorkspaceJobs.empty().mode == "observation"
    assert WorkspaceJobs(mode="delivery").mode == "delivery"


def test_workspace_jobs_renders_active_and_pending_terminal() -> None:
    jobs = WorkspaceJobs(
        active_jobs=(
            {"job_id": "a2", "status": "running"},
            {"job_id": "a1", "status": "submitted"},
        ),
        pending_terminal_jobs=({"job_id": "t9", "status": "finished"},),
    )

    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]

    assert section.key == "workspace_jobs"
    assert section.tag == "workspace_jobs"
    assert section.order == SectionOrder.WORKSPACE_JOBS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
    assert section.content == (
        'active_job_1 {"job_id": "a2", "status": "running"}\n'
        'active_job_2 {"job_id": "a1", "status": "submitted"}\n'
        'pending_terminal_job_1 {"job_id": "t9", "status": "finished"}'
    )


def test_workspace_header_prefixes_groups_when_workspace_present() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/w1",
        active_jobs=({"job_id": "a1", "status": "running"},),
        recent_terminal_jobs=({"job_id": "r1", "status": "finished"},),
    )
    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == (
        "workspace /share/w1\n"
        'active_job_1 {"job_id": "a1", "status": "running"}\n'
        'recent_terminal_job_1 {"job_id": "r1", "status": "finished"}'
    )


def test_workspace_header_omitted_when_no_jobs() -> None:
    jobs = WorkspaceJobs(workspace="/share/w1")
    assert WorkspaceJobsSource.from_jobs(jobs).to_sections() == ()


def test_recent_terminal_group_renders() -> None:
    jobs = WorkspaceJobs(recent_terminal_jobs=({"job_id": "r9", "status": "finished"},))
    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == (
        'recent_terminal_job_1 {"job_id": "r9", "status": "finished"}'
    )


def test_workspace_jobs_only_active_renders_without_terminal_lines() -> None:
    jobs = WorkspaceJobs(active_jobs=({"job_id": "a1", "status": "running"},))
    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == 'active_job_1 {"job_id": "a1", "status": "running"}'


def test_workspace_jobs_only_pending_terminal_renders() -> None:
    jobs = WorkspaceJobs(pending_terminal_jobs=({"job_id": "t1", "status": "failed"},))
    section = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == (
        'pending_terminal_job_1 {"job_id": "t1", "status": "failed"}'
    )


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
        mode="delivery",
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


def test_delivery_instruction_lists_all_pending_jobs_without_overflow() -> None:
    jobs = WorkspaceJobs(
        mode="delivery",
        pending_terminal_jobs=(
            _job("t1", "finished", job_name="one"),
            _job("t2", "failed", job_name="two"),
            _job("t3", "stopped", job_name="three"),
        ),
        detail_limit=1,
    )

    text = WorkspaceJobsSource.delivery_instruction_text(jobs)

    assert "pending_terminal_overflow" not in text
    assert "t1, one" in text
    assert "t2, two" in text
    assert "t3, three" in text


def test_delivery_instruction_covers_all_pending_jobs_in_order() -> None:
    pending = tuple(_job(f"t{index}") for index in range(5))
    jobs = WorkspaceJobs(
        mode="delivery",
        pending_terminal_jobs=pending,
        detail_limit=2,
    )

    lines = WorkspaceJobsSource.delivery_instruction_text(jobs).splitlines()
    ids = [
        line.split(", ", 1)[0]
        for line in lines
        if line.startswith("t") and ", " in line
    ]

    assert ids == [job["job_id"] for job in pending]


def test_delivery_active_only_renders_no_instruction_text_or_sections() -> None:
    jobs = WorkspaceJobs(
        mode="delivery",
        active_jobs=(_job("a1", "running"),),
    )

    assert WorkspaceJobsSource.delivery_instruction_text(jobs) == ""
    assert WorkspaceJobsSource.from_jobs(jobs).to_sections() == ()


def test_delivery_empty_jobs_render_no_sections() -> None:
    assert (
        WorkspaceJobsSource.delivery_instruction_text(WorkspaceJobs(mode="delivery"))
        == ""
    )
    assert (
        WorkspaceJobsSource.from_jobs(WorkspaceJobs(mode="delivery")).to_sections()
        == ()
    )


def test_observation_mode_does_not_render_delivery_directive() -> None:
    jobs = WorkspaceJobs(
        mode="observation",
        pending_terminal_jobs=(_job("t1", "failed"),),
    )

    sections = WorkspaceJobsSource.from_jobs(jobs).to_sections()

    assert len(sections) == 1
    assert sections[0].key == "workspace_jobs"
    assert sections[0].content.startswith("pending_terminal_job_1 ")


def test_detail_limit_compresses_pending_with_overflow_summary() -> None:
    jobs = WorkspaceJobs(
        pending_terminal_jobs=(
            _job("f1", "failed"),
            _job("t1"),
            _job("t2"),
            _job("t3", "stopped"),
        ),
        detail_limit=2,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    assert lines[0].startswith('pending_terminal_job_1 {"job_id": "f1"')
    assert lines[1].startswith('pending_terminal_job_2 {"job_id": "t1"')
    assert len(lines) == 3
    assert lines[2] == (
        'pending_terminal_overflow '
        '{"by_status": {"finished": 1, "stopped": 1}, '
        '"count": 2, "job_ids": ["t2", "t3"]}'
    )


def test_detail_limit_compresses_active_independently() -> None:
    jobs = WorkspaceJobs(
        active_jobs=(
            _job("a1", "running"),
            _job("a2", "running"),
            _job("a3", "submitted"),
        ),
        pending_terminal_jobs=(_job("t1"),),
        detail_limit=1,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    assert lines[0].startswith("active_job_1 ")
    assert lines[1] == (
        'active_overflow '
        '{"by_status": {"running": 1, "submitted": 1}, '
        '"count": 2, "job_ids": ["a2", "a3"]}'
    )
    assert lines[2].startswith("pending_terminal_job_1 ")
    assert len(lines) == 3


def test_detail_limit_covers_all_ids_between_detail_and_overflow() -> None:
    all_ids = [f"j{i}" for i in range(7)]
    jobs = WorkspaceJobs(
        pending_terminal_jobs=tuple(_job(i) for i in all_ids),
        detail_limit=3,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    detail_ids = [json.loads(line.split(" ", 1)[1])["job_id"] for line in lines[:3]]
    overflow = json.loads(lines[3].split(" ", 1)[1])
    assert detail_ids + overflow["job_ids"] == all_ids


def test_detail_limit_no_overflow_when_limit_covers_all() -> None:
    jobs = WorkspaceJobs(
        pending_terminal_jobs=(_job("t1"), _job("t2")),
        detail_limit=2,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines
    assert len(lines) == 2
    assert not any("overflow" in line for line in lines)


def test_overflow_job_ids_keep_same_job_id_across_sandboxes() -> None:
    jobs = WorkspaceJobs(
        pending_terminal_jobs=(
            _job("keep"),
            _job("dup", sandbox=False),
            _job("dup", sandbox=True),
        ),
        detail_limit=1,
    )
    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    overflow = json.loads(lines[1].split(" ", 1)[1])
    assert overflow["count"] == 2
    assert overflow["job_ids"] == ["dup", "dup"]
