# External Integrations

**Analysis Date:** 2026-04-02

## APIs & External Services

**LLM Providers (unified via OpenAI-compatible interface):**
- All LLM calls go through `matmaster/providers/openai_provider.py` → `OpenAIProvider` (wraps `openai.AsyncOpenAI`)
- Provider selection is config-driven via `matmaster/config/llm.py` → `LLMConfig` profiles
- Factory: `matmaster/providers/llm_factory.py` → `build_provider()` resolves route → builds OpenAIProvider
- Supported providers (all via OpenAI-compatible API):
  - OpenAI direct: `OPENAI_API_KEY`, `GPT_BASE_URL`, `GPT_CHAT_MODEL`
  - Anthropic: `ANTHROPIC_API_KEY` (via OpenAI-compatible proxy or native)
  - Google GenAI: via `google-genai` SDK
  - DeepSeek: `DEEPSEEK_API_KEY` (OpenAI-compatible endpoint)
  - Azure OpenAI: `AZURE_API_BASE`, `AZURE_API_KEY`, `AZURE_API_VERSION`
- Cached token extraction supports both OpenAI (`prompt_tokens_details.cached_tokens`) and Anthropic (`cache_read_input_tokens`) formats

**LiteLLM Proxy:**
- Purpose: Unified LLM API gateway — all providers accessible through one base_url
- Auth: `LITELLM_PROXY_API_BASE`, `LITELLM_PROXY_API_KEY`
- Usage: Production default; OpenAIProvider points `base_url` at the proxy, proxy routes to actual provider
- Advantage: Single API key management, rate limiting, cost tracking at proxy level

**MCP (Model Context Protocol) — Tool Integration:**
- Client implementation: `matmaster/mcp/connection.py` → `MCPConnection` (ABC with stdio/SSE/HTTP subclasses)
- Manager: `matmaster/mcp/manager.py` — Connection pool and lifecycle management
- Transport support: stdio (`StdioServerParameters`), SSE (`sse_client`), streamable HTTP (`streamablehttp_client`)
- Connection timeout: `MCP_CONNECT_TIMEOUT` env var (default 15s, 3 retries = 45s max)
- Lazy loading: `matmaster/tools/lazy_mcp.py` → `LazyMCPTool` holds cached schema, connects on first `execute()`
- Schema caching: `matmaster/tools/schema_cache.py`, `matmaster/tools/cache_mcp_schemas.py`, cached in `matmaster/cache/`
- Skill routing: `matmaster/tools/skill_tool.py` — LLM calls `use_skill` to trigger on-demand MCP connection
- LazyMCP Skill definitions (9 material science MCP servers):
  - `matmaster/skills/lazymcp/mcp-mat-compdart/SKILL.md`
  - `matmaster/skills/lazymcp/mcp-mat-doc/SKILL.md`
  - `matmaster/skills/lazymcp/mcp-mat-dpa/SKILL.md`
  - `matmaster/skills/lazymcp/mcp-mat-electron-microscope/SKILL.md`
  - `matmaster/skills/lazymcp/mcp-mat-nmr/SKILL.md`
  - `matmaster/skills/lazymcp/mcp-mat-sg/SKILL.md`
  - `matmaster/skills/lazymcp/mcp-mat-sn/SKILL.md`
  - `matmaster/skills/lazymcp/mcp-mat-struct-db/SKILL.md`
  - `matmaster/skills/lazymcp/mcp-mat-xrd/SKILL.md`
- Playground skills: `matmaster/skills/playground-skills/` (e.g., bohrium-job)
- MCP config: `matmaster_config/mcp.yaml` (server definitions)

**Bohrium HPC Platform:**
- Purpose: Remote scientific computation (job submission, SSH execution, file storage)
- Credential helpers: `matmaster/integration/bohrium_env.py` (pure-function module, no evomaster dependency)
  - `get_bohrium_credentials()` — Read from env or params
  - `get_bohrium_storage_config()` — HTTPS storage config dict
  - `inject_bohrium_executor()` — Deep-copy executor template with auth
  - `build_bohrium_skill_remote_env()` — Extract session credentials into env dict
- Bohrium Open API: `BOHRIUM_OPENAPI_HOST` (default `https://open.bohrium.com`)
  - Node management: `src/services/bohrium_node_service.py`
  - Endpoints: `/openapi/v1/nodes` (create, list, status, delete)
  - HTTP client: `httpx` (async)
- Bohrium Core API: `BOHRIUM_CORE_BASE_URL` — Access key management (`src/utils/constant.py`)
- SSH execution: `matmaster/sessions/ssh.py` (paramiko), `matmaster/sessions/sftp_pool.py` (SFTP pooling)
  - PlaygroundContext distinguishes `workdir` (local) vs `execution_workdir` (remote SSH)
  - Tmux sessions: `matmaster/sessions/tmux.py` for persistent remote shells
- Auth env vars: `BOHRIUM_USER_ID`, `BOHRIUM_EMAIL`, `BOHRIUM_PASSWORD`, `BOHRIUM_PROJECT_ID`, `BOHRIUM_ACCESS_KEY`
- Environment-aware URLs: auto-resolved from `SERVICE_ENV` (test/uat/prod → `openapi{.test/.uat}.dp.tech`)
- Job submission SDK: `bohrium-open-sdk` used in `matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py`

## Data Storage

**Databases:**
- MySQL (PyMySQL, synchronous)
  - Connection: `src/utils/constant.py` → `DB_CONFIG` dict (host, port, user, password, database)
  - Client: `pymysql` with `DictCursor`, no ORM (raw SQL)
  - Base class: `src/base/base_table.py` → `BaseTable` (context manager for connection lifecycle)
  - DAO layer:
    - `src/dao/chat_sessions_table.py` → `ChatSessionsTable` (table: `evo_chat_sessions`)
    - `src/dao/chat_events_table.py` → Events persistence (table: `evo_chat_events`)
    - `src/dao/bohrium_nodes_table.py` → Bohrium node tracking
  - Env vars: `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`

**File Storage:**
- Alibaba Cloud OSS
  - Implementation: `src/dao/oss_io.py` → `_get_bucket()`, upload/download functions
  - Client: `oss2` package with `EnvironmentVariableCredentialsProvider`
  - Purpose: Upload local structure/files to publicly accessible URLs (mandatory for calculation MCP)
  - Env vars: `OSS_ENDPOINT`, `OSS_BUCKET_NAME`, `OSS_ACCESS_KEY_ID`, `OSS_ACCESS_KEY_SECRET`

**Caching:**
- Redis 5.0+
  - Connection: `REDIS_URL` env var (format: `redis://:password@host:port/db`)
  - Client: `redis` package (synchronous)
  - Implementation: `src/dao/redis_dao.py`
  - Usage patterns:
    - Agent run queue: `chat:agent_run_queue` (BLPOP by worker, LPUSH by API)
    - Stop signals: `chat:stop:{session_id}` (TTL 3600s)
    - Run active tracking: `chat:run_active:{session_id}` (TTL 3600s)
    - Confirmation replies: `chat:confirmation_reply:{session_id}` (list)
    - SSE stream relay: `chat:stream:{session_id}` (Pub/Sub for cross-pod event delivery)
    - Session run queued: `matmaster_chat:session_run_queued:{session_id}` (TTL 300s)
  - Optional: single-process mode works without Redis; multi-worker requires it

- MCP Schema Cache (local filesystem):
  - Location: `matmaster/cache/`
  - Purpose: Cache MCP tool schemas to avoid re-fetching on every startup
  - Management: `matmaster/tools/cache_mcp_schemas.py`, `matmaster/tools/schema_cache.py`

## Authentication & Identity

**Auth Provider:**
- Custom (API gateway injection)
  - Implementation: `src/services/user_service.py` → `UserService`
  - Method: `require_user_id()` extracts user_id from HTTP request headers
  - No OAuth/OIDC: relies on upstream API gateway for authentication

**Cross-Service Auth:**
- Account API: `ACCOUNT_API_BASE_URL` (e.g., `https://account{.test}.dp.tech`) — User profile lookups (nickname, email)
- Quota Service: `MATMASTER_TOOLS_SERVER` — Tool usage quota enforcement
- Support Service: `SUPPORT_SERVICE_BASE_URL` — Email notifications via template IDs

## Monitoring & Observability

**Error Tracking:**
- No dedicated error tracking service (Sentry, etc.) detected
- Errors logged to application logs only

**Logs:**
- Structured logging via `src/utils/logger.py` → `LoggingConfig`, `setup_logging()`
- `LogContext` for request-scoped contextual logging
- Standard Python `logging` module throughout codebase

**Memory Profiling:**
- `tracemalloc` enabled at startup (`app.py` lifespan)
- Debug endpoint: `/api/v1/debug/tracemalloc` for allocation snapshots

**Notifications:**
- Feishu (Lark) webhook: `src/utils/feishu_notifier.py`
  - Purpose: Worker task lifecycle notifications (queued, started, completed, failed)
  - Format: Interactive card messages (blue/green/orange/red templates)
  - Hardcoded webhook URL in source
- Email notifications: `src/utils/support_notifier.py`
  - Purpose: Session completion emails to users
  - Delivery via Support Service API with template IDs (per-environment: test=140, uat=21, prod=116)

**Planned (Not Implemented):**
- OPIK observability: `OPIK_PROJECT_NAME` env var in `.env.template` but not yet wired

## CI/CD & Deployment

**Hosting:**
- Docker containers (Kubernetes-ready)
  - `Dockerfile` with multi-stage build:
    - `builder` stage: installs deps, pre-commit hooks
    - `api` target (default): Gunicorn + UvicornWorker on port 80
    - `worker` target: `python -m src.worker.agent_worker` (BLPOP loop)
  - Base image: `registry.dp.tech/public/python:3.13-slim`
  - jemalloc preloaded in production

**CI Pipeline:**
- Pre-commit hooks run in Docker build (hooks pre-installed in image)
- No `.github/workflows` or `.gitlab-ci.yml` detected in current tree (may be in separate deployment repo)

**Worker Architecture:**
- API pod: FastAPI app, enqueues agent runs to Redis
- Worker pod: `src/worker/agent_worker.py`
  - BLPOP from `chat:agent_run_queue` (configurable timeout, default 30s)
  - Heartbeat thread (10s interval) for liveness detection
  - Graceful drain on SIGTERM (finish current run, stop accepting new tasks)
  - Worker registry: `src/services/worker_registry_service.py`, `src/services/worker_registry_adapter.py`

## Event System (Internal)

**MessageBus:**
- Implementation: `matmaster/core/bus.py` — `asyncio.Queue`-based event bus
- 18 event types defined in `matmaster/types/events.py`:
  - AgentEvent (8 types): thought, response, tool_call, tool_result, assistant_state, llm_token, context_compaction, log_line
  - SystemEvent (10 types): run lifecycle, confirmation, spawn, etc.
- Discriminated union: `BusEvent = AgentEvent | SystemEvent` via Pydantic `Literal` discriminator on `type` field
- `spawn_id` field on all events for parent/child Agent isolation

**EventRouter:**
- Implementation: `matmaster/integration/event_router.py`
- Consumers:
  - `matmaster/integration/persistence_handler.py` → `PersistenceHandler` (writes to MySQL via events_table)
  - `matmaster/integration/sse_handler.py` → `SSEHandler` (pushes to frontend via send_cb)
  - `matmaster/integration/workspace_handler.py` → `WorkspaceHandler` (workspace file operations)
- Filter rules: skip internal-only events (assistant_state, log_line, llm_token), streaming deltas

## Environment Configuration

**Required env vars (by feature):**

| Category | Variables | Notes |
|----------|-----------|-------|
| LLM (at least one) | `LITELLM_PROXY_API_BASE`, `LITELLM_PROXY_API_KEY` | Production default |
| LLM (alternatives) | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `AZURE_API_*` | Direct provider access |
| Database | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` | Mandatory |
| Bohrium | `SERVICE_ENV`, `BOHRIUM_USER_ID`, `BOHRIUM_EMAIL`, `BOHRIUM_PASSWORD`, `BOHRIUM_PROJECT_ID`, `BOHRIUM_ACCESS_KEY` | For HPC |
| OSS | `OSS_ENDPOINT`, `OSS_BUCKET_NAME`, `OSS_ACCESS_KEY_ID`, `OSS_ACCESS_KEY_SECRET` | For calculation MCP |
| Redis | `REDIS_URL` | Optional (single-process ok without) |
| Memory | `MEMORY_SERVICE_URL` | Optional (default: `101.126.90.82:8002`) |

**Secrets location:**
- `.env` file in project root (loaded by `python-dotenv`)
- Never committed to git
- `.env.template` serves as documentation of all available variables

## Webhooks & Callbacks

**Incoming:**
- SSE streaming endpoint: `/api/v1/chat/subscribe/{session_id}` — Real-time event stream to frontend
- REST endpoints in `src/apis/chat_api.py`:
  - `POST /api/v1/chat/send` — Send user message, start agent run
  - `POST /api/v1/chat/stop` — Stop running agent
  - Various session/history management endpoints

**Outgoing:**
- Feishu webhook: Worker lifecycle notifications (hardcoded URL in `src/utils/feishu_notifier.py`)
- Support Service: Session completion email via `SUPPORT_SERVICE_BASE_URL`
- Bohrium Open API: Node and job management calls
- Account API: User profile lookups
- Memory Service: Session memory read/write at `MEMORY_SERVICE_URL`

---

*Integration audit: 2026-04-02*
