from __future__ import annotations

import json
from dataclasses import dataclass

from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class SessionJobsSource:
    """Placeholder renderer for active jobs.

    The JSON-line shape is intentionally temporary; the Bohrium job ledger phase
    will define stable fields and may replace this renderer without treating
    the Phase 2A string format as product contract.
    """

    lines: tuple[str, ...] = ()

    @classmethod
    def from_jobs(cls, jobs: SessionJobs) -> SessionJobsSource:
        return cls(
            lines=tuple(
                f"job_{index} {json.dumps(job, ensure_ascii=False, sort_keys=True)}"
                for index, job in enumerate(jobs.active_jobs, 1)
            )
        )

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.lines:
            return ()
        return (
            ContextSection(
                key="session_jobs",
                tag="session_jobs",
                content="\n".join(self.lines),
                order=SectionOrder.SESSION_JOBS,
                views=_VIEWS,
            ),
        )
