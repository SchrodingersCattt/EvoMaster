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
- 第一阶段先改“运行时行为”，不优先做 schema 重设计。
- `evo_chat_sessions` 表里的 `project_id` 继续保持当前 Bohrium `project_id` 语义。

## 当前模型

当前 MatMaster-Evo 的主要行为：

- 生产运行目录本质上是 `runs/mat_master_web/workspaces/{task_id}`。
- 每次发送消息都会生成新的 `task_id`。
- 多轮对话连续性主要依赖 DB 历史，而不是复用同一个 workspace。
- 本地 Web 文件树仍然默认“展示最近一次 task 的 workspace”。

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

## 目标模型

### 后端

- 保持 `task_id` 的生成方式不变。
- 不再使用 `task_id` 决定主 Agent 的 workspace。
- 主 workspace 按 `session_id` 解析。
- 同一 session 的多轮对话复用同一个 workspace。

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

- 为新旧两种 workspace 行为加一个可回滚开关。

建议配置/环境变量：

- `MAT_MASTER_WORKSPACE_MODE=task|session`
- `MAT_MASTER_SESSION_WORKSPACE_ROOT=/share/workspace`

工作项：

- [ ] 抽出统一的 workspace resolver。
- [ ] 默认仍保留旧行为。
- [ ] 在日志中打印当前解析出的 workspace mode 和 path。

建议修改文件：

- `src/services/agent_run_service.py`
- `playground/mat_master/core/playground.py`
- `playground/mat_master/service/server/paths.py`
- 如果 planner 有直接拼路径的地方，也一起接入 resolver

验证项：

- [ ] 默认配置下仍走 task 模式。
- [ ] 现有 direct 流程不回归。
- [ ] 现有 planner 流程不回归。

### Phase 1：先改本地 Web 后端，只打通 Direct

目标：

- 让本地 Web 调试链路先切到稳定的 session workspace。

工作项：

- [ ] 保留本地 run 流程中的 `task_id` 生成逻辑。
- [ ] 将 Agent 实际使用的 workspace 改为 `/share/workspace/{session_id}`。
- [ ] 首次进入新 session 时自动创建目录。
- [ ] 文件相关 API 改为按 `session_id` 解析，不再按“最近一次 task 目录”解析。

建议修改文件：

- `playground/mat_master/service/server/run_agent.py`
- `playground/mat_master/core/playground.py`
- `playground/mat_master/service/server/paths.py`
- `playground/mat_master/service/server/http_routes.py`

验证项：

- [ ] 第 1 轮能在 session workspace 写文件。
- [ ] 第 2 轮能读到第 1 轮生成的文件。
- [ ] 前端刷新后，仍能看到同一个 session 的文件。
- [ ] 新建 session 时会创建新的目录。

### Phase 1.5：单独收敛 Planner

目标：

- 在不重新引入 task-scoped 主 workspace 的前提下，保证 planner 正常工作。

工作项：

- [ ] 将 planner 中间状态迁到 `session_root/_runs/{task_id}`。
- [ ] 最终用户可见产物继续留在稳定的 session 根目录。
- [ ] 清理本地 planner 对 `run_dir/workspaces/{task_id}` 的旧假设。

建议修改文件：

- `playground/mat_master/core/solvers/_research_planner_runtime.py`
- `playground/mat_master/core/solvers/research_planner_execution/_precheck.py`
- `playground/mat_master/core/solvers/research_planner_execution/_step.py`

验证项：

- [ ] planner 第 1 轮的中间文件进入 `_runs/{task_id}`。
- [ ] planner 第 2 轮不会覆盖第 1 轮的中间态。
- [ ] 最终 session 可见产物仍在稳定的 session 根目录下。

### Phase 2：前端适配

目标：

- 让前端尽快适配稳定的 session workspace，但不要求第一版就支持完整 `/share` 浏览。

工作项：

- [ ] 文案从“最近一次 run 的 workspace”改成“当前会话目录”或“当前 session 工作区”。
- [ ] 以 `workspace_root` 为主显示文件根。
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

### Phase 3：生产 API / Worker 迁移

目标：

- 让生产 SSE / Worker 链路也遵循 session-scoped workspace 模型。

工作项：

- [ ] 保持 `task_id` 和 `invocation_id` 语义不变。
- [ ] 生产 Agent 主 workspace 改为按 `session_id` 解析。
- [ ] workspace 上传逻辑改为使用 session 根目录。
- [ ] 重新确认 stop / retry / end 在稳定 session 目录下的行为。

建议修改文件：

- `src/services/stream_service.py`
- `src/services/agent_run_service.py`
- `src/apis/chat_api.py`

验证项：

- [ ] 生产多轮对话可以读取上一轮文件。
- [ ] `workspace_uploaded` 事件仍然正常。
- [ ] stop / rerun 不会切换 workspace 根目录。
- [ ] SSE 历史仍然按 `task_id` / `invocation_id` 区分轮次。

### Phase 4：Bohrium 远程对齐

目标：

- 让 SSH / Bohrium 远程运行也使用同一套 session workspace 约定。

工作项：

- [ ] Bohrium 远程 working dir 指向 `/share/workspace/{session_id}`。
- [ ] 保持同一 Bohrium 项目内 `/share` 可共享。
- [ ] 检查远程 skill 同步、远程文件回调、路径解析是否还带旧目录假设。

建议修改文件：

- `src/services/agent_run_bohrium.py`
- `evomaster/core/playground_session.py`
- 若远程回调里依赖旧路径规则，也一并修改

验证项：

- [ ] session A 的输出写在自己的 session 根目录下。
- [ ] 同一 Bohrium 项目下的 session B 能读取共享 `/share` 内容。
- [ ] session A 和 B 不会互相覆盖各自 session 根目录。

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

- [ ] 新 session 创建 `/share/workspace/{session_id}`
- [ ] 第二轮能读到上一轮文件
- [ ] 上传文件落到 session 根目录

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

- [ ] 第一版前端是否只展示 session 目录，不展示共享目录？
- [ ] workspace 上传未来是做“整个 session 上传”还是“增量上传”？
- [ ] 旧的 task workspace 目录要不要做迁移，还是直接视为历史遗留？
- [ ] 是否还有 MCP / skill / callback 代码写死了 `/personal/workspace`？

## 全量迁移完成后的清理项

- [ ] 如果不再需要回滚，移除 task-mode 开关。
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
