from __future__ import annotations

from dataclasses import dataclass

from matmaster.bohrium.status import LEDGER_FAILURE_STATUSES
from matmaster.context.ports import WorkspaceJobs
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder
from matmaster.context.workspace_jobs_compute import (
    SUMMARY_COLUMNS,
    render_csv_block,
    render_inline_lines,
    render_job_json,
    summary_to_dict,
)

_DELIVERY_FAILED_HEADER = "以下作业失败："
_DELIVERY_SUCCEEDED_HEADER = "以下作业成功结束："
_DELIVERY_TABLE_HEADER = "job_id, job_name"
_DELIVERY_SUCCESS_STATUS = "finished"
_DELIVERY_FAILURE_STATUSES = frozenset(LEDGER_FAILURE_STATUSES)

_READ_HINT = (
    "Full job details are in the CSV file. Use Read or Bash to inspect/filter "
    "it when you need specific job ids, job names, failed rows, or result "
    "directories."
)
_ACTION_HINT = (
    "Failed terminal jobs exist. Inspect failed rows first; do not enumerate "
    "all jobs in the final answer."
)
_EXPORT_ERROR_HINT = (
    "Full job details could not be exported; do not assume omitted pending "
    "jobs were delivered."
)
_DELIVERY_SUCCEEDED_COUNT_TEMPLATE = "以下作业成功结束：共 {count} 个（详见导出文件）"
_DELIVERY_EXPORTED_TEMPLATE = "完整明细已导出：{path}"
_DELIVERY_READ_HINT = (
    "需要某个作业的 input_dir / result_dir 等，用 Read 或 Bash 读取该 CSV。"
)
_DELIVERY_EXPORT_FAILED_TEXT = "完整明细导出失败，被省略的作业未必已交付。"


@dataclass(frozen=True)
class WorkspaceJobsSource:
    """Renderer for the workspace job view.

    observation 模式：inline / compact / error 三态，按 ``export`` /
    ``export_error`` 决定，不查 DAO、不写文件、不判阈值。
    delivery 模式：不在本 section 渲染；其 job 表格由 ``delivery_instruction_text``
    生成，再经 compositions 注入到 turn instruction。
    """

    lines: tuple[str, ...] = ()

    @classmethod
    def from_jobs(cls, jobs: WorkspaceJobs) -> WorkspaceJobsSource:
        if jobs.mode == "session_workspace_delivery":
            return cls(lines=())
        if jobs.export_error is not None:
            return cls(lines=cls._error_lines(jobs))
        if jobs.export is not None:
            return cls(lines=cls._compact_lines(jobs))
        return cls(lines=render_inline_lines(jobs))

    @classmethod
    def delivery_instruction_text(cls, jobs: WorkspaceJobs) -> str:
        if jobs.mode != "session_workspace_delivery":
            return ""
        if jobs.export_error is not None:
            return cls._delivery_export_failed_text(jobs)
        if jobs.export is not None:
            return cls._delivery_compact_text(jobs)
        return cls._delivery_full_text(jobs)

    @classmethod
    def _delivery_full_text(cls, jobs: WorkspaceJobs) -> str:
        if not jobs.pending_terminal_jobs:
            return ""
        failed = tuple(
            job
            for job in jobs.pending_terminal_jobs
            if str(job.get("status")) in _DELIVERY_FAILURE_STATUSES
        )
        succeeded = tuple(
            job
            for job in jobs.pending_terminal_jobs
            if str(job.get("status")) == _DELIVERY_SUCCESS_STATUS
        )
        if not (failed or succeeded):
            return ""
        lines = (
            cls._render_delivery_table(_DELIVERY_FAILED_HEADER, failed)
            + ("",)
            + cls._render_delivery_table(_DELIVERY_SUCCEEDED_HEADER, succeeded)
        )
        return "\n".join(lines)

    @classmethod
    def _delivery_compact_text(cls, jobs: WorkspaceJobs) -> str:
        export = jobs.export
        assert export is not None
        finished = (
            jobs.summary.by_status.get(_DELIVERY_SUCCESS_STATUS, 0)
            if jobs.summary is not None
            else 0
        )
        lines = (
            *cls._render_delivery_table(_DELIVERY_FAILED_HEADER, jobs.priority_samples),
            "",
            _DELIVERY_SUCCEEDED_COUNT_TEMPLATE.format(count=finished),
            "",
            _DELIVERY_EXPORTED_TEMPLATE.format(path=export.path),
            _DELIVERY_READ_HINT,
        )
        return "\n".join(lines)

    @classmethod
    def _delivery_export_failed_text(cls, jobs: WorkspaceJobs) -> str:
        lines = (
            *cls._render_delivery_table(_DELIVERY_FAILED_HEADER, jobs.priority_samples),
            "",
            _DELIVERY_EXPORT_FAILED_TEXT,
        )
        return "\n".join(lines)

    @staticmethod
    def _head_lines(jobs: WorkspaceJobs) -> list[str]:
        lines: list[str] = []
        if jobs.workspace:
            lines.append(f"workspace {jobs.workspace}")
        if jobs.mode:
            lines.append(f"mode {jobs.mode}")
        if jobs.summary is not None:
            lines.append(f"summary {render_job_json(summary_to_dict(jobs.summary))}")
        return lines

    @classmethod
    def _compact_lines(cls, jobs: WorkspaceJobs) -> tuple[str, ...]:
        lines = cls._head_lines(jobs)
        export = jobs.export
        assert export is not None
        lines.append(
            "details_exported "
            + render_job_json(
                {
                    "format": export.format,
                    "path": export.path,
                    "rows": export.row_count,
                    "columns": list(export.columns),
                    "reason": export.reason,
                }
            )
        )
        lines.append(f'read_hint "{_READ_HINT}"')
        if jobs.summary is not None and (
            jobs.summary.failed or jobs.summary.lost or jobs.summary.stopped
        ):
            lines.append(f'action_hint "{_ACTION_HINT}"')
        if jobs.priority_samples:
            lines.extend(
                render_csv_block(
                    "priority_samples",
                    SUMMARY_COLUMNS,
                    jobs.priority_samples,
                )
            )
        if jobs.omitted_count is not None:
            lines.append(
                "omitted_from_prompt "
                + render_job_json(
                    {
                        "count": jobs.omitted_count,
                        "reason": "large job set exported to csv",
                    }
                )
            )
        return tuple(lines)

    @classmethod
    def _error_lines(cls, jobs: WorkspaceJobs) -> tuple[str, ...]:
        lines = cls._head_lines(jobs)
        err = jobs.export_error
        assert err is not None
        lines.append(
            "workspace_jobs_export_error "
            + render_job_json(
                {
                    "reason": err.reason,
                    "rows": err.rows,
                    "target_path": err.target_path,
                }
            )
        )
        lines.append(f'action_hint "{_EXPORT_ERROR_HINT}"')
        if jobs.priority_samples:
            lines.extend(
                render_csv_block(
                    "priority_samples",
                    SUMMARY_COLUMNS,
                    jobs.priority_samples,
                )
            )
        return tuple(lines)

    @classmethod
    def _render_delivery_table(
        cls,
        header: str,
        jobs: tuple,
    ) -> tuple[str, ...]:
        return (
            header,
            _DELIVERY_TABLE_HEADER,
            *(cls._render_delivery_row(job) for job in jobs),
        )

    @staticmethod
    def _render_delivery_row(job: dict) -> str:
        return (
            f"{WorkspaceJobsSource._delivery_cell(job.get('job_id'))}, "
            f"{WorkspaceJobsSource._delivery_cell(job.get('job_name'))}"
        )

    @staticmethod
    def _delivery_cell(value: object) -> str:
        if value is None:
            return ""
        return str(value).replace("\r", " ").replace("\n", " ").strip()

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.lines:
            return ()
        return (
            ContextSection(
                key="workspace_jobs",
                tag="workspace_jobs",
                content="\n".join(self.lines),
                order=SectionOrder.WORKSPACE_JOBS,
                views=ALL_VIEWS,
            ),
        )
