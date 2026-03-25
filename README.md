# EvoMaster

[English](README.md) | [简体中文](README-zh.md)

EvoMaster is a framework for building scientific agents. It provides MCP tooling, skills, and multi-agent coordination so you can focus on domain logic. The main application in this repository is **MatMaster**, a materials-science agent with a web UI.

---

## MatMaster-Evo

MatMaster is a scientific agent for materials research, with a Next.js frontend and FastAPI backend. Development runs backend and frontend together via a single script.

### Two backends (entrypoints and ports)

This repo exposes **two HTTP stacks** for MatMaster-related work. Do not mix up ports or treat the local dev server as the production API:

| | **Platform API (`src/` + root `app.py`)** | **Local MatMaster Web (`playground/mat_master/service/server`)** |
|------|-------------------------------------------|-------------------------------------------------------------------|
| **Role** | Production-style integration: DB sessions, SSE, Redis + Worker | Local debugging: Next dashboard under `playground/mat_master`, WebSocket chat, in-memory sessions, fixed workspace |
| **Typical entry** | `uv run python app.py` (default **8000**) | `python -m playground.mat_master.service.server` or `start_dev.sh` (default **BACKEND_PORT=50001**) |
| **Protocol** | REST + SSE (e.g. `/api/v1/.../chat/sessions/...`) | WebSocket `/ws/chat`, etc. |
| **Notes** | Multi-process layout: see **Service architecture** in `AGENTS.md` | See [playground/mat_master/README_WEB.md](playground/mat_master/README_WEB.md) |

The platform API and the local Web stack use **different `run_agent_sync` implementations** (persistence, OSS, Bohrium, event push rules, etc.). See [docs/mat_master/run_agent_sync_comparison.md](docs/mat_master/run_agent_sync_comparison.md).

The **Start development** section below refers to the **local Web** stack (default port 50001).

### Start development (frontend + backend)

From the project root:

```bash
cd playground/mat_master/
bash start_dev.sh
```

Then open the dashboard at `http://<host>:<FRONTEND_PORT>` (default `http://127.0.0.1:50004`). Backend API runs on `BACKEND_PORT` (default `50001`; on Windows/Git Bash the script uses `8000` unless you set `BACKEND_PORT`).

### Start with a custom work directory (CLI)

Install the project in editable mode, then run the full stack (backend + frontend) with a **custom work directory** that is used as a **shared workspace**: the frontend file tree and agent outputs use `work_dir` directly (no per-session `workspaces/` subfolders). Logs and run data also go under `work_dir`. This lets you point MatMaster at any local path (e.g. a manuscript or project folder).

```bash
pip install -e .
matmaster run ./myproject
```

You can run `matmaster` from any directory; authentication still comes from the **repository root `.env`** (no need to copy `.env` into the work dir).

| Option | Default | Description |
|--------|---------|-------------|
| `work_dir` | (required) | Shared workspace directory: file tree, agent outputs, and logs all go here. |
| `--backend-port` | `8000` (Windows) / `50001` (others) | Backend port. |
| `--frontend-port` | `50004` | Frontend port. |
| `--public-host` | Auto-detect | Host for API/WS URLs (e.g. for remote access). |

**With uv:** From the repo, run `uv run matmaster run /path/to/work_dir`. Or activate the project venv (`source .venv/bin/activate` or `.venv\Scripts\activate` on Windows), then run `matmaster run work_dir` from any directory.

### Environment variables used by `start_dev.sh`

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_PORT` | `50001` (Windows: `8000`) | FastAPI server port. |
| `FRONTEND_PORT` | `50004` | Next.js dev server port. |
| `PUBLIC_HOST` | Host IP or `127.0.0.1` | Host used for API/WS URLs shown to the frontend. Set this when accessing from another machine (e.g. `PUBLIC_HOST=your-host.example.com`). |
| `NEXT_PUBLIC_API_URL` | `http://<PUBLIC_HOST>:<BACKEND_PORT>` | Override the API base URL the frontend calls. |
| `NEXT_PUBLIC_WS_URL` | `ws://<PUBLIC_HOST>:<BACKEND_PORT>/ws/chat` | Set automatically from `NEXT_PUBLIC_API_URL` if not provided. |

**Local run without Redis:** Do not set `REDIS_URL` in `.env`. The backend will start in single-process mode: stop and stream work in-process only. This is enough for local dev and tracemalloc baseline/diff on one process.

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
├── configs/mat_master/  # MatMaster YAML + mcp_config*.json
└── docs/                # Documentation
```

---

## CLI (optional)

You can run agents from the command line without the web UI.

**Prerequisites:** `uv sync` (or `pip install -e .`). Configure LLM and Bohrium in `.env` and/or in `configs/mat_master/config.yaml` (and MCP JSON as needed).

```bash
# MatMaster agent (default config under configs/mat_master/)
python run.py --agent mat_master --config configs/mat_master/config.yaml --task "Your task"

# Task from file
python run.py --agent mat_master --config configs/mat_master/config.yaml --task task.txt

# Interactive
python run.py --agent mat_master --config configs/mat_master/config.yaml --interactive

# Planner vs direct mode (MatMaster)
python run.py --agent mat_master --config configs/mat_master/config.yaml --mode planner --task "Your task"
```

---

## Links

- [SciMaster](https://scimaster.bohrium.com/chat/)
- [Bohrium](https://www.bohrium.com/)
