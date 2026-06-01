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
- 不把 `bohr_job_id` 提升为核心字段；原始 submit 返回放入 JSON 以便排障。

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

    `status` VARCHAR(64) NOT NULL DEFAULT 'submitted',
    `status_code` INT NULL,

    `poll_count` INT NOT NULL DEFAULT 0,
    `next_poll_at` DATETIME NULL,
    `last_polled_at` DATETIME NULL,

    `submitted_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `finished_at` DATETIME NULL,
    `result_dir` TEXT NULL,

    `submit_response_json` JSON NULL,
    `last_detail_json` JSON NULL,
    `last_error` TEXT NULL,

    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY `uk_job_id` (`job_id`),
    KEY `idx_session_status` (`session_id`, `status`),
    KEY `idx_poll_due` (`status`, `next_poll_at`),
    KEY `idx_project_status` (`project_id`, `status`)
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

Canonical Bohrium 作业 ID。后续 poll、download、kill 默认使用该字段。
第一版不单列 `bohr_job_id`，避免两个相近 ID 成为并列核心字段。

`job_name`

提交作业时的人类可读名称。agent 展示作业状态时应优先显示 `job_name` 和
`job_id`，避免只暴露一串 ID。

`project_id`

Bohrium project ID 快照。即使 session 后续切换 project，历史 job 仍属于提交时
的 project，因此在 job 表中保留一份快照。

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

下一次应轮询时间。后台 poller 的主要调度条件应基于该字段，而不是全表扫描。

典型查询：

```sql
SELECT *
FROM `bohrium_jobs`
WHERE `status` IN ('submitted', 'running', 'terminating', 'unknown')
  AND (`next_poll_at` IS NULL OR `next_poll_at` <= NOW())
ORDER BY `next_poll_at` ASC, `submitted_at` ASC
LIMIT 50;
```

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

submit 成功时的原始响应快照。可包含 `job_id`、`bohr_job_id`、`use_sandbox` 等
字段。这样不会丢失 `bohr_job_id`，但也不把它提升为第一版核心字段。

示例：

```json
{
  "success": true,
  "job_id": "12345",
  "bohr_job_id": "12345",
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

第一版的状态更新规则保持简单：

- submit 成功后插入记录，`status = 'submitted'`。
- poller 或工具 poll 发现非终态时，统一写 `status = 'running'`。
- poller 或工具 poll 发现成功终态时，写 `status = 'finished'` 和 `finished_at`。
- poller 或工具 poll 发现失败终态时，写 `status = 'failed'` 和 `finished_at`。
- kill 请求成功后先写 `status = 'terminating'`，最终状态仍以后续 poll 结果为准。
- download 成功后写 `status = 'downloaded'` 和 `result_dir`。
- 查询异常时不覆盖明确终态；仅更新 `last_error`，必要时将非终态作业标记为
  `unknown`。

## Integration Points

`BohriumTool._submit`

在 `job/add` 成功且工具返回 `job_id` 后 upsert `bohrium_jobs`。如果只完成
`job/create` 但上传或 `job/add` 失败，不写入作业表。

`BohriumTool._poll`

直接 poll 时同步更新 `status`、`status_code`、`poll_count`、`last_polled_at`、
`next_poll_at`、`last_detail_json` 和 `last_error`。

`BohriumTool._download`

下载成功后更新 `status = 'downloaded'` 和 `result_dir`。

`BohriumTool._kill`

kill 请求成功后更新 `status = 'terminating'`。终态由后续 poller 或工具 poll 确认。

Background poller

独立扫描 `next_poll_at` 到期的非终态作业，调用 Bohrium `get_job_detail`，把平台
返回归一化到 `status`。poller 不依赖进程内 `JobRegistry` 或当前 HTTP 请求。

`SessionJobsPort`

从 `bohrium_jobs` 按 `session_id` 读取当前活跃作业和最近终态作业，填充
`SessionJobs.active_jobs`。这会替代当前空实现，使 agent 能在上下文中看到实时
作业状态。

## Rationale

只保留一个 `job_id` 是为了避免 `job_id` 与 `bohr_job_id` 在第一版模型中并列出现。
当前系统真正需要的是一个 canonical ID；额外 ID 放入 `submit_response_json`，既可
保留排障信息，也不会扩大表的核心语义。

第一版不保存 `input_dir`、`image`、`machine`、`cmd`、`disk_size`。这些字段对复现
和审计有价值，但不是 agent 实时读取作业状态的必要条件。后续如果要做作业详情页、
重跑或成本统计，可以通过迁移补充。

第一版不拆分 `bohrium_status`、`artifact_status`、`lifecycle_status`。单字段
`status` 已能覆盖当前需求，后台 poller 通过 `status IN (...)` 判断是否继续轮询。
如果未来 `downloaded` 与平台状态混用造成复杂度，再拆分状态维度。

## Testing Plan

- DAO 单测：插入 submit 成功记录、重复 upsert、按 session 查询、按 poll due 查询。
- 状态归一化单测：覆盖 running、finished、failed、stopped、unknown。
- 工具集成单测：submit/poll/download/kill 成功后正确写表。
- poller 单测：只轮询到期非终态作业，终态作业不再进入轮询队列。
- 上下文装配单测：`SessionJobsPort` 从 `bohrium_jobs` 返回结构化 active jobs。

