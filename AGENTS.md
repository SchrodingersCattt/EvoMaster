## 服务架构（API / Worker 分离）

本服务采用 **API 进程与 Worker 进程分离** 的架构，二者可独立扩缩容，通过 Redis 协调。

| 角色 | 职责 | 入口与部署 |
|------|------|------------|
| **API** | 处理 HTTP 请求、SSE 订阅与流式推送；接收 /chat/send 后入队，通过 Redis 订阅 stream 事件并转发给前端；维护 session 状态、run_owner 查询与 run_interrupted 判定。**生产仅支持 Worker 队列模式**：发送消息需配置 `REDIS_URL`，未配置时 POST /stream 返回 503。 | `app.py`（如 uvicorn）；可多实例（多 Pod）。 |
| **Worker** | 从 Redis 队列 BLPOP 拉取任务，执行 `run_agent_sync`；将事件 publish 到 Redis、写 DB；周期刷新 `worker_alive` 与当前 session 的 `session_run_owner` TTL。 | `src/worker/agent_worker.py` 独立进程（Dockerfile 可选 `--target worker`）；可多实例。 |

- **协调方式**：API 与 Worker 之间通过 Redis 通信：任务队列、stream 事件发布/订阅、`session_run_owner` / `worker_alive`、stop 请求等。新增或修改功能时，不得依赖「处理当前 HTTP 请求的进程」与「执行该会话 agent 的进程」为同一进程。

### MatMaster：平台 API 与会话 Playground

MatMaster 的对话与任务执行以 **根目录 `app.py` + `src/`（API）** 与 **`src/worker/agent_worker.py`（Worker）** 为主路径；会话级工作区与归档等行为由 **`matmaster.core.playground`**（`matmaster/core/playground.py`）与 `AgentRunService` 协同完成。

## RuntimePorts、run_meta 与 HookExecutor 边界

- `run_meta` 只承载临时被动运行 metadata，例如 `task_id`、`active_skills`、`attachment_manifest`。不得向 `run_meta` 注入服务能力 callback、sink、factory、barrier 或外部 service 对象。
- `session_id` 是 `PlaygroundContext.session_id` 顶层显式字段，不得通过 `run_meta` 流通；当前轮图片输入属于 `TurnInput.attachments.images` / `image_detail`，不得恢复 `run_meta["current_user_images"]` 路径。
- 服务能力 callback 必须通过 `PlaygroundContext.runtime_ports` / `AgentRuntimeSpec.runtime_ports` 传递。`RuntimePorts` 是窄能力端口，不是 typed 版 `run_meta`。
- `PlaygroundContext` 的 nested 更新统一走 `with_updates(...)`：`metadata={...}` 用于运行事实数据，`runtime_ports={...}` 用于运行能力端口。调用方不得手写 `PlaygroundRuntimePorts(...)` 做全量替换；runtime port patch 由 `with_updates(runtime_ports={...})` 内部通过 `dataclasses.replace` 合并，以避免丢失 sibling ports。
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


## 代码风格（pre-commit 强制）

以下规则由 `.pre-commit-config.yaml` 定义，本地 commit 与 CI 合入 main 时均强制执行。

1. **格式化（Black）**：行宽 88 字符，保留原始引号风格（`--skip-string-normalization`）；其余缩进、空行、尾逗号等遵循 black 默认规则。
2. **Import 排序（isort `--profile black`）**：分组顺序为 标准库 → 第三方 → 本地，组间空一行；使用 black 兼容模式，二者不冲突。
3. **死代码清理（autoflake + pyupgrade）**：自动删除未使用的 import 和变量；自动将旧式语法升级为现代写法（如 `format()` → f-string）。
4. **静态检查（flake8 + flake8-bugbear）**：`max-line-length=88`，忽略 E501（行长由 black 管控）、E203（black 切片格式）、B008（FastAPI `Depends()` 等依赖注入）、B036；其余规则全部生效。
5. **文件卫生**：单文件不超过 1000 行，超过1000行会导致CICD pipeline fail；自动修正行尾空白、文件末尾换行、混合换行符和 BOM；JSON 自动格式化并保留非 ASCII 原文。