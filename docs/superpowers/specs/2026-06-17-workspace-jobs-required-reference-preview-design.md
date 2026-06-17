# Workspace Jobs Required / Reference / Preview 设计（2026-06-17）

> 本文档补充并取代 `2026-06-15-workspace-jobs-csv-export-design.md`
> 中关于 observation job bucket、limit 语义、compact sample 预算的部分。
> CSV export、delivery snapshot、confirm gating 的既有方向保留，但需要按本文的不变量修正。

## 1. 背景

当前 `workspace_jobs` observation 设计把 active、pending terminal、recent terminal
放在同一套大快照与 prompt sample 预算下，导致多个 limit 同时存在：

```text
BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT
BOHRIUM_WORKSPACE_JOBS_INLINE_CHAR_LIMIT
BOHRIUM_WORKSPACE_JOBS_ACTION_SAMPLE_LIMIT
BOHRIUM_WORKSPACE_JOBS_PRIORITY_SAMPLE_LIMIT
BOHRIUM_WORKSPACE_JOBS_OBSERVATION_MAX_ROWS
```

这些 limit 分别作用在 DB 查询、inline 判断、compact 样本选择和防御性大上限上，
但命名都像是在控制 job 数量，容易被误读为同一个 N。

更关键的是，当前 `query_workspace_recent_terminal()` 查询条件是：

```sql
terminal_at IS NOT NULL
```

它没有排除 `handled_at IS NULL`，因此未 handled terminal job 可能同时出现在
`pending_terminal_jobs` 与 `recent_terminal_jobs` 中。这样会让 summary 重复计数，
也让 recent terminal 的业务含义不清楚。

本设计的目标是把 workspace jobs observation 按业务语义拆成三类数据，并让每个
limit 只服务一个明确层级。

## 2. 核心语义

observation 模式下的 workspace jobs 分成三组：

```text
active_jobs
  当前仍在运行的作业。
  必看数据，agent 必须在本轮 context 中看到。

unhandled_terminal_jobs
  已经 terminal，且 handled_at IS NULL 的作业。
  必看数据，agent 必须在本轮 context 中看到。

handled_recent_terminal_jobs
  已经 terminal，且 handled_at IS NOT NULL 的最近作业。
  参考数据，只提供历史背景，最多纳入最近 N 条。
```

这里的看到定义为：

```text
进入 prompt
或进入 CSV
```

因此，20 条限制不应该发生在 prompt compact sample 层，而应该发生在
handled recent terminal reference bucket 的 DB 读取与 snapshot 纳入层。

## 3. 不变量

实现完成后必须满足以下不变量：

```text
1. active jobs 是 required context，必须进入 prompt 或 CSV。
2. unhandled terminal jobs 是 required context，必须进入 prompt 或 CSV。
3. handled recent terminal jobs 是 reference context，最多纳入 HANDLED_RECENT_LIMIT 条。
4. prompt 中直接展示的 job 明细最多 PROMPT_PREVIEW_LIMIT 条。
5. CSV 包含本轮纳入 snapshot 的完整数据。
6. required context 被 REQUIRED_FETCH_LIMIT 截断时，必须显式标记 required_truncated=true。
7. handled recent 超过 HANDLED_RECENT_LIMIT 时，只标记 handled_recent_has_more=true，
   不算 required_truncated。
8. delivery 模式不使用 HANDLED_RECENT_LIMIT。
9. delivery snapshot 的 ack 边界不受 prompt preview 限制影响。
10. observation 模式下如果 required context 不完整或 CSV 导出失败，不得 ack
    未被完整呈现的 delivery_snapshot.rows。
```

## 4. 配置

公开配置收敛为三项：

```text
BOHRIUM_WORKSPACE_JOBS_REQUIRED_FETCH_LIMIT=2000
BOHRIUM_WORKSPACE_JOBS_HANDLED_RECENT_LIMIT=20
BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT=50
```

内部派生字符保护：

```text
prompt_char_limit = min(PROMPT_PREVIEW_LIMIT * 240, 24000)
```

默认情况下：

```text
PROMPT_PREVIEW_LIMIT=50
prompt_char_limit=12000
```

### 4.1 REQUIRED_FETCH_LIMIT

`REQUIRED_FETCH_LIMIT` 是必看数据的安全 fetch cap。

作用对象：

```text
active_jobs
unhandled_terminal_jobs
```

语义：

```text
这些 job 是 required context。
数量较多时可以导出 CSV，但不能因为 prompt 太长就丢弃。
如果命中 REQUIRED_FETCH_LIMIT，说明 required context 可能不完整。
```

查询应使用 `limit + 1`：

```text
查询 REQUIRED_FETCH_LIMIT + 1 条
只纳入前 REQUIRED_FETCH_LIMIT 条
如果原始返回条数超过 REQUIRED_FETCH_LIMIT，则 required_truncated=true
```

`required_truncated=true` 是严重降级信号，不等价于 handled recent 的正常省略。

### 4.2 HANDLED_RECENT_LIMIT

`HANDLED_RECENT_LIMIT` 是已 handled recent terminal 参考数据的纳入上限。

作用对象：

```text
terminal_at IS NOT NULL
AND handled_at IS NOT NULL
ORDER BY terminal_at DESC, id DESC
```

语义：

```text
这些 job 只是历史参考。
最多纳入最近 HANDLED_RECENT_LIMIT 条。
超过的 handled recent jobs 不进入 prompt，也不进入 CSV。
```

查询应使用 `limit + 1`：

```text
查询 HANDLED_RECENT_LIMIT + 1 条
只纳入前 HANDLED_RECENT_LIMIT 条
如果原始返回条数超过 HANDLED_RECENT_LIMIT，则 handled_recent_has_more=true
```

`handled_recent_has_more=true` 是正常策略信号，不表示 required context 不完整。

### 4.3 PROMPT_PREVIEW_LIMIT

`PROMPT_PREVIEW_LIMIT` 控制 prompt 中直接展示多少行 job 明细。

它不控制 agent 是否能看到完整数据。完整数据由 CSV 承载。

语义：

```text
snapshot_rows <= PROMPT_PREVIEW_LIMIT 且字符数未超时，可以 full inline。
snapshot_rows > PROMPT_PREVIEW_LIMIT 或字符数超限时，导出 CSV。
compact prompt 中最多展示 PROMPT_PREVIEW_LIMIT 条 preview rows。
```

## 5. 旧配置处理

删除旧配置读取：

```text
BOHRIUM_WORKSPACE_JOBS_OBSERVATION_MAX_ROWS
BOHRIUM_WORKSPACE_JOBS_INLINE_ROW_LIMIT
BOHRIUM_WORKSPACE_JOBS_INLINE_CHAR_LIMIT
BOHRIUM_WORKSPACE_JOBS_ACTION_SAMPLE_LIMIT
BOHRIUM_WORKSPACE_JOBS_PRIORITY_SAMPLE_LIMIT
```

不在主代码中做旧 env fallback。

迁移关系只写入部署说明与设计文档：

```text
OBSERVATION_MAX_ROWS
  -> REQUIRED_FETCH_LIMIT

INLINE_ROW_LIMIT
  -> PROMPT_PREVIEW_LIMIT

INLINE_CHAR_LIMIT
  -> prompt_char_limit 内部派生

ACTION_SAMPLE_LIMIT
  -> 删除，不再作为独立 prompt 预算

PRIORITY_SAMPLE_LIMIT
  -> 删除，不再作为 compact sample 预算
```

注意：旧 `PRIORITY_SAMPLE_LIMIT=20` 不应静默映射为
`HANDLED_RECENT_LIMIT=20`。两者默认值相同，但语义不同。

## 6. 数据结构

context 层应显式表达三类 bucket：

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
    preview_rows: tuple[JsonObject, ...] = ()
    omitted_count: int | None = None
    required_truncated: bool = False
    handled_recent_has_more: bool = False
```

`pending_terminal_jobs` 与 `recent_terminal_jobs` 这两个字段名不再适合 observation
语义：

```text
pending_terminal_jobs
  改为 unhandled_terminal_jobs

recent_terminal_jobs
  改为 handled_recent_terminal_jobs
```

实施时直接迁移到新字段名，不保留旧字段名作为运行时兼容层，避免继续混淆。

summary 至少需要包含：

```text
total
active
unhandled_terminal
handled_recent_terminal
by_status
failed
stopped
lost
required_truncated
handled_recent_has_more
```

其中 `total` 只统计本轮纳入 snapshot 的行：

```text
total = active + unhandled_terminal + handled_recent_terminal
```

超过 `HANDLED_RECENT_LIMIT` 的更旧 handled recent rows 不属于本轮 snapshot，
不计入 `total`，只通过 `handled_recent_has_more=true` 表示。

## 7. DAO 查询

observation 模式需要三类查询。

### 7.1 Active Jobs

查询条件：

```sql
WHERE user_id = ?
  AND org_id = ?
  AND workspace = ?
  AND status IN (active statuses)
ORDER BY submitted_at ASC, id ASC
LIMIT REQUIRED_FETCH_LIMIT + 1
```

active 查询当前没有 limit，需要补上。

### 7.2 Unhandled Terminal Jobs

查询条件：

```sql
WHERE user_id = ?
  AND org_id = ?
  AND workspace = ?
  AND terminal_at IS NOT NULL
  AND handled_at IS NULL
ORDER BY terminal_at ASC, submitted_at ASC, id ASC
LIMIT REQUIRED_FETCH_LIMIT + 1
```

这是原 `query_workspace_pending_terminal()` 的业务语义，但建议命名改为
`query_workspace_unhandled_terminal()`。

### 7.3 Handled Recent Terminal Jobs

查询条件：

```sql
WHERE user_id = ?
  AND org_id = ?
  AND workspace = ?
  AND terminal_at IS NOT NULL
  AND handled_at IS NOT NULL
ORDER BY terminal_at DESC, id DESC
LIMIT HANDLED_RECENT_LIMIT + 1
```

这是原 `query_workspace_recent_terminal()` 需要修正的地方。新的 recent bucket 必须只包含
已 handled terminal rows，避免与 unhandled terminal 重叠。

## 8. Observation 流程

### 8.1 读取

读取 required context：

```python
active_raw = query_active(limit=required_fetch_limit + 1)
unhandled_raw = query_unhandled_terminal(limit=required_fetch_limit + 1)

active = active_raw[:required_fetch_limit]
unhandled = unhandled_raw[:required_fetch_limit]

required_truncated = (
    len(active_raw) > required_fetch_limit
    or len(unhandled_raw) > required_fetch_limit
)
```

读取 reference context：

```python
handled_recent_raw = query_handled_recent_terminal(
    limit=handled_recent_limit + 1
)

handled_recent = handled_recent_raw[:handled_recent_limit]
handled_recent_has_more = len(handled_recent_raw) > handled_recent_limit
```

组成本轮 snapshot：

```python
snapshot_rows = active + unhandled + handled_recent
```

CSV 只包含 `snapshot_rows`。它不会包含超过 `HANDLED_RECENT_LIMIT` 的更旧 handled
recent rows。

### 8.2 Inline / Export 决策

如果本轮 snapshot 较小：

```python
if (
    len(snapshot_rows) <= prompt_preview_limit
    and compute_inline_chars(snapshot) <= prompt_char_limit
):
    return full_inline(snapshot)
```

否则：

```python
csv_path = export_workspace_jobs_csv(snapshot)
preview_rows = select_observation_preview_rows(
    active=active,
    unhandled_terminal=unhandled,
    handled_recent_terminal=handled_recent,
    limit=prompt_preview_limit,
)
return compact(snapshot, csv_path, preview_rows)
```

## 9. Preview 选择策略

compact prompt 的 preview rows 只是直接展示给模型的预览。完整本轮 snapshot 在 CSV。

observation preview 顺序：

```text
1. unhandled terminal 中 failed / lost / stopped
2. active jobs
3. unhandled terminal 中其他状态
4. handled recent terminal
```

伪代码：

```python
ACTION_STATUSES = {"failed", "lost", "stopped"}


def select_observation_preview_rows(
    *,
    active,
    unhandled_terminal,
    handled_recent_terminal,
    limit: int,
):
    unhandled_action = [
        job for job in unhandled_terminal
        if job["status"] in ACTION_STATUSES
    ]
    unhandled_other = [
        job for job in unhandled_terminal
        if job["status"] not in ACTION_STATUSES
    ]

    selected = []
    for pool in (
        unhandled_action,
        active,
        unhandled_other,
        handled_recent_terminal,
    ):
        remaining = limit - len(selected)
        if remaining <= 0:
            break
        selected.extend(pool[:remaining])
    return tuple(selected)
```

不再保留独立的 action sample limit 或 priority sample limit。

如果产品希望 prompt 里直接展示 200 条失败作业，应调整：

```text
BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT=200
```

而不是新增另一个 action budget。

## 10. Renderer 输出

### 10.1 Full Inline

full inline 输出三组互斥 bucket：

```text
workspace /share/project
mode workspace_observation
summary {...}
handled_recent_has_more true
required_truncated false

active job_id,job_name,status
...

unhandled_terminal job_id,job_name,status
...

handled_recent_terminal job_id,job_name,status
...
```

如果 `handled_recent_has_more=true`，需要说明 handled recent 只是参考数据，已按
`HANDLED_RECENT_LIMIT` 截断。

如果 `required_truncated=true`，需要明显说明 required context 不完整。

### 10.2 Compact / Export

compact 输出：

```text
workspace /share/project
mode workspace_observation
summary {...}
required_truncated false
handled_recent_has_more true

details_exported {"format": "csv", "path": "...", "rows": 143, ...}
csv_contains active + unhandled_terminal + handled_recent_terminal_limited
read_hint ...

prompt_preview {"preview_limit": 50, "preview_rows": 50, "omitted_rows": 93}
preview_policy unhandled_action > active > unhandled_other > handled_recent

preview_rows group,job_id,job_name,status
unhandled_terminal,f1,relax-fail,failed
active,a1,relax-running,running
handled_recent_terminal,r1,old-finished,finished
```

`omitted_rows` 只表示已纳入 snapshot 但未直接展示在 prompt 中的行数。

超过 `HANDLED_RECENT_LIMIT` 的更旧 handled recent rows 不属于 snapshot，不计入
`omitted_rows`。

## 11. CSV 语义

CSV 包含本轮纳入 snapshot 的完整数据：

```text
active
unhandled_terminal
handled_recent_terminal
```

CSV 的 group 列应使用新 bucket 名：

```text
active
unhandled_terminal
handled_recent_terminal
```

如果继续使用 `pending_terminal` 或 `recent_terminal`，CSV 读者无法判断 required/reference
边界，不符合本设计。

CSV 不包含超过 `HANDLED_RECENT_LIMIT` 的更旧 handled recent rows。

## 12. Delivery 模式

delivery 模式不使用 `HANDLED_RECENT_LIMIT`。

delivery 的数据源仍是：

```text
delivery_snapshot.rows
```

这些 rows 是本 session run 起点的 terminal but unhandled jobs，全部是 required context。

流程：

```python
rows = delivery_snapshot.rows

if (
    len(rows) <= prompt_preview_limit
    and compute_delivery_inline_chars(rows) <= prompt_char_limit
):
    return delivery_instruction_text(rows)

csv_path = export_delivery_jobs_csv(rows)
preview_rows = select_delivery_preview_rows(
    rows,
    limit=prompt_preview_limit,
)
return delivery_compact_instruction_text(
    summary=summary,
    csv_path=csv_path,
    preview_rows=preview_rows,
)
```

delivery preview 优先 failed / lost / stopped：

```text
failed / lost / stopped rows
```

finished rows 在 compact 模式下只写数量 summary，不需要用 preview budget 填充。

示例：

```text
delivery pending:
  990 finished
  10 failed

PROMPT_PREVIEW_LIMIT=50
```

prompt：

```text
以下作业失败：
10 条 failed 明细

以下作业成功结束：共 990 个（详见导出文件）
完整明细已导出：...
```

CSV：

```text
1000 条 delivery_snapshot.rows
```

## 13. Ack 与失败处理

ack 主体仍归 worker 层负责，session-scoped 规则不变。

### 13.1 CSV 导出失败

如果 delivery 或 observation 因超 preview 限制需要导出 CSV，但 CSV 导出失败：

```text
不能 ack 被省略的 delivery_snapshot.rows。
observed_terminal 仍可按 job key 独立 ack。
```

这沿用现有 `snapshot.export_failure` gating 方向。

### 13.2 Required Context 截断

如果 observation 的 `required_truncated=true`：

```text
workspace required context 不完整。
不得假装 CSV 覆盖了全部 required jobs。
```

ack 策略：

```text
observation 模式下，如果 required_truncated=true，应跳过 delivery_snapshot.rows 的 ack。
```

理由：

```text
delivery_snapshot.rows 是本 session 的 required unhandled terminal rows。
当 workspace required bucket 命中 safety cap 时，不能保证本 session 的所有 rows
都已经进入 observation snapshot 或 CSV。
跳过 ack 是安全降级；这些 rows 会在后续 run 中重新出现。
```

`handled_recent_has_more=true` 不影响 ack。

### 13.3 Delivery 模式

delivery 模式不通过 observation query 读取 required rows，而是使用完整
`delivery_snapshot.rows`。

因此 delivery 模式下：

```text
PROMPT_PREVIEW_LIMIT 不影响 ack 范围。
CSV 成功时可 ack snapshot.rows。
CSV 失败时跳过 snapshot.rows。
```

## 14. 典型案例

### 14.1 只有 handled recent 30 条

输入：

```text
active = 0
unhandled_terminal = 0
handled_recent_terminal = 30
HANDLED_RECENT_LIMIT = 20
PROMPT_PREVIEW_LIMIT = 50
```

结果：

```text
DB 查询 handled recent 21 条
纳入 snapshot 20 条
handled_recent_has_more=true
snapshot_rows=20
20 <= 50，直接 full inline
不导出 CSV
```

更旧 10 条不进入 prompt，也不进入 CSV。

### 14.2 Unhandled terminal 60 条

输入：

```text
active = 0
unhandled_terminal = 60
handled_recent_terminal = 500
HANDLED_RECENT_LIMIT = 20
PROMPT_PREVIEW_LIMIT = 50
```

结果：

```text
unhandled_terminal 纳入 60 条
handled_recent_terminal 纳入 20 条
snapshot_rows=80
80 > 50，导出 CSV
prompt preview 最多 50 条
```

CSV 包含：

```text
60 条 unhandled_terminal
20 条 handled_recent_terminal
```

更旧 handled recent 不进入 CSV。

### 14.3 Active 3 + unhandled 120 + handled recent 500

输入：

```text
active = 3
unhandled_terminal = 120
handled_recent_terminal = 500
HANDLED_RECENT_LIMIT = 20
PROMPT_PREVIEW_LIMIT = 50
```

结果：

```text
active 纳入 3
unhandled_terminal 纳入 120
handled_recent_terminal 纳入 20
snapshot_rows=143
导出 CSV
prompt preview 最多 50 条
handled_recent_has_more=true
required_truncated=false
```

CSV 包含：

```text
3 条 active
120 条 unhandled_terminal
20 条 handled_recent_terminal
```

### 14.4 Required rows 超过 safety cap

输入：

```text
active = 10
unhandled_terminal = 5000
REQUIRED_FETCH_LIMIT = 2000
HANDLED_RECENT_LIMIT = 20
```

结果：

```text
active 纳入 10
unhandled_terminal 纳入 2000
required_truncated=true
```

prompt / CSV 必须说明 required context 不完整。

observation 模式下不得 ack `delivery_snapshot.rows`，避免盲 ack。

## 15. 实现影响

主要影响文件：

```text
src/services/bohrium_jobs_wiring.py
  读取新 budget。
  observation 改为 required/reference/preview 三层装配。
  delivery 改用 PROMPT_PREVIEW_LIMIT，不再使用 ACTION_SAMPLE_LIMIT。

src/dao/bohrium_jobs_table.py
  active 查询增加 limit。
  pending terminal 查询改名或语义化为 unhandled terminal。
  recent terminal 查询改为 handled recent terminal。
  三类查询均支持 limit + 1。

matmaster/context/ports.py
  WorkspaceJobs 字段改为 active / unhandled_terminal / handled_recent_terminal。
  增加 required_truncated 与 handled_recent_has_more。
  priority_samples 改为 preview_rows。

matmaster/context/workspace_jobs_compute.py
  summary、inline render、CSV rows、preview selection 改用新 bucket。
  删除 action_sample_limit / priority_sample_limit 预算。

matmaster/context/sources/workspace_jobs.py
  renderer 输出新 bucket、新 summary、新 preview_policy。
  compact 输出 preview_rows，不再输出 priority_samples。

src/services/bohrium_delivery_ack.py
  保留 export_failure gating。
  如 observation required_truncated 需要 gate ack，则扩展 snapshot 中的失败/阻断状态。

src/services/agent_run_service.py
  budget 仍通过 workspace jobs port 内部从 env 读取，不向 run_meta 注入。
```

## 16. 验证计划

验证应覆盖以下不变量。实现时优先更新现有 workspace jobs 相关用例，避免新增无关测试文件。

```text
1. handled recent 30 条时，只纳入 20 条，handled_recent_has_more=true。
2. unhandled terminal 60 条时，CSV 包含 60 条 unhandled + 20 条 handled recent。
3. unhandled terminal 与 handled recent 互斥，summary 不重复计数。
4. active 查询带 REQUIRED_FETCH_LIMIT + 1，命中时 required_truncated=true。
5. compact prompt preview rows 数量不超过 PROMPT_PREVIEW_LIMIT。
6. delivery compact preview 不突破 PROMPT_PREVIEW_LIMIT，CSV 包含完整 snapshot.rows。
7. CSV 导出失败时跳过 delivery_snapshot.rows ack。
8. observation required_truncated=true 时跳过 delivery_snapshot.rows ack。
9. handled_recent_has_more=true 不影响 ack。
10. 主代码中无旧 env fallback。
```

## 17. 非目标

```text
1. 不改变 bohrium_jobs 表结构。
2. 不改变 worker session-scoped ack 的总体归属。
3. 不让 observation ack 其他 session 的 jobs。
4. 不把 handled recent 的更旧历史写入 CSV。
5. 不在 run_meta 中传递 workspace jobs service 能力。
6. 不保留旧 env 的运行时兼容读取。
```

## 18. 完成标准

```text
1. observation 的三组 bucket 互斥且语义清楚。
2. REQUIRED_FETCH_LIMIT、HANDLED_RECENT_LIMIT、PROMPT_PREVIEW_LIMIT 三者职责独立。
3. prompt 中直接展示的 job 明细不超过 PROMPT_PREVIEW_LIMIT。
4. CSV 包含本轮纳入 snapshot 的完整数据。
5. handled recent 超限只产生 handled_recent_has_more。
6. required context 超限产生 required_truncated，并阻断相关 snapshot.rows ack。
7. 删除 ACTION_SAMPLE_LIMIT 与 PRIORITY_SAMPLE_LIMIT 预算模型。
8. 删除旧 env fallback。
```
