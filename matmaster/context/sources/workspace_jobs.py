from __future__ import annotations

import json
from dataclasses import dataclass

from matmaster.context.ports import WorkspaceJobs
from matmaster.context.sections import (
    ALL_VIEWS,
    RUNTIME_ONLY_VIEWS,
    ContextSection,
    SectionOrder,
)

_DELIVERY_PENDING_INTRO = (
    "以下 Bohrium 作业已结束、结果待处理（属于本轮交付确认范围）："
)
_DELIVERY_ACTIVE_INTRO = "以下 Bohrium 作业仍在运行（仅作上下文，无需处理）："
_DELIVERY_DIRECTIVE = (
    "请逐一拉取并核对以上已结束作业的结果：成功项汇总关键产出，失败项诊断原因，"
    "给出整体结论与下一步。处理完成即视为交付确认。"
)
_PENDING_OVERFLOW_DIRECTIVE_SUFFIX = (
    "（末尾 overflow 摘要中的 job_ids 同属本批次，请按其 status 一并处理。）"
)


@dataclass(frozen=True)
class WorkspaceJobsSource:
    """Renderer for the workspace job view."""

    lines: tuple[str, ...] = ()
    delivery_directive: str | None = None

    @classmethod
    def from_jobs(cls, jobs: WorkspaceJobs) -> WorkspaceJobsSource:
        if jobs.mode == "delivery":
            return cls._from_delivery_jobs(jobs)
        return cls._from_observation_jobs(jobs)

    @classmethod
    def _from_observation_jobs(cls, jobs: WorkspaceJobs) -> WorkspaceJobsSource:
        active, _ = cls._render_group(
            "active_job", "active_overflow", jobs.active_jobs, jobs.detail_limit
        )
        pending, _ = cls._render_group(
            "pending_terminal_job",
            "pending_terminal_overflow",
            jobs.pending_terminal_jobs,
            jobs.detail_limit,
        )
        recent, _ = cls._render_group(
            "recent_terminal_job",
            "recent_terminal_overflow",
            jobs.recent_terminal_jobs,
            jobs.detail_limit,
        )
        body = active + pending + recent
        if not body:
            return cls(lines=())
        header = (f"workspace {jobs.workspace}",) if jobs.workspace else ()
        return cls(lines=header + body)

    @classmethod
    def _from_delivery_jobs(cls, jobs: WorkspaceJobs) -> WorkspaceJobsSource:
        pending, pending_overflowed = cls._render_group(
            "pending_terminal_job",
            "pending_terminal_overflow",
            jobs.pending_terminal_jobs,
            jobs.detail_limit,
            intro=_DELIVERY_PENDING_INTRO,
        )
        active, _ = cls._render_group(
            "active_job",
            "active_overflow",
            jobs.active_jobs,
            jobs.detail_limit,
            intro=_DELIVERY_ACTIVE_INTRO,
        )
        body = pending + active
        if not body:
            return cls(lines=())
        header = (f"workspace {jobs.workspace}",) if jobs.workspace else ()
        directive = None
        if jobs.pending_terminal_jobs:
            directive = _DELIVERY_DIRECTIVE
            if pending_overflowed:
                directive += _PENDING_OVERFLOW_DIRECTIVE_SUFFIX
        return cls(lines=header + body, delivery_directive=directive)

    @staticmethod
    def _render_group(
        prefix: str,
        overflow_tag: str,
        items: tuple,
        limit: int | None,
        *,
        intro: str | None = None,
    ) -> tuple[tuple[str, ...], bool]:
        """前 limit 条完整详情，其余压成一行溢出摘要；全量 job_id 始终可见。"""
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
        if intro and lines:
            lines = (intro,) + lines
        return lines, bool(rest)

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.lines:
            return ()
        sections = (
            ContextSection(
                key="workspace_jobs",
                tag="workspace_jobs",
                content="\n".join(self.lines),
                order=SectionOrder.WORKSPACE_JOBS,
                views=ALL_VIEWS,
            ),
        )
        if self.delivery_directive is None:
            return sections
        return sections + (
            ContextSection(
                key="delivery_directive",
                tag="delivery_directive",
                content=self.delivery_directive,
                order=SectionOrder.TURN_INSTRUCTION_LAST,
                views=RUNTIME_ONLY_VIEWS,
            ),
        )
