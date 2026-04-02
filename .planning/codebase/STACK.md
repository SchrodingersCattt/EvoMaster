# Technology Stack

**Analysis Date:** 2026-04-02

## Languages

**Primary:**
- Python 3.13 (pinned in `.python-version`) — All backend code, agent kernel, API, worker
- Requires `>=3.10` per `pyproject.toml`

**Secondary:**
- TOML — Exp definition files (`matmaster/exps/*.toml`)
- YAML — Configuration files (`matmaster_config/config.yaml`, `matmaster_config/llm_config.yaml`)
- Markdown — Skill definitions (`matmaster/skills/lazymcp/mcp-*/SKILL.md`)

## Runtime

**Environment:**
- CPython 3.13 (production image: `registry.dp.tech/public/python:3.13-slim`)
- jemalloc enabled in production via `LD_PRELOAD` (`Dockerfile` line 48)

**Package Manager:**
- uv (installed via pip in Docker; used for venv creation and dependency resolution)
- Lockfile: `uv.lock` present and committed
- All development commands use `uv run` or `.venv/bin/activate`

## Frameworks

**Core:**
- FastAPI `>=0.100.0` — HTTP API framework (`app.py`, `src/apis/`)
- Pydantic (bundled with FastAPI) — All data models, event types, config validation (`matmaster/types/`, `matmaster/config/`)
- Uvicorn `>=0.22.0` — ASGI server (development)
- Gunicorn `>=25.0.3` — Production process manager with UvicornWorker (`Dockerfile`)

**Testing:**
- pytest `>=9.0.2` — Test runner (`[project.optional-dependencies].dev`)
- pytest-asyncio `>=0.24.0` — Async test support; `asyncio_mode = "auto"` in `pyproject.toml`

**Build/Dev:**
- Hatchling — Build backend (`[build-system]` in `pyproject.toml`)
- pre-commit — Git hooks for code quality (`.pre-commit-config.yaml`)

## Key Dependencies

**Critical (Agent Core):**
- `openai` — AsyncOpenAI client for all LLM calls via OpenAI-compatible interface (`matmaster/providers/openai_provider.py`)
- `anthropic` — Anthropic SDK (declared dependency; actual calls route through OpenAI-compatible interface via LiteLLM Proxy)
- `google-genai` — Google GenAI SDK (declared dependency; same routing pattern)
- `mcp` — Model Context Protocol client SDK (`matmaster/mcp/connection.py`); supports stdio, SSE, streamable HTTP transports
- `tiktoken >=0.7.0` — Token counting for context compaction (`matmaster/core/context_compactor.py`)
- `pydantic` — Frozen models for inter-layer data transfer (PlaygroundContext, AgentRuntimeSpec, all 18 event types)

**Infrastructure:**
- `redis >=5.0.0` — Cross-worker pub/sub, agent run queue (BLPOP), stop signals, session state (`src/dao/redis_dao.py`)
- `pymysql >=1.1.2` — MySQL database access via raw SQL, custom DAO pattern (`src/base/base_table.py`, `src/dao/`)
- `paramiko >=4.0.0` — SSH/SFTP for remote Bohrium execution (`matmaster/sessions/ssh.py`, `matmaster/sessions/sftp_pool.py`)
- `httpx >=0.28.1` — Async HTTP client for external API calls
- `aiohttp` — Additional async HTTP support
- `websockets >=11.0` — WebSocket protocol support for SSE/streaming

**File Processing:**
- `python-docx >=1.2.0` — Word document parsing
- `pymupdf` — PDF parsing
- `beautifulsoup4` + `lxml` — HTML parsing
- `markdownify >=0.14.1` — HTML-to-Markdown conversion

**Cloud/Storage:**
- `oss2` — Alibaba Cloud OSS file upload/download (`src/dao/oss_io.py`)
- `bohrium-sdk >=0.15.0` — Bohrium HPC platform SDK
- `bohrium-open-sdk >=1.0.0` — Optional Bohrium Open SDK (`[project.optional-dependencies].bohrium-open`)

**Calculation (Optional `[calculation]` extra):**
- `ase >=3.22` — Atomic Simulation Environment
- `pymatgen >=2024.1` — Materials science Python library
- `dpdata >=0.2.18` — Deep Potential data processing
- `cp2k-input-tools >=0.9.0` — CP2K input generation
- `molcrys-kit` — Molecular crystal toolkit (git dependency from GitHub)

## Development Tools

**Formatting:**
- Black `25.9.0` — Code formatter (`--skip-string-normalization`, `--line-length=88`)
- isort `6.0.1` — Import sorting (`--profile black`)

**Linting:**
- flake8 `7.3.0` + flake8-bugbear — Linting (`--extend-ignore=B008,E501,B036,E203`, `--max-line-length=88`)
- autoflake `2.3.1` — Remove unused imports and variables
- pyupgrade `3.20.0` — Upgrade Python syntax to modern idioms

**Pre-commit Hooks (`.pre-commit-config.yaml`):**
- Custom file line count check: `.pre-commit/check_file_lines.py` enforces 1000-line max per file
- pre-commit-hooks `v6.0.0`: large file check, AST check, JSON auto-formatting (`--no-ensure-ascii`), merge conflict detection, trailing whitespace, YAML/TOML/XML validation, AWS credential detection, private key detection
- `no-commit-to-branch`: prevents direct commits to protected branches
- `name-tests-test` with `--pytest-test-first`: enforces test file naming convention

**Type Checking:**
- No mypy or pyright configuration detected. Type annotations used extensively (PEP 604 union syntax, Pydantic models, `@runtime_checkable` Protocols) but not enforced via CI.

## Configuration

**Environment:**
- `.env` file loaded via `python-dotenv` at startup
- `.env.template` documents all required/optional variables (see Integrations for full list)
- YAML configs support `${VAR_NAME}` expansion at load time (`matmaster/config/loader.py`)

**Build:**
- `pyproject.toml` — Package metadata, dependencies, build system, pytest config, hatch wheel targets
- `Dockerfile` — Multi-stage build: `builder` base, `worker` target, `api` target (default)
- `.pre-commit-config.yaml` — Code quality hooks (installed in Docker image for CI)

**Runtime Config Files:**
- `matmaster_config/config.yaml` — Main application config
- `matmaster_config/llm_config.yaml` — LLM profile definitions (model, base_url, api_key, temperature, max_tokens, timeouts)
- `matmaster_config/mcp.yaml` — MCP server connection definitions
- `matmaster/exps/_base.toml` — Base system prompt inherited by all Exp types
- `matmaster/exps/direct.toml`, `matmaster/exps/explore.toml` — Specific Exp configurations

## Platform Requirements

**Development:**
- Python 3.13 (via `.python-version`)
- uv package manager
- MySQL instance (local or remote)
- Redis optional for single-process development
- `.env` file with at minimum one LLM API key

**Production:**
- Docker (multi-target: `api` on port 80, `worker` via `python -m src.worker.agent_worker`)
- MySQL database (mandatory)
- Redis (required for multi-worker deployment: queue, pub/sub, heartbeat)
- Bohrium credentials (for HPC execution)
- OSS credentials (for file upload/download in calculation path)
- Gunicorn + UvicornWorker (`-w 1 --preload`)

**CLI Tool:**
- `mm-devshell` — Interactive dev shell for local agent testing (`matmaster/devshell/cli.py`, registered as `[project.scripts]`)

---

*Stack analysis: 2026-04-02*
