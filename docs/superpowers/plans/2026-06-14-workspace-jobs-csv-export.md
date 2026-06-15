# Workspace Jobs CSV Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 workspace job 集合超过阈值时，把完整 job 明细导出为当前 session 文件系统下的 CSV，prompt 只保留摘要、关键样本与 CSV 路径，prompt 长度有硬上限。

**Architecture:** 分四层，依赖方向 `src/services → matmaster/context`。
- DTO（`matmaster/context/ports.py`）：`WorkspaceJobs` 扩展 + 三个新 frozen dataclass。
- 纯函数（`matmaster/context/workspace_jobs_compute.py`）：summary 统计、inline 行渲染与精确长度、priority sample 选择、CSV 文本生成，全部无副作用。
- renderer（`matmaster/context/sources/workspace_jobs.py`）：纯渲染，按 `export`/`export_error` 决定 inline / compact / error 三态，不查 DAO、不写文件、不判阈值。
- service（`src/services/workspace_jobs_export.py` + `bohrium_jobs_wiring.py` + `bohrium_delivery_ack.py` + `agent_run_service.py`）：read port 算 summary、判阈值、调 exporter；exporter 用 `session.write_file` 落盘；delivery 模式把导出失败记到 run-local 的 `DeliverySnapshot`，`confirm` 据此决定是否跳过 pending rows 的 ack。

**Tech Stack:** Python 3.10+，dataclass，标准库 `csv`/`json`/`io`，pytest（`uv run pytest`，`asyncio_mode = auto`），`MagicMock`。

**Spec:** `docs/superpowers/specs/2026-06-14-workspace-jobs-csv-export-design.md`

---

## 实现补充（对 spec 的细化，需 review）

写 plan 时真实代码暴露了 spec 未覆盖的点，本 plan 按以下默认落实：

1. **`observation_query_limit` 替代 `detail_limit` 的 query 职责。** `detail_limit` 在 observation 模式同时是 `query_workspace_pending_terminal/recent_terminal` 的 DB `LIMIT`。spec 只说移除 `detail_limit`，但 query LIMIT 不能丢。本 plan 把它拆开：renderer 截断职责删除，query LIMIT 职责改名 `observation_query_limit`，从新 env `BOHRIUM_WORKSPACE_JOBS_OBSERVATION_QUERY_LIMIT`（默认 20）读。
2. **`WorkspaceJobs` 增加 `priority_samples` 与 `omitted_count` 字段。** compact 形态需要这两项，但 renderer 不读 env、无法自选样本，故由 wiring 算好填入。spec 第 7 节数据结构应补这两个字段。
3. **exporter 文件名用 `invocation_id` 优先、`task_id` 兜底。** `invocation_id` 是 `str | None`，可能为空。`task_id` 假定在 `AgentRunService` 作用域可用（Task 6 步骤会先确认）。
4. **`DeliverySnapshot.export_failure` 用可变 `dict` 容器。** `DeliverySnapshot` 是 `frozen=True`，无法重绑 `bool` 字段；沿用 `observed_terminal`（可变 set 绑 frozen 字段）的模式，用 `field(default_factory=dict)` 的可变 dict 承载导出失败状态。

---

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `matmaster/context/ports.py` | DTO | 改：加 3 dataclass + 扩 `WorkspaceJobs` |
| `matmaster/context/workspace_jobs_compute.py` | 纯函数 | 新建 |
| `matmaster/context/sources/workspace_jobs.py` | renderer | 改：三态渲染，去 `detail_limit` |
| `src/services/workspace_jobs_export.py` | CSV exporter | 新建 |
| `src/services/bohrium_jobs_wiring.py` | read port 装配 | 改：阈值/导出/samples/mode + 去 `detail_limit` |
| `src/services/bohrium_delivery_ack.py` | snapshot/confirm | 改：去 `detail_limit`，加 `export_failure`，confirm 拆两路 |
| `src/services/agent_run_service.py` | 注入 exporter | 改：构造 exporter 传入 wiring |
| `tests/matmaster/context/test_workspace_jobs_dto.py` | DTO 测试 | 新建 |
| `tests/matmaster/context/test_workspace_jobs_compute.py` | 纯函数测试 | 新建 |
| `tests/matmaster/context/sources/test_workspace_jobs.py` | renderer 测试 | 改：删 `detail_limit`，加三态 |
| `tests/services/test_workspace_jobs_export.py` | exporter 测试 | 新建 |
| `tests/services/test_bohrium_jobs_wiring.py` | wiring 测试 | 改：删 `detail_limit`，加阈值/export/mode |
| `tests/services/test_bohrium_delivery_ack.py` | delivery 测试 | 改：删 `detail_limit`，加 export_failure |

**任务顺序：** T1 → T2 → T3 → T4 → T5 → T6 → T7。每个 Task 结束时代码可运行、测试通过。`detail_limit` 的移除分散在改到它的 Task 内就地清理。

---

## Task 1: 扩展 DTO

给 `WorkspaceJobs` 加新字段并新增三个元数据 dataclass。本步保留 `detail_limit`（过渡），由 Task 5 末尾移除。

**Files:**
- Modify: `matmaster/context/ports.py:98-108`
- Test: `tests/matmaster/context/test_workspace_jobs_dto.py`

- [ ] **Step 1.1: 写失败测试**

新建 `tests/matmaster/context/test_workspace_jobs_dto.py`：

```python
from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
    WorkspaceJobsSummary,
)


def test_workspace_jobs_new_fields_default() -> None:
    jobs = WorkspaceJobs()
    assert jobs.mode is None
    assert jobs.summary is None
    assert jobs.export is None
    assert jobs.export_error is None
    assert jobs.priority_samples == ()
    assert jobs.omitted_count is None


def test_export_metadata_constructs() -> None:
    export = WorkspaceJobsExport(
        path="/w/.matmaster/context/workspace_jobs/s-i.csv",
        format="csv",
        row_count=1020,
        columns=("group", "job_id"),
        reason="row_limit",
    )
    assert export.row_count == 1020
    assert export.reason == "row_limit"


def test_summary_and_error_construct() -> None:
    summary = WorkspaceJobsSummary(
        total=3, active=2, pending_terminal=1, recent_terminal=0,
        by_status={"running": 2, "failed": 1}, failed=1, stopped=0, lost=0,
    )
    err = WorkspaceJobsExportError(reason="write_failed", rows=3, target_path="/w/x.csv")
    assert summary.total == 3
    assert summary.by_status["failed"] == 1
    assert err.reason == "write_failed"
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/context/test_workspace_jobs_dto.py -v`
Expected: FAIL，`ImportError: cannot import name 'WorkspaceJobsExport'`

- [ ] **Step 1.3: 改 `ports.py`**

`ports.py` 顶部已有 `from collections.abc import Callable, Mapping`、`from dataclasses import dataclass`、`from typing import Literal, ...`，无需新 import。

把 `WorkspaceJobs`（当前行 98-108）整体替换为下面内容（新增三个 dataclass 放在 `WorkspaceJobs` 之前）：

```python
@dataclass(frozen=True)
class WorkspaceJobsExport:
    path: str
    format: Literal["csv"]
    row_count: int
    columns: tuple[str, ...]
    reason: Literal["row_limit", "char_limit"]


@dataclass(frozen=True)
class WorkspaceJobsSummary:
    total: int  # == active + pending_terminal + recent_terminal == CSV row_count
    active: int
    pending_terminal: int
    recent_terminal: int
    by_status: Mapping[str, int]
    failed: int
    stopped: int
    lost: int


@dataclass(frozen=True)
class WorkspaceJobsExportError:
    reason: Literal[
        "session_missing", "bad_target_path", "write_failed", "serialize_failed"
    ]
    rows: int
    target_path: str


@dataclass(frozen=True)
class WorkspaceJobs:
    workspace: str | None = None
    active_jobs: tuple[JsonObject, ...] = ()
    pending_terminal_jobs: tuple[JsonObject, ...] = ()
    recent_terminal_jobs: tuple[JsonObject, ...] = ()
    detail_limit: int | None = None  # 过渡字段，Task 5 末尾移除
    mode: Literal["workspace_observation", "session_workspace_delivery"] | None = None
    summary: WorkspaceJobsSummary | None = None
    export: WorkspaceJobsExport | None = None
    export_error: WorkspaceJobsExportError | None = None
    priority_samples: tuple[JsonObject, ...] = ()
    omitted_count: int | None = None

    @classmethod
    def empty(cls) -> WorkspaceJobs:
        return cls()
```

- [ ] **Step 1.4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/context/test_workspace_jobs_dto.py -v`
Expected: PASS（3 passed）

- [ ] **Step 1.5: Commit**

```bash
git add matmaster/context/ports.py tests/matmaster/context/test_workspace_jobs_dto.py
git commit -m "feat(context): add workspace jobs export/summary DTOs"
```

---

## Task 2: 纯函数模块

新建无副作用的统计、渲染、采样、CSV 生成函数。renderer 与 wiring 共用，保证 inline 长度精确一致。

**Files:**
- Create: `matmaster/context/workspace_jobs_compute.py`
- Test: `tests/matmaster/context/test_workspace_jobs_compute.py`

- [ ] **Step 2.1: 写失败测试**

新建 `tests/matmaster/context/test_workspace_jobs_compute.py`：

```python
from matmaster.context.ports import WorkspaceJobs
from matmaster.context.workspace_jobs_compute import (
    CSV_COLUMNS,
    build_csv_rows,
    build_csv_text,
    compute_inline_chars,
    compute_summary,
    render_csv_block,
    render_inline_lines,
    select_priority_samples,
)


def _job(job_id: str, status: str, **extra) -> dict:
    return {"job_id": job_id, "job_name": f"n-{job_id}", "status": status, **extra}


def test_compute_summary_counts_groups_and_statuses() -> None:
    active = (_job("a1", "running"), _job("a2", "running"))
    pending = (_job("p1", "failed"), _job("p2", "finished"))
    recent = (_job("r1", "finished"),)
    s = compute_summary(active, pending, recent)
    assert s.total == 5
    assert (s.active, s.pending_terminal, s.recent_terminal) == (2, 2, 1)
    assert s.by_status == {"running": 2, "failed": 1, "finished": 2}
    assert (s.failed, s.stopped, s.lost) == (1, 0, 0)


def test_render_inline_lines_columnar_and_chars_consistent() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        active_jobs=(_job("a1", "running"),),
        mode="workspace_observation",
        summary=compute_summary((_job("a1", "running"),), (), ()),
    )
    lines = render_inline_lines(jobs)
    assert lines[0] == "workspace /share/p"
    assert lines[1] == "mode workspace_observation"
    assert lines[2].startswith("summary {")
    assert lines[3] == "active job_id,job_name,status"
    assert lines[4] == "a1,n-a1,running"
    assert compute_inline_chars(jobs) == len("\n".join(lines))


def test_render_csv_block_header_then_escaped_values() -> None:
    rows = [{"job_id": "1", "status": "failed", "input_dir": "a,b"}]
    block = render_csv_block(
        "pending_terminal", ("job_id", "status", "input_dir"), rows
    )
    assert block[0] == "pending_terminal job_id,status,input_dir"
    assert block[1] == '1,failed,"a,b"'


def test_select_priority_samples_action_first_then_fill() -> None:
    pending = (
        _job("p1", "failed"),
        _job("p2", "lost"),
        _job("p3", "finished"),
    )
    active = (_job("a1", "running"),)
    recent = (_job("r1", "finished"),)
    samples = select_priority_samples(
        active, pending, recent, action_limit=200, fill_limit=20
    )
    # 前两条是 action（failed/lost）
    assert samples[0]["job_id"] == "p1"
    assert samples[1]["job_id"] == "p2"
    # fill 含其余 pending(finished) + active + recent
    fill_ids = {s["job_id"] for s in samples[2:]}
    assert fill_ids == {"p3", "a1", "r1"}


def test_select_priority_samples_action_limit_truncates() -> None:
    pending = tuple(_job(f"f{i}", "failed") for i in range(5))
    samples = select_priority_samples(
        (), pending, (), action_limit=2, fill_limit=20
    )
    # 只前 2 条 failed；其余 failed 既不在 action 也不在 fill
    assert len(samples) == 2
    assert [s["job_id"] for s in samples] == ["f0", "f1"]


def test_build_csv_rows_adds_group_and_total_matches() -> None:
    active = (_job("a1", "running"),)
    pending = (_job("p1", "failed"),)
    recent = (_job("r1", "finished"),)
    rows = build_csv_rows(active, pending, recent)
    assert len(rows) == 3
    assert rows[0]["group"] == "active"
    assert rows[1]["group"] == "pending_terminal"
    assert rows[2]["group"] == "recent_terminal"


def test_build_csv_text_fixed_header_bool_none_and_extras_dropped() -> None:
    rows = [
        {
            "group": "pending_terminal", "job_id": "1", "job_name": "n",
            "status": "failed", "sandbox": True, "result_dir": None,
            "user_id": "SECRET", "org_id": "SECRET",  # 列集外，必须被丢弃
        }
    ]
    text = build_csv_text(rows)
    header = text.splitlines()[0]
    assert header == ",".join(CSV_COLUMNS)
    assert "SECRET" not in text
    body = text.splitlines()[1]
    assert "true" in body  # bool 小写
    # result_dir=None 与缺失列均为空串：尾部连续逗号
    assert body.count(",") == len(CSV_COLUMNS) - 1
```

- [ ] **Step 2.2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/context/test_workspace_jobs_compute.py -v`
Expected: FAIL，`ModuleNotFoundError: matmaster.context.workspace_jobs_compute`

- [ ] **Step 2.3: 创建 `workspace_jobs_compute.py`**

```python
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
```

- [ ] **Step 2.4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/context/test_workspace_jobs_compute.py -v`
Expected: PASS（6 passed）

- [ ] **Step 2.5: Commit**

```bash
git add matmaster/context/workspace_jobs_compute.py tests/matmaster/context/test_workspace_jobs_compute.py
git commit -m "feat(context): add workspace jobs compute helpers"
```

---

## Task 3: CSV exporter

新增 service helper，用 `session.write_file` 把 CSV 落到 `{execution_workdir}/.matmaster/context/workspace_jobs/` 下，失败时返回 `WorkspaceJobsExportError`。

**Files:**
- Create: `src/services/workspace_jobs_export.py`
- Test: `tests/services/test_workspace_jobs_export.py`

- [ ] **Step 3.1: 写失败测试**

新建 `tests/services/test_workspace_jobs_export.py`：

```python
from unittest.mock import MagicMock

from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
)
from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter


def _jobs() -> WorkspaceJobs:
    return WorkspaceJobs(
        workspace="/share/p",
        active_jobs=({"job_id": "a1", "status": "running"},),
        pending_terminal_jobs=({"id": 1, "job_id": "p1", "status": "failed"},),
    )


def _exporter(session, *, workdir="/share/p") -> WorkspaceJobsCsvExporter:
    return WorkspaceJobsCsvExporter(
        session=session,
        execution_workdir=workdir,
        session_id="sess 123",   # 含空格，验证 slug
        invocation_id="inv-456",
        task_id="task-789",
    )


def test_export_writes_csv_and_returns_metadata() -> None:
    session = MagicMock()
    result = _exporter(session).export(_jobs(), reason="row_limit")
    assert isinstance(result, WorkspaceJobsExport)
    assert result.path == (
        "/share/p/.matmaster/context/workspace_jobs/sess_123-inv-456.csv"
    )
    assert result.row_count == 2
    assert result.reason == "row_limit"
    args, kwargs = session.write_file.call_args
    assert args[0] == result.path
    assert kwargs == {"encoding": "utf-8"}
    assert args[1].splitlines()[0].startswith("group,job_id")


def test_export_uses_task_id_when_invocation_missing() -> None:
    session = MagicMock()
    exporter = WorkspaceJobsCsvExporter(
        session=session,
        execution_workdir="/share/p",
        session_id="s",
        invocation_id=None,
        task_id="task-789",
    )
    result = exporter.export(_jobs(), reason="char_limit")
    assert result.path.endswith("/s-task-789.csv")


def test_export_session_missing() -> None:
    result = _exporter(None).export(_jobs(), reason="row_limit")
    assert isinstance(result, WorkspaceJobsExportError)
    assert result.reason == "session_missing"
    assert result.rows == 2


def test_export_bad_target_path_when_workdir_empty() -> None:
    session = MagicMock()
    result = _exporter(session, workdir="").export(_jobs(), reason="row_limit")
    assert isinstance(result, WorkspaceJobsExportError)
    assert result.reason == "bad_target_path"
    session.write_file.assert_not_called()


def test_export_write_failed() -> None:
    session = MagicMock()
    session.write_file.side_effect = OSError("disk full")
    result = _exporter(session).export(_jobs(), reason="row_limit")
    assert isinstance(result, WorkspaceJobsExportError)
    assert result.reason == "write_failed"
```

- [ ] **Step 3.2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_workspace_jobs_export.py -v`
Expected: FAIL，`ModuleNotFoundError: src.services.workspace_jobs_export`

- [ ] **Step 3.3: 创建 `workspace_jobs_export.py`**

```python
from __future__ import annotations

import logging
import re
from typing import Literal

from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
)
from matmaster.context.workspace_jobs_compute import (
    CSV_COLUMNS,
    build_csv_rows,
    build_csv_text,
)
from matmaster.types.session import Session

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(value: str) -> str:
    return _UNSAFE.sub("_", value)


class WorkspaceJobsCsvExporter:
    """把 workspace job 完整明细导出为当前 session 文件系统下的 CSV。"""

    def __init__(
        self,
        *,
        session: Session | None,
        execution_workdir: str,
        session_id: str,
        invocation_id: str | None,
        task_id: str | None,
    ) -> None:
        self._session = session
        self._execution_workdir = execution_workdir
        self._session_id = session_id
        self._invocation_id = invocation_id
        self._task_id = task_id

    def export(
        self, jobs: WorkspaceJobs, *, reason: Literal["row_limit", "char_limit"]
    ) -> WorkspaceJobsExport | WorkspaceJobsExportError:
        rows = build_csv_rows(
            jobs.active_jobs, jobs.pending_terminal_jobs, jobs.recent_terminal_jobs
        )
        row_count = len(rows)
        target = self._target_path()
        if self._session is None:
            return self._error("session_missing", row_count, target)
        if not self._under_workdir(target):
            return self._error("bad_target_path", row_count, target)
        try:
            text = build_csv_text(rows)
        except Exception:  # noqa: BLE001
            logger.warning(
                "workspace jobs csv serialize failed session_id=%s workspace=%s "
                "rows=%d target_path=%s",
                self._session_id, jobs.workspace, row_count, target, exc_info=True,
            )
            return self._error("serialize_failed", row_count, target)
        try:
            self._session.write_file(target, text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.warning(
                "workspace jobs csv write failed session_id=%s workspace=%s "
                "rows=%d target_path=%s",
                self._session_id, jobs.workspace, row_count, target, exc_info=True,
            )
            return self._error("write_failed", row_count, target)
        return WorkspaceJobsExport(
            path=target,
            format="csv",
            row_count=row_count,
            columns=CSV_COLUMNS,
            reason=reason,
        )

    def _target_path(self) -> str:
        suffix = self._invocation_id or self._task_id or "run"
        name = f"{_slug(self._session_id)}-{_slug(suffix)}.csv"
        base = self._execution_workdir.rstrip("/")
        return f"{base}/.matmaster/context/workspace_jobs/{name}"

    def _under_workdir(self, target: str) -> bool:
        base = self._execution_workdir.rstrip("/")
        return bool(base) and target.startswith(base + "/")

    @staticmethod
    def _error(
        reason: Literal[
            "session_missing", "bad_target_path", "write_failed", "serialize_failed"
        ],
        rows: int,
        target: str,
    ) -> WorkspaceJobsExportError:
        return WorkspaceJobsExportError(reason=reason, rows=rows, target_path=target)
```

- [ ] **Step 3.4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_workspace_jobs_export.py -v`
Expected: PASS（5 passed）

- [ ] **Step 3.5: Commit**

```bash
git add src/services/workspace_jobs_export.py tests/services/test_workspace_jobs_export.py
git commit -m "feat(bohrium): add workspace jobs csv exporter"
```

---

## Task 4: renderer 三态改造

把 `WorkspaceJobsSource` 改成纯三态渲染（inline / compact / error），删除 `detail_limit` 截断逻辑与对应测试。

**Files:**
- Modify: `matmaster/context/sources/workspace_jobs.py`（整体重写）
- Modify: `tests/matmaster/context/sources/test_workspace_jobs.py`（删 `detail_limit` 测试，加三态）

- [ ] **Step 4.1: 改测试——删旧增新**

打开 `tests/matmaster/context/sources/test_workspace_jobs.py`，删除全部 `test_detail_limit_*` 与 `test_overflow_*` 用例（renderer 不再有 `detail_limit`/overflow 概念）。保留并按需调整基础渲染用例，补充三态用例：

```python
from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
    WorkspaceJobsSummary,
)
from matmaster.context.sources.workspace_jobs import WorkspaceJobsSource


def _summary() -> WorkspaceJobsSummary:
    return WorkspaceJobsSummary(
        total=2, active=1, pending_terminal=1, recent_terminal=0,
        by_status={"running": 1, "failed": 1}, failed=1, stopped=0, lost=0,
    )


def test_inline_renders_summary_and_columnar_details() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        active_jobs=({"job_id": "a1", "job_name": "n1", "status": "running"},),
        pending_terminal_jobs=(
            {"job_id": "p1", "job_name": "n2", "status": "failed"},
        ),
        mode="workspace_observation",
        summary=_summary(),
    )
    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content
    lines = content.splitlines()
    assert lines[0] == "workspace /share/p"
    assert lines[1] == "mode workspace_observation"
    assert lines[2].startswith("summary {")
    assert lines[3] == "active job_id,job_name,status"
    assert lines[4] == "a1,n1,running"
    assert "pending_terminal job_id,job_name,status" in content
    assert "p1,n2,failed" in content


def test_compact_renders_export_samples_omitted() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        summary=_summary(),
        export=WorkspaceJobsExport(
            path="/share/p/.matmaster/context/workspace_jobs/s-i.csv",
            format="csv", row_count=1020, columns=("group", "job_id"),
            reason="row_limit",
        ),
        priority_samples=(
            {"job_id": "p1", "job_name": "n2", "status": "failed"},
        ),
        omitted_count=1019,
    )
    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content
    assert "details_exported {" in content
    assert "read_hint " in content
    assert "action_hint " in content  # summary.failed=1
    assert "priority_samples job_id,job_name,status" in content
    assert "p1,n2,failed" in content
    assert "omitted_from_prompt {" in content
    assert "active job_id" not in content  # compact 不渲染 active 明细 block


def test_compact_delivery_adds_ack_scope() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="session_workspace_delivery",
        summary=_summary(),
        export=WorkspaceJobsExport(
            path="/share/p/x.csv", format="csv", row_count=10,
            columns=("group",), reason="row_limit",
        ),
        omitted_count=10,
    )
    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content
    assert "delivery_ack_scope " in content


def test_error_renders_export_error_not_details() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="session_workspace_delivery",
        summary=_summary(),
        export_error=WorkspaceJobsExportError(
            reason="write_failed", rows=1000, target_path="/share/p/x.csv"
        ),
    )
    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content
    assert "workspace_jobs_export_error {" in content
    assert "details_exported" not in content
    assert "do not assume omitted pending jobs were delivered" in content


def test_empty_jobs_render_nothing() -> None:
    assert WorkspaceJobsSource.from_jobs(WorkspaceJobs()).to_sections() == ()
```

- [ ] **Step 4.2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/context/sources/test_workspace_jobs.py -v`
Expected: FAIL（新三态用例因旧 renderer 缺少 export 分支而失败）

- [ ] **Step 4.3: 重写 `workspace_jobs.py`**

整体替换文件内容：

```python
from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.ports import WorkspaceJobs
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder
from matmaster.context.workspace_jobs_compute import (
    SUMMARY_COLUMNS,
    render_csv_block,
    render_inline_lines,
    render_job_json,
    summary_to_dict,
)

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
_ACK_SCOPE = (
    "On successful run, pending terminal rows exported in the CSV are "
    "considered delivered and may be marked handled."
)


@dataclass(frozen=True)
class WorkspaceJobsSource:
    """Renderer for the workspace job view（inline / compact / error 三态）。"""

    lines: tuple[str, ...] = ()

    @classmethod
    def from_jobs(cls, jobs: WorkspaceJobs) -> WorkspaceJobsSource:
        if jobs.export_error is not None:
            return cls(lines=cls._error_lines(jobs))
        if jobs.export is not None:
            return cls(lines=cls._compact_lines(jobs))
        return cls(lines=render_inline_lines(jobs))

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
        if jobs.mode == "session_workspace_delivery":
            lines.append(f'delivery_ack_scope "{_ACK_SCOPE}"')
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
```

- [ ] **Step 4.4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/context/sources/test_workspace_jobs.py -v`
Expected: PASS

- [ ] **Step 4.5: Commit**

```bash
git add matmaster/context/sources/workspace_jobs.py tests/matmaster/context/sources/test_workspace_jobs.py
git commit -m "refactor(context): make workspace jobs renderer bounded three-state"
```

---

## Task 5: wiring 阈值与导出装配

read port 算 summary、判阈值、调 exporter、选样本、填 `mode`；`build_bohrium_jobs_ports` 加 `exporter` 参数；observation port 的 `detail_limit` 拆为 `observation_query_limit`；末尾移除 `WorkspaceJobs.detail_limit` 字段。

**Files:**
- Modify: `src/services/bohrium_jobs_wiring.py`
- Modify: `matmaster/context/ports.py`（移除 `WorkspaceJobs.detail_limit`）
- Modify: `tests/services/test_bohrium_jobs_wiring.py`

- [ ] **Step 5.1: 改测试——删 `detail_limit`、加阈值/导出/mode**

打开 `tests/services/test_bohrium_jobs_wiring.py`，删除 `test_delivery_mode_uses_snapshot_detail_limit` 等断言 `detail_limit` 的用例。补充（用 `monkeypatch.setenv` 控阈值，`MagicMock` 作 exporter）：

```python
import pytest
from unittest.mock import MagicMock

from matmaster.context.ports import (
    WorkspaceJobsExport,
    WorkspaceJobsExportError,
    WorkspaceJobsQuery,
)
from src.services.bohrium_jobs_wiring import build_bohrium_jobs_ports


def _agent_job(job_id: str, status: str = "running") -> dict:
    return {"job_id": job_id, "job_name": f"n-{job_id}", "status": status}


@pytest.mark.asyncio
async def test_observation_inline_when_small(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", "50")
    table = MagicMock()
    table.query_workspace_active.return_value = [_agent_job("a1")]
    table.query_workspace_pending_terminal.return_value = []
    table.query_workspace_recent_terminal.return_value = []
    exporter = MagicMock()
    _, port = build_bohrium_jobs_ports(
        session_id="s", invocation_id="i", user_id="u", org_id="o",
        workspace="/share/p", job_context_mode="workspace_observation",
        table=table, exporter=exporter,
    )
    result = await port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))
    assert result.mode == "workspace_observation"
    assert result.summary.total == 1
    assert result.export is None
    exporter.export.assert_not_called()


@pytest.mark.asyncio
async def test_observation_exports_when_row_limit_exceeded(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", "2")
    table = MagicMock()
    table.query_workspace_active.return_value = [
        _agent_job(f"a{i}") for i in range(5)
    ]
    table.query_workspace_pending_terminal.return_value = []
    table.query_workspace_recent_terminal.return_value = []
    exporter = MagicMock()
    exporter.export.return_value = WorkspaceJobsExport(
        path="/share/p/x.csv", format="csv", row_count=5,
        columns=("group",), reason="row_limit",
    )
    _, port = build_bohrium_jobs_ports(
        session_id="s", invocation_id="i", user_id="u", org_id="o",
        workspace="/share/p", job_context_mode="workspace_observation",
        table=table, exporter=exporter,
    )
    result = await port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))
    assert result.export is not None
    assert result.export.reason == "row_limit"
    assert result.omitted_count == 5 - len(result.priority_samples)
    assert exporter.export.call_args.kwargs["reason"] == "row_limit"


@pytest.mark.asyncio
async def test_exporter_failure_sets_export_error(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", "1")
    table = MagicMock()
    table.query_workspace_active.return_value = [_agent_job("a1"), _agent_job("a2")]
    table.query_workspace_pending_terminal.return_value = []
    table.query_workspace_recent_terminal.return_value = []
    exporter = MagicMock()
    exporter.export.return_value = WorkspaceJobsExportError(
        reason="write_failed", rows=2, target_path="/share/p/x.csv"
    )
    _, port = build_bohrium_jobs_ports(
        session_id="s", invocation_id="i", user_id="u", org_id="o",
        workspace="/share/p", job_context_mode="workspace_observation",
        table=table, exporter=exporter,
    )
    result = await port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))
    assert result.export is None
    assert result.export_error is not None
    assert result.export_error.reason == "write_failed"
```

- [ ] **Step 5.2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py -v`
Expected: FAIL（`build_bohrium_jobs_ports` 暂无 `exporter` 参数 / 结果无 `summary`）

- [ ] **Step 5.3: 改 `bohrium_jobs_wiring.py`——加导入与装配 helper**

在文件顶部 import 区（现有 `from matmaster.context.ports import (...)` 处）扩展导入，并加 compute / exporter / DTO 导入：

```python
from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExportError,
    WorkspaceJobsPort,
    WorkspaceJobsQuery,
)
from matmaster.context.workspace_jobs_compute import (
    compute_inline_chars,
    compute_summary,
    select_priority_samples,
)
from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter
```

在 `_normalize_ledger_workspace` 之后、`_BohriumJobsTableRef` 之前，加一个共享装配纯函数：

```python
def _assemble_workspace_jobs(
    *,
    workspace: str,
    mode: str,
    active: tuple[dict[str, Any], ...],
    pending: tuple[dict[str, Any], ...],
    recent: tuple[dict[str, Any], ...],
    exporter: WorkspaceJobsCsvExporter | None,
) -> WorkspaceJobs:
    summary = compute_summary(active, pending, recent)
    row_limit = env_int("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", 50)
    char_limit = env_int("BOHRIUM_WORKSPACE_JOBS_INLINE_CHAR_LIMIT", 12000)
    inline = WorkspaceJobs(
        workspace=workspace,
        active_jobs=active,
        pending_terminal_jobs=pending,
        recent_terminal_jobs=recent,
        mode=mode,
        summary=summary,
    )
    if summary.total <= row_limit:
        if compute_inline_chars(inline) <= char_limit:
            return inline
        reason = "char_limit"
    else:
        reason = "row_limit"

    samples = select_priority_samples(
        active,
        pending,
        recent,
        action_limit=env_int("BOHRIUM_WORKSPACE_JOBS_ACTION_SAMPLE_LIMIT", 200),
        fill_limit=env_int("BOHRIUM_WORKSPACE_JOBS_PRIORITY_SAMPLE_LIMIT", 20),
    )
    omitted = summary.total - len(samples)
    if exporter is None:
        outcome: Any = WorkspaceJobsExportError(
            reason="session_missing", rows=summary.total, target_path=""
        )
    else:
        outcome = exporter.export(inline, reason=reason)
    if isinstance(outcome, WorkspaceJobsExportError):
        return WorkspaceJobs(
            workspace=workspace,
            mode=mode,
            summary=summary,
            export_error=outcome,
            priority_samples=samples,
            omitted_count=omitted,
        )
    return WorkspaceJobs(
        workspace=workspace,
        mode=mode,
        summary=summary,
        export=outcome,
        priority_samples=samples,
        omitted_count=omitted,
    )
```

- [ ] **Step 5.4: 改两个 read port**

`_SessionWorkspaceDeliveryJobsPort`：构造函数加 `exporter`，`load_workspace_jobs` 改用 `_assemble_workspace_jobs`，删 `detail_limit`：

```python
class _SessionWorkspaceDeliveryJobsPort:
    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        workspace: str,
        snapshot: DeliverySnapshot | None = None,
        exporter: WorkspaceJobsCsvExporter | None = None,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._snapshot = snapshot
        self._exporter = exporter

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        try:
            table = self._table_ref.get()
            active = await asyncio.to_thread(
                table.query_session_active,
                user_id=self._user_id,
                org_id=self._org_id,
                session_id=query.session_id,
                workspace=self._workspace,
            )
            pending = self._snapshot.rows if self._snapshot is not None else ()
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs(delivery) failed session_id=%s workspace=%s",
                query.session_id,
                self._workspace,
                exc_info=True,
            )
            return WorkspaceJobs.empty()
        return _assemble_workspace_jobs(
            workspace=self._workspace,
            mode="session_workspace_delivery",
            active=tuple(active),
            pending=tuple(pending),
            recent=(),
            exporter=self._exporter,
        )
```

`_WorkspaceObservationJobsPort`：`detail_limit` 改名 `observation_query_limit`（仅用于 DB query），加 `exporter`，`load_workspace_jobs` 改用 helper：

```python
class _WorkspaceObservationJobsPort:
    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        workspace: str,
        observation_query_limit: int,
        exporter: WorkspaceJobsCsvExporter | None = None,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._observation_query_limit = observation_query_limit
        self._exporter = exporter

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        try:
            table = self._table_ref.get()
            active, pending, recent = await asyncio.gather(
                asyncio.to_thread(
                    table.query_workspace_active,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                ),
                asyncio.to_thread(
                    table.query_workspace_pending_terminal,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                    limit=self._observation_query_limit,
                ),
                asyncio.to_thread(
                    table.query_workspace_recent_terminal,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                    limit=self._observation_query_limit,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs(observation) failed workspace=%s",
                self._workspace,
                exc_info=True,
            )
            return WorkspaceJobs.empty()
        return _assemble_workspace_jobs(
            workspace=self._workspace,
            mode="workspace_observation",
            active=tuple(active),
            pending=tuple(pending),
            recent=tuple(recent),
            exporter=self._exporter,
        )
```

- [ ] **Step 5.5: 改 `build_bohrium_jobs_ports`**

签名加 `exporter` 参数；observation 分支用新 env 与参数名；delivery 分支传 `exporter`：

```python
def build_bohrium_jobs_ports(
    *,
    session_id: str,
    invocation_id: str | None,
    user_id: str,
    org_id: str,
    workspace: str | None,
    job_context_mode: str = "session_workspace_delivery",
    spawn_id: str | None = None,
    delivery_snapshot: DeliverySnapshot | None = None,
    exporter: WorkspaceJobsCsvExporter | None = None,
    table: BohriumJobsTable | None = None,
    table_factory: Callable[[], BohriumJobsTable] = get_bohrium_jobs_table,
) -> tuple[_BohriumJobLedger | None, WorkspaceJobsPort]:
```

`ledger` 构造段不变。把读 port 选择段改为：

```python
    if normalized_workspace is None or not (user_id and org_id):
        jobs: WorkspaceJobsPort = _EmptyWorkspaceJobsPort()
    elif job_context_mode == "workspace_observation":
        jobs = _WorkspaceObservationJobsPort(
            table_ref=table_ref,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            observation_query_limit=env_int(
                "BOHRIUM_WORKSPACE_JOBS_OBSERVATION_QUERY_LIMIT", 20
            ),
            exporter=exporter,
        )
    elif job_context_mode == "session_workspace_delivery":
        jobs = _SessionWorkspaceDeliveryJobsPort(
            table_ref=table_ref,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            snapshot=delivery_snapshot,
            exporter=exporter,
        )
    else:
        jobs = _EmptyWorkspaceJobsPort()
    return ledger, jobs
```

- [ ] **Step 5.6: 移除 `WorkspaceJobs.detail_limit` 字段**

此时 renderer（Task 4）与 wiring（本 Task）均不再读/塞 `detail_limit`。在 `matmaster/context/ports.py` 的 `WorkspaceJobs` 中删除这一行：

```python
    detail_limit: int | None = None  # 过渡字段，Task 5 末尾移除
```

确认无残留引用（`DeliverySnapshot.detail_limit` 是独立字段，Task 7 处理）：

Run: `grep -rn "\.detail_limit\|detail_limit=" matmaster/ src/services/bohrium_jobs_wiring.py`
Expected: 仅 `bohrium_delivery_ack.py` 仍出现（Task 7 清理），`bohrium_jobs_wiring.py` 与 `matmaster/` 无输出

- [ ] **Step 5.7: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py tests/matmaster/context/ -v`
Expected: PASS

- [ ] **Step 5.8: Commit**

```bash
git add matmaster/context/ports.py src/services/bohrium_jobs_wiring.py tests/services/test_bohrium_jobs_wiring.py
git commit -m "feat(bohrium): wire workspace jobs csv export thresholds"
```

---

## Task 6: 注入 exporter 到 AgentRunService

构造 `WorkspaceJobsCsvExporter` 并传入 `build_bohrium_jobs_ports`。

**Files:**
- Modify: `src/services/agent_run_service.py:543-551`

- [ ] **Step 6.1: 确认 `task_id` 在作用域**

Run: `grep -n "task_id" src/services/agent_run_service.py`
Expected: 确认 `task_id` 是 `run_agent` 参数或局部变量，在 `build_bohrium_jobs_ports` 调用点可用。若变量名不同（如 `_task_id`），下一步按实际名替换。

- [ ] **Step 6.2: 构造并注入 exporter**

在文件顶部 import 区加：

```python
from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter
```

在 `build_bohrium_jobs_ports(...)` 调用（当前约 543-551 行）之前构造 exporter，并把它作为参数传入：

```python
            workspace_jobs_exporter = WorkspaceJobsCsvExporter(
                session=environment.session,
                execution_workdir=environment.execution_workdir
                or str(environment.workdir),
                session_id=session_id,
                invocation_id=invocation_id,
                task_id=task_id,
            )
            bohrium_ledger_port, bohrium_jobs_port = build_bohrium_jobs_ports(
                session_id=session_id,
                invocation_id=invocation_id,
                user_id=_ledger_user_id,
                org_id=_ledger_org_id,
                workspace=stage_result.workspace,
                job_context_mode=job_context_mode,
                delivery_snapshot=delivery_snapshot,
                exporter=workspace_jobs_exporter,
            )
```

- [ ] **Step 6.3: 冒烟验证导入与现有 service 测试**

Run: `uv run pytest tests/services/ -q`
Expected: PASS（无 import error，现有 service 测试不回归）

- [ ] **Step 6.4: Commit**

```bash
git add src/services/agent_run_service.py
git commit -m "feat(bohrium): inject workspace jobs csv exporter into run service"
```

---

## Task 7: Delivery ack 边界

`DeliverySnapshot` 移除 `detail_limit`、加可变 `export_failure`；delivery port 导出失败时写入该状态；`confirm` 拆两路：导出失败仅跳过 `snapshot.rows` 的 ack，`observed_terminal` 照常 confirm。

**Files:**
- Modify: `src/services/bohrium_delivery_ack.py`
- Modify: `src/services/bohrium_jobs_wiring.py`（delivery port 写 `export_failure`）
- Modify: `tests/services/test_bohrium_delivery_ack.py`

- [ ] **Step 7.1: 改测试——删 `detail_limit`、加 export_failure 语义**

打开 `tests/services/test_bohrium_delivery_ack.py`，删除 `test_snapshot_reads_detail_limit_from_env`、`test_snapshot_detail_limit_defaults_when_env_unset`，并从所有 `DeliverySnapshot(...)` 构造里去掉 `detail_limit=...` 参数。补充：

```python
def _row(row_id: int, job_id: str) -> dict:
    return {"id": row_id, "job_id": job_id, "status": "finished"}


def test_confirm_skips_rows_when_export_failed_but_keeps_observed() -> None:
    table = MagicMock()
    table.mark_handled_by_ids.return_value = 0
    table.mark_handled_by_job_keys.return_value = 1
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u", org_id="o", session_id="s", workspace="/share/p",
        rows=(_row(11, "a"), _row(12, "b")),
    )
    snap.observed_terminal.add((True, "J"))
    snap.export_failure.update({"reason": "write_failed", "rows": 2})

    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 1
    table.mark_handled_by_ids.assert_not_called()
    table.mark_handled_by_job_keys.assert_called_once()


def test_confirm_acks_rows_when_export_not_failed() -> None:
    table = MagicMock()
    table.mark_handled_by_ids.return_value = 2
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u", org_id="o", session_id="s", workspace="/share/p",
        rows=(_row(11, "a"), _row(12, "b")),
    )
    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 2
    table.mark_handled_by_ids.assert_called_once()
```

- [ ] **Step 7.2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_bohrium_delivery_ack.py -v`
Expected: FAIL（`DeliverySnapshot` 无 `export_failure` / `confirm` 未跳过 rows）

- [ ] **Step 7.3: 改 `bohrium_delivery_ack.py`**

`DeliverySnapshot`：删 `detail_limit`，加 `export_failure`：

```python
@dataclass(frozen=True)
class DeliverySnapshot:
    """一次 run 的交付边界快照 + run 内前台观察集（worker 内存对象，不落表）。

    rows 持全量行、不预截断。observed_terminal 元素为 (sandbox, job_id)。
    export_failure 是可变 dict 容器（frozen 字段绑可变对象）：CSV 导出失败时由
    delivery read port 写入 {reason, rows, target_path}，confirm 据此跳过 rows ack。
    写入发生在 run 内工具执行/上下文装配，confirm 读取在 run 结束后，无时间重叠。
    """

    user_id: str
    org_id: str
    session_id: str
    workspace: str
    rows: tuple[dict[str, Any], ...]
    observed_terminal: set[tuple[bool, str]] = field(default_factory=set)
    export_failure: dict[str, Any] = field(default_factory=dict)
```

`snapshot()`：删 `detail_limit=env_int(...)`，并删除文件顶部不再使用的 `from src.utils.constant import env_int` 导入。`return` 改为：

```python
    return DeliverySnapshot(
        user_id=user_id,
        org_id=org_id,
        session_id=session_id,
        workspace=workspace,
        rows=tuple(rows),
    )
```

`confirm()`：rows 那一路加 `not snap.export_failure` 守卫：

```python
    affected = 0
    if snap.rows and not snap.export_failure:
        affected += table.mark_handled_by_ids(
            user_id=snap.user_id,
            org_id=snap.org_id,
            session_id=snap.session_id,
            workspace=snap.workspace,
            row_ids=tuple(int(j["id"]) for j in snap.rows),
        )
    if snap.observed_terminal:
        affected += table.mark_handled_by_job_keys(
            user_id=snap.user_id,
            org_id=snap.org_id,
            session_id=snap.session_id,
            workspace=snap.workspace,
            job_keys=tuple(snap.observed_terminal),
        )
    return affected
```

- [ ] **Step 7.4: delivery port 导出失败时写 `export_failure`**

在 `src/services/bohrium_jobs_wiring.py` 的 `_SessionWorkspaceDeliveryJobsPort.load_workspace_jobs` 末尾，把 `return _assemble_workspace_jobs(...)` 改为先接收结果、失败时回写 snapshot：

```python
        result = _assemble_workspace_jobs(
            workspace=self._workspace,
            mode="session_workspace_delivery",
            active=tuple(active),
            pending=tuple(pending),
            recent=(),
            exporter=self._exporter,
        )
        if self._snapshot is not None and result.export_error is not None:
            self._snapshot.export_failure.update(
                {
                    "reason": result.export_error.reason,
                    "rows": result.export_error.rows,
                    "target_path": result.export_error.target_path,
                }
            )
        return result
```

- [ ] **Step 7.5: 跑测试确认通过**

Run: `uv run pytest tests/services/test_bohrium_delivery_ack.py tests/services/test_bohrium_jobs_wiring.py -v`
Expected: PASS

- [ ] **Step 7.6: 全量回归 + 确认 `detail_limit` 彻底清除**

Run: `grep -rn "detail_limit" matmaster/ src/`
Expected: 无输出

Run: `uv run pytest tests/ -q`
Expected: PASS（无回归）

- [ ] **Step 7.7: Commit**

```bash
git add src/services/bohrium_delivery_ack.py src/services/bohrium_jobs_wiring.py tests/services/test_bohrium_delivery_ack.py
git commit -m "feat(bohrium): gate delivery ack on workspace jobs csv export"
```

---

## 完成标准

- `uv run pytest tests/ -q` 全绿。
- `grep -rn "detail_limit" matmaster/ src/` 无输出。
- 1000 条 job 场景：`workspace_jobs` section 不含 1000 个 job_id，含 CSV path、summary、priority samples；CSV 行数 == `summary.total`。
- delivery 模式：CSV 成功 → `snapshot.rows` 照常 confirm；CSV 失败 → 仅 `observed_terminal` 被 confirm。
