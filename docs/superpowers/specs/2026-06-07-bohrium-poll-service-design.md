# Bohrium Poll Service Design

## Context

当前 Bohrium 作业闭环已经有三块基础设施：

- `BohriumTool` 支持 `submit`、`poll`、`download`、`kill` 等前台工具动作。
- `bohrium_jobs` 是作业状态事实源，`BohriumJobsTable` 是唯一写入口。
- `BohriumJobPoller.run_once()` 已经能 claim 到期作业、查询远端状态并写回 ledger，但没有生产调度入口。

问题在于 poll 行为仍分散在两处：

- `BohriumTool._poll()` 面向 agent 工具调用，负责参数校验、平台查询、状态文案、log tail、ledger 写回。
- `BohriumJobPoller._poll_one()` 面向后台刷新，负责 AK 读取、平台查询、状态归一化、ledger 写回。

这两条路径都在理解 Bohrium 平台状态、ledger 状态和错误语义。后续一旦需要调整终态确认、状态映射或 poll 退避，就容易出现前台 tool 与后台 poller 行为分叉。

本设计把 poll 拆成独立 service 层用例：负责把 Bohrium 远端作业状态同步到 `bohrium_jobs`。它不直接承担 agent 唤醒、结果消费或 `mark_handled`。

## Goals

- 抽出共享的 Bohrium 作业状态查询与 ledger 写回逻辑。
- 让前台 tool poll 与后台 due-job poll 复用同一套状态归一化和终态确认。
- 为生产后台 poll worker 提供清晰入口。
- 保持 `bohrium_jobs` DAO 作为唯一状态机写入口。
- 保持 poll 成本与 agent run 成本分层，避免 poll 服务直接触发 LLM run。

## Non-Goals

- 不设计完整作业完成调度器。
- 不在 poll 服务中调用 `trigger_run`。
- 不在 poll 服务中调用 `mark_handled`。
- 不新增 RuntimePorts 字段。
- 不把服务端业务能力塞入 `HookExecutor`。
- 不新增 inline 兼容或迁移逻辑；若表结构后续需要调整，走外部迁移脚本。
- 不替代 `BohriumTool(action="poll")` 的前台用户体验；tool 仍负责 tool result 文案和可选 log tail。

## Architecture

新增或重构出三层：

```text
BohriumTool(action="poll")
  -> BohriumPollService.poll_target(source="tool")
  -> BohriumJobsTable.apply_poll / mark_poll_error

BohriumPollWorker
  -> BohriumPollService.poll_due_batch()
  -> BohriumJobsTable.claim_due_batch()
  -> BohriumPollService.poll_target(source="poller")
  -> BohriumJobsTable.apply_poll / mark_poll_error

BohriumCompletionScheduler
  -> 后续单独设计
  -> pending_terminal_jobs 聚合、节流、dedup、trigger_run、消费确认
```

`BohriumPollService` 是本设计核心。它属于 `src/services`，持有以下依赖：

- `BohriumJobsTable` 或测试替身 table。
- `get_existing_bohrium_access_key(user_id, org_id)`，后台路径使用只读 AK 获取，不自动创建 AK。
- `get_job_detail(ctx, job_id=...)`。
- `confirm_terminal_status(ctx, job_id=..., detail_data=...)`。
- Bohrium base URL provider。

`BohriumPollWorker` 是生产调度入口。它只周期性调用 service，不持有状态机规则。

`BohriumCompletionScheduler` 是后续独立设计。poll 服务最多让作业进入 `terminal_at IS NOT NULL AND handled_at IS NULL` 的待交付队列，不决定何时唤醒 agent。

## Data Model

第一版不新增表字段，复用现有 `bohrium_jobs`：

- `next_poll_at` 是后台 poll 调度条件。
- `poll_count` 用于计算 backoff。
- `last_polled_at` 记录最近一次远端查询。
- `status` 保存 MatMaster 归一化状态。
- `terminal_at` 标记终态确认时间。
- `handled_at` 标记 agent 已消费，不由 poll 服务写。

DAO 仍是唯一写入口：

- `insert_submitted()` 只由 submit ledger 路径调用。
- `claim_due_batch()` 只负责抢到期活跃作业并短期占位。
- `apply_poll()` 只负责成功 poll 的状态推进。
- `mark_poll_error()` 只负责 poll 失败或无法查询时的 active job 退避。
- `mark_handled()` 只由结果消费确认路径调用。

业务代码不得裸写 `status`、`next_poll_at`、`terminal_at`、`handled_at`。

## Service Interface

建议引入两个小 DTO。字段都是运行所需事实，不承载兜底 dict。

```python
@dataclass(frozen=True)
class BohriumPollTarget:
    user_id: str
    org_id: str
    project_id: int
    job_id: str
    sandbox: bool
    session_id: str | None = None
    workspace: str | None = None
    poll_count: int = 0


@dataclass(frozen=True)
class BohriumPollOutcome:
    job_id: str
    sandbox: bool
    success: bool
    status_code: int | None
    ledger_status: str
    is_terminal: bool
    error: str | None = None
```

`BohriumPollService` 暴露两个主方法：

```python
class BohriumPollService:
    def poll_target(
        self,
        target: BohriumPollTarget,
        *,
        source: Literal["tool", "poller"],
        backoff_seconds: int,
        access_key: str | None = None,
    ) -> BohriumPollOutcome:
        ...

    def poll_due_batch(
        self,
        *,
        limit: int = 50,
        claim_timeout_seconds: int = 120,
    ) -> dict[str, int]:
        ...
```

`poll_target()` 做单个作业的远端查询和 ledger 写回。`poll_due_batch()` 从 DAO claim 到期作业，并逐个调用 `poll_target(source="poller")`。

前台 tool 可以传入当前 session 已解析出的 credential，避免通过 user/org 再读 AK。后台 poller 必须通过 row 上的 `user_id`、`org_id`、`project_id` 构造 context，不能读 session 当前 project。

## Foreground Tool Flow

前台 `BohriumTool(action="poll")` 保留 tool 交互层职责：

1. 校验 `job_id`。
2. 拒绝 `result_dir` 参数，并提示使用 `download`。
3. 构造当前会话 Bohrium context。
4. 调用 `BohriumPollService.poll_target(source="tool")`。
5. 根据 outcome 组装 tool result，包括 status、message、download 提示。
6. sandbox 路径继续 best-effort 拉取 `log_tail`。

如果继续保留短轮询 loop，则每次真实远端查询都经过 `poll_target()`，由同一套状态归一化和终态确认写 ledger。tool 自己不再调用 `to_ledger_status()`，也不直接判断哪些平台状态属于 ledger 终态。

tool 路径的 ledger 语义保持当前容错原则：ledger 写失败不阻断用户可见 poll 结果。实现上可以让 `source="tool"` 在 service 内抛出前被 tool 捕获，或保留 tool 外层 `_safe_ledger` 风格，但不能让 tool 再复制状态映射逻辑。

## Background Poll Flow

后台 worker 调用 `poll_due_batch()`：

1. `claim_due_batch(limit, claim_timeout_seconds)` 抢到期 job。
2. 本轮内按 `(user_id, org_id)` 缓存 AK。
3. 对每个 job 计算 `backoff_seconds = compute_poll_backoff(poll_count)`。
4. AK 缺失时调用 `mark_poll_error()`，不查平台。
5. AK 存在时构造 `BohriumContext`。
6. 调用 `get_job_detail()`。
7. 对疑似终态状态调用 `confirm_terminal_status()`，与前台 tool 保持一致。
8. 调用 `to_ledger_status()` 得到 ledger 状态。
9. 调用 `apply_poll()` 写回。
10. 返回 `claimed`、`polled`、`errors`、`terminal` 等 summary。

生产入口建议放在 `src/worker/bohrium_poll_worker.py`：

```text
while not stopped:
  summary = service.poll_due_batch(limit=config.limit)
  log summary
  sleep(config.idle_interval)
```

worker 是独立进程或 Worker 侧独立循环，不放到 API 请求进程生命周期里。多实例并发依赖 `claim_due_batch()` 的 `FOR UPDATE SKIP LOCKED`，不需要 Redis 做 poll claim。

## Completion Boundary

poll 服务检测到终态后只做一件事：通过 `apply_poll()` 把 job 推进到 `finished`、`failed` 或 `stopped`，并让 `next_poll_at = NULL`、`terminal_at` 非空。

它不直接触发 agent run，原因有三点：

- poll 是廉价状态同步，agent run 是昂贵 LLM 调用，两者频率必须解耦。
- 高通量场景下逐 job 触发会导致 run 次数和 job 数绑定。
- `pending_terminal_jobs` 已经能作为待交付缓冲，完成调度器可以在此基础上做聚合、节流和 dedup。

`mark_handled()` 也不属于 poll 服务。只有 agent 确认消费了 pending terminal jobs 后，才能把它们标记为 handled。否则下一轮 context assembly 会看不到尚未处理的作业。

## Error Semantics

AK 缺失：

- 后台路径调用 `mark_poll_error()`，状态进入或保持 `unknown`，按 backoff 继续尝试。
- 不调用自动创建 AK 的接口。

平台 API 异常：

- 后台路径调用 `mark_poll_error()`。
- 前台 tool 返回用户可见错误或保留当前 tool 语义。

平台返回未知状态码：

- `to_ledger_status()` 映射为 `unknown`。
- `unknown` 不是终态，继续保留 `next_poll_at`。

DAO 写失败：

- 后台路径本轮计入 errors。由于 claim 只把 `next_poll_at` 占位到未来，claim timeout 后可重试。
- 前台 tool 不应因为 ledger 写失败丢失用户可见 poll 结果。

终态回退：

- 由 `BohriumJobsTable.apply_poll()` 保护。业务层不重复实现终态不回退规则。

## Configuration

第一版建议使用保守固定默认：

- `limit = 50`
- `claim_timeout_seconds = 120`
- idle sleep `5s` 到 `10s`
- error sleep 带上限退避
- backoff 沿用 `compute_poll_backoff()`：`30s -> 60s -> 120s -> ... -> 600s`

这些配置只影响后台调度频率，不改变 ledger 状态机。

## Testing

最小验证集：

- `tests/services/test_bohrium_poller.py`
  - 覆盖 due batch、AK 缓存、AK 缺失、API 异常、终态停止 poll。
- `tests/matmaster/tools/builtin/test_bohrium_tool_poll.py`
  - 覆盖前台 poll 仍能返回 Running / Finished。
- `tests/matmaster/tools/builtin/test_bohrium_tool_ledger.py`
  - 覆盖 tool 路径 ledger 失败不破坏 tool result。
- `tests/dao/test_bohrium_jobs_table.py`
  - 覆盖 DAO 状态机不变量。

如果本次只拆服务逻辑，不改 schema，不新增 destructive DB fixture。

## Migration Plan

1. 提取 `BohriumPollService` 和 DTO，先让现有 `BohriumJobPoller` 委托它。
2. 直接迁移 `BohriumJobPoller` 的调用方和测试引用；本项目仍在开发期，不保留 alias 或过渡层。
3. 让 `BohriumTool._poll()` 调用 service，删除 tool 内重复状态归一化和 ledger 写入逻辑。
4. 增加独立 `bohrium_poll_worker` 入口，只接后台周期调用，不接 agent 唤醒。
5. 后续另开 completion scheduler spec，设计 pending terminal jobs 的聚合、节流、dedup 和 handled 确认。

## Open Follow-Up

本 spec 不解决完成调度器的策略问题。后续需要单独确认：

- 聚合作用域使用 session、invocation 还是其他维度。
- 触发 agent run 的数量阈值、时间阈值和整批完成条件。
- busy / dedup / retry 的硬背压策略。
- agent 消费 pending terminal jobs 后的确认协议。

这些问题只影响完成调度器，不阻塞 poll 服务拆分。
