# EvoMaster

[English](README.md) | [简体中文](README-zh.md)

**EvoMaster** is a lightweight open-source framework for building scientific research agents. This repository includes **MatMaster**, an LLM-based agent for alloy design, as described in our paper on INVAR alloy discovery.

MatMaster brings together literature search, composition optimization (DART genetic algorithm), computational materials tools (DPA machine learning potentials), and structured writing — all accessible through natural-language interaction, with no coding required.

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/SchrodingersCattt/EvoMaster.git
cd EvoMaster
pip install -e .
```

Or with [uv](https://github.com/astral-sh/uv):

```bash
uv sync
```

### 2. Configure

Copy the environment template and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env` with at minimum:

| Variable | Description |
|----------|-------------|
| `LITELLM_PROXY_API_BASE` | Your LLM API base URL (OpenAI-compatible; or use `OPENAI_API_BASE` directly). |
| `LITELLM_PROXY_API_KEY` | Your LLM API key. |

For Bohrium job submission (remote GPU/CPU compute):

| Variable | Description |
|----------|-------------|
| `BOHRIUM_ACCESS_KEY` | Bohrium access key — Personal Center → Access Key. |
| `BOHRIUM_PROJECT_ID` | Your Bohrium project ID. |
| `BOHRIUM_USER_ID` | Your Bohrium user ID. |

For OSS file upload (needed by calculation MCP tools):

| Variable | Description |
|----------|-------------|
| `OSS_ENDPOINT` | Aliyun OSS endpoint (e.g. `https://oss-cn-zhangjiakou.aliyuncs.com`). |
| `OSS_BUCKET_NAME` | Your OSS bucket name. |
| `OSS_ACCESS_KEY_ID` | Aliyun AccessKey ID. |
| `OSS_ACCESS_KEY_SECRET` | Aliyun AccessKey secret. |

### 3. Configure MCP servers

```bash
cp configs/mat_master/mcp_config.example.json configs/mat_master/mcp_config.json
```

Edit `mcp_config.json` to point to your MCP server instances, or set the corresponding environment variables (e.g. `MAT_SN_MCP_URL`, `MAT_COMPDART_MCP_URL`). See `configs/mat_master/mcp_config.example.json` for the full list of servers and their descriptions.

---

## Running MatMaster

### Web UI (recommended)

Start the local web interface (FastAPI backend + Next.js frontend):

```bash
cd playground/mat_master/
bash start_dev.sh
```

Then open `http://127.0.0.1:50004` in your browser.

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_PORT` | `50001` | FastAPI backend port. |
| `FRONTEND_PORT` | `50004` | Next.js frontend port. |
| `PUBLIC_HOST` | Auto-detect | Host for API/WS URLs (set for remote access). |

### CLI

```bash
# Run a task directly
python run.py --agent mat_master --config configs/mat_master/config.yaml --task "Optimize INVAR alloy composition for low CTE"

# Interactive mode
python run.py --agent mat_master --config configs/mat_master/config.yaml --interactive

# Planner mode (multi-step planning)
python run.py --agent mat_master --config configs/mat_master/config.yaml --mode planner --task "Your task"

# Or use the CLI shortcut
matmaster run ./myproject
```

---

## What MatMaster Can Do

MatMaster implements the agent used in our INVAR alloy design paper. Key capabilities:

| Skill | Description |
|-------|-------------|
| `composition-optimization` | DART genetic algorithm for alloy composition search |
| `deep-survey` | Systematic literature retrieval with evidence collection |
| `lit-data-organizer` | Build structured literature tables from evidence |
| `manuscript-scribe` | Write and assemble research reports |
| `structure-manager` | Generate and validate crystal structures |
| `bohrium-job` | Submit and monitor computational jobs on Bohrium |
| `input-manual-helper` | Generate DFT/MD input files |
| `result-analysis` | Analyze and summarize simulation results |
| `ask-human` | Human-in-the-loop review and approval |

---

## Project Layout

```
EvoMaster/
├── evomaster/              # Core agent framework (loop, tools, MCP, skills, sessions)
├── matmaster/              # MatMaster tool layer (registry, builtins, devshell)
├── playground/
│   └── mat_master/
│       ├── core/           # Agent, planner, callbacks
│       ├── prompts/        # System prompts and tool rules
│       ├── skills/         # Domain skills (see table above)
│       ├── tools/          # Web search, webpage, aissq
│       ├── service/        # Local FastAPI+WebSocket dev server
│       ├── frontend/       # Next.js local web UI
│       └── cli/            # matmaster CLI entry
├── configs/
│   └── mat_master/
│       ├── config.yaml           # Main agent configuration
│       ├── mcp_config.json       # MCP server endpoints (fill in yours)
│       └── mcp_config.example.json  # Template with descriptions
├── evaluation/             # MATTER evaluation framework + question bank
├── docs/                   # Architecture and API documentation
└── .env.example            # Environment variable template
```

---

## Citation

If you use EvoMaster or MatMaster in your research, please cite our paper:

```bibtex
@article{evomaster2025,
  title   = {EvoMaster: A Framework for Evolving Autonomous Scientific Research Agents},
  author  = {TODO},
  year    = {2025},
}
```

---

## Links

- [EvoMaster upstream framework](https://github.com/sjtu-sai-agents/EvoMaster)
- [Bohrium cloud platform](https://www.bohrium.com/)
- [bohr-agent-sdk (MCP server side)](https://github.com/dptech-corp/bohr-agent-sdk)
