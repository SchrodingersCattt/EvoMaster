# Bohrium Job Ledger Design

## Context

当前 Bohrium 作业状态依赖两层临时机制：

- 对话事件表中的 `tool_call` / `tool_result` 可以被
  `ChatEventsTable.get_bohrium_events()` 解析成轻量生命周期事件。
- `JobRegistry.rebuild_from_events()` 会把这些事件重建为进程内
  `JobRegistry`，用于在单次 agent run 内恢复作业的轻量状态与 poll 计数。

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
- 不设作业自动放弃或超时丢弃机制；长期处于 `unknown` 的作业持续按 backoff 轮询，
  是否人工干预由使用者判断。
- 第一版 kill 仅支持 sandbox 作业（`terminate_job` 当前只接了 sandbox 端点）；
  非 sandbox kill 不在第一版范围。

## Table

第一版新增表名为 `bohrium_jobs`，不使用 `evo_` 前缀。

```sql
CREATE TABLE `bohrium_jobs` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,

    `session_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    `task_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    `user_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    `org_id` VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,

    `job_id` VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    `job_name` VARCHAR(255) NULL,
    `project_id` BIGINT UNSIGNED NOT NULL,
    `sandbox` TINYINT(1) NOT NULL DEFAULT 0,

    `status` VARCHAR(32) COLLATE utf8mb4_bin NOT NULL DEFAULT 'submitted',

    `poll_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `next_poll_at` TIMESTAMP NULL,
    `last_polled_at` TIMESTAMP NULL,

    `submitted_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `terminal_at` TIMESTAMP NULL,
    `result_dir` VARCHAR(1024) NULL,

    `last_error` TEXT NULL,
    `last_error_at` TIMESTAMP NULL,

    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY `uk_owner_job_id` (`user_id`, `org_id`, `sandbox`, `job_id`),
    KEY `idx_poll_due` (`next_poll_at`, `id`),
    KEY `idx_session_active` (`user_id`, `org_id`, `session_id`, `submitted_at`),
    KEY `idx_session_recent` (`user_id`, `org_id`, `session_id`, `terminal_at`, `submitted_at`),

    CONSTRAINT `chk_sandbox` CHECK (`sandbox` IN (0, 1)),
    CONSTRAINT `chk_status` CHECK (`status` IN (
        'submitted', 'running', 'terminating', 'unknown',
        'finished', 'failed', 'stopped', 'downloaded'
    )),
    CONSTRAINT `chk_active_poll` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `next_poll_at` IS NOT NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'downloaded') AND `next_poll_at` IS NULL)
    ),
    CONSTRAINT `chk_terminal_at` CHECK (
        (`status` IN ('submitted', 'running', 'terminating', 'unknown') AND `terminal_at` IS NULL)
        OR
        (`status` IN ('finished', 'failed', 'stopped', 'downloaded') AND `terminal_at` IS NOT NULL)
    ),
    CONSTRAINT `chk_downloaded_dir` CHECK (`status` <> 'downloaded' OR `result_dir` IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Bohrium 作业状态表';
```

## Field Semantics

本表所有时间列使用 `TIMESTAMP` 而非 `DATETIME`。`TIMESTAMP` 在 InnoDB 内部以 UTC
（epoch）存储，读写按连接时区自动换算，因此 `next_poll_at <= NOW()` 这类跨进程比较的
正确性是存储层的内在属性，不依赖应用与 poller 各副本的连接时区是否一致：即使某条连接
漏设时区，比较的绝对时刻仍然一致。这正是本表作为调度事实源最需要的性质。

对照之下，`DATETIME` 存的是不带时区的裸字面值，要保证 `NOW()` 与存储值同一基准，必须让
每一条连接都设对 `time_zone`，任意一条漏设就会写错一个时区且无法从列值看出，因此本表不用
`DATETIME`。所有调度时间仍一律由 DB `NOW()` 计算，不在 Python 侧用本地时间算
`next_poll_at`，避免漂移。

`TIMESTAMP` 的范围上限为 2038-01-19，但本表只存当下附近的时间（`submitted_at` 为当下，
`next_poll_at` 为当下加 backoff，`terminal_at` 为过去或当下），不存远期时间，2038 之前不会
触顶，届时若仍在运行可通过迁移更换列类型。MySQL 8.0 默认
`explicit_defaults_for_timestamp=ON`，`TIMESTAMP NULL` 即普通可空列，不再有旧版本隐式
NOT NULL 与自动初始化的行为。

`id`

数据库内部自增主键。业务代码不应把它当作 Bohrium 作业标识。

`session_id`

作业所属 MatMaster 会话。agent 加载当前会话作业状态时以该字段为主查询条件。

`task_id`

提交该作业的 agent run 任务 ID。Bohrium 作业只可能在某次 agent run 的工具执行链中提交，
因此 submit 时 `task_id` 必然存在，第一版设为 NOT NULL。它用于排查作业由哪一次后端运行
产生，并可与聊天事件表中的 `task_id` 对齐。

`session_id`、`task_id`、`user_id`、`org_id`、`job_id` 均使用 binary collation。
这些字段是 opaque identifier，不应受 MySQL 默认大小写不敏感 collation 影响；例如
`AbC123` 与 `abc123` 不应在唯一键或查询条件中被视为同一个 ID。

`user_id`

提交该作业时的 MatMaster 用户 ID 快照。后台 poller 不能从当前会话状态反推用户，
因为同一 session 后续可能切换 project 或组织。poller 必须使用 job row 上的
`user_id` / `org_id` 现查 access_key。

`org_id`

提交该作业时的 Bohrium 组织 ID 快照。它和 `user_id` 一起用于调用
`UserService.get_bohrium_access_key`。access_key 不落库，但查 key 所需的用户与组织
必须随 job 固化，不能依赖 `evo_chat_sessions` 的当前值。

`job_id`

Canonical Bohrium 作业 ID。poll、download、kill 均以该字段为键，
`get_job_detail`、`terminate_job`、`get_file_token` 都只接受 `job_id`。
sandbox 与非 sandbox 各自平台返回的 `jobId` 共用此列，但二者属于不同的发号空间，
因此唯一性由 `(user_id, org_id, sandbox, job_id)` 共同保证，而不是 `job_id` 单列。

`job_name`

提交作业时的人类可读名称。agent 展示作业状态时应优先显示 `job_name` 和
`job_id`，避免只暴露一串 ID。

`project_id`

Bohrium project ID 快照。submit 需要 project，因此第一版要求非空，列为
`BIGINT UNSIGNED`。注意 `BohriumCredentials.project_id` 在解析失败时默认哨兵值 -1，而
`UNSIGNED` 列在严格模式下无法写入 -1，因此 DAO 在写入前必须断言 `project_id > 0`、拒绝
哨兵值，而不是让它在 insert 时报错或被静默截断。即使 session 后续切换 project，历史 job
仍属于提交时的 project，后台 poller 也必须使用 job row 上的 `project_id` 构造 Bohrium
context，不能读取 session 当前 project。

`sandbox`

是否为 sandbox 作业，提交时取 `ctx.sandbox` 快照。这是后台 poller 的必读判别位：
poll、download、kill 都按它分支（sandbox 与非 sandbox 走不同端点、`job_id` 强转
方式也不同）。它同时是 `(user_id, org_id, sandbox, job_id)` 唯一键的一维，因此提升为
独立列参与索引与判别。

`status`

MatMaster 归一化后的作业状态。第一版只保留一个状态字段，暂不拆分平台状态、
产物状态和生命周期状态。

允许值：

- `submitted`: 作业提交成功，但尚未确认运行中。
- `running`: Bohrium 返回运行中、排队中、调度中等非终态。
- `finished`: Bohrium 计算成功完成，但结果尚未被 MatMaster 下载。
- `failed`: Bohrium 计算失败。
- `stopped`: 作业被停止或取消。
- `terminating`: 已请求 kill，或 Bohrium 返回停止中 / 终止中 / killing，但尚未确认终态。
- `downloaded`: 计算已完成，且 MatMaster 已成功下载结果。
- `unknown`: 查询失败或 Bohrium 返回无法识别状态。

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
ORDER BY `next_poll_at` ASC, `id` ASC
LIMIT 50;
```

配合 `idx_poll_due (next_poll_at, id)`，该查询是有序索引范围扫描 + LIMIT 提前
结束；终态作业因 `next_poll_at IS NULL`、`<= NOW()` 不成立被天然排除，无需 filesort。
poller 实际认领时在此基础上加 `FOR UPDATE SKIP LOCKED` 做并发隔离，见 Background poller。

`last_polled_at`

最近一次查询 Bohrium 作业详情的时间。agent 可以据此判断状态新鲜度。

`submitted_at`

作业提交成功时间。应在 `job/add` 成功后写入，而不是在 `job/create` 成功后写入。
如果上传失败导致计算作业没有提交，不应写入 `bohrium_jobs`。

`terminal_at`

作业进入计算终态的时间。第一版用一个字段覆盖 `finished`、`failed`、`stopped`
等平台终态时间，不拆分 `downloaded_at`、`kill_requested_at`。

`terminal_at` 与终态的关系是双向的，由 `chk_terminal_at` 强制：终态（`finished`、
`failed`、`stopped`、`downloaded`）必须有 `terminal_at`，活跃态恒为 NULL。这正是
`SessionJobsPort` 用 `terminal_at IS NOT NULL` 判定终态的依据。

`downloaded` 是 MatMaster 产物状态，不重写已有的平台终态时间；但 `download` 自身会先
`get_job_detail` 判定终态，可能是首次确认平台终态的路径（用户跳过显式 poll、直接下载已完成
作业时）。此时若 `terminal_at` 仍为空，必须用 `COALESCE(terminal_at, NOW())` 补齐，否则会
写出 `terminal_at` 为空的 `downloaded` 记录、被最近终态查询漏掉。补齐值是下载时刻的近似，
第一版不单独保存精确下载时间。

`result_dir`

结果下载目录。结果尚未下载时为空；下载成功后写入。agent 看到
`status = 'downloaded'` 且 `result_dir` 非空时，可以直接读取结果目录。
failed / stopped job 若只下载了日志或失败产物，也可写入 `result_dir`，但 `status`
保留失败终态。

`last_error`

最近一次 poll、download、kill 或数据库同步失败的错误文本。它表示 MatMaster 侧
查询或处理失败，不等价于 Bohrium 作业失败。

`last_error_at`

最近一次写入 `last_error` 的时间。agent-facing JSON 展示错误时可据此判断错误新鲜度，
避免把很久以前的 transient failure 误读为当前故障。

`created_at`

记录创建时间。

`updated_at`

记录最后更新时间。

## Status Rules

第一版的状态更新规则保持简单，但必须保持单调：平台 poll 不能把已经下载的作业从
`downloaded` 回退成 `finished`。贯穿规则：

- 活跃状态为 `submitted`、`running`、`terminating`、`unknown`，必须有
  `next_poll_at`，且 `terminal_at` 为 NULL。
- 终态为 `finished`、`failed`、`stopped`、`downloaded`，必须把 `next_poll_at`
  置为 NULL，且 `terminal_at` 非空。
- `downloaded` 是 MatMaster 侧产物状态，一旦写入，后续 poll 只允许更新
  `last_polled_at`、`last_error`，不得把
  `status` 写回 `finished` / `failed` / `stopped`。
- `terminal_at` 表示平台计算进入终态的时间。download 不覆盖已有 `terminal_at`，但当
  download 自己首次确认平台终态、而 `terminal_at` 仍为空时，必须 `COALESCE(terminal_at,
  NOW())` 补齐，保证“终态 ⟺ `terminal_at IS NOT NULL`”严格成立。

平台状态码归一化：

| Bohrium code | Bohrium label | ledger status | 是否继续轮询 |
|--------------|---------------|---------------|--------------|
| -10 | `Prepared` | `running` | 是 |
| 0 | `Pending` | `running` | 是 |
| 1 | `Running` | `running` | 是 |
| 3 | `Scheduling` | `running` | 是 |
| 4 | `Stopping` | `terminating` | 是 |
| 6 | `Terminating` | `terminating` | 是 |
| 7 | `Killing` | `terminating` | 是 |
| 8 | `Uploading` | `running` | 是 |
| 9 | `Wait` | `running` | 是 |
| 2 | `Finished` | `finished` | 否 |
| -1 | `Failed` | `failed` | 否 |
| -2 | `Deleted` | `stopped` | 否 |
| 5 | `Stopped` | `stopped` | 否 |
| 其他或无法解析 | `Unknown(...)` | `unknown` | 是 |

具体写入规则：

- submit 成功后插入记录，`status = 'submitted'`，`next_poll_at = submitted_at`。
- poller 或工具 poll 发现活跃状态时，按上表写 `status`，更新
  `poll_count`、`last_polled_at`，并按 backoff 推进
  `next_poll_at`。
- poller 或工具 poll 发现平台终态时，若当前 `status != 'downloaded'`，按上表写
  `status`、`terminal_at`，并置 `next_poll_at = NULL`；若当前已是 `downloaded`，
  不覆盖 `status` 和 `result_dir`。
- sandbox kill 请求成功后先写 `status = 'terminating'`，保留 `next_poll_at` 以便
  后续 poll 确认最终状态。非 sandbox kill 当前未实现，不写 ledger 状态。
- successful finished job 的 download 成功后写 `status = 'downloaded'`、
  `result_dir`，置 `next_poll_at = NULL`，并 `terminal_at = COALESCE(terminal_at, NOW())`。
  failed / stopped job 的日志或产物下载若成功，只补 `result_dir`，保留 `failed` /
  `stopped` 状态；若该次 download 是首次确认平台终态，同样补齐 `terminal_at`。
- 查询异常时不覆盖明确终态；仅更新 `last_error`，必要时把活跃作业标记为 `unknown`
  并按 backoff 推进 `next_poll_at`。

这些不变量必须由 DAO 集中封装，业务代码不得裸写 `status`、`next_poll_at`、
`terminal_at`、`result_dir`。在此之上，线上 MySQL 为 8.0.16+（该版本起强制执行 CHECK
约束），因此建表语句已加入以下数据库级 CHECK 作为 DAO 之外的第二道防线：

- `chk_sandbox`：`sandbox IN (0, 1)`。
- `chk_status`：`status` 只能取本节允许值。配合 `status` 列的 `utf8mb4_bin` collation，
  该 CHECK 才是大小写敏感的——否则在默认大小写不敏感 collation 下，`Finished` 这类变体会被
  放行，破坏归一化小写约定。
- `chk_active_poll`：活跃状态必须有 `next_poll_at`，终态必须 `next_poll_at IS NULL`
  （该约束同时隐含 `status` 落在允许集合内）。
- `chk_terminal_at`：终态必须有 `terminal_at`，活跃态 `terminal_at` 必须为 NULL。它把
  “终态 ⟺ `terminal_at IS NOT NULL`” 下沉为数据库不变量，使 `SessionJobsPort` 的最近
  终态查询可以安全地用 `terminal_at IS NOT NULL` 判定终态。
- `chk_downloaded_dir`：`status = 'downloaded'` 时 `result_dir IS NOT NULL`。

增删 `status` 允许值或调整活跃/终态划分时，需同步迁移 `chk_status`、`chk_active_poll`
与 `chk_terminal_at`。DAO 单测仍需独立覆盖上述每一条不变量，不把 CHECK 当作唯一保证。

## Integration Points

`BohriumJobLedgerPort`

新增一个窄的 service-layer port，用于把 Bohrium 工具执行结果同步到 `bohrium_jobs`。
生产路径由 `AgentRunService` 构造该 port，携带本轮 `session_id`、`task_id`、
`user_id`、`org_id`；`Exp._init_builtin_tools()` 再把 port 注入 `BohriumTool`
构造器。工具不从 `run_meta`、全局 `SESSIONS`、`HookExecutor` 或临时 dict 里取这些
身份字段，也不把数据库 DAO 暴露给 kernel。

建议第一版 port 方法保持同步接口，因为当前 `BohriumTool._execute()` / `_submit()` /
`_poll()` / `_download()` / `_kill()` 是同步实现，并由 `execute_with_context()` 放到
线程池执行。后续如果工具整体改成 async，再迁移 port 形态。

`BohriumTool._submit`

在 `job/add` 成功且工具返回 `job_id` 后 upsert `bohrium_jobs`，同时写入 `sandbox`
（取 `ctx.sandbox`）、`project_id`（取 `ctx.credentials.project_id`）、以及 port
携带的 `user_id` / `org_id` / `session_id` / `task_id` 快照，并设置
`next_poll_at = submitted_at`。如果只完成 `job/create` 但上传或 `job/add` 失败，
不写入作业表。

`BohriumTool._poll`

直接 poll 时同步更新 `status`、`poll_count`、`last_polled_at`、
`next_poll_at` 和 `last_error`。状态归一化必须走 Status Rules
中的映射表，且不得把 `downloaded` 覆盖回平台状态。

`BohriumTool._download`

`_download` 自身会先 `get_job_detail` 判定终态，因此可能是首次确认平台终态的路径。
successful finished job 下载成功后更新 `status = 'downloaded'`、`result_dir`，并
`terminal_at = COALESCE(terminal_at, NOW())`。failed / stopped job 的日志或产物下载成功后
更新 `result_dir`、保留原失败状态，若此次 download 首次确认平台终态则一并补齐 `terminal_at`。

`BohriumTool._kill`

sandbox kill 请求成功后更新 `status = 'terminating'`。终态由后续 poller 或工具 poll
确认。非 sandbox kill 当前工具层未实现，调用失败时不写 ledger 状态。

Background poller

独立扫描 `next_poll_at <= NOW()` 的作业（终态作业 `next_poll_at` 为 NULL，天然不入
队），调用 Bohrium `get_job_detail`，把平台返回归一化到 `status`。poller 不依赖进程内
`JobRegistry`、当前 HTTP 请求或 `evo_chat_sessions` 当前 Bohrium 字段：所需的
`user_id`、`org_id`、`project_id`、`sandbox` 均来自 `bohrium_jobs` 提交时快照。
`base_url` 不入表，由 `get_bohrium_base_url()` 从进程 env 解析（见 Rationale 的单一
endpoint 不变量）：poller 与 submit 进程必须配同一个 endpoint，否则会拿同一 `job_id`
去另一个 Bohrium endpoint 查询。poller 经
`UserService.get_bohrium_access_key(user_id, org_id)` 现查 access_key，key
不落库；同一 `(user_id, org_id)` 的 key 可在一轮轮询内缓存复用。这样同一 session
后续切换 project 或 org，也不会影响历史 job 的轮询。

并发认领用 `SELECT ... FOR UPDATE SKIP LOCKED` 实现短事务抢批。注意：这种设计只保证
同一轮抢批时不同 poller 拿到的行不相交；`COMMIT` 后 Bohrium 慢调用在事务外执行，
整体语义仍是 at-least-once。若慢调用超过占位 interval，同一 job 可能被后续 poller
再次领取，因此写回必须是幂等的，并用 DAO 原子条件保护 `downloaded` 和终态不被旧结果
回退。

```sql
-- 短事务：抢一批并占位
BEGIN;
SELECT `id`, `session_id`, `user_id`, `org_id`, `project_id`,
       `job_id`, `sandbox`, `status`
FROM `bohrium_jobs`
WHERE `next_poll_at` <= NOW()
ORDER BY `next_poll_at` ASC, `id` ASC
LIMIT 50
FOR UPDATE SKIP LOCKED;          -- 命中行被本事务锁住，其他实例直接跳过

UPDATE `bohrium_jobs`
SET `next_poll_at` = NOW() + INTERVAL <claim_timeout> SECOND  -- 占位，降低重复领取概率
WHERE `id` IN (<上一步选中的 id>);
COMMIT;                          -- 立刻释放行锁

-- 事务外：用 job row 快照构造 BohriumContext，逐个调 get_job_detail，
-- 再按 Status Rules 写回最终 status 与 next_poll_at
```

行锁只在短事务内持有，期间只做抢取一批和占位两件事；Bohrium 慢调用一律放在事务外，
避免长事务。poller 在 COMMIT 前崩溃时事务回滚、锁自动释放，作业回到可领取状态，不会
卡死。poller 在 COMMIT 后崩溃时，作业会在 `claim_timeout` 到期后重新可领。前提：
`SKIP LOCKED` 需要 MySQL 8.0+。

poller 写回必须通过类似以下的原子条件完成，避免旧 poll 覆盖用户刚完成的 download：

```sql
UPDATE `bohrium_jobs`
SET
    `status` = CASE
        WHEN `status` = 'downloaded' THEN `status`
        ELSE <normalized_status>
    END,
    `last_polled_at` = NOW(),
    `poll_count` = `poll_count` + 1,
    `terminal_at` = CASE
        WHEN `status` = 'downloaded' THEN `terminal_at`
        WHEN <is_platform_terminal> THEN COALESCE(`terminal_at`, NOW())
        ELSE `terminal_at`
    END,
    `next_poll_at` = CASE
        WHEN `status` = 'downloaded' THEN NULL
        WHEN <is_platform_terminal> THEN NULL
        ELSE NOW() + INTERVAL <backoff_seconds> SECOND
    END
WHERE `id` = %s;
```

`SessionJobsPort`

从 `bohrium_jobs` 按 `user_id`、`org_id`、`session_id` 读取当前活跃作业和最近终态
作业。这会替代当前空实现，使 agent 能在上下文中看到实时作业状态。虽然当前
`SessionJobs` DTO 只有 `active_jobs`，本设计不把最近终态作业塞进该字段；第一版同步把
`SessionJobs` 扩展为 `active_jobs` 与 `recent_terminal_jobs` 两个 tuple，并更新
renderer 与测试，避免字段名和语义不一致。

第一版 agent-facing JSON object 固定为以下字段，避免 DAO 临时 dict 形状泄漏到 prompt：

```json
{
  "job_id": "12345",
  "job_name": "matmaster-job",
  "status": "running",
  "sandbox": true,
  "project_id": 42,
  "submitted_at": "2026-06-01 12:00:00",
  "last_polled_at": "2026-06-01 12:01:00",
  "result_dir": null,
  "last_error": null,
  "last_error_at": null
}
```

读取规则：

- 活跃作业：`WHERE user_id = ? AND org_id = ? AND session_id = ? AND
  status IN ('submitted', 'running', 'terminating', 'unknown')`，
  按 `submitted_at ASC` 返回。`idx_session_active (user_id, org_id, session_id,
  submitted_at)` 前三列等值定位后，`submitted_at` 直接命中排序，`status` 回表过滤——活跃
  作业每 session 通常很少，这次回表代价可忽略，也避免了把 `status` 放进索引中段导致的
  filesort。
- 最近终态作业：`WHERE user_id = ? AND org_id = ? AND session_id = ? AND
  terminal_at IS NOT NULL`，按 `terminal_at DESC, submitted_at DESC` 返回最近 5 条。
  这里用 `terminal_at IS NOT NULL` 而非 `status IN (...)` 作为终态过滤：在本设计的状态
  规则下，只有平台终态才写 `terminal_at`，`downloaded` 沿用 finished 时写入的
  `terminal_at`，活跃态（含 `terminating`）的 `terminal_at` 恒为 NULL，故
  “终态 ⟺ `terminal_at IS NOT NULL`” 严格成立。这样
  `idx_session_recent (user_id, org_id, session_id, terminal_at, submitted_at)` 的
  `terminal_at` 列可同时用于过滤与排序，配合 `LIMIT 5` 是有序索引范围扫 + 提前结束，无需
  回表判 `status`、无 filesort。这里不用 `updated_at` 排序，因为后续补写 `result_dir`、
  `last_error` 都会刷新 `updated_at`，但不代表作业最近结束。
- 不向 agent 暴露 `user_id`、`org_id`。

## Rationale

job_id 已在工具层统一为单一 canonical ID：sandbox 与非 sandbox 都取平台返回的
`jobId`，不再有第二个 `bohr_job_id`。但 sandbox 与非 sandbox 是两套独立发号空间，
同一数字 `jobId` 可能在两边各出现一次，进表后会落到同一 `job_id` 字符串。同时
后台 poller 需要按提交时的用户与组织现查 access_key，因此唯一键取
`(user_id, org_id, sandbox, job_id)`，避免跨用户、跨组织或 sandbox/非 sandbox
作业互相覆盖。

唯一键不含 `base_url`，是基于一条显式部署不变量：本设计假设单一 Bohrium endpoint，submit
与后台 poller 进程的 `SERVICE_ENV` / `BOHRIUM_BASE_URL` 必须配置一致（`base_url` 由
`get_bohrium_base_url()` 从进程 env 解析，见 `endpoints.py`）。在该前提下
`(user_id, org_id, sandbox, job_id)` 已足够唯一，`base_url` 无需进表或进唯一键。之所以不
提前加 `base_url` 列，是因为当前确为单一 endpoint，提前为多 endpoint 建模属于 YAGNI；将来
真要对接多个 endpoint，再迁移补 `base_url` 列并入唯一键。与时区不同，endpoint 是进程级
配置（只有 submit 与 poller 两类进程），配错会整片明显失败而非个别行静默出错，纪律成本低，
因此这里接受不变量，而时区选择用 `TIMESTAMP` 从根上免除依赖。

第一版不保存 `input_dir`、`image`、`machine`、`cmd`、`disk_size`。这些字段对复现
和审计有价值，但不是 agent 实时读取作业状态的必要条件。后续如果要做作业详情页、
重跑或成本统计，可以通过迁移补充。

同理，第一版不保存 Bohrium 原始状态码 `status_code`，也不保存原始响应快照（submit
响应与 `get_job_detail` 返回）。它们都是可现查信息：排障或核对平台行为时，用 job row
上的 `user_id`、`org_id`、`project_id`、`sandbox`、`job_id` 现查一次 `get_job_detail`
即可拿到当前权威状态，无需在主表常驻一份会过期的镜像。表只保留归一化后的 `status`——
它是 agent 展示与 poller 调度（活跃 / 终态划分）直接消费的派生结论，且是本地独有、无法
从平台现查的信息。把原始码与原始 JSON 移出表的同时也免除了写库前的脱敏负担：本表不再
写入任何 JSON 原始返回，因此 DAO 不需要 redaction 与 64KB 截断逻辑。

第一版不拆分 `bohrium_status`、`artifact_status`、`lifecycle_status`。单字段
`status` 已能覆盖当前需求，但必须用单调规则约束 `downloaded`，否则后台 poller 会把
下载后的作业重新写成平台 `finished`。是否继续轮询由 `next_poll_at` 是否为 NULL 决定，
`status` 只用于语义展示和归类。如果未来 `downloaded` 与平台状态混用继续造成复杂度，
再通过迁移拆分状态维度。

ledger 写入通过专用 `BohriumJobLedgerPort`，而不是把 DAO 塞进 `run_meta` 或
`HookExecutor`。这是因为作业写表需要返回业务数据后的顺序写入和错误语义，属于服务能力
端口，不是运行事件 observe/intercept/rewrite。

并发认领选 `FOR UPDATE SKIP LOCKED` 而非第一版就新增显式租约列：前者不扩表，能在
短事务内支持多副本抢批，且 poller 在 COMMIT 前崩溃时行锁随事务回滚自动释放。代价是
COMMIT 后的慢调用阶段没有真实锁，整体只能承诺 at-least-once，而不是 exactly-once；
因此 DAO 写回必须幂等并保护状态单调性。若未来需要锁状态可见、长时间 claim 监控或更强
互斥语义，再通过迁移补 `claim_owner` / `claim_expires_at`。

时间列选 `TIMESTAMP` 而非 `DATETIME`：本表是跨进程调度事实源，`next_poll_at <= NOW()`
的正确性不应依赖“所有连接都设对 `time_zone`”这条运维纪律。`TIMESTAMP` 以 UTC 锚定，
比较的绝对时刻天然一致；`DATETIME` 则要靠全局连接时区统一，任一连接漏设即写错且不可见。
代价是 2038 上限，但本表只存当下附近时间，不受影响，必要时再通过迁移更换列类型。

`session_id` 不设到 `evo_chat_sessions` 的外键。三个原因：一是 collation 不兼容，
`bohrium_jobs.session_id` 用 `utf8mb4_bin`，而 `evo_chat_sessions.session_id` 用
`utf8mb4_unicode_ci`，建 FK 要被迫放弃一侧 collation；二是生命周期独立，session 被删时
后台作业可能仍在 Bohrium 上运行，cascade 删除会丢掉对运行中作业的跟踪；三是本表本就设计成
自包含事实源、poller 不读 session 当前值，加 FK 反而把它重新耦合回 session 表。因此约定
retention 语义：删除 session 不删除、不停止 `bohrium_jobs` 记录，poller 继续按
`next_poll_at` 轮询到终态；终态记录如需清理，由独立策略处理，不走 session cascade。

## Testing Plan

- DAO 单测：插入 submit 成功记录、重复 upsert、按 session 查询、按 poll due 查询；
  验证唯一键包含 `user_id`、`org_id`、`sandbox`、`job_id`。
- ledger port 单测：submit 写入提交时 `session_id`、`task_id`、`user_id`、`org_id`、
  `project_id`、`sandbox` 快照；缺少必要身份字段时失败，而不是写半截记录。
- 状态归一化单测：覆盖 `Prepared`、`Pending`、`Running`、`Scheduling`、`Stopping`、
  `Terminating`、`Killing`、`Uploading`、`Wait`、`Finished`、`Failed`、`Deleted`、
  `Stopped`、unknown code。
- 状态单调性单测：`downloaded` 后再次 poll 到 `Finished` 不会把状态回退为
  `finished`；failed / stopped job 下载日志后保留原失败状态但写入 `result_dir`。
- 工具集成单测：submit/poll/download/sandbox kill 成功后正确写表；非 sandbox kill
  失败时不写 `terminating`。
- poller 单测：只轮询到期活跃作业，终态作业不再进入轮询队列；poller 使用 job row
  的 `user_id` / `org_id` / `project_id` / `sandbox` 构造 Bohrium context，不读取
  session 当前 project。
- 并发认领单测：两个并发批次经 `FOR UPDATE SKIP LOCKED` 得到不相交作业集；事务回滚后
  被占作业重新可领；COMMIT 后慢调用超时导致重复领取时，写回保持幂等且不会覆盖
  `downloaded`。
- 上下文装配单测：`SessionJobsPort` 从 `bohrium_jobs` 返回固定字段的 active jobs 与
  最近终态 jobs，且不暴露 `user_id`、`org_id`、原始响应 JSON；同步覆盖
  `SessionJobs.recent_terminal_jobs` 的 renderer 输出。
- 表约束单测：CHECK 生效——`sandbox` 取 0/1 之外、`status` 取允许集合之外、活跃状态
  `next_poll_at` 为 NULL、终态 `next_poll_at` 非 NULL、`downloaded` 但 `result_dir` 为
  NULL，均应被数据库拒绝；`project_id <= 0`（含哨兵 -1）应在 DAO 写入前被拒绝。
- binary collation 单测：`AbC123` 与 `abc123` 在唯一键
  `(user_id, org_id, sandbox, job_id)` 与按 session 查询条件中不被视为同一标识。
- 时间语义单测：在不同连接时区下写入与读取 `next_poll_at`，验证 `next_poll_at <= NOW()`
  的判定结果一致（`TIMESTAMP` 的 UTC 锚定不受连接时区影响）。
- `status` collation 单测：写入大小写变体（如 `Finished`、`RUNNING`）被 `chk_status`
  拒绝，确认 `utf8mb4_bin` 让 CHECK 大小写敏感。
- terminal_at 不变量单测：直接 download 一个已完成作业（不经显式 poll）后，记录
  `status = 'downloaded'` 且 `terminal_at` 非空、能被最近终态查询返回；写入终态 status 但
  `terminal_at` 为空、或活跃态写入非空 `terminal_at`，均被 `chk_terminal_at` 拒绝。
