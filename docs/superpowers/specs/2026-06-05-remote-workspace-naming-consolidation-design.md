# Remote Workspace 命名整合设计

- 日期：2026-06-05
- 状态：设计已确认，待实施计划
- 类型：命名与边界收口
- 关联：
  - `2026-04-17-session-directory-runtime-connection-design.md`
  - `2026-06-05-bohrium-job-workspace-propagation-design.md`

## 1. 背景

当前远程 workspace 语义被多个名称承载：

- `session_directory`
- `directory`
- `remote_workdir`
- `session_directory_source`
- `remote_session_workspace_root`
- `execution_workdir`
- `RuntimeTopology.workspace_root`
- `control_root`
- `remote_project_root`
- `workspace_paths`

这些名字不是全部同义。真正的问题是：同一条远程 workspace 链路在 API、DB、Redis job、Worker、Bohrium SSH、runtime topology 中用了不同命名，且部分名称没有表达生命周期边界。

典型困惑是 `remote_workdir` 能否承担 workspace 语义。按当前实现，它能表达本轮解析后的远端执行 workspace，但不能表达 session 持久绑定，也不能表达本地 run workspace，更不能替代工具实际 runtime 根 `execution_workdir`。

本设计的目标是把命名收口成清晰的三层模型：session 持久绑定、本轮远程 workspace 快照、工具实际执行目录。

## 2. 目标

- 统一远程 workspace 链路的内部命名，让字段名反映生命周期与所在平面。
- 保留确有不同语义的 runtime 名称，避免把不同边界强行合并。
- 移除主代码中的旧字段名，不做内联兼容、兜底或双读。
- 明确 DB/API/Redis/Worker/Bohrium/job ledger 的字段迁移边界。
- 为 Bohrium job workspace 快照设计使用新命名，避免继续扩散旧名。

## 3. 非目标

- 不改变 workspace 解析行为。
- 不改变 `/share` 路径校验规则。
- 不改变 `Playground.prepare()` 的本地 run workspace 生成方式。
- 不改变工具路径校验逻辑。
- 不在主代码中保留旧字段自动迁移逻辑。
- 不处理 `result_dir` 写回机制；它与 workspace 快照相关，但属于 Bohrium job 结果路径补全问题。

## 4. 语义分层

### 4.1 Session 持久绑定

含义：一个 `session_id` 默认绑定的远端 workspace 路径。

当前名称：

- DB：`evo_chat_sessions.session_directory`
- API route：`/session-directory`
- API response：`directory`

目标名称：

- DB 字段：`session_workspace_path`
- 服务层字段：`session_workspace_path`
- response 字段：`workspace_path`

它不是本轮最终执行目录。用户可以在单次请求里传入 override，覆盖本轮 run 的远端 workspace，但不改变 session 持久绑定。

### 4.2 本轮远端 workspace 快照

含义：本轮 run 选择出来的远端 workspace 路径。它可能来自本轮请求，也可能来自 session 默认绑定。

当前名称：

- `remote_workdir`
- `session_directory_source`

目标名称：

- `remote_workspace_path`
- `workspace_source`

`workspace_source` 取值保持当前三态：

```python
Literal["request", "session", "none"]
```

本层字段应该进入 Redis job、Worker 参数、`AgentRunService.run_agent()`、Bohrium setup、Bohrium job ledger 快照。

### 4.3 工具实际执行目录

含义：工具命令和文件操作最终看到的工作目录。

保留名称：

- `ExecutionEnvironment.execution_workdir`
- `BohriumExecutionContext.execution_workdir`
- `RuntimeTopology.workspace_root`

理由：`execution_workdir` 能同时表达 local 和 remote。Bohrium 成功 attach 后，它等于 `remote_workspace_path`；没有 Bohrium 时，它是本地 run workspace。工具路径校验应该继续只关心 `RuntimeTopology.workspace_root`。

## 5. 保留名称

以下名称不应纳入本次重命名：

| 名称 | 保留理由 |
|------|----------|
| `execution_workdir` | 工具实际执行目录，local/remote 统一。 |
| `RuntimeTopology.workspace_root` | 工具路径边界根，由 `execution_workdir` 派生。 |
| `control_root` | 控制平面本地目录，与 session 工具执行目录不同。 |
| `remote_project_root` | 远端 `.matmaster`、skill mirror 等 runtime 资源根，不是用户 workspace。 |
| `workspace_paths` | 用户输入附件里的路径列表，不是 run workspace。 |
| `workdir` | 本地物理 run workspace 或工具构造参数中已有惯用名；只在局部含义明确时保留。 |

## 6. 目标命名映射

| 当前名称 | 目标名称 | 层级 | 备注 |
|----------|----------|------|------|
| `session_directory` | `session_workspace_path` | session 持久绑定 | DB 与服务层同步迁移。 |
| `directory` | `workspace_path` | API 请求/响应 | 若前端同步迁移，直接替换；不做双字段兼容。 |
| `remote_workdir` | `remote_workspace_path` | 本轮远端快照 | Redis job、Worker、service、Bohrium setup 全链路替换。 |
| `session_directory_source` | `workspace_source` | 本轮远端快照元数据 | 取值不变。 |
| `ResolvedSessionDirectory` | `ResolvedRunWorkspace` | resolver 结果 | 表达本轮 workspace 决策，而不是只表达 session directory。 |
| `SessionDirectoryResolver` | `RunWorkspaceResolver` | resolver | 继续只负责解析，不启动 SSH、不写 DB、不入队。 |
| `normalize_remote_share_path` | `normalize_remote_workspace_path` | 校验 helper | 规则不变，只改名。 |
| `normalize_session_directory_for_storage` | `normalize_session_workspace_path_for_storage` | 存储 helper | 规则不变，只改名。 |
| `remote_session_workspace_root` | `remote_workspace_base` | 配置 | 默认 `/share`，是共享根，不是 session workspace。 |

## 7. 数据流

目标数据流：

```text
POST /stream.workspace_path
  + evo_chat_sessions.session_workspace_path
  -> RunWorkspaceResolver
  -> ResolvedRunWorkspace(remote_workspace_path, workspace_source, bohrium_required)
  -> Redis job.remote_workspace_path / workspace_source
  -> Worker
  -> AgentRunService.run_agent(remote_workspace_path=...)
  -> run_bohrium_stage
  -> BohriumSetupService.run_setup(remote_workspace_path=...)
  -> SSHSessionConfig.workspace_path
  -> ExecutionEnvironment.execution_workdir
  -> RuntimeTopology.workspace_root
```

本地 run 没有远端 workspace 时：

```text
remote_workspace_path = None
workspace_source = "none"
ExecutionEnvironment.execution_workdir = local run workspace
RuntimeTopology.workspace_root = local run workspace
```

Bohrium 被 project/org 触发但没有显式 workspace 时：

```text
remote_workspace_path = None
remote_workspace_base = "/share"
SSH workspace_path = remote_workspace_base
execution_workdir = "/share"
```

这说明 `remote_workspace_path` 仍然是本轮远端 workspace 快照，而不是最终执行目录的唯一来源。

## 8. API 与 DB 迁移

项目处于开发阶段，不保留主代码兼容逻辑。

### 8.1 DB

将 `evo_chat_sessions.session_directory` 改为 `session_workspace_path`。

需要同步更新：

- DDL 脚本
- 迁移脚本
- DAO SELECT/UPDATE
- 列表分组索引
- session list grouping 逻辑
- tests fixture

如果当前环境已有旧表，迁移通过外部 SQL 完成，例如 rename column。主代码不做 `session_directory` 与 `session_workspace_path` 双读。

### 8.2 API

将请求/响应字段从 `directory` 调整为 `workspace_path`。

`/session-directory` route 可以在本次保留，因为 route 名称是公开 URL，且影响前端路由与文档。但 route 内部模型和服务字段应使用 `workspace_path`。

若决定同时改 route，目标可为 `/session-workspace`，但这会扩大前端联动范围。本设计默认不改 route。

### 8.3 历史事件

用户 query 历史中的：

- `session_directory`
- `session_directory_source`

改为：

- `remote_workspace_path`
- `workspace_source`

旧历史数据不兼容读取。需要保留旧会话时，用外部迁移脚本统一改 JSON content；否则开发环境可丢弃旧数据。

## 9. Bohrium job 快照

`bohrium_jobs` 不应继续新增旧名字段。

目标字段：

- `remote_workspace_path VARCHAR(1024) NULL`
- `workspace_source VARCHAR(16) NULL`

提交作业时写入本轮 run 的快照。唤醒终态作业时，从 job row 取出这两个字段传给 `trigger_run`，再进入 `_prepare_run`。

关键语义：作业唤醒必须使用提交时快照，不能在唤醒时重新查询 session 当前 workspace。因为用户可能在作业运行期间切换 session 默认 workspace。

## 10. 模块级改动边界

### 10.1 `src/services/session_directory_service.py`

重命名为 `run_workspace_service.py` 或 `workspace_resolution_service.py`。

推荐 `run_workspace_service.py`，因为它解析的是一次 run 的 workspace 决策，不只是路径规范化。

内部类型：

```python
WorkspaceSource = Literal["request", "session", "none"]

@dataclass(frozen=True)
class ResolvedRunWorkspace:
    remote_workspace_path: str | None
    source: WorkspaceSource
    bohrium_required: bool
```

### 10.2 `src/services/stream_service.py`

替换 `_prepare_run` 参数：

- `remote_workdir` -> `remote_workspace_path`
- `session_directory_source` -> `workspace_source`

`trigger_run` 新增同名参数，禁止继续硬编码空 workspace。

### 10.3 `src/worker/agent_worker.py`

从 job payload 读取：

- `remote_workspace_path`
- `workspace_source`

传给 `AgentRunService.run_agent(remote_workspace_path=...)`。

`workspace_source` 若 run_agent 暂不消费，也应继续保留在 trigger/job/ledger 链路里，供 job 唤醒和审计使用。

### 10.4 `src/services/agent_run_service.py`

`run_agent` 参数改为 `remote_workspace_path`。

装配 Bohrium job ports 时传入：

- `remote_workspace_path`
- `workspace_source`

### 10.5 `src/services/agent_run_bohrium*.py`

Bohrium setup 参数改为 `remote_workspace_path`。

SSH workspace 选择规则保持不变：

```python
ssh_workspace_path = remote_workspace_path or remote_workspace_base
```

返回的 `execution_workdir` 仍然使用 `ssh_workspace_path`。

### 10.6 `matmaster/integration/workspace_resolver.py`

配置项 `remote_session_workspace_root` 改为 `remote_workspace_base`。

helper 改为：

```python
get_remote_workspace_base(...)
```

默认值仍为 `/share`。

## 11. 测试策略

只做命名迁移需要更新现有测试，不新增新的行为覆盖。

需要更新的测试簇：

- session workspace path normalization
- stream prepare job payload
- worker Redis bridge
- AgentRunService -> Bohrium setup pass-through
- Bohrium SSH workspace contract
- chat events workspace metadata
- session list grouping
- workspace resolver config override
- Bohrium job workspace propagation

断言重点：

- 请求 workspace 优先于 session workspace。
- 空请求回落到 session workspace。
- 无 workspace 时 `remote_workspace_path is None`。
- 有远端 workspace 时强制 Bohrium。
- Bohrium setup 的 SSH workspace path 等于 `remote_workspace_path`。
- 工具 runtime 仍使用 `execution_workdir` / `RuntimeTopology.workspace_root`。

## 12. 实施顺序

1. 重命名 resolver 类型与 helper。
2. 替换 stream job payload 与 Worker/run_agent 参数。
3. 替换 Bohrium setup 参数与测试。
4. 替换 DB/API service 层 `session_directory` 为 `session_workspace_path`。
5. 替换历史事件 JSON key。
6. 将 Bohrium job workspace 快照字段采用新名接入。
7. 更新文档与测试断言。
8. 执行 focused tests 与 changed-files pre-commit。

## 13. 风险

- API 字段改名需要前端同步，否则发送 workspace 的请求会失效。
- DB 字段 rename 需要外部 SQL 或重建表；主代码不做双读。
- 历史事件 JSON key 改名会影响 replay 与 UI 展示；旧数据需要外部迁移或丢弃。
- Bohrium job workspace 快照若只改写入不改唤醒，仍然无法恢复正确 workspace。
- `execution_workdir` 不应被误改成 `remote_workspace_path`，否则本地 run 语义会被破坏。

## 14. 验收标准

- 全仓不再出现主代码字段名 `remote_workdir`。
- 全仓不再出现主代码字段名 `session_directory_source`。
- `session_directory` 只允许出现在旧 spec 文档或明确的迁移说明中。
- 运行时工具根仍由 `execution_workdir` 派生为 `RuntimeTopology.workspace_root`。
- Bohrium SSH attach 使用 `remote_workspace_path or remote_workspace_base`。
- Bohrium job ledger 使用 `remote_workspace_path` / `workspace_source` 快照。
- focused tests 通过。
- 不新增内联兼容、自动迁移或旧字段 fallback。
