from __future__ import annotations

from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.session_jobs import SessionJobsSource


def test_session_jobs_empty_returns_no_sections() -> None:
    assert SessionJobsSource.from_jobs(SessionJobs.empty()).to_sections() == ()


def test_session_jobs_renders_active_and_pending_terminal() -> None:
    jobs = SessionJobs(
        active_jobs=(
            {"job_id": "a2", "status": "running"},
            {"job_id": "a1", "status": "submitted"},
        ),
        pending_terminal_jobs=({"job_id": "t9", "status": "finished"},),
    )

    section = SessionJobsSource.from_jobs(jobs).to_sections()[0]

    assert section.key == "session_jobs"
    assert section.tag == "session_jobs"
    assert section.order == SectionOrder.SESSION_JOBS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
    assert section.content == (
        'active_job_1 {"job_id": "a2", "status": "running"}\n'
        'active_job_2 {"job_id": "a1", "status": "submitted"}\n'
        'pending_terminal_job_1 {"job_id": "t9", "status": "finished"}'
    )


def test_session_jobs_only_active_renders_without_terminal_lines() -> None:
    jobs = SessionJobs(active_jobs=({"job_id": "a1", "status": "running"},))
    section = SessionJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == 'active_job_1 {"job_id": "a1", "status": "running"}'


def test_session_jobs_only_pending_terminal_renders() -> None:
    jobs = SessionJobs(pending_terminal_jobs=({"job_id": "t1", "status": "failed"},))
    section = SessionJobsSource.from_jobs(jobs).to_sections()[0]
    assert section.content == (
        'pending_terminal_job_1 {"job_id": "t1", "status": "failed"}'
    )


def _job(job_id: str, status: str = "finished", sandbox: bool = False) -> dict:
    return {"job_id": job_id, "status": status, "sandbox": sandbox}


def test_detail_limit_compresses_pending_with_overflow_summary() -> None:
    jobs = SessionJobs(
        pending_terminal_jobs=(
            _job("f1", "failed"),
            _job("t1"),
            _job("t2"),
            _job("t3", "stopped"),
        ),
        detail_limit=2,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines

    assert lines[0].startswith('pending_terminal_job_1 {"job_id": "f1"')
    assert lines[1].startswith('pending_terminal_job_2 {"job_id": "t1"')
    assert len(lines) == 3
    assert lines[2] == (
        'pending_terminal_overflow '
        '{"by_status": {"finished": 1, "stopped": 1}, '
        '"count": 2, "job_ids": ["t2", "t3"]}'
    )


def test_detail_limit_compresses_active_independently() -> None:
    jobs = SessionJobs(
        active_jobs=(
            _job("a1", "running"),
            _job("a2", "running"),
            _job("a3", "submitted"),
        ),
        pending_terminal_jobs=(_job("t1"),),
        detail_limit=1,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines

    assert lines[0].startswith("active_job_1 ")
    assert lines[1] == (
        'active_overflow '
        '{"by_status": {"running": 1, "submitted": 1}, '
        '"count": 2, "job_ids": ["a2", "a3"]}'
    )
    # pending 共 1 条 ≤ limit=1：全展开、无溢出行
    assert lines[2].startswith("pending_terminal_job_1 ")
    assert len(lines) == 3


def test_detail_limit_covers_all_ids_between_detail_and_overflow() -> None:
    all_ids = [f"j{i}" for i in range(7)]
    jobs = SessionJobs(
        pending_terminal_jobs=tuple(_job(i) for i in all_ids),
        detail_limit=3,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines

    import json as _json

    detail_ids = [_json.loads(line.split(" ", 1)[1])["job_id"] for line in lines[:3]]
    overflow = _json.loads(lines[3].split(" ", 1)[1])
    # 硬规则：详情行 + 溢出 job_ids 合起来覆盖 snapshot 全量 id
    assert detail_ids + overflow["job_ids"] == all_ids


def test_detail_limit_no_overflow_when_limit_covers_all() -> None:
    jobs = SessionJobs(
        pending_terminal_jobs=(_job("t1"), _job("t2")),
        detail_limit=2,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines
    assert len(lines) == 2
    assert not any("overflow" in line for line in lines)


def test_overflow_job_ids_keep_same_job_id_across_sandboxes() -> None:
    # 唯一键含 sandbox：同 job_id 可两行并存，计数与 id 列表以 row 为准、不去重
    jobs = SessionJobs(
        pending_terminal_jobs=(
            _job("keep"),
            _job("dup", sandbox=False),
            _job("dup", sandbox=True),
        ),
        detail_limit=1,
    )
    lines = SessionJobsSource.from_jobs(jobs).lines

    import json as _json

    overflow = _json.loads(lines[1].split(" ", 1)[1])
    assert overflow["count"] == 2
    assert overflow["job_ids"] == ["dup", "dup"]
