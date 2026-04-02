# Codebase Structure

**Analysis Date:** 2026-04-02

## Directory Layout

```
matmaster-evo/
├── app.py                    # FastAPI application entry point
├── pyproject.toml            # Project metadata and dependencies (uv)
├── pytest.ini                # Pytest configuration
├── Dockerfile                # Production container build
├── Dockerfile.remote         # Remote execution container
├── uv.lock                   # Locked dependency versions
├── .python-version           # Python version pin (3.10+)
│
├── config/                   # Runtime configuration (matmaster-native)
│   ├── config.yaml           # Main config (agents, session, playground, workspace)
│   ├── llm_config.yaml       # LLM provider profiles (model, api_key, base_url)
│   ├── mcp.yaml              # MCP runtime config (path_adaptor, calc executors)
│   ├── mcp_config.json       # MCP server connection config (mcpServers)
│   ├── mcp_config.test.json  # MCP config for test environment
│   └── mcp_config.uat.json   # MCP config for UAT environment
│
├── matmaster/                # Core agent framework package
│   ├── core/                 # Three-layer execution engine
│   ├── types/                # Protocol definitions and data contracts
│   ├── tools/                # Tool system (registry, builtin, MCP, skills)
│   ├── config/               # Typed config loaders (ExpConfig, LLMConfig)
│   ├── providers/            # LLM provider implementations
│   ├── hooks/                # Service-layer hook implementations
│   ├── integration/          # Event routing, SSE, persistence, workspace
│   ├── sessions/             # Session implementations (local, SSH)
│   ├── skills/               # Skill definitions (lazymcp, playground-skills)
│   ├── mcp/                  # MCP connection management
│   ├── adaptors/             # External system adaptors (calculation)
│   ├── devshell/             # Developer CLI/REPL
│   ├── exps/                 # Exp TOML definitions
│   └── cache/                # MCP schema cache files
│
├── src/                      # Web service layer
│   ├── apis/                 # FastAPI route handlers
│   ├── services/             # Business logic and orchestration
│   ├── dao/                  # Data access (MySQL, Redis, OSS)
│   ├── models/               # Pydantic request/response models
│   ├── worker/               # Background worker
│   ├── utils/                # Shared utilities
│   └── sql/                  # SQL scripts
│
├── tests/                    # Test suite
│   ├── matmaster/            # Unit tests for matmaster package
│   ├── fixtures/             # Test fixtures
│   └── utils/                # Test utilities
│
├── evaluation/               # Agent evaluation framework
│   ├── core/                 # Evaluation engine
│   ├── question_bank/        # Test questions by category
│   ├── validators/           # Answer validators
│   └── scripts/              # Evaluation runner scripts
│
├── docs/                     # Documentation
├── scripts/                  # Development/operational scripts
├── ci/                       # CI pipeline helpers
├── runs/                     # Runtime workspace (gitignored)
├── workspace/                # Default workspace root
└── utils/                    # Legacy utilities (env.py, oss_io.py)
```

## Directory Purposes

**`matmaster/core/` -- Three-Layer Execution Engine:**
- Purpose: Core agent execution pipeline (Playground -> Exp -> AgentKernel)
- Key files:
  - `agent.py`: AgentKernel -- pure async execution loop with `_run_items()` generator, `run()`, `run_stream()`
  - `exp.py`: Exp -- config-driven assembly layer with `assemble()`, `build_runtime()`, `run()`
  - `playground.py`: Playground + PlaygroundManager -- workspace/session/logging setup
  - `bus.py`: MessageBus -- asyncio.Queue-backed event transport
  - `hooks.py`: Hook Protocol, BaseHook, EventEmitterHook, run_* helper functions
  - `tool_runner.py`: ToolRunner Protocol, InlineToolRunner (Phase 1 transition)
  - `guard_pipeline.py`: GuardPipeline + LoopDetectionGuard
  - `context_builder.py`: ContextBuilder -- sectioned system prompt assembler
  - `context_compactor.py`: ContextCompactor -- token-aware context compression
  - `config_loader.py`: Legacy config loading bridge

**`matmaster/types/` -- Protocol Definitions and Data Contracts:**
- Purpose: All `@runtime_checkable` Protocol interfaces and frozen Pydantic models
- Key files:
  - `runtime.py`: AgentRuntimeSpec, CompactionConfig, KernelResult, KernelRunResult, AgentRuntime
  - `context.py`: PlaygroundContext, WorkspaceArchivalConfig
  - `events.py`: 18 event types (8 AgentEvent + 10 SystemEvent), BusEvent union
  - `messages.py`: Message hierarchy (SystemMessage, UserMessage, AssistantMessage, ToolMessage), LLMResponse, StreamChunk, ToolCallData
  - `guards.py`: Guard Protocol, GuardContext, GuardResult, RecentCall
  - `llm_provider.py`: LLMProvider Protocol (chat, chat_stream, async context manager)
  - `session.py`: Session Protocol, SessionConfig, LocalSessionConfig, SSHSessionConfig
  - `topology.py`: ToolPlane enum, SessionCapabilities, RuntimeTopology (Tool Runtime v2)
  - `tool_spec.py`: ToolSpec, ResourceClaim, ToolBinding, ToolInstance (Tool Runtime v2)
  - `tool_decision.py`: ToolDecision (Tool Runtime v2)
  - `errors.py`: LLMError
  - `worker_registry.py`: WorkerRegistry Protocol

**`matmaster/tools/` -- Tool System:**
- Purpose: Tool Protocol, registry, builtin tools, MCP tools, skill tools
- Key files:
  - `tool_registry.py`: Tool Protocol + ToolRegistry (flat namespace, source tracking)
  - `tool_result.py`: ToolResult dataclass + `normalize_tool_result()` helper
  - `tool_catalog.py`: ToolCatalog -- Phase 1 facade over ToolRegistry with version tracking
  - `lazy_mcp.py`: LazyMCPConnector + LazyMCPTool -- on-demand MCP server connection
  - `skill_tool.py`: SkillTool -- `use_skill` meta-tool that triggers lazy MCP loading
  - `schema_cache.py`: ToolSchemaCache -- disk cache for MCP tool schemas
  - `builtin/`: Native tool implementations
    - `base.py`: BuiltinTool base class
    - `bash_tool.py`, `read_tool.py`, `write_tool.py`, `edit_tool.py`: File system tools
    - `glob_tool.py`, `grep_tool.py`, `listdir_tool.py`: Search tools
    - `web_search_tool.py`, `web_fetch_tool.py`: Web tools
    - `spawn_tool.py`: Sub-agent spawning tool
    - `read_tracker.py`: ReadTracker for Read-Before-Modify protocol
    - `task/`: Task management tools (create, get, list, update, complete)
    - `monitor_job/`: MonitorJobTool (science-specific job monitoring)

**`matmaster/config/` -- Typed Configuration:**
- Purpose: Config models and loaders for matmaster-native configuration
- Key files:
  - `exp.py`: ExpConfig, ExpToolsConfig, ExpSkillsConfig
  - `llm.py`: LLMConfig, LLMProfile
  - `loader.py`: `load_exp_config()`, `load_llm_config()`, `load_base_system_prompt()`, `list_available_exps()`

**`matmaster/providers/` -- LLM Providers:**
- Purpose: LLM backend implementations
- Key files:
  - `openai_provider.py`: OpenAIProvider -- OpenAI SDK wrapper (via LiteLLM Proxy)
  - `llm_factory.py`: `build_provider()` factory function (profile selection, model override)

**`matmaster/hooks/` -- Service-Layer Hooks:**
- Purpose: Hook implementations for service-layer concerns
- Key files:
  - `output_processor.py`: OutputProcessorHook -- emits ResponseEvent from segment_complete
  - `skill_hit.py`: SkillHitHook -- emits SkillHitEvent when skills are invoked
  - `assistant_state.py`: AssistantStateHook -- emits AssistantStateEvent for persistence
  - `confirmation.py`: ConfirmationHook -- human-in-the-loop confirmation flow

**`matmaster/integration/` -- Event Infrastructure:**
- Purpose: Event routing, SSE delivery, DB persistence, workspace management
- Key files:
  - `event_router.py`: EventRouter -- asyncio.Task consumer + multi-handler dispatch
  - `sse_handler.py`: SSEHandler -- format and deliver events via callback
  - `persistence_handler.py`: PersistenceHandler -- persist events to database
  - `workspace_handler.py`: WorkspaceHandler -- workspace archival on run completion
  - `event_payloads.py`: Event payload formatters

**`matmaster/sessions/` -- Session Implementations:**
- Purpose: Execution environment backends
- Key files:
  - `local.py`: LocalSession -- subprocess-based local execution
  - `ssh.py`: SSHSession -- Paramiko-based remote execution
  - `sftp_pool.py`: SFTP connection pooling for SSH
  - `tmux.py`: Tmux session support (legacy)

**`matmaster/skills/` -- Skill System:**
- Purpose: Skill definitions for lazy MCP tool injection
- Key files:
  - `registry.py`: SkillRegistry -- discovers SKILL.md files, provides routing table
  - `lazymcp/`: LazyMCP skill definitions (one directory per MCP server)
    - `mcp-mat-struct-db/`, `mcp-mat-dpa/`, `mcp-mat-xrd/`, etc.
    - Each contains a `SKILL.md` file describing the skill
  - `playground-skills/`: Complex skill definitions with scripts, prompts, references
    - `ask-human/`, `bohrium-job/`, `deep-survey/`, `input-manual-helper/`, etc.

**`matmaster/exps/` -- Exp Definitions:**
- Purpose: TOML-based experiment configuration
- Key files:
  - `_base.toml`: Base system prompt (inherited by all exps)
  - `direct.toml`: Default exp configuration
  - `explore.toml`: Exploration-focused exp

**`matmaster/devshell/` -- Developer CLI:**
- Purpose: Local development REPL bypassing web service layer
- Key files:
  - `__main__.py`: Entry point (`python -m matmaster.devshell`)
  - `cli.py`: CLI argument parsing
  - `runner.py`: DevShell runner (Playground -> Exp -> Kernel)
  - `repl.py`: Interactive REPL loop
  - `stream_hook.py`: Terminal-based event display

**`src/apis/` -- API Layer:**
- Purpose: FastAPI route handlers
- Key files:
  - `api_router.py`: Router aggregation
  - `chat_api.py`: Chat endpoints (send, stream, history, sessions)
  - `debug_api.py`: Debug/admin endpoints

**`src/services/` -- Service Layer:**
- Purpose: Business logic and pipeline orchestration
- Key files:
  - `agent_run_service.py`: AgentRunService -- 6-stage pipeline orchestrator
  - `agent_run_bohrium.py`: BohriumSetupService -- HPC container allocation + SSH setup
  - `stream_service.py`: SSE streaming management
  - `sessions_service.py`: Session state management
  - `chat_history.py`: ChatHistoryConverter -- event-to-message conversion
  - `quota_service.py`: Usage quota enforcement
  - `events_service.py`: Event persistence helpers
  - `worker_registry_service.py`: Multi-worker coordination

**`src/dao/` -- Data Access:**
- Purpose: Database and external storage operations
- Key files:
  - `chat_events_table.py`: Event persistence (MySQL)
  - `chat_sessions_table.py`: Session metadata (MySQL)
  - `redis_dao.py`: Redis operations (stop signals, queues, worker registry)
  - `oss_io.py`: Aliyun OSS file upload

## Key File Locations

**Entry Points:**
- `app.py`: FastAPI application with lifespan, CORS, middleware
- `src/worker/agent_worker.py`: Background worker entry
- `matmaster/devshell/__main__.py`: CLI development entry

**Configuration:**
- `config/config.yaml`: Main runtime config (agents, session, playground)
- `config/llm_config.yaml`: LLM provider profiles
- `config/mcp.yaml`: MCP runtime config
- `config/mcp_config.json`: MCP server connection config
- `matmaster/exps/_base.toml`: Base system prompt
- `matmaster/exps/direct.toml`: Default exp config
- `pyproject.toml`: Dependencies and project metadata

**Core Logic:**
- `matmaster/core/agent.py`: AgentKernel execution loop
- `matmaster/core/exp.py`: Exp assembly layer
- `matmaster/core/playground.py`: Playground environment layer
- `matmaster/core/bus.py`: MessageBus event transport
- `matmaster/core/hooks.py`: Hook system
- `matmaster/core/tool_runner.py`: Tool execution strategy
- `src/services/agent_run_service.py`: Pipeline orchestrator

**Type Contracts:**
- `matmaster/types/runtime.py`: AgentRuntimeSpec, KernelResult, AgentRuntime
- `matmaster/types/context.py`: PlaygroundContext
- `matmaster/types/events.py`: 18 BusEvent types
- `matmaster/types/messages.py`: Message hierarchy

**Testing:**
- `tests/matmaster/`: Unit tests for core matmaster modules
- `tests/conftest.py`: Shared fixtures
- `tests/fixtures/`: Test data
- `evaluation/`: Agent evaluation framework with question banks

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (e.g., `tool_registry.py`, `agent_run_service.py`)
- TOML configs: `snake_case.toml` (e.g., `direct.toml`, `_base.toml`)
- Skill directories: `kebab-case` (e.g., `mcp-mat-struct-db`, `ask-human`)

**Directories:**
- Python packages: `snake_case` (e.g., `matmaster/`, `tools/`, `builtin/`)
- Skill roots: `kebab-case` (e.g., `lazymcp/`, `playground-skills/`)
- Config dirs: `snake_case` or flat name (`config/`)

**Classes:**
- PascalCase for all classes (e.g., `AgentKernel`, `PlaygroundContext`, `ToolRegistry`)
- Protocol classes: noun or adjective (e.g., `Tool`, `Hook`, `Guard`, `LLMProvider`, `Session`)
- Config models: suffixed with `Config` (e.g., `ExpConfig`, `CompactionConfig`)
- Event models: suffixed with `Event` (e.g., `ThoughtEvent`, `ToolCallEvent`)

**Functions/Methods:**
- `snake_case` for all functions
- Private methods: `_prefix` (e.g., `_run_items`, `_call_llm`)
- Hook runners: `run_` prefix (e.g., `run_pre_tool_call`, `run_should_continue`)
- Loaders: `load_` prefix (e.g., `load_exp_config`, `load_llm_config`)
- Factories: `build_` or `get_` prefix (e.g., `build_provider`, `get_agent_run_service`)

## Where to Add New Code

**New Exp Mode:**
- Create `matmaster/exps/{name}.toml` (inherits `_base.toml` system_prompt)
- Define `name`, `description`, `max_turns`, `tools.builtin`, `skills.enabled`, `system_prompt` (override), `developer_instructions`
- No Python code changes needed -- `load_exp_config(name)` auto-discovers

**New Builtin Tool:**
- Implement tool class in `matmaster/tools/builtin/{tool_name}.py`
- Satisfy `Tool` Protocol: `name`, `description`, `json_schema` properties + `execute()` async method
- Optionally extend `BuiltinTool` base class from `matmaster/tools/builtin/base.py`
- Register in `Exp._init_builtin_tools()` (`matmaster/core/exp.py`) -- add to `native_tools` or `additional_builtins` list
- Export from `matmaster/tools/builtin/__init__.py`

**New MCP Skill:**
- Create `matmaster/skills/lazymcp/mcp-{name}/SKILL.md`
- Add MCP server config to `config/mcp_config.json` under `mcpServers`
- Cache schema via `matmaster/tools/cache_mcp_schemas.py`
- No Python code changes -- skill registry auto-discovers SKILL.md files

**New Playground Skill:**
- Create `matmaster/skills/playground-skills/{name}/SKILL.md`
- Add `scripts/` directory with implementation scripts
- Optionally add `reference/` and `prompts/` directories

**New Hook:**
- Implement `Hook` Protocol or extend `BaseHook` from `matmaster/core/hooks.py`
- Override specific hook methods (7 available)
- Register in `_build_service_hooks()` in `src/services/agent_run_service.py`

**New Guard:**
- Implement `Guard` Protocol from `matmaster/types/guards.py`
- Single method: `evaluate(ctx: GuardContext) -> GuardResult`
- Add to `spec.guards` list in `Exp.assemble()` or via config

**New API Endpoint:**
- Add route handler in `src/apis/chat_api.py` or create new router file
- Register router in `src/apis/api_router.py`
- Add Pydantic models in `src/models/`

**New Service:**
- Create `src/services/{name}_service.py`
- Use singleton pattern with `@lru_cache` getter (e.g., `get_{name}_service()`)
- Inject DAO dependencies via constructor

**New Event Type:**
- Define Pydantic model in `matmaster/types/events.py` with `type: Literal["name"] = "name"`
- Add to `AgentEvent` or `SystemEvent` union, and to `BusEvent` union
- Export from `matmaster/types/__init__.py`

**Tests:**
- Place in `tests/matmaster/` mirroring source structure
- Use `conftest.py` fixtures

## Special Directories

**`matmaster/cache/`:**
- Purpose: MCP tool schema cache files (JSON)
- Generated: Yes (by `cache_mcp_schemas.py`)
- Committed: Yes (pre-cached schemas for offline startup)

**`runs/`:**
- Purpose: Runtime workspace directories and logs
- Generated: Yes (by Playground at runtime)
- Committed: No (gitignored)

**`workspace/`:**
- Purpose: Default workspace root for local sessions
- Generated: Yes
- Committed: No (gitignored)

**`matmaster/exps/`:**
- Purpose: Exp TOML definitions (declarative agent configurations)
- Generated: No (hand-authored)
- Committed: Yes

**`evaluation/question_bank/`:**
- Purpose: Evaluation test cases organized by category (input generation, structure construction, etc.)
- Generated: No
- Committed: Yes

## Import Dependency Graph (High-Level)

```
src/apis/         -> src/services/      -> src/dao/
                                        -> matmaster/
                                        
src/services/     -> matmaster/core/    (Playground, Exp, MessageBus)
                  -> matmaster/integration/ (EventRouter, handlers)
                  -> matmaster/config/  (loader)
                  -> matmaster/hooks/   (service hooks)
                  -> matmaster/providers/ (LLM factory)
                  -> src/dao/

matmaster/core/   -> matmaster/types/   (contracts, protocols)
                  -> matmaster/tools/   (ToolRegistry, builtins)
                  -> matmaster/config/  (ExpConfig)
                  -> matmaster/providers/
                  -> matmaster/skills/

matmaster/types/  -> pydantic           (base models, no internal deps)

matmaster/tools/  -> matmaster/types/   (Tool Protocol, ToolResult)
                  -> matmaster/sessions/ (Session Protocol for tool execution)
                  -> matmaster/skills/  (SkillRegistry for lazy MCP)
```

**Dependency Rule:** `matmaster/types/` has zero internal dependencies (only pydantic). All other packages depend downward toward `types/`. `matmaster/core/` never imports from `src/`. `src/` imports from `matmaster/` but not vice versa.

---

*Structure analysis: 2026-04-02*
