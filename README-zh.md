# EvoMaster

[English](README.md) | [简体中文](README-zh.md)

EvoMaster是用于构建科学智能体的框架，提供MCP工具、技能与多智能体协作能力，便于专注领域逻辑。本仓库中的主要应用为**MatMaster**，面向材料科学的智能体，带Web前端。

---

## MatMaster-Evo

MatMaster 是面向材料研究的科学智能体。本仓库中的 **平台 API** 为 FastAPI（根目录 `app.py` + `src/`）；对话链路按 **Redis + Worker** 设计（见 `AGENTS.md`「服务架构」）。

### 平台 API（入口）

| | **平台 API（`src/` + 根目录 `app.py`）** |
|------|-------------------------------------------|
| **用途** | HTTP：会话落库、SSE、经 Redis 将任务交给 Worker。 |
| **典型入口** | `uv run python app.py`（默认 **8000**）。 |
| **说明** | 生产向部署一般为 API 与 Worker 多进程/多实例。 |

历史上曾存在的 **`playground/mat_master`**（独立 Next + FastAPI 本地栈）已从本仓库 **移除**。会话侧 Agent 行为仍通过 `matmaster.core.playground`（`matmaster/core/playground.py`）由平台 API 与 Worker 使用。若检出中包含该文档，关于另一套 `run_agent_sync` 的对照说明见 [docs/mat_master/run_agent_sync_comparison.md](docs/mat_master/run_agent_sync_comparison.md)。

### Agent DevShell（命令行）

不启动平台 HTTP 时，可用 `pyproject.toml` 中的 **`mm-devshell`** 做交互 REPL 或单次运行：

```bash
uv sync
uv run mm-devshell repl --workdir ./workspace --log-dir ./logs
# 或：uv run mm-devshell run --workdir ./workspace --log-dir ./logs -p "你的提示"
```

鉴权仍使用**仓库根目录**的 `.env`。更多参数见 `mm-devshell --help`（如 `--exp`、`--config`）。

**未配置 Redis：** 若未设置 `REDIS_URL`，部分聊天入队能力可能不可用（503）；全栈联调请按 `AGENTS.md` 配置。

---

## Bohrium鉴权

MatMaster及计算类MCP工具需要Bohrium鉴权。请复制环境变量模板并填写：

```bash
cp .env.template .env
```

在`.env`中至少配置：

| 变量 | 说明 |
|------|------|
| `BOHRIUM_ACCESS_KEY` | Bohrium访问密钥。控制台路径：**个人中心 → Access Key**（创建或复制）。图示见[Access Key (ak-1, ak-2)](docs/images/ak-1.png)、[ak-2](docs/images/ak-2.png)。 |
| `BOHRIUM_USER_ID` | 用户ID。控制台路径：**个人中心 → 账号**。图示见[User ID](docs/images/userID.png)。 |

完整使用计算/存储时还可配置：`BOHRIUM_PROJECT_ID`、`BOHRIUM_EMAIL`、`BOHRIUM_PASSWORD`。`SERVICE_ENV`指定Bohrium环境（`prod`/`uat`/`test`），鉴权信息从对应站点获取（如test环境为 https://www.test.bohrium.com/）。

---

## 项目结构

```
EvoMaster/
├── evomaster/           # 核心库（agent、session、tools、skills、LLM）
├── matmaster/           # MatMaster 适配层、实验 TOML、打包技能
├── src/                 # 平台 API、DAO、服务、Worker
├── config/              # MatMaster 配置与 mcp_config*.json
└── evaluation/          # 题库与评测流水线（见 evaluation/README_CN.md）
```

---

## CLI（可选）

智能体命令行入口见上文 **Agent DevShell**（`mm-devshell`）。批量评测与脚本见 `evaluation/scripts/` 及 `evaluation/README_CN.md`。

---

## 链接

- [SciMaster](https://scimaster.bohrium.com/chat/)
- [Bohrium](https://www.bohrium.com/)
