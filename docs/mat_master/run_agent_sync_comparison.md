# `run_agent_sync` 双路径对照

生产环境通过 `AgentRunService.run_agent_sync`（`src/services/agent_run_service.py`）执行；本地 MatMaster Web（WebSocket）通过 `_run_agent_sync`（`playground/mat_master/service/server/run_agent.py`）执行。二者共享 `StreamingMatMasterAgent`、`ChatHistoryConverter.events_to_dialog_messages`、`normalize_event_source` 等，但**在会话隔离、落库、Bohrium、OSS、推送策略等处行为不一致**。修改 Agent 行为、事件契约或工具链时，请按本页逐项核对是否需同步另一侧。

---

## 1. 路径与调用方

| 项目 | 生产 | 本地 Web |
|------|------|----------|
| 实现位置 | `AgentRunService.run_agent_sync` | `_run_agent_sync` |
| 典型调用方 | `src/worker/agent_worker.py`、`src/services/stream_service.py`（线程池 / Worker） | `playground/mat_master/service/server/websocket_chat.py` |
| `send_cb` | 可为协程（配合 `loop`）或同步（Worker 无 `loop`） | 经 `asyncio.run_coroutine_threadsafe` 调回主线程 |
| 返回值 | `(run_result, elapsed_ms)`（类型标注仍为历史遗留，以实际为准） | 无（异常向上抛） |

---

## 2. Playground 生命周期与目录

| 项目 | 生产 | 本地 Web |
|------|------|----------|
| 实例策略 | **按 `session_id`** `_get_or_create_playground`，run 结束 `pop` + `pg.cleanup()` + `gc.collect()` | **单例** `state._cached_pg` 复用（启动时在 `playground_init` 中初始化）；否则当场 `get_playground_class` + `setup()` |
| `run_dir` | `_project_root / 'runs' / 'mat_master_web'` | `_runs_dir() / _get_run_id_web()`（沙箱固定在 `playground/mat_master` 下） |
| MCP 连接进度 | `setup()` 前后通过 `event_callback` 发 `mcp_connect` / `mcp_server_status` 等 | 首次完整 `setup()` 时有；复用 `_cached_pg` 时无与生产对称的 MCP 进度序列 |
| 预加载 | `init_playground_sync` 仅预加载模块与目录；真正 `pg` 在首次 run 创建 | `playground_init` 在线程里加载 `_cached_pg` |

---

## 3. 事件 payload 与推送

| 项目 | 生产 | 本地 Web |
|------|------|----------|
| 序号 | 无 `msg_id` | 有递增 `msg_id`（仅本地历史/UI） |
| 关联字段 | `task_id`、`invocation_id`（若有）；`end` 带 `task_completed` | `task_id` 在部分路径由本地生成 |
| 持久化前过滤 | `should_persist_chat_event`（`playground/mat_master/core/run_helpers.py`） | 同上函数；`assistant_state` 仍仅在本地路径先写入历史再 **return**（与生产分支结构不同） |
| **推送跳过逻辑** | `should_skip_push_for_frontend(mode, raw_source, ...)`（同上 `run_helpers`） | **与生产共用同一实现**（含 direct 下非流式 `thought` 不推） |

> **注意**：若在 direct 模式下调试「完整 thought」是否出现在 UI，本地 Web 与生产 SSE 表现可能不同，原因见上表最后一行。

---

## 4. 持久化与多轮历史

| 项目 | 生产 | 本地 Web |
|------|------|----------|
| 事件存储 | `get_chat_events_table().add_event` → DB | `state.SESSIONS` + `persistence._persist_history_event`（本地文件） |
| 历史来源 | DB：`get_session_events` → `trim_events_for_dialog_history`（`playground/mat_master/core/dialog_history_helpers.py`） | 内存 history 同样经 `trim_events_for_dialog_history`；去掉条件为最后一条 `User` + `query` |
| 条数上限 | 环境变量 `CHAT_DIALOG_HISTORY_MAX_EVENTS`（默认 500，见 `agent_run_service`） | `state.DIALOG_HISTORY_MAX_EVENTS`（同源环境变量名，默认 500） |
| 孤儿 tool 调用 | 无 `_heal_orphaned_tool_calls` | 有 `persistence._heal_orphaned_tool_calls` |

---

## 5. Bohrium / SSH

| 项目 | 生产 | 本地 Web |
|------|------|----------|
| 凭据来源 | `load_run_credentials` + `apply_run_credentials_to_session`（会话/DB） | WebSocket 传入的 `bohrium_access_key`、`bohrium_project_id`（可选） |
| 节点生命周期 | `setup_bohrium_for_run` / `cleanup_bohrium_after_run`（`src/services/agent_run_bohrium.py`：复用表、镜像检查、节点创建/销毁等） | `bohrium_node_service.create_node` → `wait_until_ready` → `attach_ssh_session`；`finally` 中 `detach_session` + `destroy_node` |
| 行为对齐 | 以 `agent_run_bohrium` 为完整生产逻辑 | 本地为简化路径，**不等价**于生产 Bohrium 全流程 |

---

## 6. LLM 与 `StreamingMatMasterAgent` 构造参数

| 项目 | 生产 | 本地 Web |
|------|------|----------|
| 动态换模型 | 支持 `llm_override`、`model_override`（`create_llm`） | 不支持，始终用 base agent 的 LLM |
| `StreamingMatMasterAgent` | 未传 `direct_max_workers`、`rate_limit`（走基类/默认） | 显式传入 `direct_max_workers`、`rate_limit`（来自 `base`） |

---

## 7. `exp.run` 与收尾

| 项目 | 生产 | 本地 Web |
|------|------|----------|
| 调用 | `exp.run(task=task, append_result=False)` | `exp.run(task=task)`（默认 `append_result` 以 Exp 实现为准） |
| 成功收尾 | `use_quota(user_id)`；`_upload_workspace_to_oss`（任务成功后再传一次） | 无配额；无 OSS 上传 |
| 工具后 OSS | `tool_result` 后按防抖 + **目录快照** 决定是否上传 workspace | 无 |
| `end` 事件 | 发 `System`/`end`（含 `elapsed_ms` 等） | 不发与生产对称的 `end`（以 `finish`/`error` 等为主） |
| Redis | `finally` 中 `delete_stop_requested` | 无对称 Redis key |
| 内存 | 清理 `agent` 上 trajectory / prompt 大对象 | 无同一段清理逻辑 |
| 停止状态 | `state._run_stop_events` 等由上层管理 | `finally` 清理 `_run_stop_events`、`_pending_cancel` |

---

## 8. 维护清单（改代码时自问）

1. 是否改动了 **`event_callback` 里写入 DB / 推送 / `tool_result` 分支**？→ 核对生产 `agent_run_service` 与本地 `run_agent.py`。
2. 是否改动了 **direct / planner 下 `thought` 的展示**？→ 改 `playground/mat_master/core/run_helpers.py` 中 `should_skip_push_for_frontend` / `should_persist_chat_event`（两处共用）。
3. 是否改动了 **多轮 `dialog_history` 的裁剪或字段**？→ 改 `playground/mat_master/core/dialog_history_helpers.py` 中 `trim_events_for_dialog_history` / `build_mat_master_discovery_task`（两处共用）。
4. 是否改动了 **Bohrium、SSH、workspace 上传**？→ 以 `agent_run_bohrium` 与 `agent_run_service` 为准；本地仅为子集。
5. 是否改动了 **`StreamingMatMasterAgent` 构造函数参数**？→ 两处 `StreamingMatMasterAgent(...)` 调用是否都要更新。
6. 是否改动了 **ask_human / 确认框超时或队列**？→ 改 `playground/mat_master/core/ask_human_helpers.py`（两处共用）。

---

## 9. 相关文件

| 说明 | 路径 |
|------|------|
| 共用推送/落库判断 | `playground/mat_master/core/run_helpers.py` |
| 首个 agent 配置与 prompt 路径 | `playground/mat_master/core/agent_config_helpers.py` |
| 多轮事件裁剪与 discovery Task | `playground/mat_master/core/dialog_history_helpers.py` |
| ask_human 配置与 ConfirmationManager | `playground/mat_master/core/ask_human_helpers.py` |
| 生产 run | `src/services/agent_run_service.py` |
| 生产 Bohrium | `src/services/agent_run_bohrium.py` |
| 本地 Web run | `playground/mat_master/service/server/run_agent.py` |
| 本地 playground 启动 | `playground/mat_master/service/server/playground_init.py`、`state.py` |
