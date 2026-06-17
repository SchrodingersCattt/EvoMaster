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
PREVIEW_COLUMNS: tuple[str, ...] = ("group", *SUMMARY_COLUMNS)

_ACTION_STATUSES = ("failed", "lost", "stopped")
REQUIRED_TRUNCATED_HINT = (
    "Workspace required context hit the safety cap and may be incomplete; some "
    "required jobs are absent from both this summary and the exported CSV."
)
HANDLED_RECENT_HINT = (
    "handled_recent_terminal is reference-only history truncated to "
    "HANDLED_RECENT_LIMIT; older handled jobs are intentionally omitted."
)
HANDLED_RECENT_UNAVAILABLE_HINT = (
    "handled_recent_terminal reference history could not be loaded; required "
    "active/unhandled context is still present if no required_context_error exists."
)


def render_job_json(job: Mapping[str, JsonValue]) -> str:
    """inline 行与 inline_chars 共用的唯一 JSON 序列化；参数不得各处分叉。"""
    return json.dumps(job, ensure_ascii=False, sort_keys=True)


def summary_to_dict(s: WorkspaceJobsSummary) -> dict[str, JsonValue]:
    return {
        "total": s.total,
        "active": s.active,
        "unhandled_terminal": s.unhandled_terminal,
        "handled_recent_terminal": s.handled_recent_terminal,
        "by_status": dict(s.by_status),
        "failed": s.failed,
        "stopped": s.stopped,
        "lost": s.lost,
        "unhandled_action": s.unhandled_action,
    }


def compute_summary(
    active: tuple[JsonObject, ...],
    unhandled: tuple[JsonObject, ...],
    handled_recent: tuple[JsonObject, ...],
) -> WorkspaceJobsSummary:
    by_status: dict[str, int] = {}
    for group in (active, unhandled, handled_recent):
        for job in group:
            status = str(job.get("status"))
            by_status[status] = by_status.get(status, 0) + 1
    unhandled_action = sum(
        1 for job in unhandled if str(job.get("status")) in _ACTION_STATUSES
    )
    return WorkspaceJobsSummary(
        total=len(active) + len(unhandled) + len(handled_recent),
        active=len(active),
        unhandled_terminal=len(unhandled),
        handled_recent_terminal=len(handled_recent),
        by_status=by_status,
        failed=by_status.get("failed", 0),
        stopped=by_status.get("stopped", 0),
        lost=by_status.get("lost", 0),
        unhandled_action=unhandled_action,
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
    has_workspace_job_content = bool(
        jobs.mode
        or jobs.summary is not None
        or jobs.active_jobs
        or jobs.unhandled_terminal_jobs
        or jobs.handled_recent_terminal_jobs
        or jobs.required_error is not None
        or jobs.required_truncated
        or jobs.handled_recent_has_more
        or jobs.handled_recent_unavailable
    )
    if not has_workspace_job_content:
        return ()
    if jobs.workspace:
        lines.append(f"workspace {jobs.workspace}")
    if jobs.mode:
        lines.append(f"mode {jobs.mode}")
    if jobs.summary is not None:
        lines.append(f"summary {render_job_json(summary_to_dict(jobs.summary))}")
    if jobs.required_error is not None:
        lines.append(
            f"required_context_error {render_job_json(dict(jobs.required_error))}"
        )
    lines.append(f"required_truncated {str(jobs.required_truncated).lower()}")
    lines.append(f"handled_recent_has_more {str(jobs.handled_recent_has_more).lower()}")
    lines.append(
        f"handled_recent_unavailable " f"{str(jobs.handled_recent_unavailable).lower()}"
    )
    if jobs.required_truncated:
        lines.append(f'required_truncated_hint "{REQUIRED_TRUNCATED_HINT}"')
    if jobs.handled_recent_has_more:
        lines.append(f'handled_recent_hint "{HANDLED_RECENT_HINT}"')
    if jobs.handled_recent_unavailable:
        lines.append(
            f'handled_recent_unavailable_hint "{HANDLED_RECENT_UNAVAILABLE_HINT}"'
        )
    for label, group in (
        ("active", jobs.active_jobs),
        ("unhandled_terminal", jobs.unhandled_terminal_jobs),
        ("handled_recent_terminal", jobs.handled_recent_terminal_jobs),
    ):
        if group:
            lines.extend(render_csv_block(label, SUMMARY_COLUMNS, group))
    return tuple(lines)


def compute_inline_chars(jobs: WorkspaceJobs) -> int:
    """inline section 的精确字符数（== renderer inline 输出），非估算。"""
    return len("\n".join(render_inline_lines(jobs)))


def _with_group(job: JsonObject, group: str) -> dict[str, JsonValue]:
    return {"group": group, **job}


def select_observation_preview_rows(
    *,
    active: tuple[JsonObject, ...],
    unhandled_terminal: tuple[JsonObject, ...],
    handled_recent_terminal: tuple[JsonObject, ...],
    limit: int,
) -> tuple[JsonObject, ...]:
    """observation compact preview。顺序：unhandled action -> active ->
    unhandled other -> handled recent。每行在选择阶段打上来源 group，renderer
    不得从裸 job 反推 bucket。
    """
    unhandled_action = [
        _with_group(j, "unhandled_terminal")
        for j in unhandled_terminal
        if str(j.get("status")) in _ACTION_STATUSES
    ]
    unhandled_other = [
        _with_group(j, "unhandled_terminal")
        for j in unhandled_terminal
        if str(j.get("status")) not in _ACTION_STATUSES
    ]
    active_rows = [_with_group(j, "active") for j in active]
    handled_recent_rows = [
        _with_group(j, "handled_recent_terminal") for j in handled_recent_terminal
    ]
    selected: list[JsonObject] = []
    for pool in (unhandled_action, active_rows, unhandled_other, handled_recent_rows):
        remaining = limit - len(selected)
        if remaining <= 0:
            break
        selected.extend(pool[:remaining])
    return tuple(selected)


def select_delivery_preview_rows(
    rows: tuple[JsonObject, ...],
    *,
    limit: int,
) -> tuple[JsonObject, ...]:
    """delivery compact preview：只取 failed/lost/stopped，单 bucket 不打 group。"""
    action = [j for j in rows if str(j.get("status")) in _ACTION_STATUSES]
    return tuple(action[:limit])


_PREVIEW_TRUNCATION_MARKER = "...<truncated>"
_PREVIEW_FIELD_CHAR_LIMIT = 240


def _truncate_preview_cell(value: JsonValue) -> JsonValue:
    if not isinstance(value, str):
        return value
    if len(value) <= _PREVIEW_FIELD_CHAR_LIMIT:
        return value
    keep = max(0, _PREVIEW_FIELD_CHAR_LIMIT - len(_PREVIEW_TRUNCATION_MARKER))
    return value[:keep] + _PREVIEW_TRUNCATION_MARKER


def _truncate_preview_row(
    row: JsonObject,
    columns: tuple[str, ...],
) -> dict[str, JsonValue]:
    out = dict(row)
    for column in columns:
        out[column] = _truncate_preview_cell(out.get(column))
    return out


def trim_preview_rows_to_char_limit(
    rows: tuple[JsonObject, ...],
    *,
    columns: tuple[str, ...],
    char_limit: int,
) -> tuple[JsonObject, ...]:
    """Bound rendered compact preview. CSV remains the complete snapshot."""
    selected: list[JsonObject] = []
    for row in rows:
        candidate = (*selected, _truncate_preview_row(row, columns))
        rendered = "\n".join(render_csv_block("preview_rows", columns, candidate))
        if len(rendered) > char_limit:
            break
        selected = list(candidate)
    return tuple(selected)


def build_csv_rows(
    active: tuple[JsonObject, ...],
    unhandled: tuple[JsonObject, ...],
    handled_recent: tuple[JsonObject, ...],
) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    for group, items in (
        ("active", active),
        ("unhandled_terminal", unhandled),
        ("handled_recent_terminal", handled_recent),
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
        writer.writerow({k: _csv_cell(v) for k, v in row.items() if k in CSV_COLUMNS})
    return buf.getvalue()
