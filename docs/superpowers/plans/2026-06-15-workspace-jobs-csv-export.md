# Workspace Jobs CSV Export 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收尾 workspace jobs CSV 导出特性——把 delivery / observation 双链路接上已落地的 compute/exporter 底座，超阈值时落 CSV、prompt 只留样本+计数+路径，并对 CSV 失败的本 session 终态行做 ack-gating。

**Architecture:** 两条独立读链路共用一个 CSV 导出底座。delivery（turn instruction）只围绕本 session 的 `snapshot.rows`、只用 row 阈值；observation（workspace_jobs section）跨 session 查完整快照、用 row+char 双阈值。两 port 在导出失败时把失败信息写回 `DeliverySnapshot.export_failure`，worker 收尾 `confirm` 据此跳过 `snapshot.rows` 的 ack（这些行下个 run 自然重现，幂等安全）。

**Tech Stack:** Python 3.10+，dataclass DTO，asyncio.to_thread 包 DAO，pytest（`uv run pytest`），MySQL（测试用 MagicMock 隔离）。

---

## 1. 现状与本计划的关系

已落地（不改动其实现，只复用）：

| commit | 内容 | 文件 |
|--------|------|------|
| `3655dee0` | T1 DTO（含过渡字段 `detail_limit`） | `matmaster/context/ports.py` |
| `d97d67e0` | T2 compute 纯函数 | `matmaster/context/workspace_jobs_compute.py` |
| `e75ca2a9` | T3 exporter | `src/services/workspace_jobs_export.py` |
| `9ea50f3c` | T4 observation 三态 **renderer** | `matmaster/context/sources/workspace_jobs.py` |

**关键现状判断（已逐行核验）：**

1. **mode 类型与代码字面量不一致。** `WorkspaceJobs.mode` 的 `Literal` 已是目标新值 `"workspace_observation"` / `"session_workspace_delivery"`（`ports.py:135`），但 wiring/renderer/compositions 仍在用旧字面量 `"observation"` / `"delivery"`（`wiring.py:191,249`、`workspace_jobs.py:51,61`、`compositions.py:101`）。本计划把这些旧字面量切到新值，消除现状的类型不一致。
2. **T4 只建了 renderer，没建 port 装配。** `WorkspaceJobsSource.from_jobs` 已有 inline/compact/error 三态分支（按 `export` / `export_error` 切换），但 wiring 的两个 port 现在只把原始查询结果原样塞进 `WorkspaceJobs`：observation 永远走 inline 态、从不导出；delivery 还在多查一次 `query_session_active`。本计划在 wiring 里补出两条装配逻辑（summary / 阈值 / 样本 / 导出）。
3. **exporter 尚未注入。** `WorkspaceJobsCsvExporter` 已存在但无人构造；`build_bohrium_jobs_ports` 没有 exporter 参数。本计划在 `agent_run_service` 构造并注入。
4. **`confirm` 尚无 gating。** `bohrium_delivery_ack.confirm` 现在无条件 ack `snapshot.rows`；`DeliverySnapshot` 没有承载导出失败的字段。

## 2. 实现决策与对 spec 的偏差（执行前请知悉）

- **新增 DTO 字段 `WorkspaceJobs.snapshot_truncated: bool = False`（Task 4）。** spec §4.1 要求 observation 查询命中大上限时“section 内显式声明快照可能不完整”，但 spec §6/§9 的 DTO 清单未列承载字段。本计划补这一最小布尔载体 + renderer 一行声明来落实 §4.1。若你（用户）不需要截断声明，可删去 Task 4 中 4.1/4.2/对应测试三处。
- **observation 的 active 组不设上限。** spec §4.1 说“三组各自应用”大上限，但 DAO `query_workspace_active`（`bohrium_jobs_table.py:216`）没有 `limit` 参数，且 spec §9 未列 DAO 改动。本计划只给 `pending_terminal` / `recent_terminal` 传 `max_rows`（这两个 DAO 方法有 `limit`），active 保持全量。`snapshot_truncated` 仅按 pending/recent 是否命中 `max_rows` 判定。active 是“还在跑”的作业，数量天然有限，风险低。
- **`build_bohrium_jobs_ports` 的 `exporter` 参数必填（无默认值）。** 符合用户“严禁内联兜底/兼容”的约束——不引入 `exporter is None` 的降级分支。生产路径由 `agent_run_service` 注入；测试用一个 helper 注入（见 Task 3）。
- **delivery port 移除 `query_session_active`。** spec §3.1 实现注：现状 delivery port 查 active 塞进 `active_jobs`，但 `delivery_instruction_text` 从不使用、compositions 的 delivery 分支也不渲染 section。已确认无其他依赖，移除（净减无用代码）。

## 3. 阈值 env 常量（spec §6）

本计划引入 5 个新 env，均在 `build_bohrium_jobs_ports` 内用 `env_int` 读取后传给 port：

| env | 默认 | 用途 |
|-----|------|------|
| `BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT` | 50 | 两链路的 row 阈值 |
| `BOHRIUM_WORKSPACE_JOBS_INLINE_CHAR_LIMIT` | 12000 | observation 的 char 阈值 |
| `BOHRIUM_WORKSPACE_JOBS_ACTION_SAMPLE_LIMIT` | 200 | action 样本上限（failed/lost/stopped） |
| `BOHRIUM_WORKSPACE_JOBS_PRIORITY_SAMPLE_LIMIT` | 20 | observation fill 样本上限 |
| `BOHRIUM_WORKSPACE_JOBS_OBSERVATION_MAX_ROWS` | 2000 | observation 三组防御性大上限 |

旧 env `BOHRIUM_DELIVERY_DETAIL_LIMIT`（现用于 `wiring.py:300`、`bohrium_delivery_ack.py:102`）随 `detail_limit` 一起在 Task 5 移除。

## 4. 文件改动地图

| 文件 | 改动 | Task |
|------|------|------|
| `matmaster/context/sources/workspace_jobs.py` | mode 判断切新值；`delivery_instruction_text` 升级三态；`_head_lines` 加截断声明 | 1, 3, 4 |
| `matmaster/context/compositions.py` | `_step_workspace_jobs` mode 判断切新值 | 1 |
| `src/services/bohrium_jobs_wiring.py` | 两 port mode 切新值；delivery/observation 两条装配；接 exporter+snapshot+阈值；`build_bohrium_jobs_ports` 加 `exporter` 参数 | 1, 3, 4 |
| `src/services/bohrium_delivery_ack.py` | `DeliverySnapshot` 加 `export_failure`、移除 `detail_limit`；`snapshot()` 去 `detail_limit`；`confirm()` 加守卫 | 2, 5 |
| `src/services/agent_run_service.py` | 构造并注入 `WorkspaceJobsCsvExporter` | 3 |
| `matmaster/context/ports.py` | 加 `snapshot_truncated`；移除 `detail_limit` | 4, 5 |

测试：`tests/matmaster/context/sources/test_workspace_jobs.py`、`tests/matmaster/context/test_compositions.py`、`tests/matmaster/context/test_workspace_jobs_dto.py`、`tests/services/test_bohrium_jobs_wiring.py`、`tests/services/test_bohrium_delivery_ack.py`、`tests/matmaster/context/test_workspace_jobs_compute.py`。

执行顺序约束：必须 1 → 2 → 3 → 4 → 5。每个 Task 结束时 `uv run pytest tests/ -q` 必须全绿后才提交。

---

## Task 1: 切 mode 值域到目标字面量

把 5 处旧字面量改成新值，并同步测试断言。此 Task 不改任何功能逻辑，只统一 mode 字符串、消除现状类型不一致。

**Files:**
- Modify: `matmaster/context/sources/workspace_jobs.py:51,61`
- Modify: `matmaster/context/compositions.py:101`
- Modify: `src/services/bohrium_jobs_wiring.py:191,249`
- Test: `tests/matmaster/context/sources/test_workspace_jobs.py`、`tests/matmaster/context/test_compositions.py`、`tests/services/test_bohrium_jobs_wiring.py`

- [ ] **Step 1: 改源码 5 处字面量**

`matmaster/context/sources/workspace_jobs.py` 第 51 行：
```python
        if jobs.mode == "session_workspace_delivery":
```
第 61 行：
```python
        if jobs.mode != "session_workspace_delivery" or not jobs.pending_terminal_jobs:
```

`matmaster/context/compositions.py` 第 101 行：
```python
    if inputs.workspace_jobs.mode == "session_workspace_delivery":
```

`src/services/bohrium_jobs_wiring.py` 第 191 行（delivery port 返回值）：
```python
            mode="session_workspace_delivery",
```
第 249 行（observation port 返回值）：
```python
            mode="workspace_observation",
```

- [ ] **Step 2: 改测试断言/构造里的 mode 字面量**

`tests/matmaster/context/sources/test_workspace_jobs.py`：
- 第 28、50、76 行 `mode="observation"` → `mode="workspace_observation"`
- 第 38 行 `assert lines[1] == "mode observation"` → `assert lines[1] == "mode workspace_observation"`
- 第 107、135、152、162、166 行 `mode="delivery"` → `mode="session_workspace_delivery"`

`tests/matmaster/context/test_compositions.py`：
- 第 91 行 `mode="delivery"` → `mode="session_workspace_delivery"`
- 第 128 行 `mode="observation"` → `mode="workspace_observation"`

`tests/services/test_bohrium_jobs_wiring.py`：
- 第 162 行 `assert result.mode == "delivery"` → `assert result.mode == "session_workspace_delivery"`
- 第 194 行 `assert result.mode == "observation"` → `assert result.mode == "workspace_observation"`
- 第 248 行 `assert result.mode == "delivery"` → `assert result.mode == "session_workspace_delivery"`

（`tests/matmaster/context/test_workspace_jobs_compute.py:33` 已是 `mode="workspace_observation"`，无需改。）

- [ ] **Step 3: 跑受影响测试，确认全绿**

Run:
```bash
uv run pytest tests/matmaster/context/sources/test_workspace_jobs.py tests/matmaster/context/test_compositions.py tests/services/test_bohrium_jobs_wiring.py -q
```
Expected: PASS（功能未变，仅字面量统一）。

- [ ] **Step 4: 提交**

```bash
git add matmaster/context/sources/workspace_jobs.py matmaster/context/compositions.py src/services/bohrium_jobs_wiring.py tests/matmaster/context/sources/test_workspace_jobs.py tests/matmaster/context/test_compositions.py tests/services/test_bohrium_jobs_wiring.py
git commit -m "refactor(context): switch workspace jobs mode literals to canonical values"
```

---

## Task 2: DeliverySnapshot 加 export_failure + confirm 守卫

新增导出失败的承载字段，并让 `confirm` 在导出失败时跳过 `snapshot.rows` 的 ack（仍 ack `observed_terminal`）。本 Task 不动 `detail_limit`（Task 5 才移除）。

**Files:**
- Modify: `src/services/bohrium_delivery_ack.py`
- Test: `tests/services/test_bohrium_delivery_ack.py`

- [ ] **Step 1: 写失败测试**

在 `tests/services/test_bohrium_delivery_ack.py` 末尾追加（复用文件内已有的 `_row` helper）：
```python
def test_confirm_skips_rows_when_export_failed_but_acks_observed():
    table = MagicMock()
    table.mark_handled_by_job_keys.return_value = 1
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        workspace="/share/project",
        rows=(_row(11, "f1", status="failed"),),
        detail_limit=20,
        export_failure={
            "reason": "write_failed",
            "rows": 1,
            "target_path": "/share/project/x.csv",
        },
    )
    snap.observed_terminal.add((True, "J"))

    affected = bohrium_delivery_ack.confirm(snap, jobs_table=table)

    table.mark_handled_by_ids.assert_not_called()
    table.mark_handled_by_job_keys.assert_called_once()
    assert affected == 1


def test_confirm_acks_rows_when_export_failure_empty():
    table = MagicMock()
    table.mark_handled_by_ids.return_value = 1
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        workspace="/share/project",
        rows=(_row(11, "t1"),),
        detail_limit=20,
    )

    affected = bohrium_delivery_ack.confirm(snap, jobs_table=table)

    table.mark_handled_by_ids.assert_called_once()
    assert affected == 1
```

- [ ] **Step 2: 跑测试看失败**

Run:
```bash
uv run pytest tests/services/test_bohrium_delivery_ack.py::test_confirm_skips_rows_when_export_failed_but_acks_observed -v
```
Expected: FAIL —— `TypeError: __init__() got an unexpected keyword argument 'export_failure'`（字段还不存在）。

- [ ] **Step 3: DeliverySnapshot 加字段**

`src/services/bohrium_delivery_ack.py`，在 `observed_terminal` 字段后新增（保持 `detail_limit` 暂存）：
```python
    user_id: str
    org_id: str
    session_id: str
    workspace: str
    rows: tuple[dict[str, Any], ...]
    detail_limit: int
    observed_terminal: set[tuple[bool, str]] = field(default_factory=set)
    export_failure: dict[str, Any] = field(default_factory=dict)
```

同时把类 docstring 末尾补一句（紧接现有“无时间重叠。”之前的段落内）：
```python
    export_failure 由 read port 在 CSV 导出失败时写入（{reason, rows, target_path}），
    confirm 据此 gate snapshot.rows 的 ack；写入在 run 内上下文装配、读取在 run 收尾，
    与 observed_terminal 同属 frozen 字段绑定的可变容器，无时间重叠。
```

- [ ] **Step 4: confirm 加守卫**

`src/services/bohrium_delivery_ack.py` `confirm()` 内，把 `if snap.rows:` 改为：
```python
    if snap.rows and not snap.export_failure:
        affected += table.mark_handled_by_ids(
            user_id=snap.user_id,
            org_id=snap.org_id,
            session_id=snap.session_id,
            workspace=snap.workspace,
            row_ids=tuple(int(j["id"]) for j in snap.rows),
        )
```
（`observed_terminal` 那段 `mark_handled_by_job_keys` 不变。注意函数开头的短路 `if not (snap.rows or snap.observed_terminal): return 0` 保持不变——导出失败但 rows 非空时不短路，靠新守卫跳过 rows、返回 0。）

- [ ] **Step 5: 跑测试看通过**

Run:
```bash
uv run pytest tests/services/test_bohrium_delivery_ack.py -q
```
Expected: PASS（含两个新测试 + 原有全部）。

- [ ] **Step 6: 提交**

```bash
git add src/services/bohrium_delivery_ack.py tests/services/test_bohrium_delivery_ack.py
git commit -m "feat(delivery-ack): gate snapshot row ack on csv export_failure"
```

---

## Task 3: exporter 注入 + delivery port 三态 + delivery_instruction_text 三态

把 exporter 接进 wiring，重写 delivery port 为“未超阈值全表 / 超阈值样本+导出 / 导出失败降级”三态，并把 `delivery_instruction_text` 升级为对应三态。delivery 不再查 `query_session_active`、不再用 `detail_limit`。

**Files:**
- Modify: `src/services/bohrium_jobs_wiring.py`
- Modify: `src/services/agent_run_service.py`
- Modify: `matmaster/context/sources/workspace_jobs.py`
- Test: `tests/services/test_bohrium_jobs_wiring.py`、`tests/matmaster/context/sources/test_workspace_jobs.py`

### 3A. delivery_instruction_text 三态（renderer）

- [ ] **Step 1: 写失败测试（renderer 三态）**

在 `tests/matmaster/context/sources/test_workspace_jobs.py` 末尾追加（复用文件内 `_job` / `_summary` helper；`_summary` 需要 `by_status` 含 `finished` 计数——见 Step 注）：
```python
def test_delivery_compact_renders_failed_samples_success_count_and_path() -> None:
    from matmaster.context.ports import (
        WorkspaceJobs,
        WorkspaceJobsExport,
        WorkspaceJobsSummary,
    )

    jobs = WorkspaceJobs(
        mode="session_workspace_delivery",
        summary=WorkspaceJobsSummary(
            total=982,
            active=0,
            pending_terminal=982,
            recent_terminal=0,
            by_status={"finished": 980, "failed": 2},
            failed=2,
            stopped=0,
            lost=0,
        ),
        priority_samples=(
            _job("f1", "failed", job_name="relax-fail"),
            _job("l3", "lost", job_name="relax-lost"),
        ),
        export=WorkspaceJobsExport(
            path="/share/p/.matmaster/context/workspace_jobs/s-i.csv",
            format="csv",
            row_count=982,
            columns=("group", "job_id"),
            reason="row_limit",
        ),
    )

    text = WorkspaceJobsSource.delivery_instruction_text(jobs)

    assert "以下作业失败：" in text
    assert "f1, relax-fail" in text
    assert "l3, relax-lost" in text
    assert "以下作业成功结束：共 980 个（详见导出文件）" in text
    assert "/share/p/.matmaster/context/workspace_jobs/s-i.csv" in text
    assert "Read 或 Bash" in text


def test_delivery_export_failure_renders_samples_and_warning_no_path() -> None:
    from matmaster.context.ports import (
        WorkspaceJobs,
        WorkspaceJobsExportError,
        WorkspaceJobsSummary,
    )

    jobs = WorkspaceJobs(
        mode="session_workspace_delivery",
        summary=WorkspaceJobsSummary(
            total=600,
            active=0,
            pending_terminal=600,
            recent_terminal=0,
            by_status={"finished": 599, "failed": 1},
            failed=1,
            stopped=0,
            lost=0,
        ),
        priority_samples=(_job("f1", "failed", job_name="relax-fail"),),
        export_error=WorkspaceJobsExportError(
            reason="write_failed", rows=600, target_path="/share/p/x.csv"
        ),
    )

    text = WorkspaceJobsSource.delivery_instruction_text(jobs)

    assert "f1, relax-fail" in text
    assert "完整明细导出失败，被省略的作业未必已交付。" in text
    assert "/share/p/x.csv" not in text
    assert "已导出" not in text
```

注：文件现有 `_summary()`（第 13-17 行）若 `by_status` 不含 `finished`，仅影响 inline 测试，不影响上面两个新测试（它们各自构造 summary）。无需改 `_summary`。

- [ ] **Step 2: 跑测试看失败**

Run:
```bash
uv run pytest tests/matmaster/context/sources/test_workspace_jobs.py::test_delivery_compact_renders_failed_samples_success_count_and_path -v
```
Expected: FAIL —— 当前 `delivery_instruction_text` 只有全表态，`export` 非空时仍走旧路径，断言不满足。

- [ ] **Step 3: 升级 delivery_instruction_text 为三态**

`matmaster/context/sources/workspace_jobs.py`：先在文件顶部文案常量区（`_EXPORT_ERROR_HINT` 之后）新增 delivery 专用文案：
```python
_DELIVERY_SUCCEEDED_COUNT_TEMPLATE = "以下作业成功结束：共 {count} 个（详见导出文件）"
_DELIVERY_EXPORTED_TEMPLATE = "完整明细已导出：{path}"
_DELIVERY_READ_HINT = "需要某个作业的 input_dir / result_dir 等，用 Read 或 Bash 读取该 CSV。"
_DELIVERY_EXPORT_FAILED_TEXT = "完整明细导出失败，被省略的作业未必已交付。"
```

把现有 `delivery_instruction_text`（第 59-80 行）整体替换为分派 + 三个分支方法：
```python
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
```
（`_render_delivery_table` 第二参数类型已是 `tuple`，`priority_samples`(`tuple[JsonObject, ...]`) 直接可用；每行只取 `job_id` / `job_name`。）

- [ ] **Step 4: 跑 renderer 测试看通过**

Run:
```bash
uv run pytest tests/matmaster/context/sources/test_workspace_jobs.py -q
```
Expected: PASS。

### 3B. exporter 注入（wiring + agent_run_service）

- [ ] **Step 5: 改 wiring 的 import 与 delivery port**

`src/services/bohrium_jobs_wiring.py`，把现有 `from matmaster.context.ports import (...)` 补上 `WorkspaceJobsExportError`，并新增两个 import：
```python
from matmaster.context.ports import (
    WorkspaceJobs,
    WorkspaceJobsExportError,
    WorkspaceJobsPort,
    WorkspaceJobsQuery,
)
from matmaster.context.workspace_jobs_compute import (
    compute_summary,
    select_priority_samples,
)
from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter
```

把整个 `_SessionWorkspaceDeliveryJobsPort` 类（第 146-192 行）替换为：
```python
class _SessionWorkspaceDeliveryJobsPort:
    """delivery：只围绕本 session 的 snapshot.rows，只用 row 阈值。

    未超阈值 → 返回含完整 pending_terminal_jobs 的 WorkspaceJobs（全表渲染）；
    超阈值 → 仅选 action 样本 + 导出 pending CSV，返回 summary/样本/export；
    导出失败 → 写 snapshot.export_failure + 返回 export_error。
    """

    def __init__(
        self,
        *,
        workspace: str,
        snapshot: DeliverySnapshot | None,
        exporter: WorkspaceJobsCsvExporter,
        row_limit: int,
        action_sample_limit: int,
    ) -> None:
        self._workspace = workspace
        self._snapshot = snapshot
        self._exporter = exporter
        self._row_limit = row_limit
        self._action_sample_limit = action_sample_limit

    async def load_workspace_jobs(self, query: WorkspaceJobsQuery) -> WorkspaceJobs:
        pending: tuple[dict[str, Any], ...] = (
            self._snapshot.rows if self._snapshot is not None else ()
        )
        summary = compute_summary((), pending, ())
        if len(pending) <= self._row_limit:
            return WorkspaceJobs(
                workspace=self._workspace,
                pending_terminal_jobs=pending,
                summary=summary,
                mode="session_workspace_delivery",
            )
        samples = select_priority_samples(
            (),
            pending,
            (),
            action_limit=self._action_sample_limit,
            fill_limit=0,
        )
        export_input = WorkspaceJobs(
            workspace=self._workspace,
            pending_terminal_jobs=pending,
        )
        result = self._exporter.export(export_input, reason="row_limit")
        if isinstance(result, WorkspaceJobsExportError):
            self._record_export_failure(result)
            return WorkspaceJobs(
                workspace=self._workspace,
                summary=summary,
                priority_samples=samples,
                export_error=result,
                mode="session_workspace_delivery",
            )
        return WorkspaceJobs(
            workspace=self._workspace,
            summary=summary,
            priority_samples=samples,
            export=result,
            mode="session_workspace_delivery",
        )

    def _record_export_failure(self, err: WorkspaceJobsExportError) -> None:
        if self._snapshot is None:
            return
        self._snapshot.export_failure.update(
            {
                "reason": err.reason,
                "rows": err.rows,
                "target_path": err.target_path,
            }
        )
```

- [ ] **Step 6: 改 build_bohrium_jobs_ports 签名与 delivery 分支**

`src/services/bohrium_jobs_wiring.py` `build_bohrium_jobs_ports`：在 `workspace: str | None,` 之后插入 `exporter` 必填参数：
```python
def build_bohrium_jobs_ports(
    *,
    session_id: str,
    invocation_id: str | None,
    user_id: str,
    org_id: str,
    workspace: str | None,
    exporter: WorkspaceJobsCsvExporter,
    job_context_mode: str = "session_workspace_delivery",
    spawn_id: str | None = None,
    delivery_snapshot: DeliverySnapshot | None = None,
    table: BohriumJobsTable | None = None,
    table_factory: Callable[[], BohriumJobsTable] = get_bohrium_jobs_table,
) -> tuple[_BohriumJobLedger | None, WorkspaceJobsPort]:
```

在函数体 `table_ref = ...` 之后、构造 ledger 之前，加阈值读取：
```python
    row_limit = env_int("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", 50)
    action_sample_limit = env_int("BOHRIUM_WORKSPACE_JOBS_ACTION_SAMPLE_LIMIT", 200)
```

把 `session_workspace_delivery` 分支（第 302-309 行）替换为：
```python
    elif job_context_mode == "session_workspace_delivery":
        jobs = _SessionWorkspaceDeliveryJobsPort(
            workspace=normalized_workspace,
            snapshot=delivery_snapshot,
            exporter=exporter,
            row_limit=row_limit,
            action_sample_limit=action_sample_limit,
        )
```
（observation 分支本 Task 暂不动——它仍用旧 `_WorkspaceObservationJobsPort(detail_limit=...)`，Task 4 重写。）

- [ ] **Step 7: agent_run_service 构造并注入 exporter**

`src/services/agent_run_service.py` 顶部 import 区加：
```python
from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter
```

在 `build_bohrium_jobs_ports(` 调用（第 543 行）之前插入 exporter 构造，并把它传进去：
```python
            workspace_jobs_exporter = WorkspaceJobsCsvExporter(
                session=environment.session,
                execution_workdir=environment.execution_workdir,
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
                exporter=workspace_jobs_exporter,
                job_context_mode=job_context_mode,
                delivery_snapshot=delivery_snapshot,
            )
```
（`environment.session` / `environment.execution_workdir` 来自 `ExecutionEnvironment`，`playground.py:78,81`，作用域内 `session_id` / `invocation_id` / `task_id` 均已定义。）

### 3C. 修复并补齐 wiring 测试

- [ ] **Step 8: 给所有 build 调用注入 exporter helper**

`tests/services/test_bohrium_jobs_wiring.py`：`build_bohrium_jobs_ports` 现在 `exporter` 必填，所有调用都要传。先在文件顶部（`build_bohrium_jobs_ports` import 之后）加 helper：
```python
def _exporter(session=None):
    from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter

    return WorkspaceJobsCsvExporter(
        session=session,
        execution_workdir="/share/project",
        session_id="s",
        invocation_id="inv",
        task_id="t",
    )
```

给以下 ledger 测试的 `build_bohrium_jobs_ports(...)` 调用各加一行 `exporter=_exporter(),`（它们不触发导出，传无 session 的 exporter 即可）：
`test_record_submit_passes_identity_snapshot`、`test_record_submit_fails_when_identity_missing`、`test_record_submit_allows_null_invocation_id`、`test_ledger_write_port_is_none_without_workspace`、`test_ledger_workspace_must_be_share_path`、`test_record_poll_fails_when_identity_missing`、`test_record_poll_normalizes_status_code`、`test_ports_do_not_construct_table_until_identity_allows_use`、`test_record_poll_terminal_feeds_observed_set`、`test_record_poll_without_snapshot_skips_observation`、`test_observation_mode_reads_three_groups_cross_session`、`test_observation_mode_empty_when_workspace_missing`。

- [ ] **Step 9: 重写 delivery port 测试**

删除三个已过时的 delivery 测试（它们验证“查 active”“用 detail_limit”，均已移除）：
`test_delivery_mode_serves_active_and_pending_from_snapshot`（第 142-171 行）、`test_delivery_mode_keeps_snapshot_pending_when_active_query_fails`（第 225-251 行）、`test_delivery_mode_uses_snapshot_detail_limit`（第 320-347 行）。

新增三态测试（放在 `_snapshot` helper 之后）：
```python
@pytest.mark.asyncio
async def test_delivery_under_row_limit_returns_full_pending_no_active_query() -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    snap = _snapshot(
        [
            {"id": 1, "job_id": "t1", "job_name": "ok", "status": "finished"},
            {"id": 2, "job_id": "f1", "job_name": "bad", "status": "failed"},
        ]
    )
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(),
        job_context_mode="session_workspace_delivery",
        delivery_snapshot=snap,
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.mode == "session_workspace_delivery"
    assert result.pending_terminal_jobs == snap.rows
    assert result.active_jobs == ()
    assert result.export is None
    assert result.summary.pending_terminal == 2
    table.query_session_active.assert_not_called()


@pytest.mark.asyncio
async def test_delivery_over_row_limit_exports_pending_only(monkeypatch) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    # 阈值在 build_bohrium_jobs_ports 内用 env_int 读取，故须在构造前 setenv
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", "2")
    table = MagicMock()
    rows = tuple(
        {"id": i, "job_id": f"j{i}", "job_name": "n", "status": "finished"}
        for i in range(3)
    )
    rows += ({"id": 99, "job_id": "f1", "job_name": "bad", "status": "failed"},)
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="session_workspace_delivery",
        delivery_snapshot=_snapshot(rows),
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export is not None
    assert result.export.reason == "row_limit"
    # CSV 只含 pending 组
    assert result.export.row_count == len(rows)
    assert result.pending_terminal_jobs == ()
    assert result.priority_samples  # 含 action 行 f1
    assert any(s["job_id"] == "f1" for s in result.priority_samples)


@pytest.mark.asyncio
async def test_delivery_export_failure_writes_snapshot_export_failure(monkeypatch) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", "1")
    table = MagicMock()
    rows = tuple(
        {"id": i, "job_id": f"j{i}", "job_name": "n", "status": "failed"}
        for i in range(3)
    )
    snap = _snapshot(rows)
    # exporter session=None → export 返回 session_missing 错误
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=None),
        job_context_mode="session_workspace_delivery",
        delivery_snapshot=snap,
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export_error is not None
    assert result.export_error.reason == "session_missing"
    assert snap.export_failure["reason"] == "session_missing"
    assert snap.export_failure["target_path"]
```

- [ ] **Step 10: 跑 wiring + renderer 测试看通过**

Run:
```bash
uv run pytest tests/services/test_bohrium_jobs_wiring.py tests/matmaster/context/sources/test_workspace_jobs.py -q
```
Expected: PASS（observation 相关测试此刻仍走旧 port 逻辑，断言未变，应仍绿）。

- [ ] **Step 11: 提交**

```bash
git add src/services/bohrium_jobs_wiring.py src/services/agent_run_service.py matmaster/context/sources/workspace_jobs.py tests/services/test_bohrium_jobs_wiring.py tests/matmaster/context/sources/test_workspace_jobs.py
git commit -m "feat(bohrium): wire delivery port three states with csv export and ack gating"
```

---

## Task 4: observation port 三态装配 + 大上限 + 截断声明

把 observation port 从“查三组原样返回”重写为完整装配：大上限查询、row+char 双阈值、样本、导出、导出失败写 snapshot。新增 `snapshot_truncated` 字段并在 renderer 声明。

**Files:**
- Modify: `matmaster/context/ports.py`
- Modify: `matmaster/context/sources/workspace_jobs.py`
- Modify: `src/services/bohrium_jobs_wiring.py`
- Test: `tests/matmaster/context/test_workspace_jobs_dto.py`、`tests/matmaster/context/sources/test_workspace_jobs.py`、`tests/services/test_bohrium_jobs_wiring.py`

### 4.1 + 4.2 snapshot_truncated 字段与声明

- [ ] **Step 1: DTO 加字段（含测试）**

`tests/matmaster/context/test_workspace_jobs_dto.py` 的 `test_workspace_jobs_new_fields_default` 末尾加一行：
```python
    assert jobs.snapshot_truncated is False
```

`matmaster/context/ports.py` `WorkspaceJobs` 末尾（`omitted_count` 之后）加字段：
```python
    omitted_count: int | None = None
    snapshot_truncated: bool = False
```

Run:
```bash
uv run pytest tests/matmaster/context/test_workspace_jobs_dto.py -q
```
Expected: PASS。

- [ ] **Step 2: renderer 截断声明（含测试）**

`tests/matmaster/context/sources/test_workspace_jobs.py` 末尾加测试：
```python
def test_compact_truncated_renders_snapshot_hint() -> None:
    from matmaster.context.ports import WorkspaceJobs, WorkspaceJobsExport

    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        summary=_summary(),
        snapshot_truncated=True,
        export=WorkspaceJobsExport(
            path="/share/p/.matmaster/context/workspace_jobs/s-i.csv",
            format="csv",
            row_count=3000,
            columns=("group", "job_id"),
            reason="row_limit",
        ),
    )

    lines = WorkspaceJobsSource.from_jobs(jobs).lines

    assert any("snapshot_truncated_hint" in line for line in lines)
```

`matmaster/context/sources/workspace_jobs.py`：文案常量区加：
```python
_SNAPSHOT_TRUNCATED_HINT = (
    "Workspace snapshot hit the row cap and may be incomplete; some jobs are "
    "absent from both this summary and the exported CSV."
)
```
在 `_head_lines`（被 compact/error 两态共用）的 `summary` 行之后、`return lines` 之前加：
```python
        if jobs.summary is not None:
            lines.append(f"summary {render_job_json(summary_to_dict(jobs.summary))}")
        if jobs.snapshot_truncated:
            lines.append(f'snapshot_truncated_hint "{_SNAPSHOT_TRUNCATED_HINT}"')
        return lines
```
（inline 态走 compute 的 `render_inline_lines`，不经 `_head_lines`；inline 态是“未超阈值”（total ≤ row_limit ≤ 50），与 truncated（命中 max_rows=2000）互斥，无需在 inline 渲染。）

Run:
```bash
uv run pytest tests/matmaster/context/sources/test_workspace_jobs.py -q
```
Expected: PASS。

### 4.3 observation port 装配

- [ ] **Step 3: 写失败测试（observation 三态）**

`tests/services/test_bohrium_jobs_wiring.py`：先重写 `test_observation_mode_reads_three_groups_cross_session`（未超阈值 → 返回 inline full，并校验 pending/recent 用 `max_rows` 作 limit）：
```python
@pytest.mark.asyncio
async def test_observation_mode_reads_three_groups_cross_session() -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    table.query_workspace_active.return_value = [
        {"job_id": "a", "job_name": "na", "status": "running"}
    ]
    table.query_workspace_pending_terminal.return_value = [
        {"job_id": "p", "job_name": "np", "status": "failed"}
    ]
    table.query_workspace_recent_terminal.return_value = [
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
    assert result.active_jobs == ({"job_id": "a", "job_name": "na", "status": "running"},)
    assert result.pending_terminal_jobs == (
        {"job_id": "p", "job_name": "np", "status": "failed"},
    )
    assert result.recent_terminal_jobs == (
        {"job_id": "r", "job_name": "nr", "status": "finished"},
    )
    assert result.summary.total == 3
    assert result.export is None
    # pending/recent 用大上限 max_rows（默认 2000）作 limit
    assert table.query_workspace_pending_terminal.call_args.kwargs["limit"] == 2000
    assert table.query_workspace_recent_terminal.call_args.kwargs["limit"] == 2000
```

再追加三个新测试：
```python
@pytest.mark.asyncio
async def test_observation_over_row_limit_exports_and_samples(monkeypatch) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", "2")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_pending_terminal.return_value = [
        {"job_id": f"p{i}", "job_name": "n", "status": "failed"} for i in range(5)
    ]
    table.query_workspace_recent_terminal.return_value = []
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
    assert result.priority_samples
    assert result.omitted_count == 5 - len(result.priority_samples)
    assert result.pending_terminal_jobs == ()


@pytest.mark.asyncio
async def test_observation_export_failure_writes_snapshot_and_error(monkeypatch) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", "2")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_pending_terminal.return_value = [
        {"job_id": f"p{i}", "job_name": "n", "status": "failed"} for i in range(5)
    ]
    table.query_workspace_recent_terminal.return_value = []
    snap = _snapshot([{"id": 1, "job_id": "p0", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=None),  # 导出失败
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export_error is not None
    assert result.export is None
    assert snap.export_failure["reason"] == "session_missing"


@pytest.mark.asyncio
async def test_observation_truncation_flag_set_at_max_rows(monkeypatch) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    # max_rows=3 且 pending 恰好返回 3 行（命中上限）→ 置 snapshot_truncated
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_OBSERVATION_MAX_ROWS", "3")
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT", "2")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_recent_terminal.return_value = []
    table.query_workspace_pending_terminal.return_value = [
        {"job_id": f"p{i}", "job_name": "n", "status": "failed"} for i in range(3)
    ]
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

    assert result.snapshot_truncated is True
```

- [ ] **Step 4: 跑测试看失败**

Run:
```bash
uv run pytest tests/services/test_bohrium_jobs_wiring.py::test_observation_over_row_limit_exports_and_samples -v
```
Expected: FAIL —— 旧 observation port 不装配 export/samples，`result.export is None`。

- [ ] **Step 5: 重写 observation port**

`src/services/bohrium_jobs_wiring.py`：把 import 区的 compute import 补上 `compute_inline_chars`：
```python
from matmaster.context.workspace_jobs_compute import (
    compute_inline_chars,
    compute_summary,
    select_priority_samples,
)
```

把整个 `_WorkspaceObservationJobsPort` 类（第 195-250 行）替换为：
```python
class _WorkspaceObservationJobsPort:
    """observation：跨 session 完整快照，row+char 双阈值。

    未超阈值 → 全量 inline（含三组 + summary）；超阈值 → 选样本 + 导出三组 CSV，
    返回 summary/样本/export/omitted；导出失败 → 写 snapshot.export_failure +
    返回 export_error。三组用防御性大上限 max_rows（pending/recent 应用；active
    无 DAO limit，保持全量），命中即置 snapshot_truncated。
    """

    def __init__(
        self,
        *,
        table_ref: _BohriumJobsTableRef,
        user_id: str,
        org_id: str,
        workspace: str,
        exporter: WorkspaceJobsCsvExporter,
        snapshot: DeliverySnapshot | None,
        row_limit: int,
        char_limit: int,
        action_sample_limit: int,
        priority_sample_limit: int,
        max_rows: int,
    ) -> None:
        self._table_ref = table_ref
        self._user_id = user_id
        self._org_id = org_id
        self._workspace = workspace
        self._exporter = exporter
        self._snapshot = snapshot
        self._row_limit = row_limit
        self._char_limit = char_limit
        self._action_sample_limit = action_sample_limit
        self._priority_sample_limit = priority_sample_limit
        self._max_rows = max_rows

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
                    limit=self._max_rows,
                ),
                asyncio.to_thread(
                    table.query_workspace_recent_terminal,
                    user_id=self._user_id,
                    org_id=self._org_id,
                    workspace=self._workspace,
                    limit=self._max_rows,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "load_workspace_jobs(observation) failed workspace=%s",
                self._workspace,
                exc_info=True,
            )
            return WorkspaceJobs.empty()
        active_t = tuple(active)
        pending_t = tuple(pending)
        recent_t = tuple(recent)
        truncated = (
            len(pending_t) >= self._max_rows or len(recent_t) >= self._max_rows
        )
        summary = compute_summary(active_t, pending_t, recent_t)
        full = WorkspaceJobs(
            workspace=self._workspace,
            active_jobs=active_t,
            pending_terminal_jobs=pending_t,
            recent_terminal_jobs=recent_t,
            summary=summary,
            mode="workspace_observation",
            snapshot_truncated=truncated,
        )
        if (
            summary.total <= self._row_limit
            and compute_inline_chars(full) <= self._char_limit
        ):
            return full
        samples = select_priority_samples(
            active_t,
            pending_t,
            recent_t,
            action_limit=self._action_sample_limit,
            fill_limit=self._priority_sample_limit,
        )
        reason = "row_limit" if summary.total > self._row_limit else "char_limit"
        result = self._exporter.export(full, reason=reason)
        if isinstance(result, WorkspaceJobsExportError):
            self._record_export_failure(result)
            return WorkspaceJobs(
                workspace=self._workspace,
                summary=summary,
                priority_samples=samples,
                export_error=result,
                mode="workspace_observation",
                snapshot_truncated=truncated,
            )
        return WorkspaceJobs(
            workspace=self._workspace,
            summary=summary,
            priority_samples=samples,
            export=result,
            omitted_count=summary.total - len(samples),
            mode="workspace_observation",
            snapshot_truncated=truncated,
        )

    def _record_export_failure(self, err: WorkspaceJobsExportError) -> None:
        if self._snapshot is None:
            return
        self._snapshot.export_failure.update(
            {
                "reason": err.reason,
                "rows": err.rows,
                "target_path": err.target_path,
            }
        )
```

- [ ] **Step 6: 改 build 的 observation 阈值与分支**

`src/services/bohrium_jobs_wiring.py` `build_bohrium_jobs_ports`：在 Task 3 加的 `row_limit` / `action_sample_limit` 之后补 observation 阈值：
```python
    char_limit = env_int("BOHRIUM_WORKSPACE_JOBS_INLINE_CHAR_LIMIT", 12000)
    priority_sample_limit = env_int("BOHRIUM_WORKSPACE_JOBS_PRIORITY_SAMPLE_LIMIT", 20)
    max_rows = env_int("BOHRIUM_WORKSPACE_JOBS_OBSERVATION_MAX_ROWS", 2000)
```

把 `workspace_observation` 分支（Task 3 后仍是旧 `detail_limit` 版）替换为：
```python
    elif job_context_mode == "workspace_observation":
        jobs = _WorkspaceObservationJobsPort(
            table_ref=table_ref,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            exporter=exporter,
            snapshot=delivery_snapshot,
            row_limit=row_limit,
            char_limit=char_limit,
            action_sample_limit=action_sample_limit,
            priority_sample_limit=priority_sample_limit,
            max_rows=max_rows,
        )
```
（移除了原 `detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20)`。）

- [ ] **Step 7: 跑 wiring + renderer + dto 测试看通过**

Run:
```bash
uv run pytest tests/services/test_bohrium_jobs_wiring.py tests/matmaster/context/sources/test_workspace_jobs.py tests/matmaster/context/test_workspace_jobs_dto.py -q
```
Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add matmaster/context/ports.py matmaster/context/sources/workspace_jobs.py src/services/bohrium_jobs_wiring.py tests/matmaster/context/test_workspace_jobs_dto.py tests/matmaster/context/sources/test_workspace_jobs.py tests/services/test_bohrium_jobs_wiring.py
git commit -m "feat(bohrium): assemble observation port three states with big-cap snapshot"
```

---

## Task 5: 移除 detail_limit（收尾）

`detail_limit` 已无任何读取方（delivery 在 Task 3、observation 在 Task 4 都改掉了）。移除 DTO 字段、snapshot 字段、snapshot() 构造、旧 env，并清理测试残留。

**Files:**
- Modify: `matmaster/context/ports.py`
- Modify: `src/services/bohrium_delivery_ack.py`
- Test: `tests/services/test_bohrium_delivery_ack.py`、`tests/services/test_bohrium_jobs_wiring.py`、Task 2 新增的两个 ack 测试

- [ ] **Step 1: 移除 DTO 字段**

`matmaster/context/ports.py` 删除 `WorkspaceJobs` 的：
```python
    detail_limit: int | None = None  # 过渡字段，Task 5 末尾移除
```

- [ ] **Step 2: 移除 DeliverySnapshot.detail_limit 与构造与 env import**

`src/services/bohrium_delivery_ack.py`：
- 删字段 `detail_limit: int`（dataclass 内）。
- 类 docstring 里删掉 “rows 持全量行、不预截断：展开几条详情由 renderer 按 detail_limit 决定。” 这句（已无 detail_limit）。
- `snapshot()` 返回的 `DeliverySnapshot(...)` 删 `detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20),` 这一行。
- 删顶部 `from src.utils.constant import env_int`（确认 grep 该文件内 `env_int` 仅此一处使用）。

Run:
```bash
grep -n "env_int" src/services/bohrium_delivery_ack.py
```
Expected: 无输出（确认 import 可安全删除）。

- [ ] **Step 3: 清理测试里的 detail_limit**

`tests/services/test_bohrium_delivery_ack.py`：
- 删两个仅测 detail_limit 的用例：`test_snapshot_reads_detail_limit_from_env`、`test_snapshot_detail_limit_defaults_when_env_unset`。
- 其余 `DeliverySnapshot(...)` 构造删 `detail_limit=20,` 行：`test_confirm_propagates_failure_to_caller`、`test_confirm_acks_union_of_rows_and_observed`、`test_confirm_skips_dao_calls_for_empty_sets`，以及 Task 2 新增的 `test_confirm_skips_rows_when_export_failed_but_acks_observed`、`test_confirm_acks_rows_when_export_failure_empty`。

`tests/services/test_bohrium_jobs_wiring.py`：`_snapshot` helper 删 `detail_limit=20,`：
```python
def _snapshot(rows):
    from src.services.bohrium_delivery_ack import DeliverySnapshot

    return DeliverySnapshot(
        user_id="u",
        org_id="o",
        session_id="s",
        workspace="/share/project",
        rows=tuple(rows),
    )
```

- [ ] **Step 4: grep 验证无残留**

Run:
```bash
grep -rn "detail_limit" matmaster/ src/
grep -rn "BOHRIUM_DELIVERY_DETAIL_LIMIT" matmaster/ src/
```
Expected: 两条均无输出。（测试目录内若仍有引用，回到 Step 3 清理。）

- [ ] **Step 5: 跑全量测试**

Run:
```bash
uv run pytest tests/ -q
```
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add matmaster/context/ports.py src/services/bohrium_delivery_ack.py tests/services/test_bohrium_delivery_ack.py tests/services/test_bohrium_jobs_wiring.py
git commit -m "refactor(bohrium): drop transitional detail_limit field and env"
```

---

## 5. 完成标准（spec §11）

- [ ] `uv run pytest tests/ -q` 全绿。
- [ ] `grep -rn "detail_limit" matmaster/ src/` 无输出。
- [ ] delivery 超阈值（如 1000 终态作业）：turn instruction 含失败样本 + “成功 N 个” + CSV 路径，不含 1000 行；prompt 无 workspace_jobs section；CSV 行数 == pending 数。
- [ ] observation 超阈值（如 1000 job）：section 含 summary + 样本 + CSV 路径，不含 1000 个 job_id；CSV 行数 == summary.total。
- [ ] delivery / observation CSV 失败：`confirm` 跳过 `snapshot.rows`，`observed_terminal` 照常 ack。
- [ ] observation 只 ack 本 session 行（worker 层 snapshot/confirm 机制未动，回归测试守住）。

## 6. 非目标（spec §12）

- 不改 ack 覆盖范围（仍 session-scoped，worker 层 snapshot/confirm 主体不动）。
- 不做 CSV 自动清理（随 workspace 生命周期）。
- 不为 observation 跨对话 ack。
- 不改 DAO（`query_workspace_active` 仍无 `limit`）。

## 7. Self-Review（plan 作者已核对）

- **Spec 覆盖：** §1–§2（ack 机制）由 Task 2/5 落实；§3 delivery 三态由 Task 3；§4 observation 三态 + 大上限 + 截断声明由 Task 4；§5 ack-gating 由 Task 2（confirm 守卫）+ Task 3/4（两 port 写 `export_failure`）；§6 底座复用，阈值 env 由 Task 3/4 引入；§7 组件边界逐条对应；§8 mode 切换由 Task 1，detail_limit 移除由 Task 5；§9 文件清单全覆盖（额外补 `snapshot_truncated`，已在 §2 标注）；§10 测试计划逐项有对应。
- **类型一致：** `WorkspaceJobs` 字段名（`pending_terminal_jobs` / `priority_samples` / `omitted_count` / `export` / `export_error` / `snapshot_truncated`）、`WorkspaceJobsExportError`（`reason` / `rows` / `target_path`）、exporter `.export(jobs, *, reason)` 返回 `WorkspaceJobsExport | WorkspaceJobsExportError`、`compute_summary` / `select_priority_samples`（`action_limit` / `fill_limit`）/ `compute_inline_chars` 签名，均与已落地代码一致。
- **mode 字面量：** 全程用 `"session_workspace_delivery"` / `"workspace_observation"`，无旧值残留。
- **占位符扫描：** 无 TBD / “类似上文” / 无代码的 code step。
