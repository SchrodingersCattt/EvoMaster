# EvoMaster

[English](README.md) | [简体中文](README-zh.md)

EvoMaster is a framework for building scientific agents. It provides MCP tooling, skills, and multi-agent coordination so you can focus on domain logic. The main application in this repository is **MatMaster**, a materials-science agent with a web UI.

---

## MatMaster-Evo

MatMaster is a scientific agent for materials research. The **platform API** in this repository is FastAPI (`app.py` + `src/`); chat traffic is designed to run with **Redis + Worker** (see **Service architecture** in `AGENTS.md`).

### Platform API (entrypoint)

| | **Platform API (`src/` + root `app.py`)** |
|------|-------------------------------------------|
| **Role** | HTTP API: DB-backed sessions, SSE streaming, enqueue to Worker via Redis. |
| **Typical entry** | `uv run python app.py` (default **8000**). |
| **Notes** | Production-style layout is multi-process (API pods + worker pods). |

The historical **`playground/mat_master`** tree (separate Next.js + FastAPI local stack) has been **removed from this repo**. Session-scoped agent behavior still uses `matmaster.core.playground` (`matmaster/core/playground.py`) inside the platform API and worker. For historical notes on alternate `run_agent_sync` layouts, see [docs/mat_master/run_agent_sync_comparison.md](docs/mat_master/run_agent_sync_comparison.md) when that file is present in your checkout.

### Agent DevShell (CLI)

For a lightweight agent REPL or one-shot run against a workspace (no platform HTTP server), use the **`mm-devshell`** console script from `pyproject.toml`:

```bash
uv sync
uv run mm-devshell repl --workdir ./workspace --log-dir ./logs
# or: uv run mm-devshell run --workdir ./workspace --log-dir ./logs -p "Your prompt"
```

Load `.env` from the **repository root** as usual. See `mm-devshell --help` for options (`--exp`, `--config`, etc.).

**Local API without Redis:** If `REDIS_URL` is unset, chat enqueue paths may be unavailable (503); adjust `.env` per `AGENTS.md` for full stack testing.

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
├── matmaster/           # MatMaster adapters, exp TOML, packaged skills
├── src/                 # Platform API, DAOs, services, worker
├── config/              # MatMaster YAML + mcp_config*.json
└── evaluation/          # Question bank & eval harness (see evaluation/README_CN.md)
```

---

## CLI (optional)

Use **`mm-devshell`** for agent REPL / single-shot runs (see **Agent DevShell** above). Evaluation and batch flows may use scripts under `evaluation/scripts/`; see `evaluation/README_CN.md`.

---

## Links

- [SciMaster](https://scimaster.bohrium.com/chat/)
- [Bohrium](https://www.bohrium.com/)
