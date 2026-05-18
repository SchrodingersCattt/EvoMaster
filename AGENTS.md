# AGENTS.md — AI 编程助手项目约定

本文件为 AI 编程助手提供项目级约定与上下文，请在所有编辑与生成代码时遵守。

---


## 异常处理

**应用已在全局做了 error handler，各层异常可向上抛出，由统一异常处理返回给调用方。**

- **DAO 层**：不要用 try/except 捕获并吞掉异常。避免在 DAO 里 `except ...: logger.error(...); return False/0` 等写法，否则上层无法区分“业务无数据”与“数据库错误”。
- **服务层（如调用外部 HTTP 的 quota_service）**：可不在此处捕获，让异常向上抛出，由全局 handler 统一处理；若确有降级需求（如外部不可用时返回默认值），再在调用处或本层按需捕获并写明原因。

---

## bohr-agent-sdk 与本项目的关系

**bohr-agent-sdk**（[dptech-corp/bohr-agent-sdk](https://github.com/dptech-corp/bohr-agent-sdk)）是 Bohrium 官方的科学计算 Agent SDK，用于把科学计算程序封装成 MCP 标准服务。本仓库（matmaster-evo）作为 **MCP 客户端 / Agent 侧**，与基于 bohr-agent-sdk 部署的 **MCP Server** 配合使用。

### 角色划分

| 角色 | 本项目（matmaster-evo） | bohr-agent-sdk |
|------|-------------------------|----------------|
| 定位 | MCP 客户端：发起 CallTool，传 executor / storage 等参数 | MCP Server 侧：CalculationMCPServer 接收参数，执行/提交任务 |
| 鉴权注入 | 在 Path Adaptor 中注入：`inject_bohrium_executor`、`get_bohrium_storage_config`（用 session 的 access_key 等） | Server 侧用收到的 executor / storage 做 `init_executor`、`init_storage`，不负责鉴权来源 |
| 配置 | `mcp.calculation_executors`、`mcp.calculation_servers`（config.yaml） | Server 端自己的部署与工具实现 |

### 数据流（executor / storage）

1. **本仓库**：Path Adaptor（`evomaster/adaptors/calculation/path_adaptor.py`）根据 `calculation_executors` 解析出 executor 模板，经 `inject_bohrium_executor` 注入 access_key、project_id、user_id 及 `resources.envs`（如 `BOHRIUM_PROJECT_ID`）；storage 由 `get_bohrium_storage_config` 生成。二者写入工具参数 `args`，经 `MCPTool` → `mcp_connection.call_tool(tool_name, args)` 随 MCP 协议发出。
2. **MCP Server（bohr-agent-sdk）**：CalculationMCPServer 收到的 CallTool `arguments` 中包含 `executor`、`storage` 与业务参数。`submit_job` / `run_job` 中调用 `init_executor(executor)`、`init_storage(storage)`，用 executor 提交任务（DispatcherExecutor 或 LocalExecutor），用 storage 做输入下载/结果上传。

### 与本仓库直接相关的约定

- **executor 类型**：本仓库对 `executor.type == "dispatcher"` 注入 machine.remote_profile 与 resources.envs；对 `executor.type == "local"` 仅注入 `executor.env` 的 BOHRIUM_ACCESS_KEY 与 BOHRIUM_PROJECT_ID，供 bohr-agent-sdk 的 LocalExecutor 在本地运行时使用（`evomaster/env/bohrium.py`）。
- **配置结构**：executor 模板来自 `mcp_config.calculation_executors[server_name].executor` 或 `executor_map[tool_name]`；未出现在 `calculation_executors` 中的 server（如纯 DB 检索）不会注入 executor，仅会注入 storage（若在 `calculation_servers` 中）。
- **path_params_by_tool**：可选。`calculation_executors[server_name].path_params_by_tool` 将 **远程工具名**（如 `submit_run_gromacs`）映射到需按输入工件处理的路径 selector 列表，既支持顶层参数名，也支持 `targets[].model_path` 这类嵌套 selector。selector 会对照解引用后的 schema 做校验，配置拼写错误需尽早失败。该配置适用于 MCP 将 `List[Path]` 暴露为无 `format: path` 的 string 数组、或 submit 工具 description 过短导致无法从 docstring 推断 Path 的场景；未配置时仍依赖 schema / docstring / `*_path` 三层检测。
- **calculation OSS 对象键**：`evomaster/adaptors/calculation/oss_io.upload_file_to_oss` 使用 `{prefix}/{uuid}/{原始文件名}`，使 HTTPS URL 最后一截与本地 basename 一致，便于 bohr-agent-sdk 下载后与 `gmx` 等按 basename 引用一致。
- **文档与兼容**：修改 Path Adaptor、executor/storage 结构或鉴权注入逻辑时，需考虑与 bohr-agent-sdk 的 CalculationMCPServer、DispatcherExecutor/LocalExecutor 及 storage 约定的兼容性；可参考 [bohr-agent-sdk 仓库](https://github.com/dptech-corp/bohr-agent-sdk) 的 `src/dp/agent/server/calculation_mcp_server.py` 与 `executor/`、`storage/` 实现。

---

## EvoMaster 上游仓库与本项目的关系

**EvoMaster**（[sjtu-sai-agents/EvoMaster](https://github.com/sjtu-sai-agents/EvoMaster)）是科学计算 Agent 的通用基础设施框架（SciMaster 系列背后的引擎）。本仓库（matmaster-evo）在**嵌入/复刻**其核心库的基础上，增加了 MatMaster 业务 Playground、Bohrium 集成与 MCP calculation 适配等。

### 角色与版本

| 维度 | 本项目（matmaster-evo） | EvoMaster 上游 |
|------|-------------------------|----------------|
| 定位 | 下游应用：基于 EvoMaster 的 evomaster 核心 + 自研 playground、服务端、MCP 适配 | 上游框架：Agent/Playground/Exp、Tools、Skills、Session 等通用实现 |
| 当前基于版本 | v0.0.1 架构与 API | 上游已发布 v0.0.2（配置与多 Agent 等有较大变更） |
| 代码对应 | 项目内 `evomaster/` 目录对应上游的 `evomaster/`；本仓库另有 `matmaster/`、`src/`、`evomaster/adaptors/` 等自有代码（历史 `playground/mat_master` 本地 Web 树已移除） | 上游 `evomaster/` + 上游仓库内各类 `playground/` 示例 |

### 与本仓库直接相关的约定

- **evomaster 目录**：本仓库的 `evomaster/` 来源于上游，但已包含本项目定制（如 `evomaster/adaptors/calculation/`、与 Bohrium/MCP 相关的逻辑）。修改 `evomaster/` 时需注意与上游的差异，避免破坏后续合并或参考上游时的可对照性。
- **同步/升级上游**：若从上游拉取新特性，需参考上游 [v0.0.1 → v0.0.2 迁移指南](https://github.com/sjtu-sai-agents/EvoMaster/blob/main/docs/migration/MIGRATION_GUIDE_v0.0.1_to_v0.0.2.md)。本仓库的**迁移方案**（分阶段、带兼容层）已固化为 [docs/evomaster/migration.md](docs/evomaster/migration.md)。本仓库**已完成** v0.0.2 风格迁移：配置仅用 `agents`（无顶层 `agent`）、per-agent `tools: { builtin, mcp }`（无 `enable_tools`）、Skills 对外仅导出 `Skill`；与上游的剩余差异（如顶层 `skill` 配置、Skills 内部 knowledge/operator 目录）见 migration.md 的「未对齐或未完全集成」。
- **文档与引用**：涉及 Agent/Playground/Exp、Tools、Skills 等通用行为时，可引用上游 [EvoMaster 文档](https://github.com/sjtu-sai-agents/EvoMaster)（如 architecture、agent、tools、skills）；本仓库特有逻辑（如 MatMaster、calculation path adaptor）以本仓库代码与 AGENTS.md 为准。

---

## 服务架构（API / Worker 分离）

本服务采用 **API 进程与 Worker 进程分离** 的架构，二者可独立扩缩容，通过 Redis 协调。

| 角色 | 职责 | 入口与部署 |
|------|------|------------|
| **API** | 处理 HTTP 请求、SSE 订阅与流式推送；接收 /chat/send 后入队，通过 Redis 订阅 stream 事件并转发给前端；维护 session 状态、run_owner 查询与 run_interrupted 判定。**生产仅支持 Worker 队列模式**：发送消息需配置 `REDIS_URL`，未配置时 POST /stream 返回 503。 | `app.py`（如 uvicorn）；可多实例（多 Pod）。 |
| **Worker** | 从 Redis 队列 BLPOP 拉取任务，执行 `run_agent_sync`；将事件 publish 到 Redis、写 DB；周期刷新 `worker_alive` 与当前 session 的 `session_run_owner` TTL。 | `src/worker/agent_worker.py` 独立进程（Dockerfile 可选 `--target worker`）；可多实例。 |

- **协调方式**：API 与 Worker 之间通过 Redis 通信：任务队列、stream 事件发布/订阅、`session_run_owner` / `worker_alive`、stop 请求等。新增或修改功能时，不得依赖「处理当前 HTTP 请求的进程」与「执行该会话 agent 的进程」为同一进程。

### MatMaster：平台 API 与会话 Playground

MatMaster 的对话与任务执行以 **根目录 `app.py` + `src/`（API）** 与 **`src/worker/agent_worker.py`（Worker）** 为主路径；会话级工作区与归档等行为由 **`matmaster.core.playground`**（`matmaster/core/playground.py`）与 `AgentRunService` 协同完成。

历史上曾存在独立的 **`playground/mat_master`** 本地 Web（Next + 另一 FastAPI 进程）；该目录树已从本仓库移除。修改会话、流式推送、鉴权或 agent 执行路径时，以 `src/` / `app.py` / Worker 为准。说明与入口见根目录 [README-zh.md](README-zh.md)。

**`run_agent_sync`：** 当前以 `AgentRunService.run_agent_sync`（`src/services/agent_run_service.py`）为准。若检出中包含 [docs/mat_master/run_agent_sync_comparison.md](docs/mat_master/run_agent_sync_comparison.md)，其中可能保留与历史本地 Web 栈的对照说明。

## RuntimePorts、run_meta 与 HookExecutor 边界

- `run_meta` 只承载临时被动运行 metadata，例如 `task_id`、`active_skills`、`attachment_manifest`。不得向 `run_meta` 注入服务能力 callback、sink、factory、barrier 或外部 service 对象。
- `session_id` 是 `PlaygroundContext.session_id` 顶层显式字段，不得通过 `run_meta` 流通；当前轮图片输入属于 `TurnInput.attachments.images` / `image_detail`，不得恢复 `run_meta["current_user_images"]` 路径。
- 服务能力 callback 必须通过 `PlaygroundContext.runtime_ports` / `AgentRuntimeSpec.runtime_ports` 传递。`RuntimePorts` 是窄能力端口，不是 typed 版 `run_meta`。
- `RuntimePorts` 及其子端口不得包含 `extra`、`metadata`、`state`、`context`、`services`、`payload` 或 `dict[str, Any]` 这类兜底字段；也不得用允许任意 extra fields 的 typed model 绕过该限制。
- 新增 RuntimePorts 字段前必须说明消费者、调用时机、返回值语义和异常语义。
- `HookExecutor` 专指事件扩展系统，用于 observe/intercept/rewrite 运行过程事件。不得把需要返回业务数据或承担顺序屏障语义的服务端口伪装成 `HookExecutor` handler。

---

## Python 与运行环境

**本项目的 Python 运行时以 uv 管理的环境为准。**

- **运行 / 验证时**：在项目根目录下应使用 **`uv run python`**（或先 `source .venv/bin/activate` 再执行 `python`），不要依赖系统 PATH 下第一个 `python`，以免误用其他环境（如系统 3.9、anaconda）导致行为不一致。
- **示例**：验证导入、跑脚本、跑测试时统一用 uv 环境：
  - `uv run python -c "from matmaster.core.playground import Playground; print('OK')"`
  - `uv run pytest ...`
  - `uv run python app.py` 等。
- **版本约定**：`pyproject.toml` 中 `requires-python = ">=3.11"`；实际开发/CI 使用 uv 安装的版本（如 3.13）。涉及语法或类型注解（如是否保留 `from __future__ import annotations`）时，以 **uv 环境中的 Python 版本** 为准做验证与决策。

---

## 分支与 MR 流程（test → main）

本仓库默认以 **`gitlab/test`** 作为集成/冒烟分支，以 **`gitlab/main`** 作为发布分支。日常开发流程：

1. **从 `gitlab/test` 切分支**开发，例如 `refactor/mlip-skill`；改动直接 MR 到 `test` 跑流水线与联调。
2. **待该 MR 在 `test` 上验证通过后**，再将同一组改动以 **基于 `gitlab/main`** 的新分支向 `main` 提 MR。不要把 test 分支直接改 target 为 main——test 上累积了尚未入 main 的其他改动，会污染 diff。

### test-verified 改动上 main 的标准操作

假设 `gitlab/test` 上的 commit `<SHA>` 已验证通过，要把它搬到 `main`：

```bash
git fetch gitlab main
git checkout -b <name>-main gitlab/main
git cherry-pick --no-commit <SHA>   # 或 cherry-pick 一组 commit
git commit --author="<你的名字> <你的邮箱>" -m "..."   # 按需 reword
git push -u gitlab <name>-main
# 然后到 GitLab UI 新建 MR：source=<name>-main, target=main
```

### 流水线硬约束：新 commit / MR 不得带「其它作者」

`main` 的流水线会拒绝**新提交**里出现额外作者署名，因此：

- **新 commit 消息中不得出现** `Co-Authored-By: ...`、`Made-with: ...` 等 trailer（后者是某些 IDE/agent 会自动塞进去的，需主动清理）。提交前用 `git log -1 --format='%(trailers)'` 核一下，空输出即 OK。
- **新 MR 的描述里不要写** `Co-Authored-By` 等联合作者字段；只写改动摘要、测试说明、关联 issue 即可。
- 以 AI/助手辅助编写的 commit：`Author` 与 `Committer` 都是真人开发者，不加 Co-Authored-By。

**不要改写历史 commit。** `gitlab/main`、`gitlab/test` 及各已存在分支的历史 commit 即便带 `Co-Authored-By: Claude ...` 之类的 trailer，也**保持原样**——那是流水线规则收紧之前的遗留，改写会破坏 SHA 链、影响他人分支 rebase 与回溯。约束只针对**本次及以后新产生的 commit**。

如果要从带 Co-Authored-By 的 test commit 搬改动到 main：**不要** `git rebase -i` 去 reword 原 commit；而是在 **新建的 main 基线分支**上 `cherry-pick --no-commit` 后用 `git commit -m "..."` 重新写一条干净的新 commit（cherry-pick 保留原作者，author 字段不用改）。原 test commit 保留不动。

### 约定

- test 分支是"集成/冒烟"分支；main 分支仅接受无额外作者 trailer 的新 commit。
- 不改写任何已 push 的历史 commit；只对"即将新建"的 commit 负责。
- 把测试通过的改动搬到 main 时保留**原作者**；若需要合并多条 commit 也按本节流程在**新分支**上重新组织。

---

## 代码风格（pre-commit 强制）

以下规则由 `.pre-commit-config.yaml` 定义，本地 commit 与 CI 合入 main 时均强制执行。

1. **格式化（Black）**：行宽 88 字符，保留原始引号风格（`--skip-string-normalization`）；其余缩进、空行、尾逗号等遵循 black 默认规则。
2. **Import 排序（isort `--profile black`）**：分组顺序为 标准库 → 第三方 → 本地，组间空一行；使用 black 兼容模式，二者不冲突。
3. **死代码清理（autoflake + pyupgrade）**：自动删除未使用的 import 和变量；自动将旧式语法升级为现代写法（如 `format()` → f-string）。
4. **静态检查（flake8 + flake8-bugbear）**：`max-line-length=88`，忽略 E501（行长由 black 管控）、E203（black 切片格式）、B008（FastAPI `Depends()` 等依赖注入）、B036；其余规则全部生效。
5. **文件卫生**：单文件不超过 1000 行；自动修正行尾空白、文件末尾换行、混合换行符和 BOM；JSON 自动格式化并保留非 ASCII 原文。

---

## 其他约定

- **工作区边界**：在本仓库内协作时，对代码的修改**仅限** `matmaster-evo` 仓库本身；**不得**擅自编辑同级其它仓库（例如 `../matmaster-tools-server`）。若功能依赖其它服务，在本仓库实现调用与配置，并用说明文档或对方仓库的 PR 完成对接。持久化约束见 `.cursor/rules/workspace-boundary.mdc`（Cursor 规则，`alwaysApply`）。
- **配置目录**：产品主配置与 MCP JSON 位于 `config/`。`ConfigManager` / `get_config_manager()` 未指定 `config_dir` 时默认加载该目录下的 `config.yaml`。
- **维护本文件**：在对话或开发过程中，若产生新的、值得固化的约定或逻辑（如架构决策、命名/用法约定、废弃说明等），应适时补充到 AGENTS.md，便于后续遵守。
- **多实例与 Redis**：API 与 Worker 均可多实例部署。跨实例的协调一律使用 Redis（或其它共享存储）；事件顺序、用户回复、run 归属与存活判断等均依赖 Redis，不依赖进程内状态或「请求与执行同进程」的假设。
- **服务重启**：新增或修改功能时需考虑服务重启场景。进程内内存（如 `SESSIONS`）在重启后会清空；若逻辑依赖跨请求的状态（如会话级鉴权、当前 run 所用资源），应区分「需持久化」与「仅当次 run 有效」：前者落库或共享存储，后者可保留在内存，并确保重启后新请求能从 DB/共享存储恢复必要信息继续工作。
- **run_interrupted 与长任务**：API 通过 Redis 的 `session_run_owner` 与 `worker_alive` 判断 run 是否在别的 pod 上。`session_run_owner` 有 TTL（默认 7200s）；若 Worker 在 run 期间不刷新该 key，长任务超过 TTL 后 key 过期，用户刷新页面时 API 会看到 `run_owner=None`、DB 仍为 active，从而误判为 stale 并推送 run_interrupted。因此 Worker 心跳中除刷新 `worker_alive` 外，还需周期刷新当前 session 的 `session_run_owner` TTL（见 `agent_worker._worker_heartbeat_loop` 与 `WorkerRegistryService.refresh_session_run_owner`）。
- **仅 Worker 队列模式**：run 只在 Worker 上执行，不再支持「在 API 进程内执行 run」。请求中的 `mode: 'direct' | 'planner'` 仅表示任务类型，与执行位置无关。发送消息（POST /stream）必须配置 `REDIS_URL`，否则返回 503。
- **Bohrium 远端共享目录**：Bohrium SSH / skill / bash 的远端工作目录默认直接使用项目级共享目录 `/share`；同一 Bohrium `project_id` 下的不同 session 共用该目录，不再默认创建 `/share/workspace/{session_id}`。修改远端 cwd、prompt 提示、文件浏览或下载落盘逻辑时，应遵守这一 project-scoped 语义。
- **Runtime Credential Bridge**：Bohrium 鉴权已统一走 `matmaster/integration/runtime_bridge/` 的凭证桥。所有消费 Bohrium 凭证的模块（`BohriumTool`、`CalculationPathAdaptor`、`job_service`、`bohrium_env`）通过桥解析凭证，优先级为：显式参数 > session/runtime > 环境变量回退。生产环境中凭证由 session 自动注入，`.env` 中设置 `BOHRIUM_ACCESS_KEY` 仅用于本地开发回退。`/share/...` 等远端路径输出需要活跃的远程 session 以执行 upload_directory 同步逻辑；无 session 时 poll 会拒绝远端路径。
- **Bohrium 大文件传输**：builtin `Bohrium(action="submit"|"download")`
  的数据面走独立包 `matmaster_bohrium_transfer`，不再依赖
  `bohrium-sdk`。主项目只保留 path resolution、Bohrium 控制面 API、
  tool result 组装等控制面逻辑。
- **远端 transfer runtime**：Bohrium 远端镜像必须预装
  `matmaster_bohrium_transfer`，Worker 调用
  `python -m matmaster_bohrium_transfer.remote upload-submit --payload-file <payload>` 或
  `python -m matmaster_bohrium_transfer.remote download-results --payload-file <payload>`。
  远端版本不兼容时失败并提示更新镜像，不运行时复制 helper 源码。
- **传输状态**：同会话 resume 状态保存在 `.matmaster/transfers/` 或
  `/share/.matmaster/transfers/`，manifest/payload 必须按 0600 权限写入并
  对 token/access key 做日志脱敏。
- **单文件行数**：若某源文件行数超过 1000 行，应进行重构（拆分为多个模块/子模块、抽取类或函数等），以利于维护与协作。
- **评测模块约定**：`evaluation/` 目录的详细约定（题库格式、字段规则、verify 类型、数据流、编写指南等）统一维护在 [`evaluation/AGENTS_evaluation.md`](evaluation/AGENTS_evaluation.md)。修改评测相关规则时，**必须同步更新该文件**；若通用约定有变更，**必须同步更新本文件**。
- （可在此补充项目的其他通用约定，如测试、提交信息、目录结构等。）
