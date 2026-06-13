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
        active = cls._render_group(
            "active_job", "active_overflow", jobs.active_jobs, jobs.detail_limit
        )
        pending = cls._render_group(
            "pending_terminal_job",
            "pending_terminal_overflow",
            jobs.pending_terminal_jobs,
            jobs.detail_limit,
        )
        return cls(lines=active + pending)

    @staticmethod
    def _render_group(
        prefix: str,
        overflow_tag: str,
        items: tuple,
        limit: int | None,
    ) -> tuple[str, ...]:
        """前 limit 条完整详情，其余压成一行溢出摘要；全量 job_id 始终可见。

        limit 为 None（无 delivery snapshot 的回退路径）时全量逐行，与历史行为一致。
        """
        if limit is None or len(items) <= limit:
            shown, rest = items, ()
        else:
            shown, rest = items[:limit], items[limit:]
        lines = tuple(
            f"{prefix}_{index} "
            f"{json.dumps(job, ensure_ascii=False, sort_keys=True)}"
            for index, job in enumerate(shown, 1)
        )
        if rest:
            by_status: dict[str, int] = {}
            for job in rest:
                status = str(job.get("status"))
                by_status[status] = by_status.get(status, 0) + 1
            summary = {
                "count": len(rest),
                "by_status": by_status,
                # 不按 job_id 去重：唯一键含 sandbox，计数与 ack 以 row 为准
                "job_ids": [str(job.get("job_id")) for job in rest],
            }
            lines += (
                f"{overflow_tag} "
                f"{json.dumps(summary, ensure_ascii=False, sort_keys=True)}",
            )
        return lines

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
