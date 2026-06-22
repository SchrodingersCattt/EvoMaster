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

1. `workspace_jobs` prompt 长度必须有硬上限，不随 job 数量线性增长。该硬上限由
   渲染后实测加回退兜底（见第 6 节），不单纯依赖导出前估算。
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
  `job_name` 放进 agent-facing dict。注意 `_AGENT_COLUMNS` 不含 `id`、
  `invocation_id`、`terminal_at`，因此 observation 与 active、recent 组的
  agent-facing dict 没有这三列；delivery 模式的 pending 行走
  `list_pending_terminal_snapshot()`，字段是 `_AGENT_COLUMNS` 加 `id`、
  `invocation_id`、`terminal_at`（见 `_to_snapshot_job()`），均不含 `user_id`、
  `org_id`。
- `WorkspaceJobs` 当前字段为 `workspace`、`active_jobs`、
  `pending_terminal_jobs`、`recent_terminal_jobs`、`detail_limit`（本设计将移除
  `detail_limit`，见第 7 节）。
- `WorkspaceJobsSource` 是纯 renderer，只把 `WorkspaceJobs` 渲染成
  `ContextSection(key="workspace_jobs", tag="workspace_jobs")`。
- `build_bohrium_jobs_ports()` 在 service 层构造 read port。read port 可以查询完整
  jobs，但当前没有文件导出能力。
- `AgentRunService` 在 `run_bohrium_stage()` 之后构造 ports，此时已经知道
  `ExecutionEnvironment.session` 与 `execution_workdir`。Bohrium SSH 场景下，
  `execution_workdir` 是 `/share/...` 或 `/personal/...` 下的远端 workspace。
- `Session` 协议已有 `write_file()`、`read_file()`、`download()`；LocalSession 和
  SSHSession 都实现这些方法。`write_file()` 在两端都会自动创建父目录
  （LocalSession 用 `mkdir(parents=True)`，SSHSession 用 `mkdir -p`）。ReadTool
  读的也是当前 session 文件系统。
- delivery 模式下 `DeliverySnapshot` 是 run-local 内存对象（不落表），含 run 起点
  全量 pending terminal `rows` 与可变的 `observed_terminal` 集合；read port
  `_SessionWorkspaceDeliveryJobsPort` 与 Worker confirm 共享同一引用。confirm 通过
  `mark_handled_by_ids(rows)` 与 `mark_handled_by_job_keys(observed_terminal)`
  两路 ack，仅在 `run_success and snapshot is not None` 时执行。

## 5. 总体方案

当 workspace job 集合未超过阈值时，继续内联渲染完整明细（未超阈值已保证行数
≤ row_limit、字符 ≤ char_limit，因此不再做旧的 per-group `detail_limit` 截断）。

当超过阈值时：

1. service 层将完整 rows 导出为 CSV，写到当前执行 workspace 下。
2. `WorkspaceJobs` 返回完整内存 rows、统计摘要、CSV export metadata。
3. `WorkspaceJobsSource` 检测到 export metadata 后，渲染 compact prompt：
   - workspace。
   - mode。
   - summary。
   - details_exported。
   - read_hint。
   - action_hint。
   - priority samples。
   - omitted_from_prompt。
   - delivery 模式额外加 delivery_ack_scope。

   inline 完整明细与 compact priority samples 的多行 job 部分统一用列式 CSV，且只保留
   `job_id,job_name,status` 三列——agent 在 prompt 里判断"有哪些 job、什么状态、大概是
   什么内容"的最小充分集。逐 job 其余字段（input_dir、result_dir、terminal_at 等）不进
   prompt，超阈值时按 job_id 读导出 CSV，未超阈值时用 job_id 查 bohrium 工具。summary、
   details_exported、omitted_from_prompt 等单行结构仍用 JSON。
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
elif inline_chars > char_limit:
    export csv
else:
    inline render
```

`total_rows` 与第 7 节 `summary.total` 同口径（各组之和，不去重），也等于导出时的
CSV 行数。

`inline_chars` 直接按 renderer 的 inline 形态精确计算：renderer 与 wiring 共用同一个
`render_inline_lines(jobs)` 纯函数产出 inline 各行（summary 等单行 JSON + 各组列式 CSV
block），`inline_chars` 就是 `len("\n".join(render_inline_lines(jobs)))`，是最终 section
长度的精确值，不是估算，因此不存在低估。阈值判断与是否导出全部在 wiring 层完成；
renderer 只按 `WorkspaceJobs` 上有无 `export` / `export_error` 渲染对应形态，不自行判
阈值、不回退（renderer 无写文件能力，无法产生 compact 所需的 export metadata）。

另设两个 sample 上限：

```text
BOHRIUM_WORKSPACE_JOBS_PRIORITY_SAMPLE_LIMIT=20
BOHRIUM_WORKSPACE_JOBS_ACTION_SAMPLE_LIMIT=200
```

`PRIORITY_SAMPLE_LIMIT` 只约束填充样本（非行动关键的 pending、active、recent 行）。
`ACTION_SAMPLE_LIMIT` 约束行动关键样本（failed、lost、stopped 的 pending terminal
行）：这类行优先全部内联，仅当其数量超过 `ACTION_SAMPLE_LIMIT` 时，超出部分才退回
只在 CSV 中（并按第 11.1 节显式声明）。两类 samples 都只用于 prompt，不影响 CSV
完整性。

## 7. 数据结构

新增结构化 export metadata 与 summary、export error，都用 frozen dataclass，使
renderer 和测试有稳定契约：

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
    total: int                    # == active + pending_terminal + recent_terminal == CSV row_count
    active: int
    pending_terminal: int
    recent_terminal: int
    by_status: Mapping[str, int]  # 完整状态直方图；sum(by_status.values()) == total
    failed: int                   # 行动计数，从 by_status 派生，提到顶层供 prompt 行动提示
    stopped: int
    lost: int


@dataclass(frozen=True)
class WorkspaceJobsExportError:
    reason: Literal["session_missing", "bad_target_path", "write_failed", "serialize_failed"]
    rows: int                     # 本应导出的总行数，== summary.total
    target_path: str
```

扩展 `WorkspaceJobs`：

```python
@dataclass(frozen=True)
class WorkspaceJobs:
    workspace: str | None = None
    active_jobs: tuple[JsonObject, ...] = ()
    pending_terminal_jobs: tuple[JsonObject, ...] = ()
    recent_terminal_jobs: tuple[JsonObject, ...] = ()
    mode: Literal["workspace_observation", "session_workspace_delivery"] | None = None
    summary: WorkspaceJobsSummary | None = None
    export: WorkspaceJobsExport | None = None
    export_error: WorkspaceJobsExportError | None = None
```

说明：

- `mode` 由 read port 填入，renderer 不再凭字段是否为空猜测语义。
- `total` 不变量：`summary.total == active + pending_terminal + recent_terminal ==
  details_exported.rows == omitted_from_prompt.count + 已内联样本数`，全程不去重。
  既然第 8 节拒绝跨组去重，就不存在一个需要 job identity 推断的去重 total。
- `failed`、`stopped`、`lost` 是从 `by_status` 派生的行动计数，提到顶层只为方便
  prompt 行动提示；`finished`、`running`、`submitted` 等不在顶层重复，需要时从
  `by_status` 取，避免同一数据两处冗余。
- `export_error` 用于 CSV 应导出但写入失败的场景，见第 11.2 节；`reason` 的取值与
  第 13 节失败类型一一对应。
- 移除 `detail_limit`：新模型未超阈值即完整内联、超阈值即导出 CSV，不再需要
  per-group 截断，`detail_limit` 成为死字段。一并移除 `DeliverySnapshot.detail_limit`
  及 `bohrium_jobs_wiring` 中对它的三处赋值。

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
- CSV 的输入行来自异质来源、列集不同，exporter 必须用固定 fieldnames 的
  `csv.DictWriter(restval="", extrasaction="ignore")` 把每行投影到上面的列集：
  - delivery 模式的 pending terminal 行来自 `list_pending_terminal_snapshot()`，字段是
    `_AGENT_COLUMNS` 加 `id`、`invocation_id`、`terminal_at`，正好覆盖 CSV 列集（除
    `group` 由 exporter 补）。
  - observation 模式以及 active、recent 组的行来自 `_to_agent_job()`（基于
    `_AGENT_COLUMNS`），不含 `id`、`invocation_id`、`terminal_at`。这三列对这些行由
    `restval=""` 空填，属 delivery-only 列。第一版不为此扩 `_AGENT_COLUMNS`；若后续
    observation 也需要 `terminal_at`，再单独扩列。
  - 两个数据源都不 select `user_id`、`org_id`，agent-facing dict 也不含，因此 goal 7
    天然满足；`extrasaction="ignore"` 作防御，确保任何列集外字段都不会进 CSV。
- 任何缺失字段输出空字符串。
- bool 使用 `true` / `false` 小写字符串。
- 字段值中的换行由 `csv` 模块正常转义。

如果同一 job 同时出现在 pending terminal 与 recent terminal 中，CSV 不做跨组去重。
原因是 `group` 表达的是 context 来源，重复 row 对 agent 是可解释的；同时避免为
去重引入新的 job identity 推断。prompt summary 统计时应按 group 分别统计，不把
CSV row_count 误说成去重 job count。

## 9. Prompt 格式

### 9.1 未超过阈值

未超过阈值时仍可内联完整明细，但应补充 `mode` 与 `summary`，并保留 `job_name`。
多行 job 列表用列式 CSV：每个 group 渲染为一行 `组名 job_id,job_name,status` 表头，后跟
多行逗号分隔的值（标准 CSV 转义），列名只出现一次。inline 只放 job_id、job_name、status
三列；逐 job 详情不进 prompt（超阈值按 job_id 读导出 CSV，未超阈值用 job_id 查 bohrium
工具）：

```text
<workspace_jobs>
workspace /share/project-a
mode workspace_observation
summary {"total": 3, "active": 2, "pending_terminal": 1, "recent_terminal": 0, "by_status": {"running": 2, "failed": 1}, "failed": 1, "stopped": 0, "lost": 0}
active job_id,job_name,status
101,relax-FeO-001,running
102,relax-FeO-002,running
pending_terminal job_id,job_name,status
117,relax-FeO-017,failed
</workspace_jobs>
```

### 9.2 超过阈值

超过阈值时 prompt 只放摘要、CSV 路径和样本：

```text
<workspace_jobs>
workspace /share/project-a
mode workspace_observation
summary {"total": 1020, "active": 760, "pending_terminal": 240, "recent_terminal": 20, "by_status": {"running": 760, "finished": 258, "failed": 2}, "failed": 2, "stopped": 0, "lost": 0}
details_exported {"format": "csv", "path": "/share/project-a/.matmaster/context/workspace_jobs/sess-123-inv-456.csv", "rows": 1020, "columns": ["group", "job_id", "job_name", "status", "sandbox", "project_id", "input_dir", "workspace", "submitted_at", "last_polled_at", "terminal_at", "result_dir", "invocation_id", "id"], "reason": "row_limit"}
read_hint "Full job details are in the CSV file. Use Read or Bash to inspect/filter it when you need specific job ids, job names, failed rows, or result directories."
action_hint "Failed terminal jobs exist. Inspect failed rows first; do not enumerate all jobs in the final answer."
priority_samples job_id,job_name,status
117,relax-FeO-017,failed
142,relax-FeO-042,failed
omitted_from_prompt {"count": 1018, "reason": "large job set exported to csv"}
</workspace_jobs>
```

本例数字自洽：`total = 760 + 240 + 20 = 1020`，`sum(by_status) = 760 + 258 + 2 =
1020`，`details_exported.rows = 1020`，三者口径一致（各组之和，不去重）。`failed = 2 ≤
ACTION_SAMPLE_LIMIT`，两条 failed 行全部内联为 priority sample；
`omitted_from_prompt.count = total − 已内联样本数 = 1020 − 2 = 1018`。`priority_samples`
用列式 CSV，只放 `job_id,job_name,status` 三列；要看某 job 的 input_dir、result_dir 等，
按 job_id 读 `details_exported.path` 指向的 CSV。

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

priority samples 从完整 rows 中选择，分两类、分别处理：

行动关键样本：`pending_terminal` 中状态为 `failed`、`lost`、`stopped` 的 rows，优先
全部内联，受 `BOHRIUM_WORKSPACE_JOBS_ACTION_SAMPLE_LIMIT` 约束（默认 200）。原因见
第 11 节：delivery 模式下这些行会随 run 成功被 confirm，必须保证它们无论 agent 是否
读取 CSV 都已在 prompt 中呈现，从而让 ack 范围与呈现范围对最关键的失败行一致
（goal 5、goal 6）。仅当这类行超过 `ACTION_SAMPLE_LIMIT` 时，超出部分才退回只在 CSV
中，并按第 11.1 节显式声明。

填充样本：在行动关键样本之外，按下列顺序补充，总填充数不超过
`BOHRIUM_WORKSPACE_JOBS_PRIORITY_SAMPLE_LIMIT`（默认 20）：

1. 其他 `pending_terminal` rows，按现有 DAO 顺序。
2. `active` rows，按现有 DAO 顺序。
3. `recent_terminal` rows，按现有 DAO 顺序。

样本用列式 CSV 渲染，只放 `job_id,job_name,status` 三列，列名只出现一次。逐 job 的其余
字段（input_dir、result_dir、terminal_at 等）不进 prompt，一律按 job_id 从导出 CSV 读取。

## 11. Delivery ack 语义

`session_workspace_delivery` 模式下，pending terminal rows 来自 run 起点的
`DeliverySnapshot.rows`，run 成功后 Worker 会 confirm snapshot。

confirm 实际做两件独立的事，二者性质不同：

- `mark_handled_by_ids(snapshot.rows)`：ack run 起点快照里的全量 pending terminal 行。
  这些行是否被 agent 看到，取决于本设计的呈现方式（内联样本加 CSV）。
- `mark_handled_by_job_keys(observed_terminal)`：ack run 内 agent 主动 poll 时真实观测
  到的终态 job。这部分与 CSV 导出无关，是 run 内的真实观察，任何情况下都应照常
  confirm。

引入 CSV 后，CSV 被视为 agent 可见 context 的一部分：

- 如果 pending terminal rows 已成功写入 CSV，并且 prompt 中给出 CSV path，则这些 rows
  被视为已呈现。
- 其中状态为 failed、lost、stopped 的行另外还会内联为 priority sample（受
  `ACTION_SAMPLE_LIMIT` 约束，见 11.1），保证最关键的失败行无论 agent 是否读取 CSV
  都已在 prompt 中呈现。
- prompt 中必须明确 delivery ack scope。
- run 成功后可继续按 snapshot confirm。

delivery prompt 必须包含：

```text
delivery_ack_scope "On successful run, pending terminal rows exported in the CSV are considered delivered and may be marked handled."
```

### 11.1 行动关键行的呈现保证

failed、lost、stopped 的 pending terminal 行是 ack 时最不能被静默处理的行。本设计保证：

- 若这类行数 ≤ `ACTION_SAMPLE_LIMIT`，全部内联为 priority sample，ack 范围与呈现范围
  对这些行完全一致。
- 若超过该上限，超出部分只在 CSV 中。此时 delivery prompt 必须额外包含一条声明，显式
  说明存在未单独内联、仅在 CSV 中的 failed/lost/stopped 行，它们已导出 CSV 且将随 run
  成功被 ack：

```text
action_hint "Some failed/lost/stopped jobs are only in the CSV (count exceeds the inline limit). They will be marked delivered on success; read the CSV failed rows before concluding."
```

这样把超量失败行被 ack 但未单独内联这一语义显式化，而不是留作隐藏退化。

### 11.2 CSV 导出失败

如果 CSV 写入失败，不能把被省略的 rows 当作已呈现。此时 `WorkspaceJobs` 应设置
`export_error`，renderer 输出错误提示，不输出 misleading 的 `details_exported`。

失败示例：

```text
workspace_jobs_export_error {"reason": "write_failed", "rows": 1020, "target_path": "/share/project-a/.matmaster/context/workspace_jobs/sess-123-inv-456.csv"}
action_hint "Full job details could not be exported; do not assume omitted pending jobs were delivered."
```

实现阶段必须保证 delivery 模式下：

- CSV 成功：CSV rows 是呈现范围的一部分，`snapshot.rows` 可以按 snapshot confirm。
- CSV 失败：不得 confirm `snapshot.rows` 中未呈现的行；但 `observed_terminal` 仍照常
  confirm（那是 run 内真实观测，与 CSV 无关）。最简单且正确的做法是 export 失败时整体
  跳过 `mark_handled_by_ids(snapshot.rows)`（被省略行下个 run 自然重现，幂等安全），
  保留 `mark_handled_by_job_keys(observed_terminal)`。

### 11.3 失败状态的承载

export failure 必须记录在 run-local 能力对象上，confirm 前据此决定是否跳过
`snapshot.rows` 的 ack。现有的 `DeliverySnapshot` 就是这样的对象：read port
`_SessionWorkspaceDeliveryJobsPort` 已持有它的引用，Worker confirm 时也持有同一引用，
二者共享同一份 run-local 状态，无需新建管道。

注意 `DeliverySnapshot` 是 `frozen=True`，不能在 run 内对其字段重新赋值。现有的
`observed_terminal` 之所以能在 run 内更新，是因为它是绑在 frozen 字段上的可变 `set`，
更新的是 set 对象本身。export failure 标志必须同样用可变容器承载（例如一个可变
holder、单元素 list 或 set），不能用 frozen 上的 `bool` 字段（重新赋值会抛
`FrozenInstanceError`）。

该状态必须是 run-local 能力对象，不得塞进 `run_meta`——`run_meta`（`RunMetadata`）是
frozen 的被动身份数据（run_dir / task_id / source），run 内不更新，也无写能力，不适合
承载 run-local 一次性状态。

## 12. 组件边界

### 12.1 Context renderer

`WorkspaceJobsSource` 只负责渲染：

- 接收 `WorkspaceJobs`。
- 根据 `export` 或 `export_error` 决定 inline / compact / error 输出。
- 不自行判阈值、不回退：阈值判断与导出在 wiring 层，renderer 只渲染（见第 6 节）。
- 不访问 session。
- 不写文件。
- 不查 DAO。

### 12.2 Service export helper

新增 service 层 helper，例如 `WorkspaceJobsCsvExporter`：

- 输入：`session`、`execution_workdir`、`session_id`、`invocation_id`、`task_id`、
  以及来自三组的完整 rows。
- 输入行来自异质来源：delivery pending 行来自 `list_pending_terminal_snapshot()`
  （`_AGENT_COLUMNS` 加 `id`、`invocation_id`、`terminal_at`），observation 与 active、
  recent 行来自 `_to_agent_job()`（无 `id`、`invocation_id`、`terminal_at`）。两者都不含
  `user_id`、`org_id`。exporter 用固定 fieldnames 的
  `csv.DictWriter(restval="", extrasaction="ignore")` 把每行投影到第 8 节列集——缺失列
  空填，列集外字段丢弃。
- 输出：`WorkspaceJobsExport` 或 `WorkspaceJobsExportError`。
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
6. delivery 模式下，若导出失败，把失败状态写入 `DeliverySnapshot` 的可变失败容器，供
   Worker confirm 前检查。

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
若缺失，read port 应返回 `export_error`（`reason="session_missing"`），不能静默 inline
大列表。

## 13. 错误处理

CSV 导出失败分为以下类型，对应 `WorkspaceJobsExportError.reason` 的取值：

- session 为空 → `session_missing`。
- target path 为空或不在 `execution_workdir` 下 → `bad_target_path`。
- `write_file()` 抛异常 → `write_failed`。
- CSV 序列化失败 → `serialize_failed`。

错误处理规则：

1. 日志用 warning，带 session_id、workspace、mode、row_count、target_path、reason。
2. prompt 输出 `workspace_jobs_export_error`。
3. observation 模式仍可给有限 summary 和 priority samples。
4. delivery 模式必须阻止未呈现 rows 被 confirm（仅跳过 `snapshot.rows` 的 ack，
   `observed_terminal` 照常 confirm，见第 11.2 节）。
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
覆盖比累积多个陈旧 CSV 更容易让 agent 理解。注意路径含 `invocation_id` / `task_id`，
跨 run 是不同文件，因此覆盖只发生在同一 run 内的 context 重组；跨多个 run，
`.matmaster/context/workspace_jobs/` 下会按 run 累积文件。第一版接受这一累积，不做自动
清理，留待 workspace 生命周期或后续单独的清理策略处理。

## 15. 测试计划

### 15.1 Renderer tests

覆盖：

- 未超过阈值时 inline 输出包含 `summary`、`job_name` 和完整明细。
- 超过阈值且 `export` 存在时输出 `details_exported`、`read_hint`、
  `priority_sample_*`、`omitted_from_prompt`。
- `export_error` 存在时输出 `workspace_jobs_export_error`，不输出
  `details_exported`。
- `summary.total == active + pending_terminal + recent_terminal`，且与
  `details_exported.rows`、`omitted_from_prompt.count + 已内联样本数` 一致。
- failed/lost/stopped 的 pending 行在行数 ≤ `ACTION_SAMPLE_LIMIT` 时全部出现在
  priority samples。
- inline 渲染结果实测超过 char_limit 时回退到 compact。
- 不输出全量 overflow `job_ids`。

### 15.2 Export helper tests

覆盖：

- CSV header 固定。
- bool、小数值、None、换行字段正确序列化。
- 缺失字段输出空字符串。
- 输出 CSV 的列恰好是固定 14 列（group 加 13 列），不多不少。
- 即使输入 dict 被塞进 `user_id`、`org_id`（防御性测试），输出也不含这两列。
- delivery snapshot 行与 observation agent 行混合导出时，列集统一、缺失列空填。
- target path 在 `{execution_workdir}/.matmaster/context/workspace_jobs/` 下。
- path slug 对特殊字符做替换。
- writer 调用 `session.write_file(..., encoding="utf-8")`。

### 15.3 Wiring tests

覆盖：

- row count 超阈值时调用 exporter，并返回 `WorkspaceJobs.export`。
- char estimate 超阈值时调用 exporter。
- 未超阈值时不调用 exporter。
- exporter 抛错时返回 `export_error`，`reason` 与失败类型对应。
- `mode` 正确填入 `WorkspaceJobs`。
- `summary.total` 与各组之和一致。

### 15.4 Delivery tests

覆盖：

- delivery 模式 CSV 成功时，prompt 明确包含 delivery ack scope。
- delivery 模式 CSV 失败时，Worker 不 confirm 未呈现的 `snapshot.rows`，但
  `observed_terminal` 仍被 confirm。
- failed 行数超过 `ACTION_SAMPLE_LIMIT` 时，prompt 含未内联 failed 行的声明。
- observation 模式 CSV 失败不阻断 run，但 prompt 中有 export error。

### 15.5 Integration smoke

构造 1000 条 fake job rows：

- 渲染结果长度低于 `BOHRIUM_WORKSPACE_JOBS_INLINE_CHAR_LIMIT` 的一个小倍数。
- prompt 中包含 CSV path 和 priority samples。
- CSV 行数等于导出的 group rows 数，且等于 `summary.total`。
- prompt 中不包含 1000 个 job_id。

## 16. 迁移与落地顺序

1. 添加 `WorkspaceJobsExport`、`WorkspaceJobsSummary`、`WorkspaceJobsExportError` 三个
   frozen dataclass，并在 `WorkspaceJobs` 上添加 `mode`、`summary`、`export`、
   `export_error` 字段。
2. 移除 `WorkspaceJobs.detail_limit` 及其在 `DeliverySnapshot`、`bohrium_jobs_wiring`
   三处赋值的引用；新模型下未超阈值即完整内联、超阈值即导出 CSV，不再需要 per-group
   截断。
3. 添加纯函数统计（summary，含 total 口径）与 priority sample 选择（行动关键样本全内联
   加填充样本）。
4. 添加 CSV exporter helper（固定列投影加 `extrasaction="ignore"`）。
5. `AgentRunService` 注入 exporter。
6. `bohrium_jobs_wiring` 在 read port 中计算 summary、判断阈值、调用 exporter；delivery
   模式将 export 失败记入 `DeliverySnapshot` 的可变失败容器。
7. 拆分 confirm：export 失败只 gate `snapshot.rows` 的 ack，`observed_terminal` 照常
   confirm。
8. `WorkspaceJobsSource` 改为 bounded renderer（inline / compact / error 三态加渲染后
   长度校验回退）。
9. 补齐 renderer、exporter、wiring、delivery 测试。

这是一次开发阶段迁移，不保留旧 prompt 兼容路径。
