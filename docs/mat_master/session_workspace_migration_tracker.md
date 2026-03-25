# Session Workspace 迁移跟踪文档

## 目标

将 MatMaster-Evo 从**按 task 划分 workspace**迁移为**按 session 划分 workspace**。

目标运行时模型：

- Bohrium `project_id` 是唯一的“项目”概念。
- 一个 session 对应一个稳定的工作目录。
- session 工作目录路径为 `/share/workspace/{session_id}`。
- 同一个 Bohrium 项目下，不同 session 共享同一个 `/share`。
- `task_id` 继续保留，用于日志、SSE、历史事件、planner 内部状态等“本轮运行”标识。

本文档用于后续渐进式修改、联调验证、回归跟踪和清理收尾。

## 前提假设

- `/share` 已经由 Bohrium `project_id` 自动隔离。
- 不再引入额外的业务项目模型。
- 第一阶段先改 **SSH / Bohrium 远端运行时行为**，不优先做 schema 重设计。
- `evo_chat_sessions` 表里的 `project_id` 继续保持当前 Bohrium `project_id` 语义。
- 本地 worker / 本地 Web 进程没有天然的 `/share` 目录，因此本地 workspace 继续保持当前 task-scoped 语义。

## 当前模型

当前 MatMaster-Evo 的主要行为：

- 生产运行目录本质上是 `runs/mat_master_web/workspaces/{task_id}`。
- 每次发送消息都会生成新的 `task_id`。
- 多轮对话连续性主要依赖 DB 历史，而不是复用同一个 workspace。
- 本地 Web 文件树仍然默认“展示最近一次 task 的 workspace”。
- Bohrium SSH attach 的旧默认 cwd 语义仍然来自 `/personal/workspace`。

关键代码位置：

- `src/services/stream_service.py`
- `src/services/agent_run_service.py`
- `playground/mat_master/core/playground.py`
- `playground/mat_master/service/server/paths.py`
- `playground/mat_master/frontend/src/components/FileTree.tsx`

## 来自 `scimaster-bohr-chat` 的参考

同级项目 `../scimaster-bohr-chat` 里，有两点对这次迁移很有参考价值：

- 它把**项目共享存储**和**会话存储空间**明确建模成两棵树。
- `/share` 是项目级共享目录。
- `/personal/workspace/{sessionId}` 是会话级目录。
- `projectId` 驱动共享目录，不驱动 session 目录。

参考文件：

- `../scimaster-bohr-chat/src/pages/matmaster/chat-evo/components/EvoSideTree.tsx`
- `../scimaster-bohr-chat/src/pages/matmaster/chat-evo/components/EvoDirTree.tsx`
- `../scimaster-bohr-chat/src/api/index.tsx`
- `../scimaster-bohr-chat/src/api/chat-evo.ts`

值得借鉴的行为：

- 左侧树把共享存储和会话存储分开。
- 会话树的根路径由 `sessionId` 稳定推导。
- 前端会先持久化当前项目，再发 stream 请求。
- 文件浏览基于存储路径语义，而不是基于 `task_id` 目录语义。

需要注意的差异：

- `scimaster-bohr-chat` 的 session 目录是 `/personal/workspace/{sessionId}`。
- MatMaster-Evo 这次迁移的目标是 `/share/workspace/{session_id}`。
- 原因不是再造一套“业务项目”概念，而是直接复用 Bohrium `project_id` 对 `/share` 的天然隔离能力。

## 目标模型

### 后端

- 保持 `task_id` 的生成方式不变。
- 不再使用 `task_id` 决定主 Agent 的 workspace。
- 主 workspace 按 `session_id` 解析。
- 同一 session 的多轮对话复用同一个 workspace。
- SSH / Bohrium 远端 cwd 目标为 `/share/workspace/{session_id}`。
- `/share` 继续表示 Bohrium 项目级共享存储；`/share/workspace/{session_id}` 只是其中的 session-specific 子目录。

### 本地运行

- 本地 worker 与本地 Web 继续使用现有 task-scoped workspace。
- 这轮不保留本地 session-scoped workspace 的配置入口，避免造成误解。

### 前端

- 不再把“最近一次 task 的 workspace”当作主文件根目录。
- 改为展示“当前 session 的固定 workspace”。
- 项目共享存储 `/share` 的浏览能力后续单独补，不阻塞第一版联调。

### Planner

- planner 的内部中间状态仍然按“本轮运行”隔离。
- 不建议把 planner 所有临时状态直接堆在 session 根目录。
- 推荐目录：
  - session 根目录：`/share/workspace/{session_id}`
  - planner 中间态目录：`/share/workspace/{session_id}/_runs/{task_id}/...`

## 分阶段计划

### Phase 0：开关与统一 Resolver

目标：

- 把本地 workspace 解析与 SSH 远端 workspace root 解析收口到统一入口。

建议配置/环境变量：

- `mat_master.remote_session_workspace_root=/share/workspace`

工作项：

- [x] 抽出统一的 workspace resolver。
- [x] 本地默认仍保留 task-scoped 旧行为。
- [x] 在关键入口统一使用 resolver 解析本地 workspace 与远端 SSH root。

建议修改文件：

- `src/services/agent_run_service.py`
- `playground/mat_master/core/playground.py`
- `playground/mat_master/service/server/paths.py`
- 如果 planner 有直接拼路径的地方，也一起接入 resolver

验证项：

- [x] 默认配置下仍走 task 模式。
- [x] workspace resolver 单测通过。
- [x] 关键 import smoke test 通过。

### Phase 1：先改 SSH / Bohrium 远端 session workspace

目标：

- 先把真正需要的 Bohrium SSH cwd 语义改到 `/share/workspace/{session_id}`。
- 让同一个 Bohrium 项目下的多个 session 共享 `/share`，但各自拥有独立 session 目录。
- 不要求本地 worker / 本地 Web 同步完成 session-scoped workspace。

工作项：

- [x] 新增 `remote_session_workspace_root` 配置入口。
- [x] 生产 Bohrium attach 改为 `working_dir={remote_root}` + `session_id=session_id`。
- [x] 本地 Web Bohrium attach 改为 `working_dir={remote_root}` + `session_id=session_id`。
- [x] Agent prompt 补充 `/share` 是项目级共享存储、`/share/workspace/{session_id}` 是当前会话目录的语义提示。
- [x] `monitor_job` 的 SSH fallback 从 `/personal/workspace` 调整为优先使用当前 session config。
- [ ] 做一次真实 Bohrium 远端联调，确认新 session 会在 `/share/workspace/{session_id}` 下落目录。
- [ ] 核对远端 skill sync / callback / 其他远程工具是否还依赖旧 cwd 假设。

建议修改文件：

- `src/services/agent_run_bohrium.py`
- `playground/mat_master/service/server/run_agent.py`
- `playground/mat_master/core/agent.py`
- `evomaster/agent/tools/builtin/monitor_job/_tool.py`

验证项：

- [x] resolver / import 级最小验证通过。
- [ ] 第 1 轮 Bohrium 运行能在 `/share/workspace/{session_id}` 写文件。
- [ ] 第 2 轮 Bohrium 运行能读到第 1 轮生成的文件。
- [ ] 同一 Bohrium 项目下，session B 可以读取 `/share` 中的共享内容。

### Phase 2：前端适配稳定的 session 语义

目标：

- 让前端优先适配“当前 session 的稳定目录”语义。
- 第一版不阻塞在完整 `/share` 树浏览能力上。

工作项：

- [ ] 文案从“最近一次 run 的 workspace”改成“当前会话目录”或“当前 session 工作区”。
- [ ] 以当前 session 文件根为主显示文件根。
- [ ] `task_id` 仅作为本轮运行元数据保留。
- [ ] 第一版不要因为共享树未完成而阻塞联调。

建议修改文件：

- `playground/mat_master/frontend/src/components/FileTree.tsx`
- `playground/mat_master/frontend/src/components/WorkspacePanel.tsx`
- `playground/mat_master/frontend/src/components/MatMasterView.tsx`

验证项：

- [ ] 文件面板不再暗示 task-scoped 目录。
- [ ] 新一轮运行后刷新文件树，仍然指向同一个 session 根目录。
- [ ] 上传、预览、下载功能不回归。

### Phase 3：本地 session workspace（可选，后置）

目标：

- 当前不做。
- 若未来确有需要，再重新单独设计，不沿用这轮已移除的半成品配置开关。

工作项：

- [ ] 暂无。

建议修改文件：

- 暂无。

验证项：

- [ ] 暂无。

### Phase 4：Planner 中间态收敛

目标：

- 在不重新引入 task-scoped 主 workspace 的前提下，保证 planner 正常工作。

工作项：

- [ ] 将 planner 中间状态迁到 `session_root/_runs/{task_id}`。
- [ ] 最终用户可见产物继续留在稳定的 session 根目录。
- [ ] 清理 planner 对 `run_dir/workspaces/{task_id}` 的旧假设。

建议修改文件：

- `playground/mat_master/core/solvers/_research_planner_runtime.py`
- `playground/mat_master/core/solvers/research_planner_execution/_precheck.py`
- `playground/mat_master/core/solvers/research_planner_execution/_step.py`

验证项：

- [ ] planner 第 1 轮的中间文件进入 `_runs/{task_id}`。
- [ ] planner 第 2 轮不会覆盖第 1 轮的中间态。
- [ ] 最终 session 可见产物仍在稳定的 session 根目录下。

### Phase 5：生产 API / Worker 全量对齐

目标：

- 生产 SSE / Worker 链路整体对齐 session-scoped workspace 语义。

工作项：

- [ ] 保持 `task_id` 和 `invocation_id` 语义不变。
- [ ] 生产 Agent 本地 workspace 是否切换为 session-scoped，根据联调结果决定。
- [ ] workspace 上传逻辑改为使用稳定 session 根目录或明确维持现状。
- [ ] 重新确认 stop / retry / end 在新语义下的行为。

建议修改文件：

- `src/services/stream_service.py`
- `src/services/agent_run_service.py`
- `src/apis/chat_api.py`

验证项：

- [ ] 生产多轮 session 文件连续性成立。
- [ ] `workspace_uploaded` 事件仍然正常。
- [ ] stop / rerun 不会错误切换 workspace 根目录。
- [ ] SSE 历史仍然按 `task_id` / `invocation_id` 区分轮次。

## 前端实施策略

推荐前端分两步走：

### Step A

先只适配“稳定 session workspace”。

- 暂不引入共享目录树
- 保留现有文件面板
- 把根语义从 task 改成 session

### Step B

再补项目共享目录 `/share` 的浏览能力。

可选方案：

- 加第二棵树或单独 Tab 展示 `/share`
- 或增加专门的 shared API

第一版不建议做的事：

- 强行让当前 session 文件 API 同时承担整个 `/share` 浏览
- 在不调整安全检查的情况下，依赖 symlink 穿透共享目录

## API 兼容性说明

建议保持兼容：

- `task_id`
- `invocation_id`
- `workspace_paths`
- `bohrium_project_id`

需要谨慎变更：

- 文件 API 返回的 workspace 根目录语义
- workspace 上传范围
- planner 的临时目录布局

## 验证矩阵

### Local Direct

- [ ] 保持当前 task-scoped 行为不回归
- [ ] 上传、预览、下载逻辑不回归

### Local Planner

- [ ] 中间状态进入 `_runs/{task_id}`
- [ ] 最终产物仍可在 session 根目录看到

### Frontend

- [ ] session 文件树跨轮次刷新保持稳定
- [ ] 预览/下载正常
- [ ] 上传正常

### Production

- [ ] 多轮 session 文件连续性成立
- [ ] SSE 和历史仍按 `task_id` 区分轮次
- [ ] stop / retry 行为正确

### Bohrium Remote

- [ ] 远程 working dir 为 `/share/workspace/{session_id}`
- [ ] 同项目下 session 共享 `/share`
- [ ] 不同 session 根目录隔离

## 待确认问题

- [ ] workspace 上传未来是做“整个 session 上传”还是“增量上传”？
- [ ] 旧的 task workspace 目录要不要做迁移，还是直接视为历史遗留？
- [ ] 是否还有 MCP / skill / callback 代码写死了 `/personal/workspace`？
- [ ] 是否需要把远端 skill sync 根目录也从 `/personal/workspace/.evomaster` 调整出去？

## 全量迁移完成后的清理项

- [ ] 清理 UI 和文档里“最近一次 task workspace”的旧表述。
- [ ] 删除仍按 `task_id` 解析主 workspace 的死代码。
- [ ] 行为稳定后，补充架构文档和 AGENTS 约定。

## 变更日志

建议后续每轮改动都补在这里，方便追踪。

### 2026-03-24

- 创建迁移跟踪文档。
- 确认 MatMaster-Evo 当前主 workspace 模型仍然是 task-scoped。
- 确认 `scimaster-bohr-chat` 已经把项目共享存储和会话存储拆分建模。
- 确认继续复用 Bohrium `project_id`，不引入新的业务项目概念。
- 决策调整为“SSH / Bohrium 优先，本地 workspace 后置”。
- 完成 Phase 0：新增统一 workspace resolver，并在生产 / 本地 Web / Playground 入口接入；本地继续保持 task-scoped，SSH 远端单独走 `remote_session_workspace_root`。
- 为 SSH 远端新增 `remote_session_workspace_root` 配置能力，当前目标根路径为 `/share/workspace`。
- 已将生产 Bohrium attach、本地 Web Bohrium attach、Agent `/share` 提示、`monitor_job` SSH fallback 调整到新的 session workspace 语义。
- 已完成最小验证：`uv run pytest tests/test_workspace_resolver.py` 通过，关键 import smoke test 通过。
- 清理掉未准备投入使用的本地 session workspace 配置入口，只保留 SSH 远端相关配置。
- 进一步移除本地 `workspace_root` / `MAT_MASTER_WORKSPACE_ROOT` override 支持，避免保留无用配置面。
- 进一步移除 `MAT_MASTER_REMOTE_SESSION_WORKSPACE_ROOT` 环境变量分支，只保留 `config.yaml` 中的 `remote_session_workspace_root` 配置。
