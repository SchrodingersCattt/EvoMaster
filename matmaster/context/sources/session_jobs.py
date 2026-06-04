from __future__ import annotations

import json
from dataclasses import dataclass

from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


@dataclass(frozen=True)
class SessionJobsSource:
    """Renderer for active jobs.

    The JSON-line shape is intentionally temporary; the Bohrium job ledger may
    later define stable fields and replace this renderer without treating the
    current string format as a product contract.
    """

    lines: tuple[str, ...] = ()

    @classmethod
    def from_jobs(cls, jobs: SessionJobs) -> SessionJobsSource:
        active = tuple(
            f"active_job_{index} "
            f"{json.dumps(job, ensure_ascii=False, sort_keys=True)}"
            for index, job in enumerate(jobs.active_jobs, 1)
        )
        pending = tuple(
            f"pending_terminal_job_{index} "
            f"{json.dumps(job, ensure_ascii=False, sort_keys=True)}"
            for index, job in enumerate(jobs.pending_terminal_jobs, 1)
        )
        return cls(lines=active + pending)

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.lines:
            return ()
        return (
            ContextSection(
                key="session_jobs",
                tag="session_jobs",
                content="\n".join(self.lines),
                order=SectionOrder.SESSION_JOBS,
                views=ALL_VIEWS,
            ),
        )
