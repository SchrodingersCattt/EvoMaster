# EvoMaster

[English](README.md) | [简体中文](README-zh.md)

EvoMaster is a framework for building scientific agents. It provides MCP tooling, skills, and multi-agent coordination so you can focus on domain logic. The main application in this repository is **MatMaster**, a materials-science agent with a web UI.

---

## MatMaster-Evo

MatMaster is a scientific agent for materials research, with a Next.js frontend and FastAPI backend. Development runs backend and frontend together via a single script.

### Start development (frontend + backend)

From the project root:

```bash
cd playground/mat_master/
bash start_dev.sh
```

Then open the dashboard at `http://<host>:<FRONTEND_PORT>` (default `http://127.0.0.1:50004`). Backend API runs on `BACKEND_PORT` (default `50001`; on Windows/Git Bash the script uses `8000` unless you set `BACKEND_PORT`).

### Environment variables used by `start_dev.sh`

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_PORT` | `50001` (Windows: `8000`) | FastAPI server port. |
| `FRONTEND_PORT` | `50004` | Next.js dev server port. |
| `PUBLIC_HOST` | Host IP or `127.0.0.1` | Host used for API/WS URLs shown to the frontend. Set this when accessing from another machine (e.g. `PUBLIC_HOST=your-host.example.com`). |
| `NEXT_PUBLIC_API_URL` | `http://<PUBLIC_HOST>:<BACKEND_PORT>` | Override the API base URL the frontend calls. |
| `NEXT_PUBLIC_WS_URL` | `ws://<PUBLIC_HOST>:<BACKEND_PORT>/ws/chat` | Set automatically from `NEXT_PUBLIC_API_URL` if not provided. |

**Local run without Redis:** Do not set `REDIS_URL` in `.env`. The backend will start in single-process mode: stop and stream work in-process only; monitor_job suspend/resume across time is disabled. This is enough for local dev and tracemalloc baseline/diff on one process.

---

## Bohrium authentication

MatMaster and calculation MCP tools need Bohrium credentials. Copy the template and fill in your values:

```bash
cp .env.template .env
```

Edit `.env` and set at least:

| Variable | Description |
|----------|-------------|
| `BOHRIUM_ACCESS_KEY` | Access key from Bohrium. In the console: **Personal Center → Access Key** (create or copy). See [Access Key (ak-1, ak-2)](docs/images/ak-1.png) and [ak-2](docs/images/ak-2.png). |
| `BOHRIUM_USER_ID` | Your user ID. In the console: **Personal Center → Account**. See [User ID](docs/images/userID.png). |

Optional for full calculation/storage: `BOHRIUM_PROJECT_ID`, `BOHRIUM_EMAIL`, `BOHRIUM_PASSWORD`. `SERVICE_ENV` selects the Bohrium environment (`prod`, `uat`, `test`); auth is taken from the corresponding site (e.g. https://www.test.bohrium.com/ for `test`).

---

## Project layout

```
EvoMaster/
├── evomaster/           # Core (agent, session, tools, skills, LLM)
├── playground/
│   └── mat_master/      # MatMaster app (frontend + service + start_dev.sh)
├── configs/             # Agent/config YAML
└── docs/                # Documentation
```

---

## CLI (optional)

You can run agents from the command line without the web UI.

**Prerequisites:** `pip install -r requirements.txt` (or `uv sync`). Configure LLM and Bohrium in `.env` and/or in the YAML under `configs/`.

```bash
# Default agent and task
python run.py --agent minimal --task "Your task"

# Custom config
python run.py --agent minimal --config configs/minimal/config.yaml --task "Your task"

# Task from file
python run.py --agent minimal --task task.txt

# Interactive
python run.py --agent minimal --interactive
```

Example with a specific playground config:

```bash
python run.py --agent minimal_multi_agent --config configs/minimal_multi_agent/deepseek-v3.2-example.yaml --task "Describe your task here"
```

---

## Links

- [SciMaster](https://scimaster.bohrium.com/chat/)
- [Bohrium](https://www.bohrium.com/)
