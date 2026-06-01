# Bohrium Job Ledger Design

## Context

当前 Bohrium 作业状态依赖两层临时机制：

- 对话事件表中的 `tool_call` / `tool_result` 可以被
  `ChatEventsTable.get_bohrium_events()` 解析成轻量生命周期事件。
- `JobRegistry.rebuild_from_events()` 会把这些事件重建为进程内
  `JobRegistry`，用于单次 agent run 内的轮询防抖和续跑辅助。

这套机制不适合作为作业状态事实源。事件表是对话日志，不是作业状态表；
`JobRegistry` 是内存结构，不具备跨进程、跨重启的实时性；当前
`SessionJobsPort` 仍是空实现，agent 无法从结构化数据库中读取当前作业状态。

本设计第一版只引入一张轻量级作业状态表，不做完整作业平台，不做前端作业中心，
也不新增状态历史表。

## Goals

- 提供一个专用 Bohrium 作业状态事实源。
- 支持后台 poller 按 `next_poll_at` 刷新非终态作业。
- 支持 agent 通过 session 维度读取当前作业状态。
- 避免第一版表结构过重，保留必要排障信息但不追求完整审计。

## Non-Goals

- 不替代 `evo_chat_events` 的对话审计职责。
- 不在第一版实现完整作业详情页、作业统计或前端作业中心。
- 不引入单独的状态历史表。

## Table

第一版新增表名为 `bohrium_jobs`，不使用 `evo_` 前缀。

```sql
CREATE TABLE `bohrium_jobs` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,

    `session_id` VARCHAR(255) NOT NULL,
    `task_id` VARCHAR(255) NULL,

    `job_id` VARCHAR(128) NOT NULL,
    `job_name` VARCHAR(255) NULL,
    `project_id` BIGINT NULL,
    `sandbox` TINYINT(1) NOT NULL DEFAULT 0,

    `status` VARCHAR(64) NOT NULL DEFAULT 'submitted',
    `status_code` INT NULL,

    `poll_count` INT NOT NULL DEFAULT 0,
    `next_poll_at` DATETIME NULL,
    `last_polled_at` DATETIME NULL,

    `submitted_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `finished_at` DATETIME NULL,
    `result_dir` VARCHAR(1024) NULL,

    `submit_response_json` JSON NULL,
    `last_detail_json` JSON NULL,
    `last_error` TEXT NULL,

    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY `uk_job_id` (`sandbox`, `job_id`),
    KEY `idx_session_status` (`session_id`, `status`),
    KEY `idx_poll_due` (`next_poll_at`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Bohrium 作业状态表';
```

## Field Semantics

`id`

数据库内部自增主键。业务代码不应把它当作 Bohrium 作业标识。

`session_id`

作业所属 MatMaster 会话。agent 加载当前会话作业状态时以该字段为主查询条件。

`task_id`

提交该作业的 agent run 任务 ID。用于排查作业由哪一次后端运行产生，并可与
聊天事件表中的 `task_id` 对齐。

`job_id`

Canonical Bohrium 作业 ID。poll、download、kill 均以该字段为键，
`get_job_detail`、`terminate_job`、`get_file_token` 都只接受 `job_id`。
sandbox 与非 sandbox 各自平台返回的 `jobId` 共用此列，但二者属于不同的发号空间，
因此唯一性由 `(sandbox, job_id)` 共同保证，而不是 `job_id` 单列。

`job_name`

提交作业时的人类可读名称。agent 展示作业状态时应优先显示 `job_name` 和
`job_id`，避免只暴露一串 ID。

`project_id`

Bohrium project ID 快照。即使 session 后续切换 project，历史 job 仍属于提交时
的 project，因此在 job 表中保留一份快照。

`sandbox`

是否为 sandbox 作业，提交时取 `ctx.sandbox` 快照。这是后台 poller 的必读判别位：
poll、download、kill 都按它分支（sandbox 与非 sandbox 走不同端点、`job_id` 强转
方式也不同）。它同时是 `(sandbox, job_id)` 唯一键的一维，因此提升为独立列，而不是
只留在 `submit_response_json` 里。

`status`

MatMaster 归一化后的作业状态。第一版只保留一个状态字段，暂不拆分平台状态、
产物状态和生命周期状态。

允许值：

- `submitted`: 作业提交成功，但尚未确认运行中。
- `running`: Bohrium 返回运行中、排队中、调度中等非终态。
- `finished`: Bohrium 计算成功完成，但结果尚未被 MatMaster 下载。
- `failed`: Bohrium 计算失败。
- `stopped`: 作业被停止或取消。
- `terminating`: 已请求 kill，但尚未确认终态。
- `downloaded`: 计算已完成，且 MatMaster 已成功下载结果。
- `unknown`: 查询失败或 Bohrium 返回无法识别状态。

`status_code`

Bohrium 原始状态码。业务代码读取 `status`，排障和兼容 Bohrium API 变化时读取
`status_code` 和 `last_detail_json`。

`poll_count`

状态轮询次数。后台 poller 使用该字段计算 backoff，也可辅助判断作业是否长时间
卡住。

`next_poll_at`

下一次应轮询时间，是后台 poller 的主调度条件。约定：活跃作业该字段恒为非空
（insert 时写成 `submitted_at`，新作业即到期）；进入终态时置为 NULL，表示不再轮询。
NULL 等价于不调度，因此 poll 查询无需再用 `status IN (...)` 兜底。

典型查询：

```sql
SELECT *
FROM `bohrium_jobs`
WHERE `next_poll_at` <= NOW()
ORDER BY `next_poll_at` ASC
LIMIT 50;
```

配合 `idx_poll_due (next_poll_at, status)`，该查询是有序索引范围扫描 + LIMIT 提前
结束；终态作业因 `next_poll_at IS NULL`、`<= NOW()` 不成立被天然排除，无需 filesort。
poller 实际认领时在此基础上加 `FOR UPDATE SKIP LOCKED` 做并发隔离，见 Background poller。

`last_polled_at`

最近一次查询 Bohrium 作业详情的时间。agent 可以据此判断状态新鲜度。

`submitted_at`

作业提交成功时间。应在 `job/add` 成功后写入，而不是在 `job/create` 成功后写入。
如果上传失败导致计算作业没有提交，不应写入 `bohrium_jobs`。

`finished_at`

作业进入终态的时间。第一版用一个字段覆盖 `finished`、`failed`、`stopped` 和
`downloaded` 等终态时间，不拆分 `terminal_at`、`downloaded_at`、`kill_requested_at`。

`result_dir`

结果下载目录。结果尚未下载时为空；下载成功后写入。agent 看到
`status = 'downloaded'` 且 `result_dir` 非空时，可以直接读取结果目录。

`submit_response_json`

submit 成功时的原始响应快照，仅用于排障，不要求常规消费者解析。`sandbox` 等主流程
判别位已提升为独立列，这里保留完整原始返回以便对照。

示例：

```json
{
  "success": true,
  "job_id": "12345",
  "status": "Submitted",
  "use_sandbox": true
}
```

`last_detail_json`

最近一次 `get_job_detail` 的原始返回。它是排障黑匣子，不要求常规消费者理解其
完整结构。

`last_error`

最近一次 poll、download、kill 或数据库同步失败的错误文本。它表示 MatMaster 侧
查询或处理失败，不等价于 Bohrium 作业失败。

`created_at`

记录创建时间。

`updated_at`

记录最后更新时间。

## Status Rules

第一版的状态更新规则保持简单。贯穿规则：非终态作业必须有 `next_poll_at`，进入任一
终态时统一置 `next_poll_at = NULL`。

- submit 成功后插入记录，`status = 'submitted'`，`next_poll_at = submitted_at`。
- poller 或工具 poll 发现非终态时，写 `status = 'running'`，并按 backoff 推进
  `next_poll_at`。
- poller 或工具 poll 发现成功终态时，写 `status = 'finished'`、`finished_at`，
  并置 `next_poll_at = NULL`。
- poller 或工具 poll 发现失败终态时，写 `status = 'failed'`、`finished_at`，
  并置 `next_poll_at = NULL`。
- kill 请求成功后先写 `status = 'terminating'`，保留 `next_poll_at` 以便后续 poll
  确认最终状态。
- download 成功后写 `status = 'downloaded'`、`result_dir`，并置 `next_poll_at = NULL`。
- 查询异常时不覆盖明确终态；仅更新 `last_error`，必要时把非终态作业标记为 `unknown`
  并按 backoff 推进 `next_poll_at`。

## Integration Points

`BohriumTool._submit`

在 `job/add` 成功且工具返回 `job_id` 后 upsert `bohrium_jobs`，同时写入 `sandbox`
（取 `ctx.sandbox`）和 `next_poll_at = submitted_at`。如果只完成 `job/create` 但
上传或 `job/add` 失败，不写入作业表。

`BohriumTool._poll`

直接 poll 时同步更新 `status`、`status_code`、`poll_count`、`last_polled_at`、
`next_poll_at`、`last_detail_json` 和 `last_error`。

`BohriumTool._download`

下载成功后更新 `status = 'downloaded'` 和 `result_dir`。

`BohriumTool._kill`

kill 请求成功后更新 `status = 'terminating'`。终态由后续 poller 或工具 poll 确认。

Background poller

独立扫描 `next_poll_at <= NOW()` 的作业（终态作业 `next_poll_at` 为 NULL，天然不入
队），调用 Bohrium `get_job_detail`，把平台返回归一化到 `status`。poller 不依赖进程内
`JobRegistry` 或当前 HTTP 请求：所需的 per-user `access_key` 通过 `session_id` 反查
会话表得到 `(user_id, org_id)`，再经 `UserService.get_bohrium_access_key` 现查，key
不落库；同一 `(user_id, org_id)` 的 key 可在一轮轮询内缓存复用。

并发认领用 `SELECT ... FOR UPDATE SKIP LOCKED` 实现，多副本 poller 互不重复领取：

```sql
-- 短事务：抢一批并占位
BEGIN;
SELECT `id`, `session_id`, `job_id`, `sandbox`
FROM `bohrium_jobs`
WHERE `next_poll_at` <= NOW()
ORDER BY `next_poll_at` ASC
LIMIT 50
FOR UPDATE SKIP LOCKED;          -- 命中行被本事务锁住，其他实例直接跳过

UPDATE `bohrium_jobs`
SET `next_poll_at` = NOW() + INTERVAL <poll_interval> SECOND  -- 占位，防止重复领取
WHERE `id` IN (<上一步选中的 id>);
COMMIT;                          -- 立刻释放行锁

-- 事务外：逐个调 get_job_detail，再按 Status Rules 写回最终 status 与 next_poll_at
```

行锁只在短事务内持有，期间只做抢取一批和占位两件事；Bohrium 慢调用一律放在事务外，
避免长事务。poller 在 COMMIT 前崩溃时事务回滚、锁自动释放，作业回到可领取状态，不会
卡死。前提：`SKIP LOCKED` 需要 MySQL 8.0+。

`SessionJobsPort`

从 `bohrium_jobs` 按 `session_id` 读取当前活跃作业和最近终态作业，填充
`SessionJobs.active_jobs`。这会替代当前空实现，使 agent 能在上下文中看到实时
作业状态。

## Rationale

job_id 已在工具层统一为单一 canonical ID：sandbox 与非 sandbox 都取平台返回的
`jobId`，不再有第二个 `bohr_job_id`。但 sandbox 与非 sandbox 是两套独立发号空间，
同一数字 `jobId` 可能在两边各出现一次，进表后会落到同一 `job_id` 字符串，因此唯一键
取 `(sandbox, job_id)`，避免后写记录静默覆盖前者。单一 Bohrium 部署下这两维已足够；
若将来对接多个 base_url，需再补一维。

第一版不保存 `input_dir`、`image`、`machine`、`cmd`、`disk_size`。这些字段对复现
和审计有价值，但不是 agent 实时读取作业状态的必要条件。后续如果要做作业详情页、
重跑或成本统计，可以通过迁移补充。

第一版不拆分 `bohrium_status`、`artifact_status`、`lifecycle_status`。单字段
`status` 已能覆盖当前需求；是否继续轮询由 `next_poll_at` 是否为 NULL 决定，`status`
只用于语义展示和归类。如果未来 `downloaded` 与平台状态混用造成复杂度，再拆分状态维度。

并发认领选 `FOR UPDATE SKIP LOCKED` 而非新增 `locked_at` 租约列：前者不扩表、天然
支持多副本，且 poller 崩溃时行锁随事务回滚自动释放，无需租约过期与续租逻辑。代价是依赖
MySQL 8.0+，且必须把 Bohrium 慢调用放在持锁事务外。若未来需要锁状态可见以便监控，再
迁移到显式租约列。

## Testing Plan

- DAO 单测：插入 submit 成功记录、重复 upsert、按 session 查询、按 poll due 查询。
- 状态归一化单测：覆盖 running、finished、failed、stopped、unknown。
- 工具集成单测：submit/poll/download/kill 成功后正确写表。
- poller 单测：只轮询到期非终态作业，终态作业不再进入轮询队列。
- 并发认领单测：两个并发批次经 `FOR UPDATE SKIP LOCKED` 得到不相交作业集；事务回滚后
  被占作业重新可领。
- 上下文装配单测：`SessionJobsPort` 从 `bohrium_jobs` 返回结构化 active jobs。

