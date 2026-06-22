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

另一个现状问题是截断检测使用 `len(rows) >= limit`。当行数恰好等于 limit 时，
它只能说明可能还有更多行，并不能证明已经截断。本设计统一改用 `limit + 1` 查询，
既能准确判断是否还有第 N+1 条，也能避免恰好等于 limit 时误报截断。

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

更精确地说，进入 CSV 指完整数据已落盘，并且 CSV 路径与读取提示已进入 prompt。
这不保证模型实际读取了 CSV；它是本系统既有的呈现契约，也是 ack gating 的判断基础。

因此，20 条限制不应该发生在 prompt compact sample 层，而应该发生在
handled recent terminal reference bucket 的 DB 读取与 snapshot 纳入层。

## 3. 不变量

实现完成后必须满足以下不变量：

```text
1. active jobs 是 required context，必须进入 prompt 或 CSV。
2. unhandled terminal jobs 是 required context，必须进入 prompt 或 CSV。
3. handled recent terminal jobs 是 reference context，最多纳入 HANDLED_RECENT_LIMIT 条。
4. prompt 中直接展示的 job 明细最多 PROMPT_PREVIEW_LIMIT 条。
5. 当导出 CSV 时，CSV 包含本轮纳入 snapshot 的完整数据。
6. required context 被 REQUIRED_FETCH_LIMIT 截断时，必须显式标记 required_truncated=true。
7. handled recent 超过 HANDLED_RECENT_LIMIT 时，只标记 handled_recent_has_more=true，
   不算 required_truncated。
8. delivery 模式不使用 HANDLED_RECENT_LIMIT。
9. delivery snapshot 的 ack 边界不受 prompt preview 限制影响。
10. observation 模式下如果 required context 不完整（required_truncated 或
    observation 查询失败）或 CSV 导出失败，必须通过 DeliverySnapshot 上的可变
    阻断字段 gate ack，不得 ack 未被完整呈现的 delivery_snapshot.rows。
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

还需要显式告知一次用户可见行为变化：

```text
旧 observation recent 查询最多可纳入 OBSERVATION_MAX_ROWS 条 terminal rows，
且其中包含已 handled 与未 handled。

新 observation 中：
  unhandled terminal 作为 required context，走 REQUIRED_FETCH_LIMIT；
  handled recent terminal 作为 reference context，只纳入 HANDLED_RECENT_LIMIT 条。
```

因此，已 handled 的历史作业从最多 2000 条进入 prompt/CSV，变成最多 20 条进入
prompt/CSV。这是有意的产品语义变化，因为这些 rows 只是参考历史，不是本轮必须处理
的数据。

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
```

其中 `total` 只统计本轮纳入 snapshot 的行：

```text
total = active + unhandled_terminal + handled_recent_terminal
```

超过 `HANDLED_RECENT_LIMIT` 的更旧 handled recent rows 不属于本轮 snapshot，
不计入 `total`，只通过 `handled_recent_has_more=true` 表示。

`required_truncated` 与 `handled_recent_has_more` 只作为 `WorkspaceJobs` 顶层字段，
不写入 `WorkspaceJobsSummary`。renderer 将它们渲染成独立顶层行或 hint，避免同一
flag 同时出现在 `summary {...}` 与顶层行中。

delivery 侧可以继续沿用 `pending` / `snapshot.rows` 命名。delivery 只有一个
本 session 的交付 bucket，不存在 observation 里 required/reference 重叠的歧义；
本设计只要求 observation 的 context bucket 改名为 `unhandled_terminal`。

### 6.1 DeliverySnapshot ack 阻断字段

`DeliverySnapshot` 需要新增一个可变阻断字段：

```python
@dataclass(frozen=True)
class DeliverySnapshot:
    ...
    export_failure: dict[str, Any] = field(default_factory=dict)
    required_block: dict[str, Any] = field(default_factory=dict)
```

`required_block` 与现有 `export_failure` 对称：

```text
写入方：
  observation port / wiring 在 required context 不完整时写入，
  reason ∈ {"required_truncated", "query_failed"}。

读取方：
  bohrium_delivery_ack.confirm() 在 ack snapshot.rows 前检查。

确认条件：
  snap.rows
  and not snap.export_failure
  and not snap.required_block
```

写入示例：

```python
snapshot.required_block.update(
    {
        "reason": "required_truncated",
        "active_truncated": active_truncated,
        "unhandled_terminal_truncated": unhandled_truncated,
    }
)
```

`export_failure` 与 `required_block` 都是同一 run 内的 sticky 信号：一旦被写入，
本轮 confirm 不再 ack `snapshot.rows`。如果同一 run 内后续 context reassembly
不再触发该状态，也不主动清空。这个方向是保守安全的，因为它最多导致下轮重现，
不会造成盲 ack。

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

observation port 持有本 run 的 `delivery_snapshot` 引用（与写 `export_failure`
同一对象）。三类查询在一个 gather 内完成，任一抛错都视为 required context 不完整，
写入 `delivery_snapshot.required_block` 后返回 empty（见 §13.2）：

```python
try:
    active_raw = query_active(limit=required_fetch_limit + 1)
    unhandled_raw = query_unhandled_terminal(limit=required_fetch_limit + 1)
    handled_recent_raw = query_handled_recent_terminal(
        limit=handled_recent_limit + 1
    )
except Exception:
    if delivery_snapshot is not None:
        delivery_snapshot.required_block.update({"reason": "query_failed"})
    return WorkspaceJobs.empty()
```

截断判定。required 用 per-bucket 子布尔，再 or 出 `required_truncated`：

```python
active = active_raw[:required_fetch_limit]
unhandled = unhandled_raw[:required_fetch_limit]
handled_recent = handled_recent_raw[:handled_recent_limit]

active_truncated = len(active_raw) > required_fetch_limit
unhandled_truncated = len(unhandled_raw) > required_fetch_limit
required_truncated = active_truncated or unhandled_truncated
handled_recent_has_more = len(handled_recent_raw) > handled_recent_limit
```

required context 命中 cap 时同样写 `delivery_snapshot.required_block`。此写入与下游
inline / export 分支无关：异常配置下（REQUIRED_FETCH_LIMIT < PROMPT_PREVIEW_LIMIT）
即使最终走 full_inline，也可能 `required_truncated=true`：

```python
if required_truncated and delivery_snapshot is not None:
    delivery_snapshot.required_block.update(
        {
            "reason": "required_truncated",
            "active_truncated": active_truncated,
            "unhandled_terminal_truncated": unhandled_truncated,
        }
    )
```

组成本轮 snapshot：

```python
summary = compute_summary(active, unhandled, handled_recent)
workspace_jobs = WorkspaceJobs(
    workspace=workspace,
    active_jobs=active,
    unhandled_terminal_jobs=unhandled,
    handled_recent_terminal_jobs=handled_recent,
    summary=summary,
    required_truncated=required_truncated,
    handled_recent_has_more=handled_recent_has_more,
    mode="workspace_observation",
)
snapshot_rows = (
    workspace_jobs.active_jobs
    + workspace_jobs.unhandled_terminal_jobs
    + workspace_jobs.handled_recent_terminal_jobs
)
```

CSV 只包含 `workspace_jobs` 中的三组 snapshot rows。它不会包含超过
`HANDLED_RECENT_LIMIT` 的更旧 handled recent rows。

### 8.2 Inline / Export 决策

如果本轮 snapshot 较小：

```python
if (
    len(snapshot_rows) <= prompt_preview_limit
    and compute_inline_chars(workspace_jobs) <= prompt_char_limit
):
    return full_inline(workspace_jobs)
```

否则：

```python
csv_path = export_workspace_jobs_csv(workspace_jobs)
preview_rows = select_observation_preview_rows(
    active=active,
    unhandled_terminal=unhandled,
    handled_recent_terminal=handled_recent,
    limit=prompt_preview_limit,
)
return compact(workspace_jobs, csv_path, preview_rows)
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


def with_group(group: str, job: JsonObject) -> dict[str, JsonValue]:
    return {"group": group, **job}


def select_observation_preview_rows(
    *,
    active,
    unhandled_terminal,
    handled_recent_terminal,
    limit: int,
):
    unhandled_action = [
        with_group("unhandled_terminal", job)
        for job in unhandled_terminal
        if job["status"] in ACTION_STATUSES
    ]
    unhandled_other = [
        with_group("unhandled_terminal", job)
        for job in unhandled_terminal
        if job["status"] not in ACTION_STATUSES
    ]
    active_rows = [
        with_group("active", job)
        for job in active
    ]
    handled_recent_rows = [
        with_group("handled_recent_terminal", job)
        for job in handled_recent_terminal
    ]

    selected = []
    for pool in (
        unhandled_action,
        active_rows,
        unhandled_other,
        handled_recent_rows,
    ):
        remaining = limit - len(selected)
        if remaining <= 0:
            break
        selected.extend(pool[:remaining])
    return tuple(selected)
```

不再保留独立的 action sample limit 或 priority sample limit。

`preview_rows` 必须在选择阶段携带 `group`。renderer 不得试图从裸 job dict 反推
bucket，因为 unhandled finished 与 handled recent finished 的 `status` 可能相同，
只有选择阶段知道来源。

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

delivery preview 优先 failed / lost / stopped。它全部来自单一 `snapshot.rows`，
failed / succeeded 由 status 区分并渲染成两段表头（与 observation 多 bucket 混排
不同），不需要 group 列，因此不打 group：

```python
def select_delivery_preview_rows(
    rows,
    *,
    limit: int,
):
    action_rows = [
        job
        for job in rows
        if job["status"] in ACTION_STATUSES
    ]
    return tuple(action_rows[:limit])
```

finished rows 在 compact 模式下只写数量 summary，不需要用 preview budget 填充。

这意味着 delivery 直接展示的 failed/lost/stopped 上限从旧
`BOHRIUM_WORKSPACE_JOBS_ACTION_SAMPLE_LIMIT=200` 收敛到
`BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT=50`。这是有意变化：prompt preview
不再有隐藏的 action budget。如果产品希望 delivery prompt 直接展示 200 条失败作业，
应配置 `BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT=200`。

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

写入方是 delivery / observation read port；读取方是
`bohrium_delivery_ack.confirm()`。`confirm()` 在 ack `snapshot.rows` 前必须检查：

```python
if snap.rows and not snap.export_failure and not snap.required_block:
    mark_handled_by_ids(...)
```

### 13.2 Required Context 不完整

observation 的 required context 在两种情况下不完整，都按同一路径 gate ack：

```text
required_truncated：required 查询命中 REQUIRED_FETCH_LIMIT + 1，不能保证本
  session 的 rows 都进入了 snapshot / CSV。
query_failed：三类 observation 查询抛错，本轮 workspace_jobs section 与 CSV 都
  没有呈现任何 required rows。
```

两种情况共同的判断与策略：

```text
workspace required context 不完整，不得假装 CSV 覆盖了全部 required jobs。
observation port 必须写入 delivery_snapshot.required_block，并跳过
delivery_snapshot.rows 的 ack。
```

理由：

```text
delivery_snapshot.rows 是本 session 的 required unhandled terminal rows。
required context 不完整时，不能保证本 session 的所有 rows 都已经进入 observation
snapshot 或 CSV（截断丢尾，查询失败则全部缺席）。
跳过 ack 是安全降级；这些 rows 会在后续 run 中重新出现。
```

`handled_recent_has_more=true` 不影响 ack。

写入示例（两种 reason）：

```python
# required 命中 cap
delivery_snapshot.required_block.update(
    {
        "reason": "required_truncated",
        "active_truncated": active_truncated,
        "unhandled_terminal_truncated": unhandled_truncated,
    }
)

# 三类查询抛错
delivery_snapshot.required_block.update({"reason": "query_failed"})
```

`required_block` 与 `export_failure` 一样，是 frozen dataclass 上绑定的可变容器。
写入发生在 run 内 context 装配阶段，读取发生在 run 收尾 confirm 阶段。它在同一 run
内保持 sticky：一旦写入，不主动清空。这个语义是有意的安全降级，最多导致这些 rows
下轮重现，不会造成盲 ack。

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
  observation required context 不完整（required_truncated 或查询失败）时写
  delivery_snapshot.required_block。
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
  observation preview rows 在选择阶段写入 group；新增
  PREVIEW_COLUMNS = ("group", *SUMMARY_COLUMNS) 供 renderer 输出 preview 表头。
  删除 action_sample_limit / priority_sample_limit 预算。

matmaster/context/sources/workspace_jobs.py
  renderer 输出新 bucket、新 summary、新 preview_policy。
  compact 用 PREVIEW_COLUMNS（含 group 列）输出 preview_rows，不再输出 priority_samples。

src/services/bohrium_delivery_ack.py
  保留 export_failure gating。
  DeliverySnapshot 新增 required_block 可变字段。
  confirm() 在 ack snapshot.rows 前同时检查 not export_failure 与 not required_block。

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
5. observation preview rows 数量不超过 PROMPT_PREVIEW_LIMIT，且每行携带 group。
6. delivery compact preview 不突破 PROMPT_PREVIEW_LIMIT，只展示 action rows，
   CSV 包含完整 snapshot.rows。
7. CSV 导出失败时跳过 delivery_snapshot.rows ack。
8. observation required_truncated=true 时写 required_block(reason=required_truncated)，
   并跳过 delivery_snapshot.rows ack。
9. observation 查询失败时写 required_block(reason=query_failed)，并跳过
   delivery_snapshot.rows ack。
10. required_block 在同一 run 内 sticky，不被后续正常 reassembly 清空。
11. handled_recent_has_more=true 不影响 ack。
12. 主代码中无旧 env fallback。
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
4. 导出 CSV 时，CSV 包含本轮纳入 snapshot 的完整数据。
5. handled recent 超限只产生 handled_recent_has_more。
6. required context 不完整（required_truncated 或查询失败）写 required_block，
   并阻断相关 snapshot.rows ack。
7. preview rows 携带 group，renderer 不从裸 job dict 反推来源。
8. 删除 ACTION_SAMPLE_LIMIT 与 PRIORITY_SAMPLE_LIMIT 预算模型。
9. 删除旧 env fallback。
```
