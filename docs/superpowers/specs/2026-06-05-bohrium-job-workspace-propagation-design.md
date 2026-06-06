# Bohrium 作业 workspace 透传 - 设计

- 状态：方案已确认，待实现
- 日期：2026-06-05
- 类型：独立 PR，修复 Bohrium 作业自动唤醒的 workspace 上下文缺口
- 关系：与 Bohrium 作业完成调度器（见 `2026-06-05-bohrium-job-completion-scheduler-discussion.md`）同属一个闭环。调度器决定何时唤醒 agent；本项保证唤醒出来的 run 回到作业提交时的 workspace。

## 1. 缺口

Bohrium 作业完成后唤醒 agent run，必须恢复作业提交时的远程工作目录，否则 agent 即使被叫醒，也不一定能读到作业输入、结果或继续执行后续 Bohrium 操作。

当前缺口有两段：

- trigger 唤醒路径丢掉了 workspace：`trigger_run` 调 `_prepare_run` 时硬编码 `remote_workdir=None`、`session_directory_source="none"`。
- 台账没持久化 workspace：`bohrium_jobs` 表没有可恢复的远程工作目录字段。

后果：poller 发现作业终态后，即便调用 `trigger_run`，新的 run 也可能在默认或空 workspace 执行，访问不到提交作业时所在 `/share/...` 目录下的产物。

示例：作业提交时有效 workspace 为 `/share/proj_A`，结果预期落在 `/share/proj_A/results/run_123`；若唤醒 run 没有带回 workspace，它可能在默认 `/share` 或空本地工作目录中执行，agent 无法稳定定位产物。

## 2. workspace 是什么

`workspace` 是 Bohrium 作业提交时绑定的有效远程执行 workspace，也就是唤醒 run 必须恢复的 SSH 工作目录。

它不是：

- 用户请求里的 directory 来源说明；
- 历史事件中的 `session_directory_source`；
- 当前轮 `TurnInput.workspace_paths`；
- 用户上传的附件路径列表。

现有代码里对应的底层事实是 Bohrium setup 产出的 `execution_workdir`。当用户指定或会话持久化了 `/share/...` 时，它就是该路径；当 Bohrium setup 使用默认远程根目录时，它是实际生效的默认工作目录。

因此，`workspace` 应该记录最终有效执行目录，而不是记录可空的请求参数。

## 3. 命名收敛

本设计把自动闭环相关内部链路统一收敛为 `workspace`：

- Redis job payload 使用 `workspace`。
- `ChatStreamService._prepare_run` / `trigger_run` 使用 `workspace` 参数。
- worker 读取 `workspace` 并传给 `AgentRunService.run_agent`。
- `AgentRunService.run_agent` / Bohrium stage 使用 `workspace` 表达 run 请求的远程工作目录。
- `bohrium_jobs.workspace` 持久化作业提交时的有效 workspace。
- poller claim 后把 `workspace` 传回 `trigger_run`。

保留以下局部命名，不做强行替换：

- 外部 HTTP/API 请求仍可使用 `directory`，因为这是用户会话目录接口的输入语义。
- SSH session config 仍可使用 `workspace_path`，因为它是 session 层已有概念。
- 历史事件仍可保留 `session_directory` / `session_directory_source`，用于前端和历史展示；它们不进入 Bohrium job ledger，不参与 poller 唤醒。

## 4. 表结构与强约束

`bohrium_jobs` 增加单字段：

```sql
`workspace` VARCHAR(1024) COLLATE utf8mb4_bin NOT NULL
```

并增加路径约束：

```sql
CONSTRAINT `chk_workspace_share_path` CHECK (
    `workspace` = '/share'
    OR `workspace` LIKE '/share/%'
)
```

语义：

- 每一条进入 `bohrium_jobs` 的作业记录都必须可恢复 workspace。
- `workspace` 不允许 NULL，也不允许空字符串。
- `workspace` 必须是 `/share` 或 `/share/...`，与现有 session directory 约束保持一致。
- 旧表数据如果无法确定 workspace，由外部迁移脚本或人工处理，不在主代码里做兼容、推导或兜底。

这意味着：自动完成闭环只支持有明确 workspace 的 Bohrium 作业。没有有效 workspace 的作业不应写入该 ledger，也不能被 poller 当成可自动唤醒的作业。

## 5. 写入链

写入链的目标是把提交时有效 workspace 快照进 `bohrium_jobs`：

1. 用户发送路径继续解析 `directory` / session directory，但进入运行 payload 时只保留 `workspace`。
2. `run_bohrium_stage` 执行 Bohrium setup 后，产出最终有效 `execution_workdir`。
3. `AgentRunService.run_agent` 在构造 `build_bohrium_jobs_ports` 时传入该有效 workspace。
4. `_BohriumJobLedger` 增加 run 级 `workspace` 快照字段。
5. `record_submit` 调 `insert_submitted` 时写入 `workspace`。
6. DAO 与 DB 共同验证非空和 `/share` 路径约束。

关键点：ledger 记录的是实际生效 workspace，不是用户请求中的原始 directory，也不是旧 `remote_workdir` 参数原值。

## 6. 读取链

读取链的目标是在作业完成后恢复同一个 workspace：

1. `_CLAIM_COLUMNS` 带出 `workspace`。
2. poller claim 到终态作业后，把 `workspace` 作为唤醒参数传给 `trigger_run`。
3. `trigger_run(..., workspace=...)` 透传给 `_prepare_run`。
4. `_prepare_run` 把 `workspace` 写进 Redis job payload。
5. worker 读取 payload 中的 `workspace`，传给 `AgentRunService.run_agent`。
6. Bohrium setup 用该 workspace 建立 SSH execution session，使唤醒 run 回到作业提交时的远程目录。

`trigger_run` 本身仍是通用触发原语，因此参数可以是可选的；但 Bohrium poller 调用它时必须传入非空 workspace，因为 `bohrium_jobs.workspace` 已经是强约束字段。

## 7. 提交时快照，不做唤醒时现查

`workspace` 必须来自作业提交时的运行事实，不能在唤醒时重新读取 session 当前 directory。

原因：用户可能在作业运行期间切换会话目录。作业产物仍属于提交时 workspace，唤醒 run 必须回到当时的目录，而不是当前会话目录。

示例：

1. 第 1 轮在 `/share/proj_A` 提交 `job_1`。
2. 第 2 轮把会话目录切到 `/share/proj_B`。
3. 第 3 轮 `job_1` 完成。

正确唤醒路径必须使用 `job_1.workspace = /share/proj_A`。如果唤醒时现查 session，就会错误进入 `/share/proj_B`。

这与 ledger 已有的 `user_id` / `org_id` / `project_id` / `sandbox` 快照是同一类事实：它们都是作业提交时确定、poller 不能从当前 session 状态反推的 durable context。

## 8. 与 result_dir 的边界

`result_dir` 不纳入本 PR。

原因：

- `workspace` 是提交时已经确定的执行空间。
- `result_dir` 是结果下载或交付位置，当前 `insert_submitted` 不写，可能需要在下载、终态详情回写或 agent 消费时确定。
- 二者时机不同，把它们合并会把一个清晰的 workspace 透传补丁扩大成结果交付策略设计。

因此，本 PR 只保证唤醒 run 回到正确 workspace；`result_dir` 写回机制另案设计。

## 9. 错误与不变量

强约束下不引入主代码兼容兜底：

- 不把缺失 workspace 自动替换成当前 session directory。
- 不把缺失 workspace 自动替换成 `/share`。
- 不通过 nullable 字段表达未知。
- 不在 poller 里临时推导 workspace。

如果一次 Bohrium submit 进入 ledger 写入阶段时没有可用 workspace，这是系统不变量破坏。实现时应在 run 装配或 ledger port 构造阶段失败，避免把缺 workspace 的 ledger port 暴露给 BohriumTool。

当前 `BohriumTool._safe_ledger()` 会吞掉 ledger 异常并只记录 warning。本 PR 不改变通用 DB 写失败策略，但 workspace 缺失不能依赖 `_safe_ledger()` 吞错处理；它必须在 DAO 写入前被显式拦截。无论 DB 是否可用，主代码都不得绕过 `bohrium_jobs.workspace` 的强约束写入 NULL、空字符串或推导值。

## 10. 不在本 PR 范围

- 作业完成调度器、触发节流策略、批量唤醒策略。
- `result_dir` 的写回机制。
- `mark_handled` 触发方。
- poller 独立进程或周期调度入口。
- 历史事件里的 `session_directory_source` 展示语义。

## 附：代码锚点

- 用户发送路径写入当前 job payload：`src/services/stream_service.py:764-783`。
- trigger 唤醒路径当前丢 workspace：`src/services/stream_service.py:411-429`。
- worker 读取 job payload 并传给 run_agent：`src/worker/agent_worker.py:343-350,436-453`。
- run_agent 接收并传入 Bohrium stage：`src/services/agent_run_service.py:230-247,336-347`。
- Bohrium stage 产出 execution workdir：`src/services/agent_run_bohrium_stage.py:92-118`。
- SSH workspace 实际生效点：`src/services/agent_run_bohrium.py:678-695,764-770`。
- ledger port 装配点：`src/services/agent_run_service.py:518-523`。
- ledger 快照字段与 record_submit：`src/services/bohrium_jobs_wiring.py:34-88,167-188`。
- DAO 写入与 claim 列：`src/dao/bohrium_jobs_table.py:28-31,37-88,223-259`。
- poller claim 与轮询点：`src/services/bohrium_poller.py:51-68,120-130`。
- 表 DDL：`src/sql/create_bohrium_jobs_table.sql`。
