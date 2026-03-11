# EvoMaster

[English](README.md) | [简体中文](README-zh.md)

EvoMaster是用于构建科学智能体的框架，提供MCP工具、技能与多智能体协作能力，便于专注领域逻辑。本仓库中的主要应用为**MatMaster**，面向材料科学的智能体，带Web前端。

---

## MatMaster-Evo

MatMaster是面向材料研究的科学智能体，前端为Next.js，后端为FastAPI。开发时通过一键脚本同时启动前后端。

### 启动前端调试（前后端一体）

在项目根目录下执行：

```bash
cd playground/mat_master/
bash start_dev.sh
```

然后在浏览器访问`http://<主机>:<FRONTEND_PORT>`（默认`http://127.0.0.1:50004`）。后端API端口为`BACKEND_PORT`（默认`50001`；在Windows/Git Bash下脚本会改用`8000`，除非已设置`BACKEND_PORT`）。

### 指定工作目录启动（CLI）

以可编辑方式安装项目后，可用**自定义工作目录**启动前后端：`work_dir` 作为**共享工作区**，前端文件树与 agent 输出都直接使用该目录（不再按 session 建 `workspaces/` 子目录），日志与运行数据也在此目录下。适合指向任意本地路径（如稿件或项目文件夹）。

```bash
pip install -e .
matmaster run ./myproject
```

可在任意目录执行 `matmaster`；鉴权仍使用**仓库根目录的 `.env`**，无需在工作目录再放 `.env`。

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `work_dir` | （必填） | 共享工作区目录：文件树、agent 输出与日志均写入此处。 |
| `--backend-port` | Windows 下 `8000`，其他 `50001` | 后端端口。 |
| `--frontend-port` | `50004` | 前端端口。 |
| `--public-host` | 自动检测 | API/WS 所用主机（如公网访问时设置）。 |

**使用 uv 时：** 在仓库内执行 `uv run matmaster run /path/to/work_dir`；或先激活项目 venv（`source .venv/bin/activate` 或 Windows 下 `.venv\Scripts\activate`），之后在任意目录执行 `matmaster run work_dir`。

### `start_dev.sh`涉及的环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BACKEND_PORT` | `50001`（Windows下为`8000`） | 后端FastAPI端口。 |
| `FRONTEND_PORT` | `50004` | 前端Next.js开发服务端口。 |
| `PUBLIC_HOST` | 本机IP或`127.0.0.1` | 供前端请求的API/WS所用主机。需从其他机器访问时设置（如`PUBLIC_HOST=your-host.example.com`）。 |
| `NEXT_PUBLIC_API_URL` | `http://<PUBLIC_HOST>:<BACKEND_PORT>` | 若设置，则前端使用该地址作为API根地址。 |
| `NEXT_PUBLIC_WS_URL` | `ws://<PUBLIC_HOST>:<BACKEND_PORT>/ws/chat` | 未设置时由脚本根据API地址自动推导。 |

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
├── playground/
│   └── mat_master/      # MatMaster应用（前端 + 服务 + start_dev.sh）
├── configs/             # 智能体/配置YAML
└── docs/                # 文档
```

---

## CLI（可选）

不启动Web时，可通过命令行运行智能体。

**准备：** 安装依赖 `uv sync`（或 `pip install -e .`），并在 `.env` 或 `configs/` 下 YAML 中配置 LLM 与 Bohrium。

```bash
# 默认智能体与任务
python run.py --agent minimal --task "你的任务"

# 指定配置
python run.py --agent minimal --config configs/minimal/config.yaml --task "你的任务"

# 从文件读取任务
python run.py --agent minimal --task task.txt

# 交互模式
python run.py --agent minimal --interactive
```

指定playground配置示例：

```bash
python run.py --agent minimal_multi_agent --config configs/minimal_multi_agent/deepseek-v3.2-example.yaml --task "在此描述任务"
```

---

## 链接

- [SciMaster](https://scimaster.bohrium.com/chat/)
- [Bohrium](https://www.bohrium.com/)
