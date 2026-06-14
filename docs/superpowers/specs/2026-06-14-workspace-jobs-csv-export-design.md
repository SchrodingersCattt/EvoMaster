# Workspace jobs CSV export design

## 1. 背景

`workspace_jobs` 当前会把 Bohrium job ledger 里的 active、pending terminal、
recent terminal 三组 job 渲染进 `<workspace_jobs>` context section。这个设计已经
解决了 workspace 视角与 delivery 视角的问题，但在大规模 job 场景仍有 context
膨胀风险。

典型问题是 1000 个 job 同时存在时，agent 不需要在 prompt 里看到 1000 条完整
JSON。它需要的是：

- 当前 workspace 有多少 job。
- 哪些状态需要行动，尤其是 failed、stopped、lost。
- job_name 能说明 job 大概是什么内容。
- 如果需要完整明细，可以主动读取一个结构化文件。

因此本设计把 workspace job context 改成两层交付：

- prompt 内：摘要、关键样本、完整明细文件路径。
- workspace 文件：完整 job 明细 CSV，可由 agent 用 Read 或 Bash 读取。

## 2. 目标

1. `workspace_jobs` prompt 长度必须有硬上限，不随 job 数量线性增长。
2. 超过阈值时，将完整 job 明细写入当前 agent 可访问的 CSV 文件。
3. prompt 中必须告知 CSV 文件的绝对路径、行数、字段名和读取意图。
4. `job_name` 必须出现在 prompt 样本和 CSV 中，作为 agent 理解 job 内容的主要字段。
5. delivery 模式下，CSV 视为 agent 可见 context 的一部分；成功 run 后 ack 的范围必须与已呈现范围一致。
6. CSV 写入失败时，不得把未呈现的 pending terminal rows 当成已交付。
7. 不暴露 `user_id`、`org_id`。

## 3. 非目标

1. 不在第一版识别真正的批量 job，不推断 batch key。
2. 不新增或修改 `bohrium_jobs` 表结构。
3. 不引入兼容旧渲染格式的双路径。
4. 不让 `WorkspaceJobsSource` 持有 session、DAO 或文件写能力。
5. 不为 CSV 导出新增前端下载接口；agent 通过当前 workspace 文件系统读取。

## 4. 当前实现事实

- `BohriumJobsTable._AGENT_COLUMNS` 已包含 `job_name`，`_to_agent_job()` 也已把
  `job_name` 放进 agent-facing dict。
- `WorkspaceJobs` 当前字段为 `workspace`、`active_jobs`、
  `pending_terminal_jobs`、`recent_terminal_jobs`、`detail_limit`。
- `WorkspaceJobsSource` 是纯 renderer，只把 `WorkspaceJobs` 渲染成
  `ContextSection(key="workspace_jobs", tag="workspace_jobs")`。
- `build_bohrium_jobs_ports()` 在 service 层构造 read port。read port 可以查询完整
  jobs，但当前没有文件导出能力。
- `AgentRunService` 在 `run_bohrium_stage()` 之后构造 ports，此时已经知道
  `ExecutionEnvironment.session` 与 `execution_workdir`。Bohrium SSH 场景下，
  `execution_workdir` 是 `/share/...` 或 `/personal/...` 下的远端 workspace。
- `Session` 协议已有 `write_file()`、`read_file()`、`download()`；LocalSession 和
  SSHSession 都实现这些方法。ReadTool 读的也是当前 session 文件系统。

## 5. 总体方案

当 workspace job 集合未超过阈值时，继续内联渲染完整明细。

当超过阈值时：

1. service 层将完整 rows 导出为 CSV，写到当前执行 workspace 下。
2. `WorkspaceJobs` 返回完整内存 rows、统计摘要、CSV export metadata。
3. `WorkspaceJobsSource` 检测到 export metadata 后，渲染 compact prompt：
   - workspace。
   - mode。
   - summary。
   - details_exported。
   - read_hint。
   - priority samples。
   - omitted_from_prompt。
4. agent 若需要逐 job 检查，用 Read 或 Bash 读取 CSV。

推荐 CSV 路径：

```text
{execution_workdir}/.matmaster/context/workspace_jobs/{session_id}-{invocation_id_or_task_id}.csv
```

示例：

```text
/share/project-a/.matmaster/context/workspace_jobs/sess-123-inv-456.csv
```

路径必须是当前 session 文件系统里的绝对路径。不得只写 Worker 本地临时目录。

## 6. 阈值策略

引入两个独立阈值，任一命中就导出 CSV：

```text
BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT=50
BOHRIUM_WORKSPACE_JOBS_INLINE_CHAR_LIMIT=12000
```

判断规则：

```text
total_rows = len(active_jobs) + len(pending_terminal_jobs) + len(recent_terminal_jobs)

if total_rows > row_limit:
    export csv
elif estimated_inline_chars > char_limit:
    export csv
else:
    inline render
```

`estimated_inline_chars` 使用 renderer 将要输出的 JSON 行估算，不需要完全逐字符等同
最终 section，但必须保守到不会明显低估长字段。

另设 priority sample 上限：

```text
BOHRIUM_WORKSPACE_JOBS_PRIORITY_SAMPLE_LIMIT=20
```

priority samples 只用于 prompt，不影响 CSV 完整性。

## 7. 数据结构

新增结构化 export metadata：

```python
@dataclass(frozen=True)
class WorkspaceJobsExport:
    path: str
    format: Literal["csv"]
    row_count: int
    columns: tuple[str, ...]
    reason: Literal["row_limit", "char_limit"]
```

扩展 `WorkspaceJobs`：

```python
@dataclass(frozen=True)
class WorkspaceJobs:
    workspace: str | None = None
    active_jobs: tuple[JsonObject, ...] = ()
    pending_terminal_jobs: tuple[JsonObject, ...] = ()
    recent_terminal_jobs: tuple[JsonObject, ...] = ()
    detail_limit: int | None = None
    mode: Literal["workspace_observation", "session_workspace_delivery"] | None = None
    summary: JsonObject | None = None
    export: WorkspaceJobsExport | None = None
    export_error: JsonObject | None = None
```

`mode` 由 read port 填入，renderer 不再凭字段是否为空猜测语义。

`summary` 至少包含：

```text
total
active
pending_terminal
recent_terminal
by_status
failed
stopped
lost
finished
running
submitted
```

其中 `failed`、`stopped`、`lost` 用于 prompt 的行动提示。

`export_error` 用于 CSV 应导出但写入失败的场景，见第 11 节。

## 8. CSV 内容

CSV 使用标准库 `csv.DictWriter` 生成，UTF-8 编码，包含 header。

第一版字段：

```text
group
job_id
job_name
status
sandbox
project_id
input_dir
workspace
submitted_at
last_polled_at
terminal_at
result_dir
invocation_id
id
```

说明：

- `group` 表示 row 来源：`active`、`pending_terminal`、`recent_terminal`。
- `job_name` 必须保留。
- `id`、`invocation_id`、`terminal_at` 只在 row 中存在时写入；缺失则为空字符串。
- 不写 `user_id`、`org_id`。
- bool 使用 `true` / `false` 小写字符串。
- 字段值中的换行由 `csv` 模块正常转义。

如果同一 job 同时出现在 pending terminal 与 recent terminal 中，CSV 不做跨组去重。
原因是 `group` 表达的是 context 来源，重复 row 对 agent 是可解释的；同时避免为
去重引入新的 job identity 推断。prompt summary 统计时应按 group 分别统计，不把
CSV row_count 误说成去重 job count。

## 9. Prompt 格式

### 9.1 未超过阈值

未超过阈值时仍可内联完整明细，但应补充 `mode` 与 `summary`，并保留 `job_name`：

```text
<workspace_jobs>
workspace /share/project-a
mode workspace_observation
summary {"total": 3, "active": 2, "pending_terminal": 1, "recent_terminal": 0, "by_status": {"running": 2, "failed": 1}}
active_job_1 {"job_id": "101", "job_name": "relax-FeO-001", "status": "running", ...}
active_job_2 {"job_id": "102", "job_name": "relax-FeO-002", "status": "running", ...}
pending_terminal_job_1 {"job_id": "117", "job_name": "relax-FeO-017", "status": "failed", ...}
</workspace_jobs>
```

### 9.2 超过阈值

超过阈值时 prompt 只放摘要、CSV 路径和样本：

```text
<workspace_jobs>
workspace /share/project-a
mode workspace_observation
summary {"total": 1000, "active": 760, "pending_terminal": 240, "recent_terminal": 20, "failed": 3, "finished": 237, "by_status": {"running": 760, "finished": 237, "failed": 3}}
details_exported {"format": "csv", "path": "/share/project-a/.matmaster/context/workspace_jobs/sess-123-inv-456.csv", "rows": 1020, "columns": ["group", "job_id", "job_name", "status", "sandbox", "project_id", "input_dir", "workspace", "submitted_at", "last_polled_at", "terminal_at", "result_dir", "invocation_id", "id"], "reason": "row_limit"}
read_hint "Full job details are in the CSV file. Use Read or Bash to inspect/filter it when you need specific job ids, job names, failed rows, or result directories."
action_hint "Failed terminal jobs exist. Inspect failed rows first; do not enumerate all jobs in the final answer."
priority_sample_1 {"group": "pending_terminal", "job_id": "117", "job_name": "relax-FeO-017", "status": "failed", "input_dir": "...", "result_dir": null}
priority_sample_2 {"group": "pending_terminal", "job_id": "142", "job_name": "relax-FeO-042", "status": "failed", "input_dir": "...", "result_dir": null}
omitted_from_prompt {"count": 998, "reason": "large job set exported to csv"}
</workspace_jobs>
```

`details_exported.rows` 是 CSV 行数，不是去重 job 数。

### 9.3 Read hint 文案

read hint 必须直接告诉 agent 可以读取文件，但不要强制每次读取：

```text
Full job details are in the CSV file. Use Read or Bash to inspect/filter it when you need specific job ids, job names, failed rows, or result directories.
```

全成功大批量场景可额外给出：

```text
All pending terminal jobs finished successfully. Report batch completion without enumerating every row; read the CSV only if per-job details are needed.
```

## 10. Priority sample 选择

priority samples 从完整 rows 中选择，数量不超过
`BOHRIUM_WORKSPACE_JOBS_PRIORITY_SAMPLE_LIMIT`。

排序优先级：

1. `pending_terminal` 中状态为 `failed`、`lost`、`stopped` 的 rows。
2. 其他 `pending_terminal` rows，按现有 DAO 顺序。
3. `active` rows，按现有 DAO 顺序。
4. `recent_terminal` rows，按现有 DAO 顺序。

样本内容保留 `job_id`、`job_name`、`status`、`input_dir`、`result_dir`、`submitted_at`、
`terminal_at` 等已有字段。样本中不得引入 CSV 没有的隐藏字段。

## 11. Delivery ack 语义

`session_workspace_delivery` 模式下，pending terminal rows 来自 run 起点的
`DeliverySnapshot.rows`，run 成功后 Worker 会 confirm snapshot。

引入 CSV 后，CSV 被视为 agent 可见 context 的一部分：

- 如果 pending terminal rows 已成功写入 CSV，并且 prompt 中给出 CSV path，则这些 rows
  被视为已呈现。
- prompt 中必须明确 delivery ack scope。
- run 成功后可继续按 snapshot confirm。

delivery prompt 必须包含：

```text
delivery_ack_scope "On successful run, pending terminal rows exported in the CSV are considered delivered and may be marked handled."
```

如果 CSV 写入失败，不能把被省略的 rows 当作已呈现。此时 `WorkspaceJobs` 应设置
`export_error`，renderer 输出错误提示，不输出 misleading 的 `details_exported`。

失败示例：

```text
workspace_jobs_export_error {"reason": "write_failed", "rows": 1000, "target_path": "/share/project-a/.matmaster/context/workspace_jobs/sess-123-inv-456.csv"}
action_hint "Full job details could not be exported; do not assume omitted pending jobs were delivered."
```

实现阶段必须保证 delivery 模式下：

- CSV 成功：CSV rows 是呈现范围的一部分，可以按 snapshot confirm。
- CSV 失败：不得 confirm 未呈现的 pending terminal rows。

最直接实现是让 `WorkspaceJobsPort` 在 delivery 模式中把 CSV export failure 记录到
`DeliverySnapshot` 或等价 run-local 状态，Worker confirm 前检查该状态。该状态必须是
run-local 能力对象，不得塞进 `run_meta`。

## 12. 组件边界

### 12.1 Context renderer

`WorkspaceJobsSource` 只负责渲染：

- 接收 `WorkspaceJobs`。
- 根据 `export` 或 `export_error` 决定 inline / compact / error 输出。
- 不访问 session。
- 不写文件。
- 不查 DAO。

### 12.2 Service export helper

新增 service 层 helper，例如 `WorkspaceJobsCsvExporter`：

- 输入：`session`、`execution_workdir`、`session_id`、`invocation_id`、`task_id`。
- 输出：`WorkspaceJobsExport` 或错误对象。
- 使用 `session.write_file(path, csv_text, encoding="utf-8")` 写入当前 session 文件系统。
- 创建目录依赖 `write_file()` 的父目录创建语义；SSHSession 和 LocalSession 均已支持。

### 12.3 Bohrium jobs wiring

`build_bohrium_jobs_ports()` 新增可选 exporter 参数。read port 在
`load_workspace_jobs()` 内完成：

1. 查询 rows。
2. 构造 summary。
3. 判断阈值。
4. 如需导出，调用 exporter。
5. 返回带 `summary`、`export` 或 `export_error` 的 `WorkspaceJobs`。

`build_bohrium_jobs_ports()` 的 ledger write port 不受 CSV export 影响。

### 12.4 AgentRunService

`AgentRunService` 在 `run_bohrium_stage()` 后已经持有最终 `environment`。构造
`AgentRunPorts` 前应创建 exporter：

- `session=environment.session`
- `execution_workdir=environment.execution_workdir`
- `session_id=session_id`
- `invocation_id=invocation_id`
- `task_id=task_id`

如果 `environment.session` 为空，则 exporter 不可用。生产 chat run 正常应有 session；
若缺失，read port 应返回 `export_error`，不能静默 inline 大列表。

## 13. 错误处理

CSV 导出失败包括：

- session 为空。
- target path 为空或不在 `execution_workdir` 下。
- `write_file()` 抛异常。
- CSV 序列化失败。

错误处理规则：

1. 日志用 warning，带 session_id、workspace、mode、row_count、target_path。
2. prompt 输出 `workspace_jobs_export_error`。
3. observation 模式仍可给有限 summary 和 priority samples。
4. delivery 模式必须阻止未呈现 rows 被 confirm。
5. 不把 CSV 写入失败升级为整个 agent run 失败；这是 context 辅助失败，不是用户任务执行失败。

## 14. 文件生命周期

CSV 写入当前 execution workspace：

```text
.matmaster/context/workspace_jobs/
```

生命周期跟随 workspace。当前设计不做自动清理：

- 本地 workspace 由现有 run/workspace 生命周期处理。
- Bohrium workspace 下该文件可被 agent 在同一 workspace 后续 run 继续读取。
- 若 workspace archival 开启，文件可随 workspace 一起归档。

文件名使用安全字符：

```text
{safe_session_id}-{safe_invocation_id_or_task_id}.csv
```

`safe_*` 只允许字母、数字、点、下划线、短横线；其他字符替换为 `_`。

重复写入同一路径允许覆盖。原因是同一 run 的 context reassembly 应呈现当前最新视图；
覆盖比累积多个陈旧 CSV 更容易让 agent 理解。

## 15. 测试计划

### 15.1 Renderer tests

覆盖：

- 未超过阈值时 inline 输出包含 `summary`、`job_name` 和完整明细。
- 超过阈值且 `export` 存在时输出 `details_exported`、`read_hint`、
  `priority_sample_*`、`omitted_from_prompt`。
- `export_error` 存在时输出 `workspace_jobs_export_error`，不输出
  `details_exported`。
- 不输出全量 overflow `job_ids`。

### 15.2 Export helper tests

覆盖：

- CSV header 固定。
- bool、小数值、None、换行字段正确序列化。
- 缺失字段输出空字符串。
- target path 在 `{execution_workdir}/.matmaster/context/workspace_jobs/` 下。
- path slug 对特殊字符做替换。
- writer 调用 `session.write_file(..., encoding="utf-8")`。

### 15.3 Wiring tests

覆盖：

- row count 超阈值时调用 exporter，并返回 `WorkspaceJobs.export`。
- char estimate 超阈值时调用 exporter。
- 未超阈值时不调用 exporter。
- exporter 抛错时返回 `export_error`。
- `mode` 正确填入 `WorkspaceJobs`。

### 15.4 Delivery tests

覆盖：

- delivery 模式 CSV 成功时，prompt 明确包含 delivery ack scope。
- delivery 模式 CSV 失败时，Worker 不 confirm 未呈现 snapshot rows。
- observation 模式 CSV 失败不阻断 run，但 prompt 中有 export error。

### 15.5 Integration smoke

构造 1000 条 fake job rows：

- 渲染结果长度低于 `BOHRIUM_WORKSPACE_JOBS_INLINE_CHAR_LIMIT` 的一个小倍数。
- prompt 中包含 CSV path 和 priority samples。
- CSV 行数等于导出的 group rows 数。
- prompt 中不包含 1000 个 job_id。

## 16. 迁移与落地顺序

1. 添加 `WorkspaceJobsExport`、`mode`、`summary`、`export`、`export_error` 字段。
2. 添加纯函数统计与 priority sample 选择。
3. 添加 CSV exporter helper。
4. `AgentRunService` 注入 exporter。
5. `bohrium_jobs_wiring` 在 read port 中计算 summary、判断阈值、调用 exporter。
6. `WorkspaceJobsSource` 改为 bounded renderer。
7. 补齐 renderer、exporter、wiring、delivery 测试。

这是一次开发阶段迁移，不保留旧 prompt 兼容路径。
