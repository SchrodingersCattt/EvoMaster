from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Mapping

from matmaster.context.ports import (
    JsonObject,
    JsonValue,
    WorkspaceJobs,
    WorkspaceJobsSummary,
)

CSV_COLUMNS: tuple[str, ...] = (
    "group",
    "job_id",
    "job_name",
    "status",
    "sandbox",
    "project_id",
    "input_dir",
    "workspace",
    "submitted_at",
    "last_polled_at",
    "terminal_at",
    "result_dir",
    "invocation_id",
    "id",
)

SUMMARY_COLUMNS: tuple[str, ...] = ("job_id", "job_name", "status")

_ACTION_STATUSES = ("failed", "lost", "stopped")


def render_job_json(job: Mapping[str, JsonValue]) -> str:
    """inline 行与 inline_chars 共用的唯一 JSON 序列化；参数不得各处分叉。"""
    return json.dumps(job, ensure_ascii=False, sort_keys=True)


def summary_to_dict(s: WorkspaceJobsSummary) -> dict[str, JsonValue]:
    return {
        "total": s.total,
        "active": s.active,
        "pending_terminal": s.pending_terminal,
        "recent_terminal": s.recent_terminal,
        "by_status": dict(s.by_status),
        "failed": s.failed,
        "stopped": s.stopped,
        "lost": s.lost,
    }


def compute_summary(
    active: tuple[JsonObject, ...],
    pending: tuple[JsonObject, ...],
    recent: tuple[JsonObject, ...],
) -> WorkspaceJobsSummary:
    by_status: dict[str, int] = {}
    for group in (active, pending, recent):
        for job in group:
            status = str(job.get("status"))
            by_status[status] = by_status.get(status, 0) + 1
    return WorkspaceJobsSummary(
        total=len(active) + len(pending) + len(recent),
        active=len(active),
        pending_terminal=len(pending),
        recent_terminal=len(recent),
        by_status=by_status,
        failed=by_status.get("failed", 0),
        stopped=by_status.get("stopped", 0),
        lost=by_status.get("lost", 0),
    )


def render_csv_block(
    label: str,
    columns: tuple[str, ...],
    rows: Iterable[Mapping[str, JsonValue]],
) -> tuple[str, ...]:
    """一行 `label col,col,...` 表头 + 多行 csv 值（标准转义），列名只出现一次。"""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in rows:
        writer.writerow([_csv_cell(row.get(col)) for col in columns])
    return (f"{label} {','.join(columns)}", *buf.getvalue().splitlines())


def render_inline_lines(jobs: WorkspaceJobs) -> tuple[str, ...]:
    lines: list[str] = []
    if jobs.workspace:
        lines.append(f"workspace {jobs.workspace}")
    if jobs.mode:
        lines.append(f"mode {jobs.mode}")
    if jobs.summary is not None:
        lines.append(f"summary {render_job_json(summary_to_dict(jobs.summary))}")
    for label, group in (
        ("active", jobs.active_jobs),
        ("pending_terminal", jobs.pending_terminal_jobs),
        ("recent_terminal", jobs.recent_terminal_jobs),
    ):
        if group:
            lines.extend(render_csv_block(label, SUMMARY_COLUMNS, group))
    return tuple(lines)


def compute_inline_chars(jobs: WorkspaceJobs) -> int:
    """inline section 的精确字符数（== renderer inline 输出），非估算。"""
    return len("\n".join(render_inline_lines(jobs)))


def _with_group(job: JsonObject, group: str) -> dict[str, JsonValue]:
    return {"group": group, **job}


def select_priority_samples(
    active: tuple[JsonObject, ...],
    pending: tuple[JsonObject, ...],
    recent: tuple[JsonObject, ...],
    *,
    action_limit: int,
    fill_limit: int,
) -> tuple[JsonObject, ...]:
    """行动关键样本（pending 的 failed/lost/stopped）优先全内联，受 action_limit
    约束；其余按 pending(非 action) → active → recent 顺序填充，受 fill_limit 约束。
    样本只渲染 job_id/job_name/status，不加 group。
    """
    action = [
        j for j in pending if str(j.get("status")) in _ACTION_STATUSES
    ][:action_limit]
    fill_candidates = (
        [j for j in pending if str(j.get("status")) not in _ACTION_STATUSES]
        + list(active)
        + list(recent)
    )
    return tuple(action + fill_candidates[:fill_limit])


def build_csv_rows(
    active: tuple[JsonObject, ...],
    pending: tuple[JsonObject, ...],
    recent: tuple[JsonObject, ...],
) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    for group, items in (
        ("active", active),
        ("pending_terminal", pending),
        ("recent_terminal", recent),
    ):
        for job in items:
            rows.append(_with_group(job, group))
    return rows


def _csv_cell(value: JsonValue) -> str:
    if isinstance(value, bool):  # bool 先于 int 判断
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def build_csv_text(rows: Iterable[Mapping[str, JsonValue]]) -> str:
    """固定列集 DictWriter；缺失列 restval 空填，列集外字段 extrasaction 丢弃。"""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=CSV_COLUMNS, restval="", extrasaction="ignore"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {k: _csv_cell(v) for k, v in row.items() if k in CSV_COLUMNS}
        )
    return buf.getvalue()
