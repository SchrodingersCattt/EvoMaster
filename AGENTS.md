# AGENTS.md

`matmaster-evo` 项目级约定，供 AI 编程助手遵循。文档、注释与代码冲突时以 `matmaster/`、`src/`、`config/` 及对应测试为准。

## 关键事实

- 顶层**没有** `evomaster/` 目录。`playground/` 仅剩空壳。历史引用（`evomaster/adaptors/calculation/path_adaptor.py`、`evomaster/env/bohrium.py`、`playground/mat_master/`、`run.py`、`matmaster/integration/runtime_bridge/`）一律视为迁移残留。
- 核心包：`matmaster/`（Agent 运行时）、`src/`（FastAPI + Worker + DAO 平台层）、`config/`（运行时配置）、`tests/`、`evaluation/`、`docs/`。
- 入口：
  - 平台 API：`uv run python app.py`（入口 `app.py`）
  - Worker 守护：`uv run python -m src.worker.agent_worker`（入口 `src/worker/agent_worker.py`）
  - 终端调试：`uv run python -m matmaster.devshell`（入口 `matmaster/devshell/__main__.py`）
- 生产主链路：`src/services/agent_run_service.py` → `matmaster/core/playground.py` → `matmaster/core/exp.py` → `matmaster/core/agent.py` → `matmaster/integration/fanout.py`。

## 执行分层

- **`Playground`**（`matmaster/core/playground.py`）：创建 `run_dir/workspaces/{task_id}`、打开 session（local / ssh）、初始化日志与 cache，产出 frozen `PlaygroundContext`。
- **`Exp`**（`matmaster/core/exp.py`）：`assemble(ctx)` 是纯数据变换产出 `AgentRuntimeSpec`；`build_runtime()` 才分配资源（`ToolRegistry`、`ToolCatalog`、`LLMProvider`、skills、hooks）并注册清理回调；`run_stream()` 的 `finally` 统一执行清理。
- **`AgentKernel`**（`matmaster/core/agent.py`）：generator-first 异步事件循环，`_run_items()` yield `_KernelItem`（事件或终止项）。终止条件：LLM 无 `tool_calls`、`max_turns` 到达、`CancellationToken` 触发。循环内在 turn 开头、stream chunks、retry backoff、serial tool call 之间检查取消。
- **`RunEventFanout`**（`matmaster/integration/fanout.py`）：对每次 run 建一次。派发顺序：await SSE handler → await 额外 handler → `asyncio.create_task` 丢 persistence handler。`drain_and_close()` 必须在 `Exp.run_stream()` 的 `finally` 中等待后台写入完成。

**不变量：**

- `PlaygroundContext` 与 `AgentRuntimeSpec` 构造后不可变（frozen dataclass / `ConfigDict(frozen=True)`）。可变状态集中在 `_KernelState` 等内部对象，生命周期绑定到单次 `_run_items()`。
- 跨 turn 消息历史只追加，不原地修改。
- 任何阻塞 > 250ms 的 I/O **必须**把 `CancellationToken` 传进去或间歇检查。

## API / Worker 架构

**生产只支持 Worker 队列模式**。API 进程不在自己的进程里跑 run。

- **API**（`app.py`，多实例）：处理 HTTP / SSE、接 `/chat/send` 后写入 Redis 队列、订阅 Redis stream 事件转发前端、维护 session 状态与 `run_owner` / `run_interrupted` 判定。
- **Worker**（`src/worker/agent_worker.py`，多实例）：从 Redis 队列 `BLPOP` 拉任务、调用 `run_agent_sync`、`publish` 事件到 Redis、写 DB、周期刷新 `worker_alive` 与 `session_run_owner` TTL。
- **协调只走 Redis**。不得假设「处理 HTTP 请求的进程」与「跑该会话 agent 的进程」是同一进程。
- `POST /stream` 必须配 `REDIS_URL`，否则返回 503。`mode: 'direct' | 'planner'` 只是任务类型，与执行位置无关。
- **`run_interrupted` 心跳规则**：`session_run_owner` 默认 TTL 7200s。Worker 心跳循环除刷新 `worker_alive` 外，**必须**周期刷新当前 session 的 `session_run_owner` TTL（见 `src/worker/agent_worker.py::_worker_heartbeat_loop`、`src/services/worker_registry_service.py::refresh_session_run_owner`），否则长任务会被误判为 stale 并推送 `run_interrupted`。
- **服务重启**：进程内内存（如 `SESSIONS`）会清空。跨请求状态必须区分「需持久化」（落 DB / Redis / OSS）与「仅当次 run 有效」。

## 工具运行时 v2

旧版 registry 直跑模式已废弃，新代码不要走老路径。

- `ToolCatalog`（`matmaster/tools/tool_catalog.py`）：`ToolRegistry` 之上的 versioned 门面，把 `Tool` 编译成 `ToolInstance`。`register_overlay()` 递增版本触发重编译。
- `FullToolRunner`（`matmaster/core/tool_runner.py`）执行顺序：catalog lookup → `StructuralValidation` → `CapabilityPolicy` → fast path → `ToolScheduler` → executor → release。
- **所有层的失败必须包成 `ToolResult`**（`matmaster/tools/tool_result.py`），在 `meta["layer"]` 标明来源。不要裸抛异常跨 runner 层。
- 发 LLM 消息前必须经 `matmaster/types/message_normalization.py::normalize_and_validate_openai_messages`（同时做 role/content shape 与 tool-turn 配对校验）。
- 新增内置工具：`matmaster/tools/builtin/{name}_tool.py`。
- 新增 MCP 包装工具：skill 放 `matmaster/skills/lazymcp/{server}/`，由 `matmaster/tools/lazy_mcp.py::LazyMCPTool` 按需连接。
- 注册路径始终走 `ToolRegistry.register()` 再由 `ToolCatalog` 编译，不要绕过 Catalog 直接构造 runner。
- `matmaster/tools/tool_registry.py` 仍存在（部分测试直接构造 `ToolRegistry`），属于迁移过渡期现状，不要在新代码里延续这种用法。

## Skills、Exp 配置与 MCP

- Exp 定义：`matmaster/exps/*.toml`（`_base.toml` 是共享基础 system prompt，`direct.toml` / `explore.toml` 继承）。字段映射到 `matmaster/config/exp.py::ExpConfig`。
- Skill 注册：`matmaster/skills/registry.py` 全局注册与懒加载。每个 MCP server 在 `matmaster/skills/lazymcp/{server}/` 有描述性 skill 文件。
- MCP server 注册表：`config/mcp.yaml`（server 名、tool 过滤、executor 绑定）。MCP endpoint URL：`config/mcp_config.{env}.json`，按 `SERVICE_ENV` 加载。
- MCP 连接：`matmaster/mcp/connection.py`（stdio / SSE / streamable HTTP），默认 `MCP_CONNECT_TIMEOUT=15s`。MCP 工具默认执行超时 120s，在 `matmaster/tools/lazy_mcp.py` 调整。
- 配置格式：per-agent `tools: { builtin, mcp }`，**不**用顶层 `agent`，**不**用旧的 `enable_tools`。Skills 对外仅导出 `Skill` 类型。

## Bohrium 集成

Bohrium 相关代码集中在 `matmaster/bohrium/`：

- `credentials.py`：凭证归一化、`credentials_from_env()`、`build_bohrium_context()`
- `runtime.py`：Runtime 句柄创建、attach/detach
- `executor.py` / `storage.py`：executor（dispatcher / local）与 storage 配置构造
- `endpoints.py` / `paths.py`：Bohrium API endpoint 与远端路径规范
- `upload.py` / `oss.py` / `artifacts.py`：远端上传、OSS 对象键、artifact 跟踪
- `env.py`：executor 环境变量注入

**角色**：本仓库是 MCP 客户端 / Agent 侧，与 [dptech-corp/bohr-agent-sdk](https://github.com/dptech-corp/bohr-agent-sdk) 的 `CalculationMCPServer` 交互。本仓库负责把 `executor`、`storage` 与业务参数写入 CallTool `arguments`；Server 侧用 `init_executor(executor)` / `init_storage(storage)` 执行任务，不感知鉴权来源。

**约束：**

- 凭证优先级：显式参数 > session/runtime > 环境变量回退。生产凭证由 session 注入；`.env` 的 `BOHRIUM_ACCESS_KEY` 仅用于本地开发回退。
- `executor.type == "dispatcher"`：注入 `machine.remote_profile` 与 `resources.envs`（如 `BOHRIUM_PROJECT_ID`）。
- `executor.type == "local"`：只注入 `executor.env` 下的 `BOHRIUM_ACCESS_KEY` / `BOHRIUM_PROJECT_ID`，供 bohr-agent-sdk 的 LocalExecutor 使用（`matmaster/bohrium/env.py`）。
- Executor 模板来源：`config/mcp.yaml` 的 `calculation_executors[server_name].executor` 或 `executor_map[tool_name]`。未出现在 `calculation_executors` 的 server 不注入 executor，仅在 `calculation_servers` 中时才注入 storage。
- `path_params_by_tool`（可选）：`calculation_executors[server_name].path_params_by_tool` 把**远程工具名**映射到需按输入工件处理的 selector 列表，支持 `targets[].model_path` 这类嵌套 selector。selector 会对照解引用后的 schema 校验，拼写错误要尽早失败。未配置时走 schema → docstring → `*_path` 三层检测。
- OSS 对象键格式：`{prefix}/{uuid}/{原始文件名}`，使 HTTPS URL 最后一截与本地 basename 一致。
- 远端工作目录：Bohrium SSH / skill / bash **默认直接使用项目级共享目录 `/share`**，同一 `project_id` 下不同 session 共用，**不再创建 `/share/workspace/{session_id}`**。修改远端 cwd、prompt、文件浏览、下载落盘逻辑时必须遵守 project-scoped 语义。
- `/share/...` 等远端路径输出需要活跃远程 session 执行 `upload_directory` 同步；无 session 时 poll 拒绝远端路径。
- 修改 executor/storage 结构或凭证注入时，需交叉验证 bohr-agent-sdk 的 `src/dp/agent/server/calculation_mcp_server.py` 与 `executor/`、`storage/` 实现。

## 异常处理

应用全局有统一 error handler，各层异常应向上抛出。

- **DAO 层**：不要 `try/except` 捕获并吞掉异常。禁止写 `except ...: logger.error(...); return False/0`，上层无法区分「业务无数据」与「数据库错误」。
- **Service 层**：默认让异常向上抛；确有降级需求（外部 API 不可用时返回默认值）才按需捕获，写清原因。
- **Tool 执行链路**：失败包成 `ToolResult`，在 `meta["layer"]` 标明来源，不要跨 runner 层裸抛。
- **Kernel 异常**：在 `Exp.run_stream()` 的 `finally` 兜底。cancellation 走 `_KernelStopRequested` / `asyncio.CancelledError` → cancelled terminal event，不与业务异常混用。
- **API 层**：业务异常继承 `src/utils/exceptions.py::BaseErrorResponse`，配 `BaseResponse(code, msg, data)` 信封。
- **自定义异常**：集中 `matmaster/types/errors.py`；领域相关放 `matmaster/{domain}/errors.py`。异常对象可挂 `retryable` / `error_category` / `attempts` 元数据（见 `LLMError`）供 provider 重试决策。

## Python 环境

- 运行时以 uv 管理的环境为准。**始终用 `uv run python`**（或 `source .venv/bin/activate`），不要依赖系统 PATH 下第一个 `python`。
- `pyproject.toml` 的 `requires-python = ">=3.11"`。实际版本由 `.python-version` / `uv.lock` 锁定（当前 3.13）。Black `target-version=py313`，pyupgrade `--py311-plus`。
- 类型注解：文件开头 `from __future__ import annotations`；用 `X | None`、`list[T]`、`dict[K, V]`；禁用 `Optional[X]`、`typing.List`。
- 常见命令：`uv run pytest tests/matmaster/core`、`uv run python app.py`、`uv run python -m matmaster.devshell`。

## 代码风格（pre-commit 强制）

`.pre-commit-config.yaml` 在本地 commit 与 CI 均强制：

- **Black**：`--line-length=88 --skip-string-normalization --target-version=py313`
- **isort**：`--profile black`，分组：标准库 → 第三方 → 本地，组间空行
- **pyupgrade**：`--py311-plus`
- **autoflake**：删除未用 import / 变量
- **flake8 + flake8-bugbear**：`max-line-length=88`；忽略 `E501`、`E203`、`B008`（FastAPI `Depends()`）、`B036`
- **单文件 ≤ 1000 行**（`.pre-commit/check_file_lines.py`）。超出必须重构。
- **测试命名**：`test_*.py`（`name-tests-test --pytest-test-first`）。豁免列表见 `.pre-commit-config.yaml`。
- **禁止提交凭证**。`detect-aws-credentials`、`detect-private-key` 钩子会阻断。`.env` 已在 `.gitignore`。

## 测试

- 框架：`pytest >= 9.0.2` + `pytest-asyncio >= 0.24.0`。`asyncio_mode = "auto"`，async 测试函数**不必**加 `@pytest.mark.asyncio`。
- 结构严格镜像源码：`matmaster/core/agent.py` → `tests/matmaster/core/test_*.py`。
- Fixtures：
  - 全局：`tests/conftest.py`（`MockAsyncLLMProvider`、`MockAsyncTool`，Protocol 最小实现）
  - 模块级：`tests/matmaster/{module}/conftest.py`（如 `tests/matmaster/core/conftest.py::build_mock_spec`）
  - 工厂函数：`make_tool_call`、`build_mock_spec`、`_make_topology`
- **要 mock**：LLM provider、外部 API、session、有副作用的 I/O。
- **不要 mock**：`ToolResult`、`LLMResponse`、`ToolCallData` 等核心领域对象、pure function、Pydantic 模型——用真实实例测试。
- 运行：`uv run pytest [path]` / `uv run pytest -k "name"` / `uv run pytest -v`。
- 覆盖率未启用门禁。`src/services/agent_run_bohrium.py`、`src/services/sessions_service.py`、`src/services/stream_service.py` 缺服务层测试，新增相关逻辑需补 `tests/services/test_*.py`。

## `docs/` 严禁 git 提交

`docs/` 下任何文件**禁止**通过 git commit 入库。该目录为本地工作草稿，不是仓库的一部分。

- 不要 `git add docs/...`
- 不要 `git add -A` / `git add .` 间接包含它
- `.gitignore` 或 deny-list 若未盖住 `docs/`，仍然不要提交

## 迁移残留对照

下列引用在旧文档/注释中可能出现，**不是当前入口**：

- `evomaster/adaptors/calculation/path_adaptor.py` → 已下沉到 `matmaster/bohrium/` + `matmaster/tools/lazy_mcp.py` 的 schema/docstring/`*_path` 三层检测；MCP 参数注入走 `config/mcp.yaml::path_params_by_tool`
- `evomaster/env/bohrium.py` → `matmaster/bohrium/env.py`
- `playground/mat_master/` Web 栈 → 已删除，走 `app.py` + `src/` + `src/worker/agent_worker.py`
- `run.py` 顶层入口 → 已删除，用 `app.py` / `src/worker/agent_worker.py` / `python -m matmaster.devshell`
- `matmaster/integration/runtime_bridge/` → 不存在。Bohrium 凭证桥在 `matmaster/bohrium/credentials.py` + `runtime.py`
- 顶层 `agent` / `enable_tools` 配置 → 改用 `agents.{name}` + per-agent `tools: { builtin, mcp }`
- 旧 `tool_registry` 直跑模式 → 改用 `ToolCatalog` + `FullToolRunner`

## 其他约定

- **配置目录**：`ConfigManager` / `get_config_manager()` 未指定 `config_dir` 时默认加载 `config/config.yaml`。
- **多实例协调**：跨实例状态一律走 Redis（或其它共享存储），禁止依赖进程内状态或同进程假设。
- **Checkpoint 兼容性**：context compaction 的 checkpoint 序列化受 `src/services/history_checkpoint_codec` 严格校验。修改消息序列规则或 compactor 状态结构时必须考虑反序列化回退，旧 checkpoint 不应 load 崩溃。
- **日志脱敏**：`logger.warning(..., exc_info=True)` 是默认写法。`password` / `token` / `api_key` / `secret` 字段走现有 redaction；**不要**把 LLM prompt 或 tool 全量 schema 打到 DEBUG 日志。
- **评测模块**：`evaluation/` 的详细约定在 [`evaluation/AGENTS_evaluation.md`](evaluation/AGENTS_evaluation.md)。修改评测规则**必须**同步该文件；通用约定变更**必须**同步本文件。
- **维护本文件**：对话或开发中产生新的值得固化的约定（架构决策、命名约定、废弃说明）应及时补入。**代码与本文件冲突时以代码为准**，并立即更新本文件。
