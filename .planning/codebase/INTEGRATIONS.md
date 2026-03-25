# External Integrations

**Analysis Date:** 2026-03-21

## APIs & External Services

**LLM Providers (Multiple):**
- OpenAI - Chat completion API
  - SDK/Client: `openai` package
  - Auth: `OPENAI_API_KEY` environment variable
  - Implementation: `evomaster/utils/llm.py` → `OpenAILLM` class
  - Models: gpt-4, gpt-4-turbo, gpt-4o, etc.
  - Features: Tool calls, streaming, reasoning/thinking support

- Anthropic (Claude) - Chat completion API
  - SDK/Client: `anthropic` package
  - Auth: `ANTHROPIC_API_KEY` environment variable
  - Implementation: `evomaster/utils/llm.py` → `AnthropicLLM` class
  - Models: claude-3-opus, claude-3.5-sonnet, claude-4.6, etc.
  - Features: Adaptive thinking/reasoning, tool calls, streaming

- Google Generative AI - Gemini models
  - SDK/Client: `google-genai` package
  - Auth: API key in LLMConfig
  - Implementation: `evomaster/utils/llm.py` (provider-agnostic interface)
  - Models: Gemini Pro, Gemini 2.0 Flash, etc.

- DeepSeek - LLM service
  - SDK/Client: DeepSeek API via `openai`-compatible endpoint
  - Auth: `DEEPSEEK_API_KEY` environment variable
  - Implementation: `evomaster/utils/llm.py` → `DeepSeekLLM` class
  - Models: deepseek-chat, deepseek-reasoner

**LLM Proxy:**
- LiteLLM Proxy - Unified LLM provider abstraction
  - Base URL: `LITELLM_PROXY_API_BASE` environment variable
  - Auth: `LITELLM_PROXY_API_KEY` environment variable
  - Usage: Optional fallback for provider compatibility/rate limiting

**Bohrium (Scientific Computing Cloud):**
- Bohrium Open API - HPC/GPU job submission and management
  - Base URL: `BOHRIUM_OPENAPI_HOST` or `BOHRIUM_BASE_URL` env var (defaults to environment-specific: test.dp.tech or open.bohrium.com)
  - HTTP Client: `httpx` (async)
  - Implementation: `src/services/bohrium_node_service.py`
  - Endpoints:
    - Node creation: POST /openapi/v1/nodes
    - Node list/status: GET /openapi/v1/nodes
    - Node deletion: DELETE /openapi/v1/nodes/{id}
  - Auth:
    - `BOHRIUM_USER_ID`, `BOHRIUM_EMAIL`, `BOHRIUM_PASSWORD` - Credentials
    - `BOHRIUM_PROJECT_ID`, `BOHRIUM_ACCESS_KEY` - Project/authentication
    - `SERVICE_ENV` - Environment selection (prod, uat, test)
  - SDK: `bohrium-sdk` and `bohrium-open-sdk` (optional) packages

**Search & Knowledge:**
- Bing Search (via MCP) - Web search capabilities
  - Protocol: Model Context Protocol (MCP)
  - Server: npx-based (@modelcontextprotocol/server-bing-search or bing-cn-mcp)
  - Configuration: `configs/mcp_config.json` → `bing-search` server

**Visualization & Mind Maps:**
- Mind Map MCP - Diagram generation
  - Protocol: Model Context Protocol (MCP)
  - Server: npx-based (@lucianaib/mind-map-mcp)
  - Configuration: `configs/mcp_config.json` → `mind-map` server

**Financial Data:**
- Stock Analysis MCP - Market data and analysis
  - Protocol: Model Context Protocol (MCP)
  - Server: npx-based (mcp-stock-analysis)
  - Configuration: `configs/mcp_config.json` → `stock-analysis` server

## Data Storage

**Databases:**
- MySQL 5.7+
  - Connection config: `src/utils/constant.py` → `DB_CONFIG`
  - Client: `pymysql` (synchronous cursor-based API)
  - Environment vars: `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`
  - Tables: `evo_chat_sessions`, `evo_chat_events`, `evo_bohrium_nodes` (managed in `src/dao/`)
  - ORM: Custom DAO pattern (no SQLAlchemy/ORM, raw SQL + pymysql)

**Cache/Message Queue:**
- Redis 5.0+
  - Connection: `REDIS_URL` environment variable (format: redis://:password@host:port/db)
  - Client: `redis` package
  - Usage:
    - Session stop signals (pub/sub) - cross-worker coordination
    - Agent run queue (list operations) - job distribution
    - Confirmation replies - multi-worker response collection
    - Worker heartbeat tracking - pod liveness detection
  - Optional: If not configured, single-process mode; no cross-worker features

**File Storage:**
- Aliyun OSS (Object Storage Service)
  - Endpoint: `OSS_ENDPOINT` environment variable
  - Bucket: `OSS_BUCKET_NAME` environment variable
  - Auth: `OSS_ACCESS_KEY_ID`, `OSS_ACCESS_KEY_SECRET` environment variables
  - Client: `oss2` package
  - Implementation: `src/dao/oss_io.py`
  - Purpose: Upload local structure/files to publicly accessible URLs (for DPA/calculation tools)
  - Mandatory for calculation MCP path adaptor

## Authentication & Identity

**Auth Provider:**
- Custom JWT-based
  - Implementation: `src/services/user_service.py` → `UserService.require_user_id()`
  - Method: HTTP header extraction (user_id from request)
  - No OAuth/OIDC: Direct user_id injection from API gateway

**Cross-Service Auth:**
- Internal services (Account, MatMaster Tools Server): API key/token based
  - `ACCOUNT_API_BASE_URL` - User/account info API
  - `MATMASTER_TOOLS_SERVER` - Quota and tool management
  - `SUPPORT_SERVICE_BASE_URL` - Session completion notifications

## Monitoring & Observability

**Error Tracking:**
- Not detected - Errors logged to application logs only

**Logs:**
- Structured JSON logging (configuration in `src/utils/logger.py`)
- Output: Console (stdout) and optional file
- Format: timestamp, logger name, level, message
- Levels: DEBUG, INFO, WARNING, ERROR

**Memory Profiling:**
- tracemalloc (Python stdlib)
  - Enabled in lifespan startup (`app.py`)
  - Debug endpoint: `/api/v1/debug/tracemalloc` for snapshots and diffs

**Metrics:**
- Worker heartbeat - Pod liveness (stored in Redis)
- Agent run queue length - Job backlog (Redis list length)
- Session status tracking - Run state (MySQL tables)

## CI/CD & Deployment

**Hosting:**
- Docker containers (kubernetes-ready)
  - Dockerfile with multi-target support (api, worker)
  - Base image: registry.dp.tech/public/python:3.13-slim
  - Exposed port: 80

**CI Pipeline:**
- GitLab CI
  - Configuration: `.gitlab-ci.yml` and `.deploy-ci.yml`
  - Deployment via CI/CD pipeline

**Package Management:**
- uv for reproducible environments (uv.lock)
- Optional pre-commit hooks (`.pre-commit-config.yaml`)

## Environment Configuration

**Required env vars (by feature):**

**LLM (choose at least one):**
- `OPENAI_API_KEY` - OpenAI access
- `ANTHROPIC_API_KEY` - Claude access
- `DEEPSEEK_API_KEY` - DeepSeek access
- `GPT_BASE_URL`, `GPT_CHAT_MODEL` - OpenAI-compatible endpoint
- `LITELLM_PROXY_API_BASE`, `LITELLM_PROXY_API_KEY` - LiteLLM proxy
- `AZURE_API_BASE`, `AZURE_API_KEY`, `AZURE_API_VERSION` - Azure OpenAI

**Database (mandatory):**
- `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`

**Bohrium Computation (for calculation MCP):**
- `SERVICE_ENV` - Environment (prod/uat/test)
- `BOHRIUM_USER_ID`, `BOHRIUM_EMAIL`, `BOHRIUM_PASSWORD` - Authentication
- `BOHRIUM_PROJECT_ID`, `BOHRIUM_ACCESS_KEY` - Project context
- `BOHRIUM_BASE_URL` - API endpoint (optional, auto-detected from SERVICE_ENV)

**OSS File Storage (mandatory for calculation MCP):**
- `OSS_ENDPOINT`, `OSS_BUCKET_NAME`, `OSS_ACCESS_KEY_ID`, `OSS_ACCESS_KEY_SECRET`

**Redis (optional, enables multi-worker features):**
- `REDIS_URL` - Connection string (redis://:password@host:port/db)

**Internal Services (optional):**
- `SUPPORT_SERVICE_BASE_URL` - Session completion notifications
- `ACCOUNT_API_BASE_URL` - User info lookups
- `MATMASTER_TOOLS_SERVER` - Tool quotas
- `MEMORY_SERVICE_URL` - Session memory service (default: 101.126.90.82:8002)

**Observability (optional):**
- `OPIK_PROJECT_NAME` - OPIK observability platform (not yet implemented)

**Secrets location:**
- `.env` file in project root (loaded by `python-dotenv` in `src/utils/constant.py`)
- `.env.{SERVICE_ENV}` overrides for environment-specific secrets
- Never committed to git (.gitignore prevents this)

## Webhooks & Callbacks

**Incoming:**
- WebSocket endpoint: `/ws/chat/stream/{session_id}` (FastAPI WebSocket)
  - Purpose: Real-time streaming of agent runs
  - Implementation: `src/apis/chat_api.py`
  - Events: Stream chunks, confirmation requests, completion
  - Client must handle: JSON event parsing, reconnection logic

- REST endpoints:
  - `POST /api/v1/chat/send` - Send user message and start agent run
  - `POST /api/v1/chat/plan` - Planner confirmation endpoint
  - `GET /api/v1/chat/list` - Session list
  - `GET /api/v1/chat/run_status` - Running/queued task counts

**Outgoing:**
- Session completion notifications
  - Service: Support Service (templates) at `SUPPORT_SERVICE_BASE_URL`
  - Endpoint: POST with template ID and user email
  - Trigger: Agent run completion, session archived

- Bohrium API calls (job submission/monitoring)
  - Service: Bohrium Open API at `BOHRIUM_OPENAPI_HOST`
  - Endpoints: Node lifecycle (create, list, delete)
  - Async via `httpx`

- MatMaster Tools Server (quota checks, quotas tracking)
  - Base URL: `MATMASTER_TOOLS_SERVER`
  - Async HTTP calls for quota enforcement

---

*Integration audit: 2026-03-21*
