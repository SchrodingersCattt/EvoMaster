# Workspace Jobs CSV Export 设计（最终版 / 2026-06-15）

> 本文档取代 `2026-06-14-workspace-jobs-csv-export-design.md` 中关于 delivery 渲染的部分。
> 基于 2026-06-15 与用户的设计对齐，确立 **delivery / observation 双链路 + 统一 ack-gating**。
> 已完成代码：T1 DTO、T2 compute、T3 exporter、T4 observation 三态 renderer（见第 8 节）。

## 1. 背景与核心原则

### 1.1 问题

当 workspace 下 job 数量很大（典型 1000+），逐条 inline 进 prompt 会撑爆上下文。旧的 `detail_limit`（默认 20）有两个根本缺陷：

- 它**同时是查询的 DB LIMIT**（`query_workspace_pending_terminal(limit=20)`），导致根本查不到完整数据，只有前 20 条 + 一个 overflow 摘要。这不是完整快照，是残缺视图。
- overflow 摘要里的 `job_ids` 列表本身可能上千项，仍撑爆 prompt；agent 也无法按需查单个 job 的完整字段。

### 1.2 核心语义分野（本设计的地基）

prompt 里承载 job 信息的两个位置，语义本质不同：

- **`turn instruction` = 给模型“该做什么”的行动指导**（祈使性）→ **delivery** 场景：一批作业在本次 run 期间变终态，需引导模型向用户汇报、优先处理失败作业。
- **`workspace_jobs section` = workspace 的完整快照**（陈述性）→ **observation** 场景：旁观 workspace 当前有哪些 job、什么状态，供模型按需查询。

两条链路独立，共用同一个 CSV 导出底座（超阈值时把完整明细落盘，prompt 只留摘要/样本/路径）。

### 1.3 ack 归属原则

job 的确认（`mark_handled`，避免重复交付）按 **session 归属**：一个 session 只 ack 自己 `session_id` 名下的终态作业，绝不 ack 其他 session 的。这条规则与渲染链路无关——delivery 和 observation 都遵守。

## 2. 已有的 ack 机制（本次不重建）

ack 机制已存在于 **worker 层**，且 **mode 无关、天然 session-scoped**：

- **run 起点**（`agent_worker.py:356-363`）：只要 `workspace` 存在就创建 `delivery_snapshot = bohrium_delivery_ack.snapshot(session_id, workspace)`。`snapshot()` 调 `list_pending_terminal_snapshot`，查**本 session** 的 pending terminal 终态行（`terminal_at IS NOT NULL AND handled_at IS NULL`，按 `user_id/org_id/session_id/workspace` 过滤），返回行含 `id`。
- **run 中**：`_BohriumJobLedger.record_poll` poll 到终态时把 `(sandbox, job_id)` 加入 `snapshot.observed_terminal`（两种 mode 都生效）。
- **run 成功**（`agent_worker.py:535`）：`if run_success and delivery_snapshot is not None: confirm(delivery_snapshot)`——**不检查 mode**。`confirm()` 调 `mark_handled_by_ids(snapshot.rows 的 id)` + `mark_handled_by_job_keys(observed_terminal)`，两者 SQL WHERE 都含 `session_id`（session-scoped）。

**结论**：observation 现状**已经** ack 本 session 的终态行、且绝不碰其他 session。“展示与 ack 分离”已是事实——observation 展示 workspace 完整快照（跨 session，`query_workspace_*`），ack 用本 session 的 snapshot 行。本次不重建 ack，只新增 **CSV 失败时的 gating**（第 5 节）。

## 3. delivery 链路（turn instruction）

### 3.1 数据范围

delivery 只围绕 **`pending_terminal`**（= `snapshot.rows`，本 session 在 run 起点已终态、未 handled 的作业）。`active`（还在跑的）不进交付提示、不导出、不进 CSV。

> 实现注：现状 delivery port 额外查 `query_session_active` 并塞进 `active_jobs`，但 `delivery_instruction_text` 从不使用它，compositions 的 delivery 分支也不渲染 section。确认无其他依赖后，delivery port 移除该查询（净减无用代码）。

### 3.2 渲染：`delivery_instruction_text` 三态

由 `compositions._step_turn_input` 取文本、注入 turn instruction；`_step_workspace_jobs` 对 delivery **返回空**（prompt 不含 workspace_jobs section）。

- **未超阈值**（`len(pending) <= row_limit`）：保持现状——
  ```
  以下作业失败：
  job_id, job_name
  f1, relax-fail
  ...（failed/lost/stopped 全列）

  以下作业成功结束：
  job_id, job_name
  t9, relax-ok
  ...（finished 全列）
  ```
- **超阈值且导出成功**：失败样本 + 成功计数 + CSV 路径——
  ```
  以下作业失败：
  job_id, job_name
  f1, relax-fail
  l3, relax-lost
  ...（failed/lost/stopped 优先全列，受 ACTION 上限）

  以下作业成功结束：共 980 个（详见导出文件）

  完整明细已导出：/share/project-a/.matmaster/context/workspace_jobs/sess-123-inv-456.csv
  需要某个作业的 input_dir / result_dir 等，用 Read 或 Bash 读取该 CSV。
  ```
- **超阈值但导出失败**（export_error）：降级——渲染失败样本（尽力而为）+ 明确声明“完整明细导出失败，被省略的作业未必已交付”，**不给** CSV 路径。配合第 5 节，这批 snapshot 行的 ack 被 gate。

### 3.3 阈值与样本

- **只用 row 阈值**：`len(pending) > BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT`（默认 50）即导出。delivery 文本是 job_id/job_name 两列、每行很短，行数足以代表规模，不单独算 char。
- **样本只取行动关键**：failed/lost/stopped 的 pending terminal 行，优先全列，受 `BOHRIUM_WORKSPACE_JOBS_ACTION_SAMPLE_LIMIT`（默认 200）约束；成功的只报计数（`summary.by_status['finished']`），不内联。即 delivery 的 priority_samples 只含 action 行（等价 `select_priority_samples(action_limit=N, fill_limit=0)`）。
- **CSV 只含 pending_terminal**：exporter 输入只有 pending 组（active/recent 为空），CSV 行 group 全是 `pending_terminal`。

## 4. observation 链路（workspace_jobs section）

### 4.1 完整快照 + 防御性大上限

observation 展示 workspace 完整快照（`query_workspace_active/pending_terminal/recent_terminal`，**跨 session**）。为保证“完整”，查询**去掉小 limit**，改用防御性大上限 `BOHRIUM_WORKSPACE_JOBS_OBSERVATION_MAX_ROWS`（默认 2000，远超 row_limit 50），三组各自应用。命中上限即截断，section 内**显式声明**快照可能不完整。

### 4.2 三态（renderer，T4 已建）

- **未超阈值**（总数 ≤ row_limit 且 inline 字符 ≤ char_limit）：全量 inline（summary + 三组列式 CSV block）。
- **超阈值**：全量落 CSV（完整快照，三组）+ section 给 summary + priority samples + CSV 路径 + read_hint（+ failed 时 action_hint）。
- **导出失败**：error 态——`workspace_jobs_export_error` + 不输出 misleading 的 `details_exported`。

### 4.3 CSV 内容

observation 导出三组全量（`build_csv_rows(active, pending, recent)`），CSV row 的 group 列标识来源。字段来自 `_AGENT_COLUMNS`，`id/invocation_id/terminal_at` 等 delivery-only 列由 `restval=""` 空填。

## 5. 统一 ack-gating（本次唯一新增的 ack 改动）

ack 机制（第 2 节）不变，本次只在 **confirm 层加一道 gate**，对两种 mode 统一生效：

- **CSV 成功（或未导出）**：`snapshot.rows` 视为“已呈现”，照常 `mark_handled_by_ids`。
- **CSV 失败**：`snapshot.rows` 未成功呈现 → `confirm` **跳过** `mark_handled_by_ids`（这些行下个 run 自然重现，幂等安全）；`observed_terminal` 与 CSV 无关，**照常** `mark_handled_by_job_keys`。

### 5.1 失败状态的承载

`DeliverySnapshot` 是 `frozen=True`，沿用 `observed_terminal`（可变 set 绑 frozen 字段）的模式，新增可变容器 `export_failure: dict`（`field(default_factory=dict)`）。read port 导出失败时写入 `{reason, rows, target_path}`，`confirm` 据此 gate `snapshot.rows`。写入发生在 run 内上下文装配、读取在 run 结束 confirm，无时间重叠。

### 5.2 两种 mode 都要能写 export_failure

- delivery port 现状已持有 snapshot 引用。
- **observation port 本次新增 snapshot 引用**（`build_bohrium_jobs_ports` 把 `delivery_snapshot` 也传给 `_WorkspaceObservationJobsPort`），以便其导出失败时写 `export_failure`。

> 数据流自洽：observation 的 CSV 是 workspace 完整快照，本 session 终态行（snapshot.rows）是其子集；CSV 失败 = 快照未呈现 = 本 session 终态行未呈现 = gate。4.1 的完整快照改造使 observation 的“呈现范围”覆盖“ack 范围”，消除现状“ack 全量、展示却被 limit 截断”的潜在不一致。

## 6. 共用底座

- **DTO**（T1）：`WorkspaceJobs` + `WorkspaceJobsExport` / `WorkspaceJobsSummary` / `WorkspaceJobsExportError`。
- **compute 纯函数**（T2）：`compute_summary` / `render_inline_lines` / `compute_inline_chars` / `render_csv_block` / `select_priority_samples` / `build_csv_rows` / `build_csv_text` / `render_job_json` / `summary_to_dict`。CSV 列集 `CSV_COLUMNS`（14 列），样本列 `SUMMARY_COLUMNS`（job_id/job_name/status）。
- **exporter**（T3）：`WorkspaceJobsCsvExporter`，`session.write_file({execution_workdir}/.matmaster/context/workspace_jobs/{slug(session_id)}-{slug(invocation_id or task_id)}.csv, csv_text, encoding="utf-8")`，失败返回 `WorkspaceJobsExportError`（session_missing / bad_target_path / write_failed / serialize_failed）。
- **阈值 env**：`BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT`（50）、`..._INLINE_CHAR_LIMIT`（12000，仅 observation）、`..._ACTION_SAMPLE_LIMIT`（200）、`..._PRIORITY_SAMPLE_LIMIT`（20，仅 observation fill）、`..._OBSERVATION_MAX_ROWS`（2000）。

## 7. 组件边界与数据流

### 7.1 mode 值域

全切到 `workspace_observation` / `session_workspace_delivery`，同步：wiring 两处设值、`compositions._step_workspace_jobs` 判断、`workspace_jobs.py` 的 `from_jobs` + `delivery_instruction_text` 判断、以及各测试。

### 7.2 read port 装配

- **observation port**：查三组（大上限）→ 装配（summary、row+char 双阈值、超则选样本 fill+action、导出三组 CSV）→ `WorkspaceJobs(mode=workspace_observation, ...)`；导出失败写 `snapshot.export_failure`。
- **delivery port**：取 `snapshot.rows` → 装配（summary、**仅 row 阈值**）。未超阈值返回含完整 `pending_terminal_jobs` 的 `WorkspaceJobs`（供 `delivery_instruction_text` 全表渲染）；超阈值则**仅选 action 样本** + 导出 pending CSV，返回 summary/样本/export（不含完整明细）。`mode=session_workspace_delivery`；导出失败写 `snapshot.export_failure`。

> 两条装配共享 compute 底层函数；observation 多一个 char 阈值与 fill 样本，delivery 只有 row 阈值与 action 样本。实现时可用一个参数化 helper，也可两个小函数，以可读性为准。

### 7.3 exporter 注入

`AgentRunService` 在 `build_bohrium_jobs_ports` 前构造 `WorkspaceJobsCsvExporter`（session / execution_workdir / session_id / invocation_id / task_id），作为参数传入。

### 7.4 confirm gating

`agent_worker` 的 `confirm(delivery_snapshot)` 调用点不变（仍 mode 无关）；`bohrium_delivery_ack.confirm` 内部对 `snapshot.rows` 那一路加 `not snapshot.export_failure` 守卫。

## 8. 已完成代码（T1–T4）与本次关系

| commit | 内容 | 本次是否再动 |
|--------|------|------------|
| `3655dee0` | T1 DTO（含过渡字段 detail_limit） | 末尾移除 detail_limit |
| `d97d67e0` | T2 compute 纯函数 | 复用，不改 |
| `e75ca2a9` | T3 exporter | 复用，不改 |
| `9ea50f3c` | T4 observation 三态 renderer + 保留 delivery_instruction_text（旧 mode 值） | mode 值切新；delivery_instruction_text 升级为三态（3.2） |

## 9. 实现影响（文件清单）

| 文件 | 改动 |
|------|------|
| `matmaster/context/ports.py` | 移除 `WorkspaceJobs.detail_limit` |
| `matmaster/context/sources/workspace_jobs.py` | `delivery_instruction_text` 升级三态（全表 / 样本+计数+路径 / 导出失败降级）；两处 mode 判断切新值 |
| `matmaster/context/compositions.py` | `_step_workspace_jobs` 的 mode 判断切新值 |
| `src/services/bohrium_jobs_wiring.py` | observation/delivery 两条装配（共享 compute）；两 port 接 exporter + snapshot；observation 用大上限；mode 设新值；`build_bohrium_jobs_ports` 加 exporter 参数 |
| `src/services/bohrium_delivery_ack.py` | `DeliverySnapshot` 移除 detail_limit、加 `export_failure` dict；`snapshot()` 去 detail_limit；`confirm()` 加 `not export_failure` 守卫 |
| `src/services/agent_run_service.py` | 构造并注入 exporter |
| 对应测试 | renderer / wiring / delivery_ack / compositions 测试更新 |

## 10. 测试计划

- **renderer**：observation 三态（已 T4）；delivery_instruction_text 三态（未超全表、超阈值样本+计数+路径、导出失败降级）。
- **compute**：已 T2。
- **exporter**：已 T3。
- **wiring**：observation 超 row/char 阈值导出、未超 inline、大上限截断声明、导出失败 export_error + 写 `snapshot.export_failure`；delivery 超 row 阈值导出（仅 action 样本 + 成功计数）、CSV 只 pending、导出失败写 `snapshot.export_failure`；mode 正确填入。
- **delivery_ack**：`confirm` 在 `export_failure` 非空时跳过 `snapshot.rows`、保留 `observed_terminal`；`DeliverySnapshot` 无 detail_limit。
- **compositions**：delivery 仍走 turn instruction 不回归；observation 走 section。

## 11. 完成标准

- `uv run pytest tests/ -q` 全绿。
- `grep -rn "detail_limit" matmaster/ src/` 无输出。
- delivery 1000 终态作业：turn instruction 含失败样本 + “成功 N 个” + CSV 路径，不含 1000 行；prompt 无 workspace_jobs section；CSV 行数 == pending 数。
- observation 1000 job：section 含 summary + 样本 + CSV 路径，不含 1000 个 job_id；CSV 行数 == summary.total。
- delivery / observation CSV 失败：`confirm` 跳过 `snapshot.rows`，`observed_terminal` 照常。
- observation 只 ack 本 session 行（现状机制，回归测试守住）。

## 12. 非目标

- 不改 ack 的覆盖范围（仍 session-scoped，worker 层 snapshot/confirm 主体不动）。
- 不做 CSV 自动清理（随 workspace 生命周期）。
- 不为 observation 跨对话 ack（其他 session 的 job 由各自 session 负责）。
