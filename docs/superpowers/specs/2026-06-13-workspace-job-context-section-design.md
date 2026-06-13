# Workspace Job Context Section 设计

日期：2026-06-13
状态：已获需求方 approve，待实现计划

## 1. 背景

monitor 服务已经接入独立进程路径：

- `src/monitor/monitor_worker.py` 每轮先运行 Bohrium poller，再运行 completion scheduler。
- `src/services/bohrium_poller.py` 负责推进 `bohrium_jobs` ledger 状态。
- `src/services/bohrium_completion_scheduler.py` 负责发现已终态且未交付的 job，并触发对应 session 的 agent run。
- agent context 组装侧已有 `session_jobs` section 与 `SessionJobsPort`，但当前读取边界是 `user_id + org_id + session_id`，不满足同一个 workspace 内跨 session 对话共享 job 状态的需求。

现在需要把 job context 从纯 session 视角调整为更精确的双语义：

- 用户主动 query 时，需要看到当前 workspace 中跨 session 的 job 状态。
- monitor trigger 时，不能看到同 workspace 中其他 session 的 job，避免自动交付被无关 job 干扰。
- 无论 trigger 还是用户 query，ack 都只能确认当前 session 拥有的 job，不能跨 session 抢走交付权。

## 2. 目标

本设计实现 `workspace_jobs` 观察视图，并保留 session 级交付确认权。

核心不变量：

1. `workspace_jobs` 是观察上下文，范围是 `user_id + org_id + workspace`，允许跨 session 可见。
2. delivery ack 永远是 session scoped，不跨 session。
3. ack 进一步限定在当前 workspace，避免同一个 session 切换 workspace 后误 ack 旧 workspace 的 pending job。
4. `bohrium_completion` trigger run 不组装 workspace job section。
5. 用户主动 query run 组装 workspace job section，并在 run 成功后 ack 当前 `session_id + workspace` 的 pending terminal jobs。

最终行为示例：

```text
session A, workspace=/share/w1 提交 job 101
session B, workspace=/share/w1 提交 job 202

用户在 session B 主动 query：
  context 能看到 job 101 和 job 202
  run 成功后只 ack session B + /share/w1 的 pending terminal jobs
  不 ack session A 的 job 101

monitor 检测 job 101 终态：
  trigger 仍回到 session A
  trigger run 不注入 workspace_jobs
  run 成功后 ack session A + /share/w1 的 pending terminal jobs
```

## 3. 非目标

- 不允许 workspace 级 ack。
- 不引入 workspace 级 claim、reservation 或锁。
- 不让用户 query 抢走其他 session 的 delivery。
- 不让 trigger run 读取 workspace 观察视图。
- 不做 workspace 前缀匹配；workspace 使用规范化后的精确等值匹配。
- 不在运行时代码中做迁移、兜底或兼容。

## 4. 方案选择

### 方案 A：所有 job context 和 ack 都改成 workspace 级

否决。

这种方案能让 query 看到并确认整个 workspace 的 jobs，但会产生跨 session 抢交付权的问题。多个 session 同时 query 同一个 workspace 时，DB 层 `handled_at IS NULL` 可以幂等，但业务上会出现重复处理、抢先 ack、submit session 不再收到自动 trigger 等问题。要修复这些问题，需要新增 workspace 级 reservation 或 claim 状态，改动面和状态复杂度都过高。

### 方案 B：用户 query 只观察 workspace，不做 ack

否决。

这种方案语义很干净，但会产生重复触发 bug：用户主动 query 期间，当前 session 的 job 已经终态并被 agent 处理，但 run 成功后不 ack，monitor 下一轮仍会再次触发同 session。

### 方案 C：workspace 可见性 + session scoped ack

采纳。

用户 query 使用 workspace observation 视图，解决跨 session 工作区连续性；trigger 不注入 workspace observation，避免看到无关 jobs；ack 统一由 worker 的 delivery snapshot 完成，并限定在当前 session 和当前 workspace。

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
    "none" if is_bohrium_completion else "workspace_observation"
)
```

含义：

- `none`：不向 agent context 注入 job section。
- `workspace_observation`：注入当前 workspace 的观察视图。

该 mode 传入 `AgentRunService.run_agent(...)`，再传给 `build_bohrium_jobs_ports(...)` 或后续拆分出来的 jobs port factory。

### 5.2 delivery snapshot 独立于 job section

worker 在 run 开始前仍然拍 delivery snapshot，run 成功后仍然 confirm。

但 snapshot 范围改成：

```text
user_id + org_id + session_id + workspace + pending terminal
```

不是裸 `session_id`，也不是 workspace。

理由：

- 裸 `session_id` 会误 ack 同一 session 下其他 workspace 的 pending jobs。
- workspace 级 snapshot 会误 ack 其他 session 的 pending jobs。
- `session_id + workspace` 正好表达当前 session 在当前 workspace 中拥有的交付权。

接口建议：

```python
snapshot_session_workspace_delivery(session_id: str, *, workspace: str | None)
confirm_session_workspace_delivery(snapshot: DeliverySnapshot)
```

`DeliverySnapshot` 增加 `workspace: str` 字段。若 workspace 为空或无 pending rows，返回 `None`，run 不受影响。

### 5.3 trigger run 不组装 job section

`origin == "bohrium_completion"` 时，context assembly 使用空 jobs port。

架构效果：

- trigger 不会读取 `user_id + org_id + workspace` 下其他 session 的 jobs。
- trigger 的交付信息来自触发链路自身，不依赖 workspace observation section。
- trigger 成功后的 ack 仍由 worker snapshot 控制，范围是当前 session + 当前 workspace。

### 5.4 用户 query 组装 workspace observation

非 `bohrium_completion` run 且当前 run 有 normalized workspace 时，构造 workspace observation port。

读取范围：

```text
user_id + org_id + workspace
```

展示内容：

- active jobs：`submitted/running/terminating/unknown`。
- pending terminal jobs：`terminal_at IS NOT NULL AND handled_at IS NULL`。
- recent terminal jobs：最近终态 jobs，可用于回答用户主动询问历史完成情况。

`workspace_jobs` 是观察视图。它可以包含其他 session 的 jobs，但不会决定 ack 范围。

### 5.5 ack 规则

trigger 和用户 query 都允许 ack，但 ack 权限相同：

```text
当前 session + 当前 workspace
```

也就是说，用户主动 query 可以消化当前 session 当前 workspace 的 pending terminal jobs，避免重复 trigger；但不会消化其他 session 的 pending terminal jobs。

同一个 session 内已有运行锁，不会并发跑两轮。因此 session scoped ack 不需要额外 claim 状态。

不同 session 同时 query 同一 workspace 时：

- 两者可能都看见 workspace 中全部 jobs。
- session A 只 ack session A 的 rows。
- session B 只 ack session B 的 rows。
- 不会出现跨 session handle 竞态。

## 6. 数据层设计

### 6.1 保留 session delivery 查询

新增或改造 session delivery snapshot 查询：

```python
list_session_workspace_pending_terminal_snapshot(
    *,
    user_id: str,
    org_id: str,
    session_id: str,
    workspace: str,
) -> list[dict[str, Any]]
```

SQL 语义：

```sql
WHERE user_id = %s
  AND org_id = %s
  AND session_id = %s
  AND workspace = %s
  AND terminal_at IS NOT NULL
  AND handled_at IS NULL
```

ack 方法同步收紧：

```python
mark_session_workspace_handled_by_ids(
    *,
    user_id: str,
    org_id: str,
    session_id: str,
    workspace: str,
    row_ids: Sequence[int],
) -> int
```

SQL 语义：

```sql
UPDATE bohrium_jobs
SET handled_at = NOW()
WHERE user_id = %s
  AND org_id = %s
  AND session_id = %s
  AND workspace = %s
  AND id IN (...)
  AND terminal_at IS NOT NULL
  AND handled_at IS NULL
```

### 6.2 新增 workspace observation 查询

新增 workspace 观察查询，服务用户主动 query 的 context：

```python
query_workspace_active(
    *,
    user_id: str,
    org_id: str,
    workspace: str,
) -> list[dict[str, Any]]

query_workspace_pending_terminal(
    *,
    user_id: str,
    org_id: str,
    workspace: str,
    limit: int,
) -> list[dict[str, Any]]

query_workspace_recent_terminal(
    *,
    user_id: str,
    org_id: str,
    workspace: str,
    limit: int,
) -> list[dict[str, Any]]
```

也可以合并成一个 `query_workspace_jobs(...)`，由 DAO 内部一次或多次查询返回结构化结果。实现计划阶段可按现有 DAO 风格决定。

### 6.3 scheduler 聚合补 workspace 维度

当前 scheduler 仍应触发回 submit session，但 delivery unit 需要包含 workspace，避免同一 session 下多个 workspace 混在一起。

聚合维度调整为：

```text
user_id + org_id + session_id + workspace + invocation_key
```

触发时继续：

```python
trigger_run(
    session_id=unit["session_id"],
    workspace=unit["workspace"],
    origin="bohrium_completion",
)
```

Redis NX key 补 workspace：

```text
bohrium_delivery:{user_id}:{org_id}:{session_id}:{workspace}:{max_pending_terminal_id}
```

### 6.4 索引

现有表结构有 session 维度索引，但没有 workspace 查询索引。新增外部 migration：

```sql
ALTER TABLE `bohrium_jobs`
    ADD KEY `idx_workspace_active`
        (`user_id`, `org_id`, `workspace`, `status`, `submitted_at`),
    ADD KEY `idx_workspace_pending`
        (`user_id`, `org_id`, `workspace`, `handled_at`, `terminal_at`),
    ADD KEY `idx_session_workspace_pending`
        (`user_id`, `org_id`, `session_id`, `workspace`, `handled_at`, `terminal_at`);
```

同步更新 `src/sql/create_bohrium_jobs_table.sql`，不在主代码内联建索引。

## 7. Context 契约

### 7.1 命名

把 prompt 侧 section 从 `session_jobs` 迁移为 `workspace_jobs`。不保留旧名。

建议迁移：

- `SessionJobs` -> `WorkspaceJobs`
- `SessionJobsQuery` -> `WorkspaceJobsQuery`
- `SessionJobsPort` -> `WorkspaceJobsPort`
- `SessionJobsSource` -> `WorkspaceJobsSource`
- `ContextAssemblyPorts.session_jobs` -> `workspace_jobs`
- `AgentRunPorts.session_jobs` -> `workspace_jobs`
- `SectionOrder.SESSION_JOBS` -> `WORKSPACE_JOBS`

### 7.2 数据对象

建议数据结构：

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

- `pending_terminal_jobs` 是 workspace observation 的待处理终态集合，不等于 ack 范围。
- `recent_terminal_jobs` 用于让用户主动询问时能看到最近完成情况；是否包含已 handled rows 由 DAO 查询决定。
- `detail_limit` 可保留用于大列表压缩，不再承载 delivery snapshot 语义。

### 7.3 渲染

section：

```text
<workspace_jobs>
...
</workspace_jobs>
```

空态返回 `()`，不渲染。

渲染格式不在本架构 spec 中固定为产品契约；实现计划只需要保证：

- 明确 workspace。
- 区分 active、pending terminal、recent terminal。
- 不暴露 `user_id`、`org_id`。
- 不把 observation 误描述为 ack 范围。

## 8. 服务层接线

### 8.1 worker

修改点：

- 读取 payload `origin`。
- 根据 origin 设置 `job_context_mode`。
- 拍 session + workspace scoped delivery snapshot。
- 传 `job_context_mode` 和 snapshot 给 `AgentRunService.run_agent(...)`。
- run 成功后 confirm snapshot。

### 8.2 AgentRunService

修改点：

- `run_agent(...)` 增加 `job_context_mode` 参数。
- `build_bohrium_jobs_ports(...)` 接收 mode。
- mode 为 `none` 时返回空 workspace jobs port。
- mode 为 `workspace_observation` 且 workspace 非空时返回 workspace observation port。
- ledger write port 仍按 submit-time workspace 创建，不受 observation mode 影响。

### 8.3 bohrium_jobs_wiring

职责拆分：

- ledger write port：记录 submit/poll/kill，保持现有语义。
- jobs read port：按 mode 返回空视图或 workspace observation。

建议避免继续让 delivery snapshot 影响 jobs read port。ack 和 context 分离后，读 port 不需要持有 `DeliverySnapshot`。

### 8.4 ContextAssembler

`ContextAssembler` 的调度逻辑不需要变。它仍然只调用 jobs port：

```python
await self._ports.workspace_jobs.load_workspace_jobs(...)
```

行为由 service 层注入的 port 决定。

## 9. 测试计划

### 9.1 DAO

覆盖：

- session + workspace snapshot 只返回当前 session 当前 workspace 的 pending terminal rows。
- session + workspace ack 不影响其他 session 同 workspace rows。
- session + workspace ack 不影响同 session 其他 workspace rows。
- workspace active 查询能跨 session 返回同 workspace active rows。
- workspace pending terminal 查询能跨 session返回同 workspace pending rows。
- workspace recent terminal 查询不受 handled_at 影响，并按 terminal_at 倒序限制数量。
- scheduler 聚合按 `session_id + workspace + invocation_key` 分组。

### 9.2 wiring

覆盖：

- `job_context_mode="none"` 时 jobs port 返回 empty，且不会查 workspace DAO。
- `job_context_mode="workspace_observation"` 时 jobs port 使用 `user_id + org_id + workspace` 查询。
- identity 或 workspace 缺失时返回 empty。
- ledger write port 仍在 workspace 非空时创建。

### 9.3 worker

覆盖：

- `origin="bohrium_completion"` 时传入 `job_context_mode="none"`。
- 用户 query payload origin 为空时传入 `job_context_mode="workspace_observation"`。
- worker snapshot 调用带 `session_id + workspace`。
- run 成功后 confirm snapshot。
- run 失败不 confirm snapshot。

### 9.4 context

覆盖：

- workspace jobs 渲染 tag 是 `workspace_jobs`。
- trigger run 的 context 不包含 `workspace_jobs`。
- 用户 query run 的 context 包含 workspace observation。

## 10. 验收标准

1. 用户在同 workspace 的新 session 主动 query，可以看到该 workspace 下跨 session 的 job 状态。
2. `bohrium_completion` trigger run 不包含 workspace job section。
3. trigger run 成功后只 ack 当前 session 当前 workspace 的 pending terminal jobs。
4. 用户 query run 成功后也只 ack 当前 session 当前 workspace 的 pending terminal jobs。
5. 多个 session 同时 query 同一 workspace，不会互相 ack 对方 session 的 jobs。
6. 同一个 session 切换 workspace 后，不会 ack 旧 workspace 的 pending jobs。
7. monitor trigger 仍然回到 submit session。

## 11. 实现风险

- 当前 `bohrium_jobs` 的 scheduler 聚合使用 `MIN(workspace)`，必须改成 workspace 分组，否则同 session 多 workspace 会混淆。
- 当前 worker 对所有 run 都拍 snapshot，但 snapshot 仅按 session；必须补 workspace 过滤。
- 当前 context 类型名仍是 `SessionJobs`，直接扩展会继续制造误解；建议一次性迁移为 `WorkspaceJobs`。
- workspace observation 与 ack 范围不同，渲染文案必须避免暗示这些 jobs 都会被当前 run 确认。
