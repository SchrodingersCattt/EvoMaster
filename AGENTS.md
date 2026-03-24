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
- **path_params_by_tool**：可选。`calculation_executors[server_name].path_params_by_tool` 将 **远程工具名**（如 `submit_run_gromacs`）映射到需在本地解析并上传 OSS 的参数名列表（须出现在 MCP `inputSchema.properties`）。用于 MCP 将 `List[Path]` 暴露为无 `format: path` 的 string 数组、或 submit 工具 description 过短导致无法从 docstring 推断 Path 的场景；未配置时仍依赖 schema / docstring / `*_path` 三层检测。
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
| 代码对应 | 项目内 `evomaster/` 目录对应上游的 `evomaster/`；本仓库另有 `playground/mat_master/`、`src/`、`evomaster/adaptors/` 等自有代码 | 上游 `evomaster/` + `playground/minimal*`、`playground/x_master` 等 |

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

---

## Python 与运行环境

**本项目的 Python 运行时以 uv 管理的环境为准。**

- **运行 / 验证时**：在项目根目录下应使用 **`uv run python`**（或先 `source .venv/bin/activate` 再执行 `python`），不要依赖系统 PATH 下第一个 `python`，以免误用其他环境（如系统 3.9、anaconda）导致行为不一致。
- **示例**：验证导入、跑脚本、跑测试时统一用 uv 环境：
  - `uv run python -c "from playground.mat_master.core.callback import MatToolCallbacks; print('OK')"`
  - `uv run pytest ...`
  - `uv run python app.py` 等。
- **版本约定**：`pyproject.toml` 中 `requires-python = ">=3.10"`；实际开发/CI 使用 uv 安装的版本（如 3.13）。涉及语法或类型注解（如是否保留 `from __future__ import annotations`）时，以 **uv 环境中的 Python 版本** 为准做验证与决策。

---

## 其他约定

- **维护本文件**：在对话或开发过程中，若产生新的、值得固化的约定或逻辑（如架构决策、命名/用法约定、废弃说明等），应适时补充到 AGENTS.md，便于后续遵守。
- **多实例与 Redis**：API 与 Worker 均可多实例部署。跨实例的协调一律使用 Redis（或其它共享存储）；事件顺序、用户回复、run 归属与存活判断等均依赖 Redis，不依赖进程内状态或「请求与执行同进程」的假设。
- **服务重启**：新增或修改功能时需考虑服务重启场景。进程内内存（如 `SESSIONS`）在重启后会清空；若逻辑依赖跨请求的状态（如会话级鉴权、当前 run 所用资源），应区分「需持久化」与「仅当次 run 有效」：前者落库或共享存储，后者可保留在内存，并确保重启后新请求能从 DB/共享存储恢复必要信息继续工作。
- **run_interrupted 与长任务**：API 通过 Redis 的 `session_run_owner` 与 `worker_alive` 判断 run 是否在别的 pod 上。`session_run_owner` 有 TTL（默认 7200s）；若 Worker 在 run 期间不刷新该 key，长任务超过 TTL 后 key 过期，用户刷新页面时 API 会看到 `run_owner=None`、DB 仍为 active，从而误判为 stale 并推送 run_interrupted。因此 Worker 心跳中除刷新 `worker_alive` 外，还需周期刷新当前 session 的 `session_run_owner` TTL（见 `agent_worker._worker_heartbeat_loop` 与 `WorkerRegistryService.refresh_session_run_owner`）。
- **仅 Worker 队列模式**：run 只在 Worker 上执行，不再支持「在 API 进程内执行 run」。请求中的 `mode: 'direct' | 'planner'` 仅表示任务类型，与执行位置无关。发送消息（POST /stream）必须配置 `REDIS_URL`，否则返回 503。
- **单文件行数**：若某源文件行数超过 1000 行，应进行重构（拆分为多个模块/子模块、抽取类或函数等），以利于维护与协作。
- （可在此补充项目的其他通用约定，如测试、提交信息、目录结构等。）
