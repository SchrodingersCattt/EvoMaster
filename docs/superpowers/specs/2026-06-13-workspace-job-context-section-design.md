# Workspace Job Context Section 设计

日期：2026-06-13（2026-06-14 按 snapshot / observed_terminal 重构重审；并经一轮 code review 修订 trigger 交付语义与索引方案）
状态：架构已获需求方 approve 并按当前代码基线重审；recent terminal 展示组成留待后续设计

## 1. 背景

monitor 服务已经接入独立进程路径：

- `src/monitor/monitor_worker.py` 每轮先运行 Bohrium poller，再运行 completion scheduler。
- `src/services/bohrium_poller.py` 负责推进 `bohrium_jobs` ledger 状态。
- `src/services/bohrium_completion_scheduler.py` 负责发现已终态且未交付的 job，并触发对应 session 的 agent run。

worker 侧交付链路在本 spec 初稿（2026-06-13）后又经历一次重构，当前基线已是：

- `src/services/bohrium_delivery_ack.py` 对所有 run（不分 origin）拍 delivery snapshot：run 起点解析身份、查询全量 session 级 pending terminal rows；run 成功后 confirm 批量 ack。
- snapshot 已成为 context pending 的**唯一来源**：旧的 `query_session_pending_terminal` 查询已删除，jobs read port 的 `pending_terminal_jobs` 直接读 `snapshot.rows`。
- `confirm` 现在有**两条 ack 路径**：`snapshot.rows`（`mark_handled_by_ids`）与 run 内前台 poll 观察到的终态 `observed_terminal`（`mark_handled_by_job_keys`）。
- worker 对所有 run **无条件** confirm（`src/worker/agent_worker.py:526`，`run_success and delivery_snapshot is not None` 即 ack），不区分 origin。
- `trigger_run` 已接收 `workspace` 参数，completion_scheduler 已在调用时传入。

agent context 组装侧已有 `session_jobs` section 与 `SessionJobsPort`，但当前读取边界是 `user_id + org_id + session_id`，不满足同一个 workspace 内跨 session 对话共享 job 状态的需求。

现在需要把 job context 从纯 session 视角调整为更精确的双语义：

- 用户主动 query 时，需要看到当前 workspace 中跨 session 的 job 状态。
- monitor trigger 时，不能看到同 workspace 中其他 session 的 job，避免自动交付被无关 job 干扰。
- 无论 trigger 还是用户 query，ack 都只能确认当前 session 拥有的 job，不能跨 session 抢走交付权。

## 2. 目标

本设计实现两类 job context：用户 query 的 `workspace_jobs` 观察视图，与 trigger 的 session+workspace delivery 视图，并保留 session 级交付确认权。

核心不变量：

1. workspace observation 是跨 session 观察上下文，范围是 `user_id + org_id + workspace`，允许跨 session 可见；仅用户主动 query 组装。
2. delivery ack 永远是 session scoped，不跨 session。
3. ack 进一步限定在当前 workspace，避免同一个 session 切换 workspace 后误 ack 旧 workspace 的 pending job。
4. ack 的两条路径（`snapshot.rows` 与 `observed_terminal`）都受 `session + workspace` 约束，不存在任何绕过 workspace 的 ack 入口。
5. `bohrium_completion` trigger run **不组装跨 session 的 workspace observation**；它组装当前 `session + workspace` 的 delivery section（含待交付 job 详情），保证 agent 看到要交付的 job 再 ack，**不盲确认**。
6. 用户主动 query run 组装 workspace observation section，并在 run 成功后 ack 当前 `session_id + workspace` 的 pending terminal jobs。
7. 两类 context 与 ack 的关系：
   - 用户 query 模式：context（workspace observation，跨 session）与 ack（`session + workspace` snapshot）**数据源解耦**，observation 可见范围 ⊋ ack 范围。
   - trigger 模式：context（`session + workspace` delivery）与 ack **同源**（`snapshot.rows`），agent 看到即 ack。
   - 两模式下，当前 `session + workspace` 的交付完整性都由 snapshot + observed_terminal 独立保证。

最终行为示例：

```text
session A, workspace=/share/w1 提交 job 101
session B, workspace=/share/w1 提交 job 202

用户在 session B 主动 query（workspace_observation）：
  context 能看到 job 101 和 job 202（跨 session）
  run 成功后只 ack session B + /share/w1 的 pending terminal jobs
  不 ack session A 的 job 101

monitor 检测 job 101 终态：
  trigger 仍回到 session A（session_workspace_delivery）
  trigger context 只含 session A + /share/w1 的待交付 job 详情（不跨 session）
  agent 看到 job 101 详情并处理
  run 成功后 ack session A + /share/w1 的 pending terminal jobs
```

## 3. 非目标

- 不允许 workspace 级 ack。
- 不引入 workspace 级 claim、reservation 或锁。
- 不让用户 query 抢走其他 session 的 delivery。
- 不让 trigger run 读取跨 session 的 workspace 观察视图。
- 不做 workspace 前缀匹配；workspace 使用规范化后的精确等值匹配。
- 不在运行时代码中做迁移、兜底或兼容。
- 不在本 spec 内重设计 trigger 唤醒 prompt 文案——现状 `render_prompt` 概要 + `_DELIVERY_SCOPE_SUFFIX` 指向 delivery section 已足够（trigger 仍有 section）；完整的「prompt 自带 job 列表替代 section」归后续 prompt 设计。
- 本阶段不新增 workspace 维度索引（见 §6.4），observation 的 recent terminal 展示组成留待展示设计。

## 4. 方案选择

### 方案 A：所有 job context 和 ack 都改成 workspace 级

否决。

这种方案能让 query 看到并确认整个 workspace 的 jobs，但会产生跨 session 抢交付权的问题。多个 session 同时 query 同一个 workspace 时，DB 层 `handled_at IS NULL` 可以幂等，但业务上会出现重复处理、抢先 ack、submit session 不再收到自动 trigger 等问题。要修复这些问题，需要新增 workspace 级 reservation 或 claim 状态，改动面和状态复杂度都过高。

### 方案 B：用户 query 只观察 workspace，不做 ack

否决。

这种方案语义很干净，但会产生重复触发 bug：用户主动 query 期间，当前 session 的 job 已经终态并被 agent 处理，但 run 成功后不 ack，monitor 下一轮仍会再次触发同 session。

### 方案 C：workspace 可见性 + session scoped ack

采纳。

用户 query 使用 workspace observation 视图，解决跨 session 工作区连续性；trigger 注入 session+workspace delivery 视图（不跨 session），避免看到无关 jobs 又保证交付详情可见；ack 统一由 worker 的 delivery snapshot 完成，并限定在当前 session 和当前 workspace。

> 重审与 review 后的实现要点：
> - 2026-06-14 重构把 snapshot 变成 context pending 的唯一来源后，用户 query 的 workspace observation 天然需要跨 session 查询，无法复用 session 级 snapshot，因此 observation 与 snapshot 自然解耦。
> - 但 trigger 不能简单地「不组装 section」：worker 对所有 run 无条件 confirm，trigger 若无 section 会把 `snapshot.rows` 永久标 handled 而 agent 从未拿到详情（盲 ack）。故 trigger 改为组装 `session + workspace` delivery section，context 与 ack 同源、不盲确认。

## 5. 架构设计

### 5.1 run 类型分流

worker 从队列 payload 读取 `origin`：

```python
origin = (payload.get("origin") or "").strip() or None
is_bohrium_completion = origin == "bohrium_completion"
```

据此决定 job context mode：

```python
job_context_mode = (
    "session_workspace_delivery" if is_bohrium_completion else "workspace_observation"
)
```

含义：

- `session_workspace_delivery`：注入当前 `session + workspace` 的 delivery 视图（active + pending terminal detail，不跨 session）。
- `workspace_observation`：注入当前 workspace 的跨 session 观察视图。

该 mode 传入 `AgentRunService.run_agent(...)`，再传给 `build_bohrium_jobs_ports(...)`。

> 现状：`origin` 已由 `stream_service` 写入 job payload——普通 `send` 默认 `origin=None`，`trigger_run` 设 `origin="bohrium_completion"`（completion_scheduler 已在调用）。worker 当前已读取 `session_id`/`workspace`/`delivery` 等字段，但**尚未读取 `origin`**。本阶段仅需补读，无需新造模式标记。

### 5.2 delivery snapshot：worker 侧 ack 的权威边界

worker 对所有 run（不分 origin）在 run 开始前拍 delivery snapshot，run 成功后 confirm。这是当前基线（`src/services/bohrium_delivery_ack.py`），本设计保留该框架，做一处调整：范围收紧到 `session + workspace`。

**范围收紧。** snapshot 当前查询 session 级全量 pending terminal rows：

```text
user_id + org_id + session_id + pending terminal
```

改成 session + workspace：

```text
user_id + org_id + session_id + workspace + pending terminal
```

理由：

- 裸 `session_id` 会误 ack 同一 session 下其他 workspace 的 pending jobs。
- workspace 级 snapshot 会误 ack 其他 session 的 pending jobs。
- `session_id + workspace` 正好表达当前 session 在当前 workspace 中拥有的交付权。

**接口（对齐当前真实模块，不新造命名）。** 当前函数即 `snapshot` / `confirm`，签名补 workspace：

```python
# src/services/bohrium_delivery_ack.py
def snapshot(session_id: str, *, workspace: str | None, ...) -> DeliverySnapshot | None
def confirm(snap: DeliverySnapshot, *, ...) -> int
```

`DeliverySnapshot` 增加 `workspace` 字段：

```python
@dataclass(frozen=True)
class DeliverySnapshot:
    user_id: str
    org_id: str
    session_id: str
    workspace: str
    rows: tuple[dict[str, Any], ...]
    detail_limit: int
    observed_terminal: set[tuple[bool, str]] = field(default_factory=set)
```

`workspace` 由 worker 从 payload 传入（run 级），不从 session 行解析。若 workspace 为空，snapshot 返回 `None`（无有效 ack scope，与 identity 缺失对称），run 不受影响；workspace 非空但无 pending rows 时返回空 rows snapshot，confirm 短路。这样 `DeliverySnapshot.workspace` 恒为非空 `str`。

**两条 ack 路径（`confirm` 内，均带 workspace）。**

- `snapshot.rows` → `mark_handled_by_ids`，加 workspace 谓词。
- `observed_terminal` → `mark_handled_by_job_keys`，加 workspace 谓词。

`observed_terminal` 是 run 内前台 poll 观察到的终态 job key 集合 `(sandbox, job_id)`，由 ledger（write port）的 `record_poll` 填充，confirm 时按 job key 批量 ack。两条路径都带 workspace 谓词，对称满足不变量 #3/#4（决策依据见 §6.1）。

**snapshot 与 context 的关系（按 mode 区分）。**

- `session_workspace_delivery`（trigger）：read port **复用 `snapshot.rows`** 作为 pending detail（context 与 ack 同源，agent 看到即 ack）。
- `workspace_observation`（用户 query）：read port **不用 snapshot**，pending/active 走 §6.2 的跨 session 查询；snapshot 此模式只服务 worker 的 confirm。

无论哪种 mode，`snapshot.observed_terminal` 都传给 ledger（write port），让 run 内前台 poll 继续填充。

### 5.3 trigger run 组装 session+workspace delivery section

`origin == "bohrium_completion"` 时，`job_context_mode="session_workspace_delivery"`：context assembly 组装**当前 session + 当前 workspace** 的 delivery section（active + pending terminal detail），不跨 session。

架构效果：

- trigger 不会读取 workspace 下其他 session 的 jobs（满足「不被无关 job 干扰」）。
- agent 在 section 中看到当前 `session + workspace` 的待交付 job 详情，run 成功后 confirm 的 `snapshot.rows` 正是这些行——**agent 看到即 ack，不盲确认**。
- `render_prompt` 的概要文案 + `_DELIVERY_SCOPE_SUFFIX` 指向该 section 仍然有效（section 存在），无需止血或悬空处理。

> 与初稿的差异：初稿曾设计 trigger 走 `none`（不组装 section）。但 worker 对所有 run 无条件 `confirm`（`agent_worker.py:526`），会把 `snapshot.rows` 永久标 `handled` 而 agent 从未拿到详情——盲 ack、交付语义丢失，prompt 阶段也拿不回。改为组装 `session + workspace` delivery section 后该问题消除，且这是把现状的 session 级 section 收紧到 workspace，改动最小。
> 文案微调（可选）：`_DELIVERY_SCOPE_SUFFIX` 现写「本轮交付为 session 级」，可改为「session + workspace 级」以精确。让 trigger prompt 自带完整 job 列表从而彻底替代 section，仍归后续 prompt 设计阶段，本 spec 不做、也不需要（section 已提供详情）。

### 5.4 用户 query 组装 workspace observation

非 `bohrium_completion` run 且当前 run 有 normalized workspace 时，构造 workspace observation port（`job_context_mode="workspace_observation"`）。

读取范围：

```text
user_id + org_id + workspace
```

展示内容（全部走独立 DAO 查询，**不复用 snapshot**）：

- active jobs：`submitted/running/terminating/unknown`。
- pending terminal jobs：`terminal_at IS NOT NULL AND handled_at IS NULL`。
- recent terminal jobs：最近终态 jobs，可用于回答用户主动询问历史完成情况（展示组成见下注，可能延后）。

workspace observation 是观察视图。它可以包含其他 session 的 jobs，但不会决定 ack 范围。

`detail_limit` 改由 port 独立从 env 读取（`BOHRIUM_DELIVERY_DETAIL_LIMIT` 或新键），不再随 snapshot 传入——observation port 不持有 snapshot。

> 展示组成留待展示设计：observation 的 pending terminal（§6.2，workspace 维度未 handled）与 recent terminal（最近终态）会重叠——未 handled 的终态同时落入两组；而 handled 与否是 delivery 内部状态，对回答用户运行情况并无意义。因此 observation 最终展示哪几组（是否单列 pending、recent 是否含 handled 去重，甚至是否只保留 active + recent）归入后续展示与 prompt 设计。注意这与 §6.1 的 `session + workspace` pending snapshot 是两条独立查询：后者是 worker delivery ack 的数据源，无论 observation 怎么展示都必须保留。

### 5.5 ack 规则

trigger 和用户 query 都允许 ack，但 ack 权限相同：

```text
当前 session + 当前 workspace
```

ack 通过 worker 的 `confirm` 完成，覆盖两条路径，都受 `session + workspace` 约束：

- `snapshot.rows`（run 起点锁定的 pending terminal）。
- `observed_terminal`（run 内前台 poll 到的新终态）。

也就是说，用户主动 query 可以消化当前 session 当前 workspace 的 pending terminal jobs，避免重复 trigger；但不会消化其他 session 的 pending terminal jobs。

同一个 session 内已有运行锁，不会并发跑两轮。因此 session scoped ack 不需要额外 claim 状态。

不同 session 同时 query 同一 workspace 时：

- 两者可能都看见 workspace 中全部 jobs。
- session A 只 ack session A 的 rows。
- session B 只 ack session B 的 rows。
- 不会出现跨 session handle 竞态。

## 6. 数据层设计

### 6.1 收紧 session delivery 查询到 session + workspace

当前用于 worker delivery 的查询与 ack 方法（`src/dao/bohrium_jobs_table.py`）均为 session 级，全部加 workspace 参数与谓词。

**snapshot 查询。** 当前方法：

```python
list_pending_terminal_snapshot(*, user_id, org_id, session_id) -> list[dict[str, Any]]
```

收紧为带 workspace：

```python
list_pending_terminal_snapshot(*, user_id, org_id, session_id, workspace) -> list[dict[str, Any]]
```

SQL 语义补 workspace：

```sql
WHERE user_id = %s
  AND org_id = %s
  AND session_id = %s
  AND workspace = %s
  AND terminal_at IS NOT NULL
  AND handled_at IS NULL
```

该查询无 limit（交付边界为查询瞬间全量 pending），保持现状。

**ack 方法（两条路径都收紧）。** 按 row id ack：

```python
mark_handled_by_ids(*, user_id, org_id, session_id, workspace, row_ids, chunk_size=500) -> int
```

```sql
UPDATE bohrium_jobs SET handled_at = NOW()
WHERE user_id = %s AND org_id = %s AND session_id = %s AND workspace = %s
  AND id IN (...) AND terminal_at IS NOT NULL AND handled_at IS NULL
```

按 job key ack（`observed_terminal`，spec 初稿缺失的路径）：

```python
mark_handled_by_job_keys(*, user_id, org_id, session_id, workspace, job_keys, chunk_size=500) -> int
```

```sql
UPDATE bohrium_jobs SET handled_at = NOW()
WHERE user_id = %s AND org_id = %s AND session_id = %s AND workspace = %s
  AND (sandbox, job_id) IN (...) AND terminal_at IS NOT NULL AND handled_at IS NULL
```

> 决策记录：job key 在 `(user_id, org_id, sandbox, job_id)` 上唯一、workspace 隐含确定，但仍**显式加 workspace 谓词**，与 `mark_handled_by_ids` 对称，严格满足不变量 #3/#4；成本仅一个等值谓词。

### 6.2 trigger delivery 与 workspace observation 的读查询

**trigger delivery（session + workspace，不跨 session）。** active 复用现状 read port 的 active 查询并收紧到 workspace：

```python
query_session_active(*, user_id, org_id, session_id, workspace) -> list[dict[str, Any]]
```

pending 直接复用 worker 已拍的 `snapshot.rows`（§5.2），不另查。

> 注：现状 `query_session_active` 签名为 `(user_id, org_id, session_id)`；本设计为它加 workspace 参数收紧，**不删除**（trigger delivery 仍需它）。初稿曾误判它无调用者要删，已撤销。

**workspace observation（跨 session）。** 新增 workspace 观察查询，服务用户主动 query 的 context：

```python
query_workspace_active(
    *, user_id, org_id, workspace
) -> list[dict[str, Any]]

query_workspace_pending_terminal(
    *, user_id, org_id, workspace, limit
) -> list[dict[str, Any]]

query_workspace_recent_terminal(
    *, user_id, org_id, workspace, limit
) -> list[dict[str, Any]]
```

也可以合并成一个 `query_workspace_jobs(...)`，由 DAO 内部一次或多次查询返回结构化结果。实现计划阶段可按现有 DAO 风格决定。`query_workspace_recent_terminal` 是否实现取决于 §5.4 的展示设计结论。

### 6.3 scheduler 聚合补 workspace 维度

`trigger_run` 已接收 `workspace` 参数（`src/services/stream_service.py`），completion_scheduler 已在触发时传入 `workspace=primary_unit["workspace"]`。本节剩余待办是把聚合维度、依赖查询与去重 key 补齐 workspace。

**(1) scan 查询补 workspace**（`src/dao/bohrium_jobs_table.py` 的 `scan_delivery_units`）。现状半连接内层 `pending` 子查询 `SELECT DISTINCT user_id, org_id, session_id, invocation_key`、ON 条件、外层 `GROUP BY` 都不含 workspace，workspace 取 `MIN(workspace)`，`ORDER BY` 也无 workspace tie-breaker。只改外层 `GROUP BY` 不够（review finding 4）：内层会把同 session/invocation 的所有 workspace 拉进候选，LIMIT 截断顺序也不确定。需要在四处补 workspace：

```sql
-- 内层 pending 子查询
SELECT DISTINCT user_id, org_id, session_id, workspace,
       COALESCE(invocation_id, '') AS invocation_key
FROM bohrium_jobs
WHERE terminal_at IS NOT NULL AND handled_at IS NULL
-- ON 条件追加
AND pending.workspace = t.workspace
-- 外层分组（去掉 MIN(workspace)，workspace 成为分组键直接 SELECT）
GROUP BY t.user_id, t.org_id, t.session_id, t.workspace, COALESCE(t.invocation_id, '')
-- ORDER BY tie-breaker 追加 workspace
ORDER BY first_pending_terminal_at ASC, t.user_id ASC, t.org_id ASC,
         t.session_id ASC, t.workspace ASC, invocation_key ASC
```

> 保留：`scan_delivery_units` 现已返回 `unknown_count`、`oldest_pending_age_seconds`（供 scheduler 的 STALLED 判定）以及 `max_pending_terminal_id` 等聚合字段。改分组时这些字段需随新分组维度一并保留。

**(2) scheduler 聚合 key**（`src/services/bohrium_completion_scheduler.py`）。现状：

```python
key = (unit["user_id"], unit["org_id"], unit["session_id"])
```

改为四元组：

```python
key = (unit["user_id"], unit["org_id"], unit["session_id"], unit["workspace"])
```

触发继续传 `workspace=unit["workspace"]`、`origin="bohrium_completion"`。

> 现状有一处已知偏离：同 session 多 invocation 合并为一个 trigger 时只取 primary unit 的 workspace。把 workspace 纳入分组后，不同 workspace 会自然分裂为不同 delivery unit，这一偏离随之消解。

**(3) FIRST_FAILURE 的 `get_first_pending_failed` 补 workspace**（review finding 3）。现状 `get_first_pending_failed(*, user_id, org_id, session_id, invocation_key)` 的 WHERE 不含 workspace（`src/dao/bohrium_jobs_table.py:522`），scheduler 调用（`bohrium_completion_scheduler.py:292`）也不传 workspace。workspace 分组后，scheduler 按某 workspace 触发，但该查询可能取到同 session/invocation 在**别的 workspace** 的最早失败行，导致 FIRST_FAILURE 文案与实际 ack scope 不一致。

修正：`get_first_pending_failed` 增加 `workspace` 必填参数与 `AND workspace = %s` 谓词，scheduler 调用传 `workspace=primary_unit["workspace"]`。

**(4) Redis NX 去重 key**（`bohrium_completion_scheduler.py`）。现状：

```text
bohrium_delivery:{user_id}:{org_id}:{session_id}:{max_row_id}
```

补 workspace：

```text
bohrium_delivery:{user_id}:{org_id}:{session_id}:{workspace}:{max_pending_terminal_id}
```

### 6.4 索引：复用现有索引，不新增 workspace 索引

review finding 1：`user_id`/`org_id`/`session_id` 均为 `VARCHAR(255)` utf8mb4（约 1020 bytes/列），三列完整就占满约 3060/3072 bytes（InnoDB 索引 key 上限），`workspace VARCHAR(1024)` 单列 4096 bytes。任何含这三个完整 ID 列 + workspace 的复合索引都会报 `Specified key was too long`；现有 `idx_session_pending` 已在约 3068 bytes 的极限。

因此本设计**不新增 workspace 维度索引**，workspace 过滤靠现有索引定位 + SQL 精确谓词完成：

| 查询 | 复用索引 | 说明 |
| --- | --- | --- |
| ack（session+workspace）`list_pending_terminal_snapshot` / `mark_handled_by_ids` | `idx_session_pending` | `(user_id, org_id, session_id)` 定位到单 session（行少），`workspace` 谓词回表过滤 |
| ack `mark_handled_by_job_keys` | `uk_owner_job_id` | `(user_id, org_id, sandbox, job_id)` 定位，`session_id + workspace` 回表校验 |
| trigger delivery active（session+workspace）`query_session_active` | `idx_session_active` | 定位单 session，`workspace + status` 回表过滤 |
| observation pending（跨 session）`query_workspace_pending_terminal` | 全局 `idx_pending_scan (handled_at, terminal_at)` | 未交付终态行全局少量，定位后过滤 `user_id + org_id + workspace` |
| observation active（跨 session）`query_workspace_active` | `idx_session_active` 的 `(user_id, org_id)` 前导 | 扫该 user+org 行，回表过滤 `workspace + status` |

注：review finding 1 列的 `idx_session_workspace_pending` 其实不需要——ack 是 session+workspace，定位到单 session 后行已很少，workspace 谓词回表即可。

所有查询无论是否命中专用索引，SQL **必须**带 `workspace = %s` 等精确谓词保证正确性（精确等值匹配，不做前缀匹配）。

> 成本前提与回退：observation 的 active 查询走 `(user_id, org_id)` 前缀，成本随单 user+org 历史行数增长（表只增不删）。鉴于 observation 仅在用户主动 query 时触发（低频），当前可接受。若实测单 user+org 行数增长导致 observation 查询变慢，再补 workspace 索引——届时因上述 key 长度约束，必须用 **prefix 索引**（如 `user_id(64), org_id(64), workspace(190), ...`）配合现有精确谓词，不能用裸列。recent_terminal 的展示与其索引需求一并归入后续展示设计阶段。

## 7. Context 契约

### 7.1 命名

把 prompt 侧 section 类型从 `Session*` 迁移为 `Workspace*`。不保留旧名。两种 mode（delivery / observation）共用同一套类型与 section tag，内容由 service 层注入的 port 决定。

建议迁移（左侧为当前真实标识符）：

- `SessionJobs`（`matmaster/context/ports.py`）-> `WorkspaceJobs`
- `SessionJobsQuery` -> `WorkspaceJobsQuery`
- `SessionJobsPort` -> `WorkspaceJobsPort`
- `SessionJobsSource`（`matmaster/context/sources/session_jobs.py`）-> `WorkspaceJobsSource`
- `ContextAssemblyPorts.session_jobs` -> `workspace_jobs`
- `AgentRunPorts.session_jobs`（`matmaster/types/runtime_ports.py`）-> `workspace_jobs`
- `SectionOrder.SESSION_JOBS`（`matmaster/context/sections.py`）-> `WORKSPACE_JOBS`
- 模块文件 `matmaster/context/sources/session_jobs.py` -> `workspace_jobs.py`
- read port 方法 `load_session_jobs(...)` -> `load_workspace_jobs(...)`

### 7.2 数据对象

当前 `SessionJobs` 字段为 `active_jobs` / `pending_terminal_jobs` / `detail_limit`。迁移后：

```python
@dataclass(frozen=True)
class WorkspaceJobs:
    workspace: str | None = None
    active_jobs: tuple[JsonObject, ...] = ()
    pending_terminal_jobs: tuple[JsonObject, ...] = ()
    recent_terminal_jobs: tuple[JsonObject, ...] = ()
    detail_limit: int | None = None

    @classmethod
    def empty(cls) -> WorkspaceJobs:
        return cls()
```

说明：

- 相比现状新增 `workspace` 与 `recent_terminal_jobs` 两个字段。
- 同一类型承载两种 mode：trigger delivery 填 `session + workspace` 的 active + pending；observation 填跨 session 的 active + pending + recent。
- `pending_terminal_jobs` 在 observation 模式不等于 ack 范围；在 delivery 模式等于 `snapshot.rows`（即 ack 范围）。
- `recent_terminal_jobs` 仅 observation 模式可能使用；是否包含已 handled rows 由 DAO 查询决定。
- `detail_limit` 用于大列表压缩；由 port 独立从 env 读取，不再承载 delivery snapshot 语义。

### 7.3 渲染

section：

```text
<workspace_jobs>
...
</workspace_jobs>
```

空态返回 `()`，不渲染。当前渲染 tag 是 `session_jobs`（`matmaster/context/sources/session_jobs.py` 的 `to_sections`，`key`/`tag` 均为 `"session_jobs"`），迁移后改为 `workspace_jobs`。

渲染格式不在本架构 spec 中固定为产品契约；实现计划只需要保证：

- 明确 workspace。
- 区分 active、pending terminal、（observation 模式的）recent terminal。
- 不暴露 `user_id`、`org_id`。
- observation 模式不把可见 jobs 误描述为 ack 范围；delivery 模式可明确这些是待交付 job。

## 8. 服务层接线

### 8.1 worker

修改点（`src/worker/agent_worker.py`）：

- 读取 payload `origin`（现状已读 `session_id`/`workspace`/`delivery` 等，未读 `origin`）。
- 根据 origin 设置 `job_context_mode`。
- snapshot 调用补 workspace：当前是 `bohrium_delivery_ack.snapshot(session_id)`，改为 `snapshot(session_id, workspace=workspace)`。
- 传 `job_context_mode` 给 `AgentRunService.run_agent(...)`（snapshot 已在传）。
- run 成功后 confirm snapshot（现状已 confirm，保持；两条 ack 路径已在 confirm 内）。

### 8.2 AgentRunService

修改点（`src/services/agent_run_service.py`）：

- `run_agent(...)` 已有 `workspace` 与 `delivery_snapshot` 参数；新增 `job_context_mode` 参数。
- 把 mode 传给 `build_bohrium_jobs_ports(...)`。
- mode 为 `session_workspace_delivery` 时返回 delivery port。
- mode 为 `workspace_observation` 且 workspace 非空时返回 observation port。
- ledger write port 仍按 submit-time workspace 创建，不受 mode 影响。

### 8.3 bohrium_jobs_wiring：按 mode 构造两种 read port

现状（`src/services/bohrium_jobs_wiring.py`）：`build_bohrium_jobs_ports` 返回 `(ledger, _RunSessionJobsPort)`，read port 持有 `delivery_snapshot`，active 来自 `query_session_active`、pending 来自 `snapshot.rows`、`detail_limit` 来自 snapshot；ledger 持有 `snapshot.observed_terminal`。

目标职责拆分：

- **ledger write port**：记录 submit/poll/kill，保持现有语义；继续接收 `delivery_snapshot.observed_terminal`，run 内前台 poll 终态时填充。
- **jobs read port**：按 `job_context_mode` 构造两种实现：
  - `session_workspace_delivery`（trigger）：delivery port。`load_workspace_jobs` 返回当前 `session + workspace` 的 active（`query_session_active`，加 workspace 谓词）+ pending（复用 `snapshot.rows`）。即把现状 `_RunSessionJobsPort` 收紧到 session+workspace。
  - `workspace_observation`（user query）：observation port。`load_workspace_jobs` 走 §6.2 的 `query_workspace_*`（跨 session），`detail_limit` 从 env 读，**不持有 snapshot**。

`build_bohrium_jobs_ports` 仍接收 `delivery_snapshot`（delivery port 取其 `rows`，ledger 取其 `observed_terminal`），新增 `job_context_mode` 参数决定 read port 形态。observation 模式下 read port 与 snapshot 完全解耦。

### 8.4 ContextAssembler

`ContextAssembler`（`matmaster/context/assembly.py`）的调度逻辑不需要变。它仍然只调用 jobs port：

```python
await self._ports.workspace_jobs.load_workspace_jobs(...)
```

现状是 `self._ports.session_jobs.load_session_jobs(SessionJobsQuery(session_id=...))`，随 §7.1 命名迁移更新。行为由 service 层注入的 port（delivery / observation）决定。

## 9. 测试计划

### 9.1 DAO

覆盖：

- `session + workspace` snapshot（`list_pending_terminal_snapshot`）只返回当前 session 当前 workspace 的 pending terminal rows。
- `session + workspace` ack（`mark_handled_by_ids`）不影响其他 session 同 workspace rows，也不影响同 session 其他 workspace rows。
- `session + workspace` job-key ack（`mark_handled_by_job_keys`）同样受 workspace 约束：不影响其他 session、不影响同 session 其他 workspace。
- `query_session_active` 加 workspace 后只返回当前 session 当前 workspace 的 active rows。
- workspace active 查询能跨 session 返回同 workspace active rows。
- workspace pending terminal 查询能跨 session 返回同 workspace pending rows。
- workspace recent terminal 查询（若实现）不受 `handled_at` 影响，并按 `terminal_at` 倒序限制数量。
- `get_first_pending_failed` 加 workspace 后：同 session、同 invocation、不同 workspace，各自只取本 workspace 的最早失败行（review finding 3）。
- `scan_delivery_units` 聚合按 `session_id + workspace + invocation_key` 分组：同 session 同 invocation 多 workspace 分裂为多个 delivery unit，且 LIMIT 截断顺序确定（review finding 4）。

### 9.2 wiring

覆盖：

- `job_context_mode="session_workspace_delivery"` 时 read port 是 delivery port：active 走 `query_session_active`（带 workspace）、pending 复用 `snapshot.rows`。
- `job_context_mode="workspace_observation"` 时 read port 是 observation port：使用 `user_id + org_id + workspace` 查询，**不读 `snapshot.rows`**。
- ledger 仍接收 `delivery_snapshot.observed_terminal` 并在 `record_poll` 填充。
- identity 或 workspace 缺失时返回 empty。
- ledger write port 仍在 workspace 非空时创建。

### 9.3 worker

覆盖：

- `origin="bohrium_completion"` 时传入 `job_context_mode="session_workspace_delivery"`。
- 用户 query payload origin 为空时传入 `job_context_mode="workspace_observation"`。
- worker snapshot 调用带 `session_id + workspace`。
- run 成功后 confirm snapshot。
- run 失败不 confirm snapshot。

### 9.4 context / 交付语义

覆盖：

- workspace jobs 渲染 tag 是 `workspace_jobs`。
- trigger run 的 context 含当前 `session + workspace` 的待交付 job 详情（不跨 session、不含其他 session）。
- **trigger 不盲 ack**（review finding 5）：trigger run confirm 标 handled 的行集合 == delivery section 展示的 pending rows，不存在「ack 了 agent 没看到的行」。
- 用户 query run 的 context 含跨 session 的 workspace observation，且 ack 范围 ⊊ observation 可见范围。

> 测试范围说明：本 spec 是功能迁移，保留上述测试计划。因 §6.4 不新增索引，无需 migration smoke；workspace 过滤的正确性由 DAO 测试（精确谓词）覆盖。

## 10. 验收标准

1. 用户在同 workspace 的新 session 主动 query，可以看到该 workspace 下跨 session 的 job 状态。
2. `bohrium_completion` trigger run 的 context 含当前 `session + workspace` 的待交付 job 详情，且不含其他 session 的 jobs。
3. trigger run 成功后只 ack 当前 session 当前 workspace 的 pending terminal jobs（两条路径都不越界），且 ack 的行 == agent 在 context 中看到的待交付行（不盲 ack）。
4. 用户 query run 成功后也只 ack 当前 session 当前 workspace 的 pending terminal jobs。
5. 多个 session 同时 query 同一 workspace，不会互相 ack 对方 session 的 jobs。
6. 同一个 session 切换 workspace 后，不会 ack 旧 workspace 的 pending jobs（含 `observed_terminal` 路径）。
7. monitor trigger 仍然回到 submit session；同 session 不同 workspace 各自独立成 delivery unit 触发。
8. FIRST_FAILURE trigger 文案取到的失败 job 属于触发的那个 workspace。
9. 新增/收紧的 DAO 查询在现有索引下能正确返回（无新增索引），且不报 key-too-long。

## 11. 实现风险

- **盲 ack（已通过设计消除，实现须守住）**：worker 对所有 run 无条件 confirm；trigger 必须组装 `session + workspace` delivery section 让 agent 看到待交付 job，否则会把 `snapshot.rows` 永久标 handled 而 agent 从未拿到详情。实现时不可把 trigger 退回 `none`。
- **索引 key 长度**：三个 ID 列完整即约 3060/3072 bytes，严禁新增含完整 ID 列 + workspace 的复合索引（会 migration 失败）；observation 查询靠现有索引 + 精确谓词，回退用 prefix 索引（§6.4）。
- `scan_delivery_units` 现用 `MIN(workspace)` 且半连接不含 workspace；只改外层 GROUP BY 不够，须同时改内层 DISTINCT、ON、ORDER BY，并保留 `unknown_count` / `oldest_pending_age_seconds` 等字段。
- `get_first_pending_failed` 现无 workspace 谓词；workspace 分组后必须补，否则 FIRST_FAILURE 文案与 ack scope 不一致。
- worker 现对所有 run 拍 snapshot，但 snapshot 仅按 session；必须补 workspace 过滤，且 confirm 的两条 ack 路径都要带 workspace。
- 当前 context 类型名仍是 `SessionJobs`，直接扩展会继续制造误解；建议一次性迁移为 `WorkspaceJobs`，并让 delivery / observation 两种 mode 复用同一类型。
- `query_session_active` 不可删除（trigger delivery 仍需），只能加 workspace 参数收紧。
- observation 与 ack 范围不同，渲染文案必须避免暗示这些 jobs 都会被当前 run 确认。
- workspace observation 依赖 session 行的 `user_id/org_id` 与 run 的 normalized workspace 均非空；生产 session 行 `org_id` 若为空，observation 会恒空——这正是最初反馈的 context 没真正读到 job 的首要嫌疑。实现与验证阶段需实测确认 identity 解析链路确实拿到非空 org_id。
