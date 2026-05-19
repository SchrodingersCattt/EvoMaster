from __future__ import annotations

from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.session_jobs import SessionJobsSource


def test_session_jobs_empty_returns_no_sections() -> None:
    assert SessionJobsSource.from_jobs(SessionJobs.empty()).to_sections() == ()


def test_session_jobs_source_renders_stable_json_lines() -> None:
    jobs = SessionJobs(
        active_jobs=(
            {"id": "job-2", "state": "running"},
            {"id": "job-1", "state": "queued"},
        )
    )

    section = SessionJobsSource.from_jobs(jobs).to_sections()[0]

    assert section.key == "session_jobs"
    assert section.tag == "session_jobs"
    assert section.order == SectionOrder.SESSION_JOBS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
    assert section.content == (
        'job_1 {"id": "job-2", "state": "running"}\n'
        'job_2 {"id": "job-1", "state": "queued"}'
    )
