# Workspace Jobs Required / Reference / Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split workspace-jobs observation into three mutually-exclusive buckets (active / unhandled-terminal / handled-recent-terminal), collapse five overlapping env limits into three role-specific ones, and gate delivery ack whenever required context is incomplete.

**Architecture:** Observation reads three cross-session buckets with a `limit + 1` probe to detect truncation; required buckets (active + unhandled-terminal) share `REQUIRED_FETCH_LIMIT`, the reference bucket (handled-recent) uses `HANDLED_RECENT_LIMIT`, and prompt preview uses `PROMPT_PREVIEW_LIMIT`. When required context is incomplete (cap hit OR query failure) the observation port writes a sticky `required_block` onto the run's `DeliverySnapshot`; `confirm()` skips `snapshot.rows` ack when either `export_failure` or `required_block` is set.

**Tech Stack:** Python 3.13, frozen dataclasses, raw-SQL PyMySQL DAO, asyncio, pytest (real-MySQL DAO tests gated on `.env.test`).

**Spec:** `docs/superpowers/specs/2026-06-17-workspace-jobs-required-reference-preview-design.md`

---

## Implementation Decisions (spec deviations — review before executing)

These are deliberate engineering calls made while turning the spec into code. Object now if any is wrong.

1. **The shared DTO field `pending_terminal_jobs` is renamed to `unhandled_terminal_jobs` and reused by BOTH modes.** Delivery's `snapshot.rows` are exactly "terminal but unhandled" jobs of this session, so they belong in `unhandled_terminal_jobs`. The spec's "delivery keeps `pending` naming" (§6) applies to the worker/DAO concepts (`delivery_snapshot.rows`, `list_pending_terminal_snapshot`, `scan_delivery_units` aggregate), NOT the shared context DTO. Those DAO/worker names stay `pending`.

2. **The compact `prompt_preview {...}` block includes `preview_limit` and is char-safe.** It is not redundant: observation can enter compact mode because of `char_limit` while `snapshot_rows <= PROMPT_PREVIEW_LIMIT`, so `preview_rows` may be smaller than the configured limit. Carry the configured limit on `WorkspaceJobs.preview_limit` whenever compact/export output is returned, and trim/truncate preview rows before rendering so compact preview cannot reinsert the same oversized values that forced CSV export.

3. **Required query failure and reference query failure are different.** Active and unhandled-terminal query failures write `DeliverySnapshot.required_block` and render a visible `required_context_error`. Handled-recent query failure does not write `required_block`; it sets `handled_recent_unavailable=true` and omits reference history for this observation.

4. **Delivery keeps a row-only inline threshold as an explicit deviation.** Delivery inline text is still bounded by `PROMPT_PREVIEW_LIMIT` rows, but `job_name` length is not independently capped here. This preserves the current delivery rendering path and avoids duplicating delivery text rendering inside wiring. Add an explicit regression test documenting this choice so it is not mistaken for an accidental omission.

## Scope: what is NOT renamed

`scan_delivery_units` aggregate alias `pending_terminal` (`src/dao/bohrium_jobs_table.py:395,453`) and its consumer `src/services/bohrium_completion_scheduler.py:67,74` are the delivery aggregate count — a different concept. Leave them untouched. Only the observation `WorkspaceJobsSummary` fields and `WorkspaceJobs` buckets are renamed.

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `matmaster/context/ports.py` | DTOs | Rename `WorkspaceJobs`/`WorkspaceJobsSummary` fields; add `required_truncated`/`handled_recent_has_more`/`handled_recent_unavailable`/`required_error`/`preview_limit`; add `summary.unhandled_action`; `priority_samples`→`preview_rows`; drop `snapshot_truncated` |
| `matmaster/context/workspace_jobs_compute.py` | Pure compute/render helpers | Rename summary keys + group labels; add `PREVIEW_COLUMNS`; replace `select_priority_samples` with `select_observation_preview_rows` + `select_delivery_preview_rows`; add preview char-safety helpers; flag/hint lines in `render_inline_lines` |
| `matmaster/context/sources/workspace_jobs.py` | Section/turn-instruction renderer | New bucket labels + flag lines + preview block w/ group; delivery reads `unhandled_terminal_jobs` |
| `src/dao/bohrium_jobs_table.py` | bohrium_jobs DAO | `query_workspace_active` gains `limit`; rename pending→`unhandled_terminal`; recent→`handled_recent_terminal` with `handled_at IS NOT NULL` |
| `src/services/bohrium_jobs_wiring.py` | Port assembly | Read 3 new env vars; observation `limit+1` + `required_block`; delivery uses preview limit |
| `src/services/workspace_jobs_export.py` | CSV exporter | Read renamed DTO fields |
| `src/services/bohrium_delivery_ack.py` | Snapshot + ack | Add `required_block`; `confirm()` checks it |

**Dependency order:** Task 1 (DAO) and Task 2 (ack) are independent. Tasks 3–7 form a no-compatibility rename cluster — the full suite is expected RED between them; each task makes its OWN test file green, and Task 8 restores full green. Do the tasks in order.

**Per-task verification env:** real-MySQL DAO tests (Task 1) need `.env.test`; if absent they SKIP (acceptable, note it). All other tasks run without a DB.

**Commit convention:** this repo's commits carry NO `Co-Authored-By` trailer. Use the exact commit commands shown.

---

## Task 1: DAO — three observation queries (rename + handled filter + active limit)

DAO returns `list[dict]`, independent of the DTO rename. The `limit + 1` arithmetic lives in the caller (Task 7); the DAO just applies `LIMIT %s`.

**Files:**
- Modify: `src/dao/bohrium_jobs_table.py:216-261`
- Test: `tests/dao/test_bohrium_jobs_delivery.py:477-531`

- [ ] **Step 1: Update the DAO tests to the new method names + handled-recent semantics**

Replace the three existing tests (`test_query_workspace_active_spans_sessions`, `test_query_workspace_pending_terminal_spans_sessions_with_limit`, `test_query_workspace_recent_terminal_ignores_handled_and_orders_desc`) with:

```python
def test_query_workspace_active_spans_sessions_with_limit(jobs_table, sessions_shadow):
    _register_session(sessions_shadow, session="sess-A")
    _register_session(sessions_shadow, session="sess-B")
    _seed_job(jobs_table, session="sess-A", job_id="601")
    _seed_job(jobs_table, session="sess-B", job_id="602")

    rows = jobs_table.query_workspace_active(
        user_id="u1", org_id="o1", workspace="/share/project", limit=10
    )
    assert sorted(r["job_id"] for r in rows) == ["601", "602"]

    limited = jobs_table.query_workspace_active(
        user_id="u1", org_id="o1", workspace="/share/project", limit=1
    )
    assert len(limited) == 1


def test_query_workspace_unhandled_terminal_spans_sessions_with_limit(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow, session="sess-A")
    _register_session(sessions_shadow, session="sess-B")
    _seed_job(jobs_table, session="sess-A", job_id="701", status="finished")
    _seed_job(jobs_table, session="sess-B", job_id="702", status="finished")

    rows = jobs_table.query_workspace_unhandled_terminal(
        user_id="u1", org_id="o1", workspace="/share/project", limit=10
    )
    assert sorted(r["job_id"] for r in rows) == ["701", "702"]

    limited = jobs_table.query_workspace_unhandled_terminal(
        user_id="u1", org_id="o1", workspace="/share/project", limit=1
    )
    assert len(limited) == 1


def test_query_workspace_handled_recent_terminal_excludes_unhandled_desc(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="801", status="finished")
    _seed_job(jobs_table, job_id="802", status="finished")
    _shift_terminal_at(sessions_shadow, job_id="801", seconds_ago=300)
    _shift_terminal_at(sessions_shadow, job_id="802", seconds_ago=100)
    # ack 801 → handled; 802 stays unhandled
    snap_rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    jobs_table.mark_handled_by_ids(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        row_ids=[r["id"] for r in snap_rows if r["job_id"] == "801"],
    )

    rows = jobs_table.query_workspace_handled_recent_terminal(
        user_id="u1", org_id="o1", workspace="/share/project", limit=10
    )
    # only the handled row; unhandled 802 excluded
    assert [r["job_id"] for r in rows] == ["801"]
```

- [ ] **Step 2: Run the tests, expect failures**

Run: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py -k "workspace_active or unhandled_terminal or handled_recent" -q`
Expected: FAIL with `AttributeError: ... has no attribute 'query_workspace_unhandled_terminal'` / `query_workspace_handled_recent_terminal`, and the active test fails on the unexpected `limit` kwarg. (If no `.env.test`: SKIP — proceed, this task's behavior is covered by Task 7's mocked tests too.)

- [ ] **Step 3: Rewrite the three DAO methods**

Replace `src/dao/bohrium_jobs_table.py:216-261` (the `query_workspace_active`, `query_workspace_pending_terminal`, `query_workspace_recent_terminal` block) with:

```python
    def query_workspace_active(
        self, *, user_id: str, org_id: str, workspace: str, limit: int
    ) -> list[dict[str, Any]]:
        """workspace 观察视图：跨 session 的活跃作业（required，带 fetch cap）。"""
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND workspace = %s
              AND status IN ({_SQL_ACTIVE})
            ORDER BY submitted_at ASC, id ASC
            LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, workspace, int(limit)))
                return [self._to_agent_job(r) for r in cur.fetchall()]

    def query_workspace_unhandled_terminal(
        self, *, user_id: str, org_id: str, workspace: str, limit: int
    ) -> list[dict[str, Any]]:
        """workspace 观察视图：跨 session 的未处理终态作业（required）。"""
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND workspace = %s
              AND terminal_at IS NOT NULL AND handled_at IS NULL
            ORDER BY terminal_at ASC, submitted_at ASC, id ASC
            LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, workspace, int(limit)))
                return [self._to_agent_job(r) for r in cur.fetchall()]

    def query_workspace_handled_recent_terminal(
        self, *, user_id: str, org_id: str, workspace: str, limit: int
    ) -> list[dict[str, Any]]:
        """workspace 观察视图：跨 session 的已处理最近终态作业（reference）。

        必须排除未处理终态行，避免与 unhandled_terminal bucket 重叠。
        """
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND workspace = %s
              AND terminal_at IS NOT NULL AND handled_at IS NOT NULL
            ORDER BY terminal_at DESC, id DESC
            LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, workspace, int(limit)))
                return [self._to_agent_job(r) for r in cur.fetchall()]
```

- [ ] **Step 4: Run the tests, expect pass**

Run: `uv run pytest tests/dao/test_bohrium_jobs_delivery.py -k "workspace_active or unhandled_terminal or handled_recent" -q`
Expected: PASS (or SKIP without `.env.test`).

- [ ] **Step 5: Commit**

```bash
git add src/dao/bohrium_jobs_table.py tests/dao/test_bohrium_jobs_delivery.py
git commit -m "feat(dao): split workspace observation into unhandled/handled-recent queries with active limit"
```

---

## Task 2: DeliverySnapshot.required_block + confirm() gating

Independent of the rename. Symmetric with the existing `export_failure` mutable container.

**Files:**
- Modify: `src/services/bohrium_delivery_ack.py:32-38` (dataclass) and `:118-125` (confirm guard)
- Test: `tests/services/test_bohrium_delivery_ack.py` (append two tests)

- [ ] **Step 1: Add the failing tests**

Append to `tests/services/test_bohrium_delivery_ack.py`:

```python
def test_confirm_skips_rows_when_required_block_set_but_acks_observed():
    table = MagicMock()
    table.mark_handled_by_job_keys.return_value = 1
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        workspace="/share/project",
        rows=(_row(11, "f1", status="failed"),),
        required_block={"reason": "required_truncated"},
    )
    snap.observed_terminal.add((True, "J"))

    affected = bohrium_delivery_ack.confirm(snap, jobs_table=table)

    table.mark_handled_by_ids.assert_not_called()
    table.mark_handled_by_job_keys.assert_called_once()
    assert affected == 1


def test_confirm_acks_rows_when_required_block_empty():
    table = MagicMock()
    table.mark_handled_by_ids.return_value = 1
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        workspace="/share/project",
        rows=(_row(11, "t1"),),
    )

    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 1
    table.mark_handled_by_ids.assert_called_once()
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/services/test_bohrium_delivery_ack.py -k required_block -q`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'required_block'`.

- [ ] **Step 3: Add the field**

In `src/services/bohrium_delivery_ack.py`, add to the `DeliverySnapshot` dataclass (after `export_failure`, around line 38):

```python
    required_block: dict[str, Any] = field(default_factory=dict)
```

Update the class docstring's first paragraph to mention it alongside `export_failure`:

```python
    export_failure 由 read port 在 CSV 导出失败时写入；required_block 由 observation
    read port 在 required context 不完整（命中 cap 或查询失败）时写入。confirm 据二者
    任一非空即 gate snapshot.rows 的 ack。写入在 run 内上下文装配、读取在 run 收尾，
    与 observed_terminal 同属 frozen 字段绑定的可变容器，无时间重叠。
```

- [ ] **Step 4: Add the confirm guard**

In `confirm()`, change the rows-ack guard (currently `if snap.rows and not snap.export_failure:` at line 118) to:

```python
    if snap.rows and not snap.export_failure and not snap.required_block:
```

- [ ] **Step 5: Run, expect pass**

Run: `uv run pytest tests/services/test_bohrium_delivery_ack.py -q`
Expected: PASS (all, including the pre-existing export_failure tests).

- [ ] **Step 6: Commit**

```bash
git add src/services/bohrium_delivery_ack.py tests/services/test_bohrium_delivery_ack.py
git commit -m "feat(ack): gate snapshot.rows ack on DeliverySnapshot.required_block"
```

---

## Task 3: DTO rename + flags (ports.py)

Foundational for the cluster. After this, `compute`/`exporter`/`renderer`/`wiring` reference dead field names — their tests stay red until their tasks.

**Files:**
- Modify: `matmaster/context/ports.py:107-145`
- Test: `tests/matmaster/context/test_workspace_jobs_dto.py`

- [ ] **Step 1: Rewrite the DTO test to new field names**

Replace `tests/matmaster/context/test_workspace_jobs_dto.py` body (keep imports) with:

```python
def test_workspace_jobs_new_fields_default() -> None:
    jobs = WorkspaceJobs()
    assert jobs.mode is None
    assert jobs.summary is None
    assert jobs.export is None
    assert jobs.export_error is None
    assert jobs.active_jobs == ()
    assert jobs.unhandled_terminal_jobs == ()
    assert jobs.handled_recent_terminal_jobs == ()
    assert jobs.required_error is None
    assert jobs.preview_limit is None
    assert jobs.preview_rows == ()
    assert jobs.omitted_count is None
    assert jobs.required_truncated is False
    assert jobs.handled_recent_has_more is False
    assert jobs.handled_recent_unavailable is False


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
        total=3,
        active=2,
        unhandled_terminal=1,
        handled_recent_terminal=0,
        by_status={"running": 2, "failed": 1},
        failed=1,
        stopped=0,
        lost=0,
        unhandled_action=1,
    )
    err = WorkspaceJobsExportError(
        reason="write_failed", rows=3, target_path="/w/x.csv"
    )
    assert summary.total == 3
    assert summary.unhandled_terminal == 1
    assert summary.by_status["failed"] == 1
    assert err.reason == "write_failed"
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/matmaster/context/test_workspace_jobs_dto.py -q`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'unhandled_terminal'`.

- [ ] **Step 3: Rewrite `WorkspaceJobsSummary` and `WorkspaceJobs`**

In `matmaster/context/ports.py`, replace the `WorkspaceJobsSummary` class (lines 107-116) with:

```python
@dataclass(frozen=True)
class WorkspaceJobsSummary:
    total: int  # == active + unhandled_terminal + handled_recent_terminal == snapshot rows
    active: int
    unhandled_terminal: int
    handled_recent_terminal: int
    by_status: Mapping[str, int]
    failed: int
    stopped: int
    lost: int
    unhandled_action: int
```

Replace the `WorkspaceJobs` class (lines 128-144) with:

```python
@dataclass(frozen=True)
class WorkspaceJobs:
    workspace: str | None = None
    active_jobs: tuple[JsonObject, ...] = ()
    unhandled_terminal_jobs: tuple[JsonObject, ...] = ()
    handled_recent_terminal_jobs: tuple[JsonObject, ...] = ()
    mode: Literal["workspace_observation", "session_workspace_delivery"] | None = None
    summary: WorkspaceJobsSummary | None = None
    export: WorkspaceJobsExport | None = None
    export_error: WorkspaceJobsExportError | None = None
    required_error: Mapping[str, JsonValue] | None = None
    preview_limit: int | None = None
    preview_rows: tuple[JsonObject, ...] = ()
    omitted_count: int | None = None
    required_truncated: bool = False
    handled_recent_has_more: bool = False
    handled_recent_unavailable: bool = False

    @classmethod
    def empty(cls) -> WorkspaceJobs:
        return cls()
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/matmaster/context/test_workspace_jobs_dto.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add matmaster/context/ports.py tests/matmaster/context/test_workspace_jobs_dto.py
git commit -m "refactor(ports): rename workspace-jobs buckets and add required/handled-recent flags"
```

---

## Task 4: compute — summary, labels, preview selection, PREVIEW_COLUMNS

**Files:**
- Modify: `matmaster/context/workspace_jobs_compute.py`
- Test: `tests/matmaster/context/test_workspace_jobs_compute.py`

- [ ] **Step 1: Rewrite the compute tests**

Replace `tests/matmaster/context/test_workspace_jobs_compute.py` (keep `_job` helper) — update imports and the summary/inline/build_csv tests, and replace the two `select_priority_samples` tests with `select_observation_preview_rows` + `select_delivery_preview_rows` tests:

```python
from matmaster.context.ports import WorkspaceJobs
from matmaster.context.workspace_jobs_compute import (
    CSV_COLUMNS,
    PREVIEW_COLUMNS,
    build_csv_rows,
    build_csv_text,
    compute_inline_chars,
    compute_summary,
    render_csv_block,
    render_inline_lines,
    select_delivery_preview_rows,
    select_observation_preview_rows,
    trim_preview_rows_to_char_limit,
)


def _job(job_id: str, status: str, **extra) -> dict:
    return {"job_id": job_id, "job_name": f"n-{job_id}", "status": status, **extra}


def test_compute_summary_counts_groups_and_statuses() -> None:
    active = (_job("a1", "running"), _job("a2", "running"))
    unhandled = (_job("p1", "failed"), _job("p2", "finished"))
    handled_recent = (_job("r1", "finished"),)
    s = compute_summary(active, unhandled, handled_recent)
    assert s.total == 5
    assert (s.active, s.unhandled_terminal, s.handled_recent_terminal) == (2, 2, 1)
    assert s.by_status == {"running": 2, "failed": 1, "finished": 2}
    assert (s.failed, s.stopped, s.lost) == (1, 0, 0)
    assert s.unhandled_action == 1


def test_render_inline_lines_columnar_with_flags_and_chars_consistent() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        active_jobs=(_job("a1", "running"),),
        mode="workspace_observation",
        summary=compute_summary((_job("a1", "running"),), (), ()),
        required_truncated=False,
        handled_recent_has_more=True,
        handled_recent_unavailable=True,
    )
    lines = render_inline_lines(jobs)
    assert lines[0] == "workspace /share/p"
    assert lines[1] == "mode workspace_observation"
    assert lines[2].startswith("summary {")
    assert "required_truncated false" in lines
    assert "handled_recent_has_more true" in lines
    assert "handled_recent_hint " in "\n".join(lines)
    assert "handled_recent_unavailable true" in lines
    assert "handled_recent_unavailable_hint " in "\n".join(lines)
    assert "active job_id,job_name,status" in lines
    assert "a1,n-a1,running" in lines
    assert compute_inline_chars(jobs) == len("\n".join(lines))


def test_render_inline_required_error_is_visible() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        required_error={"reason": "query_failed"},
    )

    text = "\n".join(render_inline_lines(jobs))

    assert "required_context_error " in text
    assert '"reason": "query_failed"' in text


def test_render_csv_block_header_then_escaped_values() -> None:
    rows = [{"job_id": "1", "status": "failed", "input_dir": "a,b"}]
    block = render_csv_block(
        "unhandled_terminal", ("job_id", "status", "input_dir"), rows
    )
    assert block[0] == "unhandled_terminal job_id,status,input_dir"
    assert block[1] == '1,failed,"a,b"'


def test_select_observation_preview_rows_order_and_group_tags() -> None:
    active = (_job("a1", "running"),)
    unhandled = (_job("p1", "failed"), _job("p2", "finished"))
    handled_recent = (_job("r1", "finished"),)
    rows = select_observation_preview_rows(
        active=active,
        unhandled_terminal=unhandled,
        handled_recent_terminal=handled_recent,
        limit=10,
    )
    # order: unhandled action -> active -> unhandled other -> handled recent
    assert [r["job_id"] for r in rows] == ["p1", "a1", "p2", "r1"]
    assert rows[0]["group"] == "unhandled_terminal"
    assert rows[1]["group"] == "active"
    assert rows[2]["group"] == "unhandled_terminal"
    assert rows[3]["group"] == "handled_recent_terminal"


def test_select_observation_preview_rows_respects_limit() -> None:
    unhandled = tuple(_job(f"f{i}", "failed") for i in range(5))
    rows = select_observation_preview_rows(
        active=(), unhandled_terminal=unhandled, handled_recent_terminal=(), limit=2
    )
    assert [r["job_id"] for r in rows] == ["f0", "f1"]


def test_select_delivery_preview_rows_action_only_no_group() -> None:
    rows = (
        _job("f1", "failed"),
        _job("t1", "finished"),
        _job("l1", "lost"),
    )
    selected = select_delivery_preview_rows(rows, limit=10)
    assert [r["job_id"] for r in selected] == ["f1", "l1"]
    assert "group" not in selected[0]


def test_build_csv_rows_adds_group_and_total_matches() -> None:
    active = (_job("a1", "running"),)
    unhandled = (_job("p1", "failed"),)
    handled_recent = (_job("r1", "finished"),)
    rows = build_csv_rows(active, unhandled, handled_recent)
    assert len(rows) == 3
    assert rows[0]["group"] == "active"
    assert rows[1]["group"] == "unhandled_terminal"
    assert rows[2]["group"] == "handled_recent_terminal"


def test_preview_columns_prefixes_group() -> None:
    assert PREVIEW_COLUMNS == ("group", "job_id", "job_name", "status")


def test_trim_preview_rows_to_char_limit_truncates_and_bounds_rendered_preview() -> None:
    rows = (
        {
            "group": "unhandled_terminal",
            "job_id": "p1",
            "job_name": "n" * 20000,
            "status": "failed",
        },
    )

    trimmed = trim_preview_rows_to_char_limit(
        rows,
        columns=PREVIEW_COLUMNS,
        char_limit=12000,
    )

    assert len(trimmed) == 1
    assert str(trimmed[0]["job_name"]).endswith("...<truncated>")
    rendered = "\n".join(render_csv_block("preview_rows", PREVIEW_COLUMNS, trimmed))
    assert len(rendered) <= 12000


def test_build_csv_text_fixed_header_bool_none_and_extras_dropped() -> None:
    rows = [
        {
            "group": "unhandled_terminal",
            "job_id": "1",
            "job_name": "n",
            "status": "failed",
            "sandbox": True,
            "result_dir": None,
            "user_id": "SECRET",
            "org_id": "SECRET",
        }
    ]
    text = build_csv_text(rows)
    header = text.splitlines()[0]
    assert header == ",".join(CSV_COLUMNS)
    assert "SECRET" not in text
    body = text.splitlines()[1]
    assert "true" in body
    assert body.count(",") == len(CSV_COLUMNS) - 1
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/matmaster/context/test_workspace_jobs_compute.py -q`
Expected: FAIL on import (`cannot import name 'PREVIEW_COLUMNS' / 'select_observation_preview_rows' / 'trim_preview_rows_to_char_limit'`).

- [ ] **Step 3: Update `summary_to_dict` and `compute_summary`**

In `matmaster/context/workspace_jobs_compute.py`, replace `summary_to_dict` (lines 42-52):

```python
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
```

Replace `compute_summary` (lines 55-74):

```python
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
```

- [ ] **Step 4: Update `render_inline_lines` (labels + flag lines)**

Add shared hint constants after `_ACTION_STATUSES`:

```python
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
```

Replace `render_inline_lines` (lines 90-105):

```python
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
    lines.append(
        f"handled_recent_has_more {str(jobs.handled_recent_has_more).lower()}"
    )
    lines.append(
        f"handled_recent_unavailable "
        f"{str(jobs.handled_recent_unavailable).lower()}"
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
```

- [ ] **Step 5: Add `PREVIEW_COLUMNS`; replace `select_priority_samples` with two functions**

Add after `SUMMARY_COLUMNS` (line 32):

```python
PREVIEW_COLUMNS: tuple[str, ...] = ("group", *SUMMARY_COLUMNS)
```

Keep the existing helper signature `def _with_group(job, group)`. Replace
`select_priority_samples` (lines 117-137) with:

```python
def select_observation_preview_rows(
    *,
    active: tuple[JsonObject, ...],
    unhandled_terminal: tuple[JsonObject, ...],
    handled_recent_terminal: tuple[JsonObject, ...],
    limit: int,
) -> tuple[JsonObject, ...]:
    """observation compact preview。顺序：unhandled action -> active ->
    unhandled other -> handled recent。每行在选择阶段打上来源 group，renderer
    不得从裸 job 反推 bucket。"""
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
```

- [ ] **Step 6: Update `build_csv_rows` group labels**

Replace the group tuple in `build_csv_rows` (lines 146-150):

```python
    for group, items in (
        ("active", active),
        ("unhandled_terminal", unhandled),
        ("handled_recent_terminal", handled_recent),
    ):
```

Also rename its parameters for clarity (signature line 140-144):

```python
def build_csv_rows(
    active: tuple[JsonObject, ...],
    unhandled: tuple[JsonObject, ...],
    handled_recent: tuple[JsonObject, ...],
) -> list[dict[str, JsonValue]]:
```

- [ ] **Step 7: Run, expect pass**

Run: `uv run pytest tests/matmaster/context/test_workspace_jobs_compute.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add matmaster/context/workspace_jobs_compute.py tests/matmaster/context/test_workspace_jobs_compute.py
git commit -m "feat(compute): bucket-aware summary, preview selection with group, PREVIEW_COLUMNS"
```

---

## Task 5: Exporter reads renamed DTO fields

**Files:**
- Modify: `src/services/workspace_jobs_export.py:49-51`
- Test: `tests/services/test_workspace_jobs_export.py:14-15`

- [ ] **Step 1: Update the exporter test fixture fields**

In `tests/services/test_workspace_jobs_export.py`, change the `WorkspaceJobs(...)` construction (lines 14-15) from `pending_terminal_jobs=...` to `unhandled_terminal_jobs=...`:

```python
        active_jobs=({"job_id": "a1", "status": "running"},),
        unhandled_terminal_jobs=({"id": 1, "job_id": "p1", "status": "failed"},),
```

(Leave the rest of the test unchanged. If the test asserts a CSV `group` value `pending_terminal`, update that assertion to `unhandled_terminal`.)

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/services/test_workspace_jobs_export.py -q`
Expected: FAIL (`unexpected keyword argument 'unhandled_terminal_jobs'` was Task 3; now the exporter still reads `jobs.pending_terminal_jobs` → `AttributeError`).

- [ ] **Step 3: Fix the exporter field read**

In `src/services/workspace_jobs_export.py`, replace the `build_csv_rows` call (lines 49-51):

```python
        rows = build_csv_rows(
            jobs.active_jobs,
            jobs.unhandled_terminal_jobs,
            jobs.handled_recent_terminal_jobs,
        )
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/services/test_workspace_jobs_export.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/workspace_jobs_export.py tests/services/test_workspace_jobs_export.py
git commit -m "refactor(exporter): read renamed workspace-jobs buckets"
```

---

## Task 6: Renderer (sources/workspace_jobs.py)

Inline flag lines, compact preview block with group, delivery reads `unhandled_terminal_jobs`, head lines carry the two flags.

**Files:**
- Modify: `matmaster/context/sources/workspace_jobs.py`
- Test: `tests/matmaster/context/sources/test_workspace_jobs.py`

- [ ] **Step 1: Rewrite the renderer tests**

Replace `tests/matmaster/context/sources/test_workspace_jobs.py` `_summary()` and the observation tests + the truncation test; update delivery constructions to `unhandled_terminal_jobs`. Full replacements:

```python
def _summary() -> WorkspaceJobsSummary:
    return WorkspaceJobsSummary(
        total=2,
        active=1,
        unhandled_terminal=1,
        handled_recent_terminal=0,
        by_status={"running": 1, "failed": 1},
        failed=1,
        stopped=0,
        lost=0,
        unhandled_action=1,
    )


def test_inline_renders_summary_flags_and_columnar_details() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        active_jobs=({"job_id": "a1", "job_name": "n1", "status": "running"},),
        unhandled_terminal_jobs=(
            {"job_id": "p1", "job_name": "n2", "status": "failed"},
        ),
        mode="workspace_observation",
        summary=_summary(),
    )
    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content
    assert "mode workspace_observation" in content
    assert "required_truncated false" in content
    assert "handled_recent_has_more false" in content
    assert "handled_recent_unavailable false" in content
    assert "active job_id,job_name,status" in content
    assert "a1,n1,running" in content
    assert "unhandled_terminal job_id,job_name,status" in content
    assert "p1,n2,failed" in content


def test_compact_renders_export_preview_with_group_and_omitted() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        summary=_summary(),
        export=WorkspaceJobsExport(
            path="/share/p/.matmaster/context/workspace_jobs/s-i.csv",
            format="csv",
            row_count=143,
            columns=("group", "job_id"),
            reason="row_limit",
        ),
        preview_rows=(
            {
                "group": "unhandled_terminal",
                "job_id": "p1",
                "job_name": "n2",
                "status": "failed",
            },
        ),
        preview_limit=50,
        omitted_count=93,
    )
    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content
    assert "details_exported {" in content
    assert "read_hint " in content
    assert "action_hint " in content
    assert "prompt_preview {" in content
    assert '"preview_limit": 50' in content
    assert '"omitted_rows": 93' in content
    assert "preview_rows group,job_id,job_name,status" in content
    assert "unhandled_terminal,p1,n2,failed" in content
    assert "active job_id" not in content


def test_compact_required_truncated_renders_hint() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        summary=_summary(),
        required_truncated=True,
        export=WorkspaceJobsExport(
            path="/share/p/.matmaster/context/workspace_jobs/s-i.csv",
            format="csv",
            row_count=3000,
            columns=("group", "job_id"),
            reason="row_limit",
        ),
    )
    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content
    assert "required_truncated true" in content
    assert "required_truncated_hint" in content


def test_compact_action_hint_uses_unhandled_action_only() -> None:
    summary = WorkspaceJobsSummary(
        total=1,
        active=0,
        unhandled_terminal=0,
        handled_recent_terminal=1,
        by_status={"failed": 1},
        failed=1,
        stopped=0,
        lost=0,
        unhandled_action=0,
    )
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        summary=summary,
        export=WorkspaceJobsExport(
            path="/share/p/.matmaster/context/workspace_jobs/s-i.csv",
            format="csv",
            row_count=1,
            columns=("group", "job_id"),
            reason="row_limit",
        ),
        preview_limit=50,
    )

    content = WorkspaceJobsSource.from_jobs(jobs).to_sections()[0].content

    assert "action_hint " not in content


def test_error_renders_export_error_not_details() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
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

For the delivery tests in the same file: change every `pending_terminal_jobs=(` to `unhandled_terminal_jobs=(`, and every `priority_samples=(` to `preview_rows=(`. The delivery assertions on rendered text are unchanged (delivery output format is unchanged). Keep `test_empty_jobs_render_nothing`; delete the old `test_inline_renders_summary_and_columnar_details`, `test_compact_renders_export_samples_omitted`, and `test_compact_truncated_renders_snapshot_hint` (replaced above).

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/matmaster/context/sources/test_workspace_jobs.py -q`
Expected: FAIL (renderer still references `jobs.pending_terminal_jobs` / `jobs.priority_samples` / `jobs.snapshot_truncated`).

- [ ] **Step 3: Update head lines + shared hint constants**

In `matmaster/context/sources/workspace_jobs.py`, delete the local
`_SNAPSHOT_TRUNCATED_HINT` constant (lines 41-44). The new hint strings live in
`workspace_jobs_compute.py` so inline char counting and renderer output use the
same text.

Add these names to the compute import block:

```python
HANDLED_RECENT_HINT,
HANDLED_RECENT_UNAVAILABLE_HINT,
REQUIRED_TRUNCATED_HINT,
```

Add local renderer policy constants near the other renderer constants:

```python
_PREVIEW_POLICY = "unhandled_action > active > unhandled_other > handled_recent"
_CSV_CONTAINS = "active + unhandled_terminal + handled_recent_terminal_limited"
```

Replace `_head_lines` (lines 130-141):

```python
    @staticmethod
    def _head_lines(jobs: WorkspaceJobs) -> list[str]:
        lines: list[str] = []
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
        lines.append(
            f"handled_recent_has_more {str(jobs.handled_recent_has_more).lower()}"
        )
        lines.append(
            f"handled_recent_unavailable "
            f"{str(jobs.handled_recent_unavailable).lower()}"
        )
        if jobs.required_truncated:
            lines.append(f'required_truncated_hint "{REQUIRED_TRUNCATED_HINT}"')
        if jobs.handled_recent_has_more:
            lines.append(f'handled_recent_hint "{HANDLED_RECENT_HINT}"')
        if jobs.handled_recent_unavailable:
            lines.append(
                f'handled_recent_unavailable_hint "{HANDLED_RECENT_UNAVAILABLE_HINT}"'
            )
        return lines
```

- [ ] **Step 4: Update `_compact_lines` (preview block with group)**

Replace `_compact_lines` (lines 143-183):

```python
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
        lines.append(f"csv_contains {_CSV_CONTAINS}")
        lines.append(f'read_hint "{_READ_HINT}"')
        if jobs.summary is not None and jobs.summary.unhandled_action:
            lines.append(f'action_hint "{_ACTION_HINT}"')
        lines.append(
            "prompt_preview "
            + render_job_json(
                {
                    "preview_limit": jobs.preview_limit,
                    "preview_rows": len(jobs.preview_rows),
                    "omitted_rows": jobs.omitted_count,
                }
            )
        )
        lines.append(f"preview_policy {_PREVIEW_POLICY}")
        if jobs.preview_rows:
            lines.extend(
                render_csv_block("preview_rows", PREVIEW_COLUMNS, jobs.preview_rows)
            )
        return tuple(lines)
```

- [ ] **Step 5: Update `_error_lines` (preview block) and delivery readers**

Replace the `priority_samples` block in `_error_lines` (lines 201-208) with:

```python
        if jobs.preview_rows:
            lines.extend(
                render_csv_block("preview_rows", PREVIEW_COLUMNS, jobs.preview_rows)
            )
```

In `_delivery_full_text` (lines 81-90), change the three `jobs.pending_terminal_jobs` references to `jobs.unhandled_terminal_jobs`.

In `_delivery_compact_text` (line 112) and `_delivery_export_failed_text` (line 124), change `jobs.priority_samples` to `jobs.preview_rows`.

- [ ] **Step 6: Fix imports**

Update the compute import block (lines 8-14) to add `PREVIEW_COLUMNS` and the
shared hint constants:

```python
from matmaster.context.workspace_jobs_compute import (
    HANDLED_RECENT_HINT,
    HANDLED_RECENT_UNAVAILABLE_HINT,
    PREVIEW_COLUMNS,
    REQUIRED_TRUNCATED_HINT,
    SUMMARY_COLUMNS,
    render_csv_block,
    render_inline_lines,
    render_job_json,
    summary_to_dict,
)
```

- [ ] **Step 7: Run, expect pass**

Run: `uv run pytest tests/matmaster/context/sources/test_workspace_jobs.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add matmaster/context/sources/workspace_jobs.py tests/matmaster/context/sources/test_workspace_jobs.py
git commit -m "feat(renderer): bucket flags, grouped preview block, delivery reads unhandled bucket"
```

---

## Task 7: Wiring (observation 3-layer + required_block; delivery preview)

**Files:**
- Modify: `src/services/bohrium_jobs_wiring.py`
- Test: `tests/services/test_bohrium_jobs_wiring.py`

- [ ] **Step 1: Rewrite the observation + delivery wiring tests**

In `tests/services/test_bohrium_jobs_wiring.py`:

(a) Replace `test_observation_mode_reads_three_groups_cross_session` (lines 161-201). Note the DAO methods are renamed and now all take `limit`, and the port slices with `limit + 1`:

```python
@pytest.mark.asyncio
async def test_observation_mode_reads_three_groups_cross_session() -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    table.query_workspace_active.return_value = [
        {"job_id": "a", "job_name": "na", "status": "running"}
    ]
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": "p", "job_name": "np", "status": "failed"}
    ]
    table.query_workspace_handled_recent_terminal.return_value = [
        {"job_id": "r", "job_name": "nr", "status": "finished"}
    ]
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(),
        job_context_mode="workspace_observation",
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.mode == "workspace_observation"
    assert result.active_jobs == (
        {"job_id": "a", "job_name": "na", "status": "running"},
    )
    assert result.unhandled_terminal_jobs == (
        {"job_id": "p", "job_name": "np", "status": "failed"},
    )
    assert result.handled_recent_terminal_jobs == (
        {"job_id": "r", "job_name": "nr", "status": "finished"},
    )
    assert result.summary.total == 3
    assert result.required_truncated is False
    assert result.handled_recent_has_more is False
    assert result.export is None
    # required buckets fetch REQUIRED_FETCH_LIMIT + 1 = 2001; reference HANDLED_RECENT_LIMIT + 1 = 21
    assert table.query_workspace_active.call_args.kwargs["limit"] == 2001
    assert table.query_workspace_unhandled_terminal.call_args.kwargs["limit"] == 2001
    assert (
        table.query_workspace_handled_recent_terminal.call_args.kwargs["limit"] == 21
    )
```

(b) Replace `test_observation_over_row_limit_exports_and_samples` (lines 204-231):

```python
@pytest.mark.asyncio
async def test_observation_over_preview_limit_exports_and_previews(monkeypatch) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "2")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": f"p{i}", "job_name": "n", "status": "failed"} for i in range(5)
    ]
    table.query_workspace_handled_recent_terminal.return_value = []
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export is not None
    assert result.export.row_count == 5
    assert result.preview_limit == 2
    assert len(result.preview_rows) == 2
    assert all(r["group"] == "unhandled_terminal" for r in result.preview_rows)
    assert result.omitted_count == 3
    assert result.unhandled_terminal_jobs == ()
```

(c) Add a char-limit compact regression test. This protects the invariant that
`preview_limit` is not redundant: compact mode can be triggered even when
`preview_rows < PROMPT_PREVIEW_LIMIT`.

```python
@pytest.mark.asyncio
async def test_observation_char_limit_exports_even_when_under_preview_limit(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "50")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": "p1", "job_name": "n" * 20000, "status": "failed"}
    ]
    table.query_workspace_handled_recent_terminal.return_value = []
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export is not None
    assert result.export.reason == "char_limit"
    assert result.preview_limit == 50
    assert len(result.preview_rows) == 1
    assert str(result.preview_rows[0]["job_name"]).endswith("...<truncated>")
    assert result.omitted_count == 0
    from matmaster.context.sources.workspace_jobs import WorkspaceJobsSource

    content = WorkspaceJobsSource.from_jobs(result).to_sections()[0].content
    assert len(content) <= 12000
    assert "...<truncated>" in content
```

(d) Add a handled-recent cap test. This is the core `HANDLED_RECENT_LIMIT`
semantic: reference rows are capped at snapshot inclusion time and must not set
`required_block`.

```python
@pytest.mark.asyncio
async def test_observation_handled_recent_limit_is_reference_only(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_HANDLED_RECENT_LIMIT", "2")
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "10")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_unhandled_terminal.return_value = []
    table.query_workspace_handled_recent_terminal.return_value = [
        {"job_id": "r0", "job_name": "n0", "status": "finished"},
        {"job_id": "r1", "job_name": "n1", "status": "finished"},
        {"job_id": "r2", "job_name": "n2", "status": "finished"},
    ]
    snap = _snapshot([{"id": 1, "job_id": "p0", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert [r["job_id"] for r in result.handled_recent_terminal_jobs] == ["r0", "r1"]
    assert result.handled_recent_has_more is True
    assert result.required_truncated is False
    assert snap.required_block == {}
    assert result.export is None
```

(e) Add a handled-recent query failure test. Reference query failure must not
write `required_block` or block ack; it only marks reference history unavailable.

```python
@pytest.mark.asyncio
async def test_observation_handled_recent_query_failure_is_reference_unavailable(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": "p1", "job_name": "n", "status": "failed"}
    ]
    table.query_workspace_handled_recent_terminal.side_effect = RuntimeError(
        "reference query down"
    )
    snap = _snapshot([{"id": 1, "job_id": "p1", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.required_error is None
    assert result.handled_recent_unavailable is True
    assert result.handled_recent_terminal_jobs == ()
    assert snap.required_block == {}
```

(f) Replace `test_observation_truncation_flag_set_at_max_rows` (lines 266-290) with two tests — the `limit + 1` truncation flag AND the new required-query-failure path:

```python
@pytest.mark.asyncio
async def test_observation_required_truncated_writes_required_block(monkeypatch) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_REQUIRED_FETCH_LIMIT", "2")
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "10")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_handled_recent_terminal.return_value = []
    # 3 rows returned for limit+1=3 → original exceeded REQUIRED_FETCH_LIMIT=2
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": f"p{i}", "job_name": "n", "status": "failed"} for i in range(3)
    ]
    snap = _snapshot([{"id": 1, "job_id": "p0", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.required_truncated is True
    assert len(result.unhandled_terminal_jobs) == 2  # sliced to REQUIRED_FETCH_LIMIT
    assert snap.required_block["reason"] == "required_truncated"
    assert snap.required_block["unhandled_terminal_truncated"] is True


@pytest.mark.asyncio
async def test_observation_required_query_failure_writes_required_block_and_error(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    table.query_workspace_active.side_effect = RuntimeError("db down")
    snap = _snapshot([{"id": 1, "job_id": "p0", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(),
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.mode == "workspace_observation"
    assert result.workspace == "/share/project"
    assert result.required_error == {"reason": "query_failed"}
    assert snap.required_block["reason"] == "query_failed"
```

(g) Update `test_observation_export_failure_writes_snapshot_and_error` (lines 234-263): rename the two mock return-value attributes to `query_workspace_unhandled_terminal` / `query_workspace_handled_recent_terminal`, set `monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "2")` instead of the old `INLINE_ROW_LIMIT`, and keep the `snap.export_failure["reason"] == "session_missing"` assertion.

(h) Update the delivery tests: in `test_delivery_under_row_limit_returns_full_pending_no_active_query` change `result.pending_terminal_jobs == snap.rows` to `result.unhandled_terminal_jobs == snap.rows` and `result.summary.pending_terminal == 2` to `result.summary.unhandled_terminal == 2`. In `test_delivery_over_row_limit_exports_pending_only` and `test_delivery_export_failure_writes_snapshot_export_failure`, set `monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "2")` (resp. `"1"`) instead of `INLINE_ROW_LIMIT`, change `result.pending_terminal_jobs == ()` to `result.unhandled_terminal_jobs == ()`, and change `result.priority_samples` to `result.preview_rows`.

(i) Add an explicit delivery row-only-threshold test documenting the accepted
spec deviation from Implementation Decision 3:

```python
@pytest.mark.asyncio
async def test_delivery_inline_threshold_is_row_only_even_for_long_names(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "2")
    snap = _snapshot(
        [{"id": 1, "job_id": "t1", "job_name": "n" * 20000, "status": "finished"}]
    )
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=None),
        job_context_mode="session_workspace_delivery",
        delivery_snapshot=snap,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export is None
    assert result.unhandled_terminal_jobs == snap.rows
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py -q`
Expected: FAIL (env var names, renamed DAO methods, `required_block`, `preview_rows` not yet wired).

- [ ] **Step 3: Replace the env reads in `build_bohrium_jobs_ports`**

In `src/services/bohrium_jobs_wiring.py`, replace the five env reads (lines 372-376) with:

```python
    required_fetch_limit = env_int(
        "BOHRIUM_WORKSPACE_JOBS_REQUIRED_FETCH_LIMIT", 2000
    )
    handled_recent_limit = env_int(
        "BOHRIUM_WORKSPACE_JOBS_HANDLED_RECENT_LIMIT", 20
    )
    prompt_preview_limit = env_int(
        "BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", 50
    )
    char_limit = min(prompt_preview_limit * 240, 24000)
```

- [ ] **Step 4: Replace the two port constructions in `build_bohrium_jobs_ports`**

Replace the observation branch (lines 397-410):

```python
    elif job_context_mode == "workspace_observation":
        jobs = _WorkspaceObservationJobsPort(
            table_ref=table_ref,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            exporter=exporter,
            snapshot=delivery_snapshot,
            required_fetch_limit=required_fetch_limit,
            handled_recent_limit=handled_recent_limit,
            prompt_preview_limit=prompt_preview_limit,
            char_limit=char_limit,
        )
```

Replace the delivery branch (lines 411-418):

```python
    elif job_context_mode == "session_workspace_delivery":
        jobs = _SessionWorkspaceDeliveryJobsPort(
            workspace=normalized_workspace,
            snapshot=delivery_snapshot,
            exporter=exporter,
            prompt_preview_limit=prompt_preview_limit,
        )
```

- [ ] **Step 5: Rewrite `_SessionWorkspaceDeliveryJobsPort`**

Replace the class (lines 153-225) with:

```python
class _SessionWorkspaceDeliveryJobsPort:
    """delivery：只围绕本 session 的 snapshot.rows，只用 row 阈值。

    未超阈值返回含完整 unhandled_terminal_jobs 的 WorkspaceJobs；超阈值仅选
    action preview 并导出 CSV；导出失败时写 snapshot.export_failure。
    """

    def __init__(
        self,
        *,
        workspace: str,
        snapshot: DeliverySnapshot | None,
        exporter: WorkspaceJobsCsvExporter,
        prompt_preview_limit: int,
    ) -> None:
        self._workspace = workspace
        self._snapshot = snapshot
        self._exporter = exporter
        self._prompt_preview_limit = prompt_preview_limit

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        pending: tuple[dict[str, Any], ...] = (
            self._snapshot.rows if self._snapshot is not None else ()
        )
        summary = compute_summary((), pending, ())
        if len(pending) <= self._prompt_preview_limit:
            return WorkspaceJobs(
                workspace=self._workspace,
                unhandled_terminal_jobs=pending,
                summary=summary,
                mode="session_workspace_delivery",
            )
        preview_rows = select_delivery_preview_rows(
            pending, limit=self._prompt_preview_limit
        )
        export_input = WorkspaceJobs(
            workspace=self._workspace,
            unhandled_terminal_jobs=pending,
        )
        result = self._exporter.export(export_input, reason="row_limit")
        if isinstance(result, WorkspaceJobsExportError):
            self._record_export_failure(result)
            return WorkspaceJobs(
                workspace=self._workspace,
                summary=summary,
                preview_limit=self._prompt_preview_limit,
                preview_rows=preview_rows,
                export_error=result,
                mode="session_workspace_delivery",
            )
        return WorkspaceJobs(
            workspace=self._workspace,
            summary=summary,
            preview_limit=self._prompt_preview_limit,
            preview_rows=preview_rows,
            export=result,
            mode="session_workspace_delivery",
        )

    def _record_export_failure(self, err: WorkspaceJobsExportError) -> None:
        if self._snapshot is None:
            return
        self._snapshot.export_failure.update(
            {"reason": err.reason, "rows": err.rows, "target_path": err.target_path}
        )
```

- [ ] **Step 6: Rewrite `_WorkspaceObservationJobsPort`**

Replace the class (lines 228-347) with:

```python
class _WorkspaceObservationJobsPort:
    """observation：跨 session required/reference 三 bucket，row+char 双阈值。"""

    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        workspace: str,
        exporter: WorkspaceJobsCsvExporter,
        snapshot: DeliverySnapshot | None,
        required_fetch_limit: int,
        handled_recent_limit: int,
        prompt_preview_limit: int,
        char_limit: int,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._exporter = exporter
        self._snapshot = snapshot
        self._required_fetch_limit = required_fetch_limit
        self._handled_recent_limit = handled_recent_limit
        self._prompt_preview_limit = prompt_preview_limit
        self._char_limit = char_limit

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        try:
            table = self._table_ref.get()
            active_raw, unhandled_raw = await asyncio.gather(
                asyncio.to_thread(
                    table.query_workspace_active,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                    limit=self._required_fetch_limit + 1,
                ),
                asyncio.to_thread(
                    table.query_workspace_unhandled_terminal,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                    limit=self._required_fetch_limit + 1,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs(observation required) failed workspace=%s",
                self._workspace,
                exc_info=True,
            )
            self._write_required_block(reason="query_failed")
            return WorkspaceJobs(
                workspace=self._workspace,
                mode="workspace_observation",
                required_error={"reason": "query_failed"},
            )

        handled_recent_unavailable = False
        try:
            handled_recent_raw = await asyncio.to_thread(
                table.query_workspace_handled_recent_terminal,
                user_id=self._user_id,
                org_id=self._org_id,
                workspace=self._workspace,
                limit=self._handled_recent_limit + 1,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs(observation handled_recent) failed workspace=%s",
                self._workspace,
                exc_info=True,
            )
            handled_recent_raw = []
            handled_recent_unavailable = True

        active = tuple(active_raw[: self._required_fetch_limit])
        unhandled = tuple(unhandled_raw[: self._required_fetch_limit])
        handled_recent = tuple(handled_recent_raw[: self._handled_recent_limit])
        active_truncated = len(active_raw) > self._required_fetch_limit
        unhandled_truncated = len(unhandled_raw) > self._required_fetch_limit
        required_truncated = active_truncated or unhandled_truncated
        handled_recent_has_more = (
            len(handled_recent_raw) > self._handled_recent_limit
        )
        if required_truncated:
            self._write_required_block(
                reason="required_truncated",
                active_truncated=active_truncated,
                unhandled_terminal_truncated=unhandled_truncated,
            )

        summary = compute_summary(active, unhandled, handled_recent)
        full = WorkspaceJobs(
            workspace=self._workspace,
            active_jobs=active,
            unhandled_terminal_jobs=unhandled,
            handled_recent_terminal_jobs=handled_recent,
            summary=summary,
            mode="workspace_observation",
            required_truncated=required_truncated,
            handled_recent_has_more=handled_recent_has_more,
            handled_recent_unavailable=handled_recent_unavailable,
        )
        snapshot_rows = active + unhandled + handled_recent
        if (
            len(snapshot_rows) <= self._prompt_preview_limit
            and compute_inline_chars(full) <= self._char_limit
        ):
            return full

        preview_rows = select_observation_preview_rows(
            active=active,
            unhandled_terminal=unhandled,
            handled_recent_terminal=handled_recent,
            limit=self._prompt_preview_limit,
        )
        preview_rows = trim_preview_rows_to_char_limit(
            preview_rows,
            columns=PREVIEW_COLUMNS,
            char_limit=self._char_limit,
        )
        reason = (
            "row_limit"
            if len(snapshot_rows) > self._prompt_preview_limit
            else "char_limit"
        )
        result = self._exporter.export(full, reason=reason)
        if isinstance(result, WorkspaceJobsExportError):
            self._record_export_failure(result)
            return WorkspaceJobs(
                workspace=self._workspace,
                summary=summary,
                preview_limit=self._prompt_preview_limit,
                preview_rows=preview_rows,
                export_error=result,
                mode="workspace_observation",
                required_truncated=required_truncated,
                handled_recent_has_more=handled_recent_has_more,
                handled_recent_unavailable=handled_recent_unavailable,
            )
        return WorkspaceJobs(
            workspace=self._workspace,
            summary=summary,
            preview_limit=self._prompt_preview_limit,
            preview_rows=preview_rows,
            export=result,
            omitted_count=len(snapshot_rows) - len(preview_rows),
            mode="workspace_observation",
            required_truncated=required_truncated,
            handled_recent_has_more=handled_recent_has_more,
            handled_recent_unavailable=handled_recent_unavailable,
        )

    def _write_required_block(self, *, reason: str, **extra: Any) -> None:
        if self._snapshot is not None:
            self._snapshot.required_block.update({"reason": reason, **extra})

    def _record_export_failure(self, err: WorkspaceJobsExportError) -> None:
        if self._snapshot is None:
            return
        self._snapshot.export_failure.update(
            {"reason": err.reason, "rows": err.rows, "target_path": err.target_path}
        )
```

- [ ] **Step 7: Fix the compute import**

Replace the compute import (lines 17-21):

```python
from matmaster.context.workspace_jobs_compute import (
    PREVIEW_COLUMNS,
    compute_inline_chars,
    compute_summary,
    select_delivery_preview_rows,
    select_observation_preview_rows,
    trim_preview_rows_to_char_limit,
)
```

- [ ] **Step 8: Run, expect pass**

Run: `uv run pytest tests/services/test_bohrium_jobs_wiring.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/services/bohrium_jobs_wiring.py tests/services/test_bohrium_jobs_wiring.py
git commit -m "feat(wiring): three-bucket observation with limit+1, required_block gate, preview"
```

---

## Task 8: Sweep remaining references + full-suite green + guards

**Files:**
- Modify: `tests/matmaster/context/test_compositions.py:93,131`
- Modify: `tests/matmaster/test_bohrium_ledger_injection.py:14-21`
- Test: full suite

- [ ] **Step 1: Update `test_compositions.py`**

Change both `pending_terminal_jobs=(` (lines 93, 131) to `unhandled_terminal_jobs=(`. (These build delivery-mode `WorkspaceJobs`; the rendered delivery text assertions are unchanged.)

- [ ] **Step 2: Update `test_bohrium_ledger_injection.py`**

Rename the test and field references (lines 14-21):

```python
def test_workspace_jobs_has_unhandled_terminal_jobs_field() -> None:
    sj = WorkspaceJobs()
    assert sj.unhandled_terminal_jobs == ()
    hints = WorkspaceJobs.__annotations__
    assert "unhandled_terminal_jobs" in hints
```

(Adjust to the file's actual assertion shape — keep its existing structure, only swap the field name. Read lines 1-25 first to match the surrounding code.)

- [ ] **Step 3: Guard against leftover old names**

Run: `grep -rn "pending_terminal_jobs\|recent_terminal_jobs\|priority_samples\|snapshot_truncated\|select_priority_samples\|query_workspace_pending_terminal\|query_workspace_recent_terminal" src/ matmaster/ tests/`
Expected: NO output.

Run: `grep -rn "OBSERVATION_MAX_ROWS\|INLINE_ROW_LIMIT\|INLINE_CHAR_LIMIT\|ACTION_SAMPLE_LIMIT\|PRIORITY_SAMPLE_LIMIT" src/ matmaster/ tests/`
Expected: NO output.

Run: `grep -rn "\bpending_terminal\b\|\brecent_terminal\b" matmaster/context/`
Expected: NO output (observation summary fully renamed; delivery aggregate in `src/dao` + `bohrium_completion_scheduler.py` is intentionally untouched and lives outside `matmaster/context/`).

If any hit appears, fix it and re-run.

- [ ] **Step 4: Full suite**

Run: `uv run pytest tests/ -q`
Expected: PASS (DAO real-MySQL tests may SKIP without `.env.test` — that is acceptable; everything else green).

- [ ] **Step 5: Commit**

```bash
git add tests/matmaster/context/test_compositions.py tests/matmaster/test_bohrium_ledger_injection.py
git commit -m "test: finish workspace-jobs bucket rename sweep"
```

---

## Self-Review (completed during planning)

**Spec coverage** — every spec section maps to a task:
- §1 background (`limit+1`, recent handled filter) → Task 1, Task 7. §2 semantics / three buckets → Task 3, Task 7. §3 invariants 1-5,7 → Task 7 (assembly) + Task 6 (render); inv 6 `required_truncated` → Task 7; inv 8-9 (delivery) → Task 7; inv 10 (required_block gate for required query failure/truncation and export failure) → Task 2 (confirm) + Task 7 (write). §4 three limits + derived char → Task 7, with compact preview char-safety in Task 4/7. §5 old env removal → Task 7 + Task 8 guard. §6 DTO + `required_block` field → Task 3 + Task 2, including `required_error`, `handled_recent_unavailable`, `preview_limit`, and `summary.unhandled_action`. §6.1 → Task 2. §7 DAO queries → Task 1. §8 observation flow incl. required query failure vs reference query failure → Task 7. §9 preview selection + group + char-safe trim → Task 4. §10 renderer output, inline/compact hints, visible `required_context_error` → Task 4 + Task 6. §11 CSV groups → Task 4 (build_csv_rows) + Task 5 (exporter). §12 delivery preview → Task 4 + Task 7. §13 ack/failure → Task 2 + Task 7. §15 file list → all tasks; `agent_run_service.py` confirmed no-change. §16 verification items → Tasks 1-8 tests. §17 non-goals respected (no schema change, delivery aggregate untouched). §18 completion → Task 8 guards.

**Placeholder scan** — no TBD/TODO; every code step shows full code; the only "read first to match shape" note is Task 8 Step 2 (a 2-line field swap in an unread file), which states the exact target.

**Type consistency** — method names consistent across tasks: `query_workspace_active/unhandled_terminal/handled_recent_terminal` (Task 1 ↔ Task 7), `select_observation_preview_rows`/`select_delivery_preview_rows`/`trim_preview_rows_to_char_limit`/`PREVIEW_COLUMNS` (Task 4 ↔ Task 6 ↔ Task 7), `required_block` (Task 2 ↔ Task 7), DTO fields `unhandled_terminal_jobs`/`handled_recent_terminal_jobs`/`required_error`/`preview_limit`/`preview_rows`/`required_truncated`/`handled_recent_has_more`/`handled_recent_unavailable` (Task 3 ↔ all consumers), and `WorkspaceJobsSummary.unhandled_action` (Task 3 ↔ Task 4 ↔ Task 6).
