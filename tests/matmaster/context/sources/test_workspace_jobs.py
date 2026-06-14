from __future__ import annotations

import json

from matmaster.context.ports import WorkspaceJobs
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.workspace_jobs import WorkspaceJobsSource

DELIVERY_DIRECTIVE = (
    "请逐一拉取并核对以上已结束作业的结果：成功项汇总关键产出，失败项诊断原因，"
    "给出整体结论与下一步。处理完成即视为交付确认。"
)
OVERFLOW_DIRECTIVE_SUFFIX = (
    "（末尾 overflow 摘要中的 job_ids 同属本批次，请按其 status 一并处理。）"
)


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


def _job(job_id: str, status: str = "finished", sandbox: bool = False) -> dict:
    return {"job_id": job_id, "status": status, "sandbox": sandbox}


def test_delivery_jobs_render_listing_and_directive_sections() -> None:
    jobs = WorkspaceJobs(
        mode="delivery",
        pending_terminal_jobs=(_job("t9", "finished"),),
        active_jobs=(_job("a1", "running"),),
    )

    listing, directive = WorkspaceJobsSource.from_jobs(jobs).to_sections()

    assert listing.key == "workspace_jobs"
    assert listing.tag == "workspace_jobs"
    assert listing.order == SectionOrder.WORKSPACE_JOBS
    assert listing.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
    assert listing.content.splitlines() == [
        "以下 Bohrium 作业已结束、结果待处理（属于本轮交付确认范围）：",
        (
            'pending_terminal_job_1 '
            '{"job_id": "t9", "sandbox": false, "status": "finished"}'
        ),
        "以下 Bohrium 作业仍在运行（仅作上下文，无需处理）：",
        'active_job_1 {"job_id": "a1", "sandbox": false, "status": "running"}',
    ]
    assert directive.key == "delivery_directive"
    assert directive.tag == "delivery_directive"
    assert directive.order == SectionOrder.TURN_INSTRUCTION_LAST
    assert directive.views == frozenset({ContextView.RUNTIME})
    assert directive.content == DELIVERY_DIRECTIVE


def test_delivery_pending_overflow_extends_directive_scope() -> None:
    jobs = WorkspaceJobs(
        mode="delivery",
        pending_terminal_jobs=(
            _job("t1", "finished"),
            _job("t2", "failed"),
            _job("t3", "stopped"),
        ),
        detail_limit=1,
    )

    listing, directive = WorkspaceJobsSource.from_jobs(jobs).to_sections()

    assert "pending_terminal_overflow" in listing.content
    assert directive.content == DELIVERY_DIRECTIVE + OVERFLOW_DIRECTIVE_SUFFIX


def test_delivery_without_pending_overflow_uses_plain_directive() -> None:
    jobs = WorkspaceJobs(
        mode="delivery",
        pending_terminal_jobs=(_job("t1"), _job("t2")),
        detail_limit=2,
    )

    _, directive = WorkspaceJobsSource.from_jobs(jobs).to_sections()

    assert directive.content == DELIVERY_DIRECTIVE
    assert OVERFLOW_DIRECTIVE_SUFFIX not in directive.content


def test_delivery_active_overflow_does_not_extend_directive_scope() -> None:
    jobs = WorkspaceJobs(
        mode="delivery",
        pending_terminal_jobs=(_job("t1"),),
        active_jobs=(_job("a1", "running"), _job("a2", "submitted")),
        detail_limit=1,
    )

    listing, directive = WorkspaceJobsSource.from_jobs(jobs).to_sections()

    assert "active_overflow" in listing.content
    assert directive.content == DELIVERY_DIRECTIVE
    assert OVERFLOW_DIRECTIVE_SUFFIX not in directive.content


def test_delivery_pending_listing_covers_all_pending_jobs_in_order() -> None:
    pending = tuple(_job(f"t{index}") for index in range(5))
    jobs = WorkspaceJobs(
        mode="delivery",
        pending_terminal_jobs=pending,
        detail_limit=2,
    )

    listing = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0]
    pending_ids: list[str] = []
    for line in listing.content.splitlines():
        if line.startswith("pending_terminal_job_"):
            pending_ids.append(json.loads(line.split(" ", 1)[1])["job_id"])
        if line.startswith("pending_terminal_overflow "):
            pending_ids.extend(json.loads(line.split(" ", 1)[1])["job_ids"])

    assert pending_ids == [job["job_id"] for job in pending]


def test_delivery_active_only_renders_no_directive() -> None:
    jobs = WorkspaceJobs(
        mode="delivery",
        active_jobs=(_job("a1", "running"),),
    )

    sections = WorkspaceJobsSource.from_jobs(jobs).to_sections()

    assert len(sections) == 1
    assert sections[0].key == "workspace_jobs"
    assert "delivery_directive" not in sections[0].content


def test_delivery_empty_jobs_render_no_sections() -> None:
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
