# Codebase Structure

**Analysis Date:** 2026-03-21

## Directory Layout

```
matmaster-evo/
├── .planning/              # Planning & analysis docs (orchestrator writes here)
├── .pre-commit/            # Pre-commit hook scripts
├── app.py                  # FastAPI app entry point + lifespan + global middleware
├── run.py                  # CLI runner: python run.py --agent {name} --task {desc}
├── pyproject.toml          # Project metadata, dependencies, scripts
├── pytest.ini              # Test configuration
├── ci/                     # CI/CD scripts (GitHub Actions, GitLab CI)
├── configs/                # YAML config templates for each playground
│   ├── mat_master/         # MatMaster agent config (LLM, skills, etc.)
│   ├── minimal/            # Minimal agent config
│   ├── x_master/           # X-Master agent config
│   └── ...
├── docs/                   # Documentation (evomaster/, zh/, images/)
├── evomaster/              # Core framework (from upstream EvoMaster v0.0.1, with local modifications)
│   ├── __init__.py
│   ├── core/               # BasePlayground, BaseExp, Agent lifecycle
│   │   ├── __init__.py
│   │   ├── playground.py   # BasePlayground class + AgentSlots container
│   │   ├── exp.py          # BaseExp: agent execution, trajectory, results
│   │   └── registry.py     # Playground registration / lookup
│   ├── agent/              # BaseAgent, tools, session management
│   │   ├── __init__.py
│   │   ├── agent.py        # BaseAgent: message handling, context, tool calling
│   │   ├── context.py      # ContextManager: dialogue history, truncation
│   │   ├── session/        # Session types (Docker, Local, SSH)
│   │   └── tools/          # Tool system (BaseTool, ToolRegistry, EditorTool, BashTool)
│   ├── adaptors/           # Custom adapters for integrations
│   │   ├── __init__.py
│   │   └── calculation/    # Bohrium executor / MCP calculation adaptor
│   │       ├── path_adaptor.py  # Path params preprocessing, executor injection
│   │       └── oss_io.py        # OSS file upload
│   ├── env/                # Environment configs (Docker, K8s, Bohrium)
│   │   ├── bohrium.py
│   │   ├── docker/
│   │   └── k8s/
│   ├── skills/             # Skill system (RAG, PDF, calculation, etc.)
│   │   ├── __init__.py
│   │   ├── calculation/    # Calculation skill (MCP client)
│   │   ├── mcp-builder/    # MCP tool registry builder
│   │   ├── pdf/
│   │   ├── rag/
│   │   └── skill-creator/
│   ├── utils/              # Shared utilities
│   │   ├── __init__.py
│   │   ├── types.py        # Message, Dialog, Trajectory, TaskInstance, etc.
│   │   ├── llm.py          # LLM provider factory (OpenAI, Anthropic, Google)
│   │   ├── config.py       # ConfigManager
│   │   └── ...
│   └── config.py           # Configuration loader
├── examples/               # Example experiments and playgrounds
│   ├── llm/
│   ├── env/
│   └── multi-agent/
├── playground/             # Domain-specific agent implementations
│   ├── mat_master/         # PRIMARY: MatMaster agent (scientific research + math)
│   │   ├── __init__.py
│   │   ├── README_WEB.md
│   │   ├── start_dev.sh
│   │   ├── core/           # Playground, Agent, Exp implementations
│   │   │   ├── __init__.py
│   │   │   ├── playground.py    # MatMasterPlayground (extends BasePlayground)
│   │   │   ├── agent.py         # MatMasterAgent (extends BaseAgent)
│   │   │   ├── registry.py      # Skill registry loader
│   │   │   ├── constants.py
│   │   │   ├── execution_journal.py  # Tracks step completions
│   │   │   ├── solvers/         # Solver implementations
│   │   │   │   ├── research_planner.py
│   │   │   │   └── research_planner_execution/
│   │   │   └── ...
│   │   ├── service/        # Web service layer (for app.py integration)
│   │   │   ├── stream_agent.py  # StreamingMatMasterAgent (event callbacks)
│   │   │   ├── confirm.py       # ConfirmationManager
│   │   │   └── ...
│   │   ├── skills/         # Skill implementations
│   │   │   ├── __init__.py
│   │   │   ├── research_skill.py
│   │   │   ├── file_skill.py
│   │   │   └── ...
│   │   ├── tools/          # Tool definitions
│   │   │   ├── __init__.py
│   │   │   └── ...
│   │   ├── prompts/        # System & task prompts
│   │   │   ├── README.md
│   │   │   ├── sys/
│   │   │   └── task/
│   │   ├── memory/         # Context/memory management
│   │   ├── evaluation/     # Evaluation framework
│   │   ├── frontend/       # Web UI assets (Next.js, TypeScript)
│   │   │   ├── package.json
│   │   │   ├── src/
│   │   │   └── ...
│   │   ├── cli/            # CLI commands
│   │   └── docs/
│   ├── minimal/            # Minimal agent (simple tasks)
│   │   ├── core/
│   │   └── prompts/
│   ├── minimal_kaggle/     # Kaggle competition agent
│   ├── minimal_multi_agent/# Multi-agent orchestration example
│   ├── minimal_skill_task/ # Skill-specific task example
│   └── x_master/           # X-Master agent (MCP sandbox)
├── scripts/                # Utility scripts (migrations, setup, etc.)
├── src/                    # Backend services & API (web service only)
│   ├── __init__.py
│   ├── apis/               # REST API route handlers
│   │   ├── __init__.py
│   │   ├── api_router.py   # Main router aggregator
│   │   ├── chat_api.py     # Chat session, streaming, workspace endpoints
│   │   └── debug_api.py    # Debug endpoints (memory, process info)
│   ├── base/               # Base classes for API responses and DAOs
│   │   ├── __init__.py
│   │   ├── base_res.py     # BaseResponse[T] (code, msg, data)
│   │   └── base_table.py   # BaseTable context manager for DB
│   ├── dao/                # Data access layer (DB, Redis, OSS)
│   │   ├── __init__.py
│   │   ├── chat_sessions_table.py  # MySQL: session metadata (user_id, status, etc.)
│   │   ├── chat_events_table.py    # MySQL: event logs (thought, tool_call, etc.)
│   │   ├── bohrium_nodes_table.py  # MySQL: Bohrium node allocation tracking
│   │   ├── redis_dao.py            # Redis: run state, queues, locks
│   │   └── oss_io.py               # Aliyun OSS: file upload/download
│   ├── models/             # Pydantic DTOs for API contracts
│   │   ├── __init__.py
│   │   ├── chat.py         # ChatSendRequest, RunStatusData, event models
│   │   ├── health.py       # HealthResponse
│   │   └── root.py         # RootResponse
│   ├── services/           # Business logic layer
│   │   ├── __init__.py
│   │   ├── agent_run_service.py          # Agent execution in ThreadPoolExecutor
│   │   ├── stream_service.py             # SSE queue, confirmation replies
│   │   ├── sessions_service.py           # Session state, access control
│   │   ├── events_service.py             # Event persistence and retrieval
│   │   ├── chat_history.py               # Event → Dialog conversion
│   │   ├── workspace_service.py          # Workspace metadata
│   │   ├── quota_service.py              # Usage quota enforcement
│   │   ├── deploy_state_service.py       # Version tracking (run_interrupted)
│   │   ├── worker_registry_service.py    # Multi-worker coordination
│   │   ├── bohrium_node_service.py       # Bohrium node state
│   │   ├── agent_run_bohrium.py          # Bohrium setup/cleanup per run
│   │   └── user_service.py               # User authentication (API dependency)
│   ├── sql/                # Database schema (SQL migrations)
│   │   ├── __init__.py
│   │   └── *.sql           # DDL files (created_tables, indices)
│   ├── utils/              # Utilities and helpers
│   │   ├── __init__.py
│   │   ├── constant.py     # DB_CONFIG, REDIS_URL, env constants
│   │   ├── exceptions.py   # BaseErrorResponse, 400/403/404/409 subclasses
│   │   ├── logger.py       # LogContext, LoggingConfig
│   │   ├── worker_id.py    # get_worker_id() for multi-worker
│   │   ├── build_info.py   # get_build_version()
│   │   ├── oss.py          # OSS helper functions
│   │   ├── chat_event_source.py  # Event source normalization
│   │   └── feishu_notifier.py    # Feishu (DingTalk) notifications
│   └── worker/             # Worker-side utilities
│       ├── __init__.py
│       └── agent_worker.py # Worker-specific logic
├── tests/                  # Test suite
│   ├── pytest fixtures
│   └── test_*.py files
└── README.md, LICENSE, etc.
```

## Directory Purposes

**`app.py`:**
- Purpose: FastAPI application factory and lifecycle management
- Creates: FastAPI instance with CORS, middleware, exception handlers
- Lifespan: Playground preload, Redis subscriber, worker heartbeat
- Includes: api_router at /api/v1 prefix

**`run.py`:**
- Purpose: CLI entry point for experiment execution
- Parses: --agent {name}, --task {desc}, --config {path}
- Instantiates: Playground by name, Exp, Task
- Outputs: Results to runs/{agent}_{timestamp}/ or specified --run-dir

**`src/apis/`:**
- Purpose: REST API handlers (route definitions)
- Key files:
  - `api_router.py`: Main router; aggregates sub-routers (chat, debug)
  - `chat_api.py`: Session list, stream, stop, share, workspace endpoints
  - `debug_api.py`: Memory snapshots, process info (development)
- Dependency injection: Uses FastAPI Depends() for services, auth

**`src/services/`:**
- Purpose: Business logic orchestration
- Patterns:
  - Service classes with public methods (e.g., AgentRunService.run_agent_sync)
  - Singleton factories (e.g., get_agent_run_service() with @lru_cache)
  - Thread-safe state (locks for _sessions_in_run, _playgrounds dict)
- Key services:
  - **agent_run_service**: ThreadPoolExecutor, Playground lifecycle, event callbacks
  - **stream_service**: SSE queue management, confirmation handling
  - **sessions_service**: Session CRUD, access control, multi-worker coordination
  - **events_service**: Event DB persistence, query by session_id
  - **chat_history**: Convert event log to Dialog for agent context
  - **worker_registry_service**: Worker heartbeat, run ownership tracking

**`src/dao/`:**
- Purpose: Database and external service access with no exception swallowing
- Patterns:
  - BaseTable context manager for auto-connection cleanup
  - Direct pymysql operations (no ORM)
  - Exceptions propagate to service/API layer
- Tables:
  - `evo_chat_sessions`: session_id, user_id, org_id, status, is_shared, last_task_id
  - `evo_chat_events`: session_id, source, type, content, created_at (immutable append-only)
  - `evo_bohrium_nodes`: session_id, node_id, status, ip

**`src/models/`:**
- Purpose: Pydantic request/response validation
- Key models:
  - `ChatSendRequest`: content, task_id, confirmation_reply
  - `RunStatusData`: active_count, queued_count
  - Event models: SessionStatus, Thought, ToolCall, ToolResult, Error, Finish

**`src/utils/`:**
- Purpose: Cross-cutting concerns
  - `constant.py`: DB_CONFIG, REDIS_URL, environment variables
  - `exceptions.py`: Custom exception hierarchy (400/403/404/409)
  - `logger.py`: LogContext per session_id
  - `worker_id.py`: Unique worker identification in multi-worker setups

**`playground/mat_master/`:**
- Purpose: Domain-specific agent for scientific/mathematical research
- Core layer: Playground, Agent, Exp, solvers
- Service layer: StreamingMatMasterAgent (emits events via callback)
- Skills: research, file, tool calling
- Prompts: System instructions, task-specific templates
- Frontend: Next.js web UI (built separately, served by web server)

**`evomaster/`:**
- Purpose: Upstream framework (v0.0.1) with local extensions
- Core: BasePlayground, BaseExp, Agent, message/tool handling
- Agent subsystem: Tools (Bash, Editor, Finish), Sessions (Docker, Local, SSH)
- Skills: RAG, PDF, calculation (MCP), skill-creator
- Adaptors: Bohrium/MCP calculation path adaptor
- Utils: LLM provider factory, types, config loader

## Key File Locations

**Entry Points:**
- `app.py`: Web service startup (FastAPI)
- `run.py`: CLI experiment runner
- `playground/mat_master/core/playground.py`: MatMasterPlayground instantiation point

**Configuration:**
- `configs/mat_master/config.yaml`: Agent config (LLM, skills, solvers, etc.)
- `src/utils/constant.py`: Runtime config (DB, Redis, build info)
- `.env` (not committed): Secrets (API keys, DB password)

**Core Logic:**
- `src/services/agent_run_service.py`: Agent execution, playground lifecycle
- `src/apis/chat_api.py`: HTTP streaming endpoint
- `src/services/stream_service.py`: SSE event queue, confirmation handling
- `src/services/sessions_service.py`: Session state machine, access control
- `playground/mat_master/service/stream_agent.py`: Event emission callbacks
- `evomaster/core/exp.py`: Task execution loop (agent.run → trajectory)

**Testing:**
- `tests/`: Test suite (pytest)
- `pytest.ini`: Test configuration

## Naming Conventions

**Files:**
- Snake_case: `agent_run_service.py`, `chat_events_table.py`
- Suffixes indicate role: `*_service.py`, `*_table.py`, `*_api.py`
- Underscores for internal helpers: `_is_streaming_thought_event()`, `_should_persist_event()`

**Directories:**
- Lowercase, plural when appropriate: `src/apis/`, `src/services/`, `src/utils/`
- Feature namespaces: `playground/{agent_name}/`, `evomaster/{module}/`

**Classes:**
- PascalCase: ChatSessionsService, ChatEventsTable, BasePlayground
- Suffix indicates role: `*Service`, `*Table`, `*Playground`, `*Agent`

**Functions:**
- camelCase (module-level) or snake_case (class methods): get_agent_run_service(), _is_streaming_thought_event()
- Prefix indicates scope: `_` for private/internal; `get_` for factories/singletons

**Constants:**
- UPPER_CASE: DB_CONFIG, REDIS_URL, AGENT_MAX_WORKERS, RUN_ID_WEB

## Where to Add New Code

**New Feature (e.g., new chat capability):**
- Primary code: `playground/mat_master/skills/` (if skill) or `src/services/` (if service logic)
- API route: `src/apis/chat_api.py` (extend @router decorators)
- Model: `src/models/chat.py` (new Pydantic model if new request/response)
- Tests: `tests/test_new_feature.py`

**New Skill or Tool:**
- Implementation: `playground/mat_master/skills/new_skill.py` (extends BaseSkill or BaseTool)
- Registration: `playground/mat_master/core/registry.py` (add to SKILLS dict)
- Prompts (if needed): `playground/mat_master/prompts/` (system or task-specific)

**New Playground/Agent Variant:**
- Directory: `playground/{new_agent}/core/` (copy from playground/minimal/ as template)
- Config: `configs/{new_agent}/config.yaml` (LLM, skills, solvers)
- Playground class: `playground/{new_agent}/core/playground.py` (register via @register_playground)
- Run: `python run.py --agent {new_agent} --task "..."`

**Utilities or Helpers:**
- Shared utilities: `src/utils/` (if API/service layer) or `evomaster/utils/` (if framework layer)
- Infrastructure: `src/services/` (if business logic) or `src/worker/` (if worker-specific)

**Database Schema:**
- SQL files: `src/sql/` (create_*.sql, migrate_*.sql)
- DAO class: `src/dao/{table_name}_table.py` (extends BaseTable)

## Special Directories

**`runs/`:**
- Purpose: Output directory for experiment results
- Generated: Yes (created at runtime via BasePlayground)
- Committed: No (in .gitignore)
- Contents: trajectories.json, logs, workspace snapshots

**`node_modules/` (frontend):**
- Purpose: Frontend dependencies (Next.js, React, etc.)
- Generated: Yes (npm install in playground/mat_master/frontend)
- Committed: No (in .gitignore)

**`__pycache__/` and `.pytest_cache/`:**
- Purpose: Python bytecode and pytest cache
- Generated: Yes
- Committed: No (in .gitignore)

**`.env` and secrets:**
- Purpose: Local environment variables (API keys, DB password, etc.)
- Generated: Yes (copy from .env.template, fill secrets)
- Committed: No (in .gitignore)
- Template: `.env.template` (committed, shows required vars)

**`.planning/codebase/`:**
- Purpose: GSD orchestrator writes analysis documents here
- Generated: Yes (by gsd-map-codebase)
- Committed: Yes (for team reference)
- Contains: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md, etc.

---

*Structure analysis: 2026-03-21*
