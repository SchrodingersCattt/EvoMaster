# Workspace Jobs CSV Export — 执行进度（方向 B）

> 进度记录，供中断后恢复。原 plan：`2026-06-14-workspace-jobs-csv-export.md`；
> 原 spec：`../specs/2026-06-14-workspace-jobs-csv-export-design.md`。
> 分支：`feat/bohrium_job`。截至 2026-06-15。

## 0. 关键决策：方向 B（必读）

执行 Task 4 时发现 **plan/spec 与最新代码冲突**：

- spec/plan 假设 delivery 模式的 job 走 workspace_jobs **section** 三态渲染
  （compact 态含 `delivery_ack_scope`），并要**删除** `WorkspaceJobsSource.delivery_instruction_text`。
- 但最近 commit `5527150f`(inline delivery jobs in current instruction) 把 delivery job
  经 `delivery_instruction_text` 渲染成中文表格，由 `matmaster/context/compositions.py`
  的 `_step_turn_input` inline 到 **turn instruction**；`_step_workspace_jobs` 对 delivery 返回空。
- plan/spec 的影响面**都没有 compositions.py**。

**用户拍板 = 方向 B「保留 turn instruction 注入」：**
- delivery 维持现状（`delivery_instruction_text` → turn instruction）。
- CSV 导出**只服务 observation 模式**。
- delivery 超大 job 集仍可能撑爆 turn instruction（已知权衡，本次不处理）。
- mode 值域切到新值（`workspace_observation` / `session_workspace_delivery`），**集中在 Task 5 一次切**。
- Task 7 大幅简化（delivery 不导出 → 无 `export_failure`/confirm-gating，只移除 `detail_limit`）。

## 1. 已完成（已 commit）

| commit | Task | 内容 |
|--------|------|------|
| `3655dee0` | T1 | `ports.py`: `WorkspaceJobs` 加字段 + 3 个 dataclass（`WorkspaceJobsExport`/`WorkspaceJobsSummary`/`WorkspaceJobsExportError`），**保留过渡字段 `detail_limit`**；新增 `test_workspace_jobs_dto.py` |
| `d97d67e0` | T2 | `matmaster/context/workspace_jobs_compute.py` 纯函数（compute_summary / render_inline_lines / render_csv_block / select_priority_samples / build_csv_rows / build_csv_text / render_job_json / summary_to_dict / CSV_COLUMNS / SUMMARY_COLUMNS）+ 测试 |
| `e75ca2a9` | T3 | `src/services/workspace_jobs_export.py`（`WorkspaceJobsCsvExporter`）+ 测试 |
| `9ea50f3c` | T4 | `matmaster/context/sources/workspace_jobs.py`：observation **三态**（inline/compact/error）+ **保留 delivery_instruction_text 及辅助**；mode **仍沿用旧值** "delivery"/"observation"（值域切换留给 T5）；`test_workspace_jobs.py` 重写 |

工作区当前**干净**（`git status --short | grep -v docs/` 为空）。

## 2. 剩余工作

### Task 5（方向 B）：wiring 导出装配 + mode 值域统一切换 + 移除 detail_limit

> ⚠️ 这是方向 B 最大的一步，跨 5 个源文件 + 3 个测试文件。mode 值域必须**一次性一致切换**，
> 否则 delivery 会回归（turn instruction 变空，或 section 重复渲染）。

**源码改动：**

1. `src/services/bohrium_jobs_wiring.py`（真实行号：delivery port ~146-192，observation port ~195-250，build ~258-312）：
   - **import**：`WorkspaceJobsExportError`（加入现有 ports import）；新增
     `from matmaster.context.workspace_jobs_compute import (compute_inline_chars, compute_summary, select_priority_samples)`；
     `from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter`。
   - **新增 `_assemble_workspace_jobs` helper**（mode 无关，参数传 mode）：算 summary → 判阈值
     （`BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT` 默认 50 / `..._INLINE_CHAR_LIMIT` 默认 12000）
     → 未超则返回 inline `WorkspaceJobs`；超则 `select_priority_samples`
     （`..._ACTION_SAMPLE_LIMIT` 默认 200 / `..._PRIORITY_SAMPLE_LIMIT` 默认 20）+ 调
     `exporter.export(inline, reason=...)`；exporter 为 None → `WorkspaceJobsExportError(reason="session_missing", rows=summary.total, target_path="")`；
     按结果返回带 `export` 或 `export_error` 的 `WorkspaceJobs`。
   - **`_SessionWorkspaceDeliveryJobsPort.load_workspace_jobs`**：删 detail_limit 逻辑（`pending = self._snapshot.rows if self._snapshot is not None else ()`），return 去掉 `detail_limit=`，`mode="delivery"` → `mode="session_workspace_delivery"`。**不接 exporter、不算 summary**（delivery 不导出）。
   - **`_WorkspaceObservationJobsPort`**：构造参数 `detail_limit` → `observation_query_limit`，加 `exporter: WorkspaceJobsCsvExporter | None = None`；两处 query `limit=self._observation_query_limit`；return 改为 `return _assemble_workspace_jobs(workspace=..., mode="workspace_observation", active=tuple(active), pending=tuple(pending), recent=tuple(recent), exporter=self._exporter)`。
   - **`build_bohrium_jobs_ports`**：签名在 `delivery_snapshot` 之后加 `exporter: WorkspaceJobsCsvExporter | None = None`；observation 分支 `detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20)` → `observation_query_limit=env_int("BOHRIUM_WORKSPACE_JOBS_OBSERVATION_QUERY_LIMIT", 20)` + `exporter=exporter`；delivery 分支不变。

2. `matmaster/context/compositions.py:101`：`if inputs.workspace_jobs.mode == "delivery":` → `== "session_workspace_delivery"`。

3. `matmaster/context/sources/workspace_jobs.py`：
   - `from_jobs`：`if jobs.mode == "delivery":` → `== "session_workspace_delivery"`。
   - `delivery_instruction_text`：`if jobs.mode != "delivery" ...` → `!= "session_workspace_delivery"`。

4. `matmaster/context/ports.py`：删除 `WorkspaceJobs` 的 `detail_limit: int | None = None  # 过渡字段...` 这一行。

**测试改动：**

- `tests/matmaster/context/sources/test_workspace_jobs.py`：4 处 delivery 用例的 `mode="delivery"` → `"session_workspace_delivery"`。
- `tests/matmaster/context/test_compositions.py`：若有 `mode="delivery"` 构造 → 改新值（**先 Read 确认**）。
- `tests/services/test_bohrium_jobs_wiring.py`：删 `test_delivery_mode_uses_snapshot_detail_limit`；`test_delivery_mode_serves_active_and_pending_from_snapshot`、`test_observation_mode_reads_three_groups_cross_session` 等若断言旧 mode/格式/detail_limit 需适配；新增「行数超阈值→export」「char 超阈值→export」「未超→不调 exporter」「exporter 抛错→export_error」「mode 正确填入」用例（参考原 plan Step 5.1，但**只针对 observation**，delivery 不导出）。

**验证：** `uv run pytest tests/matmaster/context/ tests/services/test_bohrium_jobs_wiring.py -q`，
并务必跑 `tests/matmaster/context/test_compositions.py` 确认 delivery 仍走 turn instruction 不回归。
`grep -rn "detail_limit" matmaster/ src/services/bohrium_jobs_wiring.py` 应只剩 `bohrium_delivery_ack.py`（T7 清）。

### Task 6：注入 exporter 到 AgentRunService（同原 plan，基本不变）

- `src/services/agent_run_service.py`（`build_bohrium_jobs_ports` 调用点真实在 ~543-551）：
  - import `from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter`。
  - 调用前构造 `WorkspaceJobsCsvExporter(session=environment.session, execution_workdir=environment.execution_workdir or str(environment.workdir), session_id=session_id, invocation_id=invocation_id, task_id=task_id)`，传 `exporter=...`。
  - **动手前确认**：调用点局部变量名是 `environment` 还是要写 `stage_result.environment`；`task_id` 变量名（Explore 当时报告是 `task_id`，赋值在 ~309）。
- 验证：`uv run pytest tests/services/ -q`（无 import error、不回归）。

### Task 7（方向 B 简化）：移除 DeliverySnapshot.detail_limit

- `src/services/bohrium_delivery_ack.py`：
  - `DeliverySnapshot` 删 `detail_limit: int` 字段；更新行 ~26 注释（提到 detail_limit 的那句）。
  - `snapshot()` 删 `detail_limit=env_int("BOHRIUM_DELIVERY_DETAIL_LIMIT", 20)` 行。
  - 确认 `env_int` 是否仅此一处使用（`grep -n env_int src/services/bohrium_delivery_ack.py`），若是则删其 import。
  - `confirm()` **不变**。**不加** `export_failure`（方向 B delivery 不导出）。
- `tests/services/test_bohrium_delivery_ack.py`：删 `test_snapshot_reads_detail_limit_from_env`、`test_snapshot_detail_limit_defaults_when_env_unset`；所有 `DeliverySnapshot(...)` 构造去掉 `detail_limit=`。**不加** export_failure 测试。
- 验证：`uv run pytest tests/ -q` 全绿；`grep -rn "detail_limit" matmaster/ src/` **无输出**。

## 3. 完成标准（方向 B）

- `uv run pytest tests/ -q` 全绿。
- `grep -rn "detail_limit" matmaster/ src/` 无输出。
- delivery 仍 inline 到 turn instruction（`test_compositions.py` 通过）。
- observation 1000 条 job 场景：workspace_jobs section 不含 1000 个 job_id，含 CSV path、summary、priority samples；CSV 行数 == summary.total。
- 不存在 export_failure / confirm-gating（delivery 不导出）。

## 4. 陷阱记录（踩过的坑）

1. **同一 message 对同一文件发多个 Edit 会被并发校验拒绝**（InputValidationError: concurrent edits）。本环境**无 MultiEdit 工具**。解决：同文件多处改动 → 用 `Write` 整体重写，或分多个 message 逐个 Edit；一个 message 内可以 Edit 不同文件。
2. **Read 大文件时显示层会串扰**（行号错乱、串入不存在的符号，如曾误报 `_AGENT_COLUMNS`、`current_instruction.py`）。解决：单独/小范围 Read，关键行用 `grep -n` 校准行号。
3. **mode 值域切换必须跨文件一次到位**：wiring 两处设值 + compositions:101 + workspace_jobs 两处判断，缺一就 delivery 回归。
4. wiring observation 旧 env 名是 `BOHRIUM_DELIVERY_DETAIL_LIMIT`（要换成新的 `BOHRIUM_WORKSPACE_JOBS_OBSERVATION_QUERY_LIMIT`）。
5. Task 计数：本会话用了 TaskCreate（#1-#7），T1-T4 已 completed，T5 in_progress，T6/T7 pending。
