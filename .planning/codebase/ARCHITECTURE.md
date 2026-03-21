# Architecture

**Analysis Date:** 2026-03-21

## Pattern Overview

**Overall:** Layered API-driven distributed agent system with FastAPI backend, thread-pool execution, multi-worker support, and Redis-based cross-worker coordination.

**Key Characteristics:**
- Async FastAPI web service with SSE streaming responses for real-time agent execution feedback
- Agent execution in separate ThreadPoolExecutor to prevent blocking HTTP handlers
- Session-based chat with persistence layer (MySQL) and in-memory session state
- Event-driven architecture with callback-based thought/tool_call/tool_result emission
- Multi-worker deployment support with Redis coordination (stop signals, run ownership, worker registry)
- Playground abstraction for experiment/task execution (pluggable with mat_master, minimal, x_master variants)

## Layers

**API Layer (HTTP handlers):**
- Purpose: Expose REST endpoints and SSE streaming for chat, session management, workspace operations
- Location: `src/apis/chat_api.py`, `src/apis/debug_api.py`
- Contains: FastAPI route handlers with dependency injection (Depends)
- Depends on: Service layer (ChatStreamService, ChatSessionsService, AgentRunService)
- Used by: Web clients (ag-ui frontend)

**Service Layer:**
- Purpose: Implement business logic, orchestrate DAO/external calls, manage lifetimes
- Location: `src/services/`
- Contains:
  - `agent_run_service.py`: Agent execution via ThreadPoolExecutor, playground lifecycle, workspace snapshot
  - `stream_service.py`: SSE queue management (in-memory or Redis), confirmation reply queues
  - `sessions_service.py`: Session state and authorization checks
  - `chat_history.py`: Event-to-dialog conversion for context
  - `workspace_service.py`: Workspace metadata tracking
  - `quota_service.py`: Usage quota enforcement
  - `events_service.py`: Event persistence (DB + SSE)
  - `deploy_state_service.py`: Version tracking for run_interrupted detection
  - `worker_registry_service.py`: Multi-worker coordination (heartbeat, active run counts)
  - `bohrium_node_service.py`, `agent_run_bohrium.py`: Bohrium executor/storage setup
- Depends on: DAO layer, EvoMaster core (Playground, Agent), utils
- Used by: API layer

**Data Access Layer (DAO):**
- Purpose: Database and external storage operations with no exception swallowing
- Location: `src/dao/`
- Contains:
  - `chat_sessions_table.py`, `chat_events_table.py`: MySQL direct operations
  - `base_table.py`: BaseTable context manager for connection pooling
  - `redis_dao.py`: Redis client factory and run/queue state
  - `oss_io.py`: Aliyun OSS file upload
- Depends on: pymysql, redis, oss2
- Used by: Service layer (no try/except in DAO—exceptions flow to global handler)

**Model/Data Layer:**
- Purpose: Pydantic models for type validation and serialization
- Location: `src/models/`
- Contains: `chat.py` (ChatSendRequest, RunStatusData, event DTOs), `health.py`, `root.py`
- Used by: API layer, Services

**Core EvoMaster Framework:**
- Purpose: Agent, Playground, Exp abstractions for experiment execution
- Location: `evomaster/core/`, `evomaster/agent/`
- Contains:
  - `playground.py`: BasePlayground lifecycle (config load, component init, exp run, cleanup)
  - `exp.py`: BaseExp encapsulates agent execution and result collection
  - `agent.py`: BaseAgent with message/tool handling and context management
- Used by: `agent_run_service.py` (instantiates Playground per session)

**Playground (Business Domain):**
- Purpose: Domain-specific agent implementations and skill systems
- Location: `playground/mat_master/` (primary), `playground/minimal/`, `playground/x_master/`
- Contains:
  - `core/playground.py`: MatMasterPlayground (extends BasePlayground, registers skills)
  - `service/stream_agent.py`: StreamingMatMasterAgent (emits thought/tool_call events via callback)
  - `skills/`: Skill implementations (research, file, tool bindings)
  - `prompts/`: System and task prompts
- Used by: `agent_run_service._get_or_create_playground()`

**Worker & Utils:**
- Purpose: Infrastructure helpers (logging, exceptions, constants, build info)
- Location: `src/worker/`, `src/utils/`
- Contains:
  - `logger.py`: LogContext for session-scoped log filtering
  - `exceptions.py`: BaseErrorResponse and subclasses (400, 403, 404, 409)
  - `constant.py`: DB_CONFIG, REDIS_URL, build env, LLM configs
  - `worker_id.py`: get_worker_id() for multi-worker tracking

## Data Flow

**User initiates chat stream:**

1. Client: POST `/api/v1/chat/sessions/{session_id}/stream` with ChatSendRequest (optional content, confirmation_reply)
2. API (`chat_api.py`):
   - Validates session access (can_access_session)
   - Ensures session exists in DB and memory
   - Calls ChatStreamService.generate_send_stream(content, session_id, user_id, ...)
3. ChatStreamService (`stream_service.py`):
   - Checks session not in-run (409 if already running)
   - Creates SSE queue (in-memory or Redis-based)
   - Spawns background task: `run_agent_sync()` in ThreadPoolExecutor
   - Returns AsyncGenerator yielding SSE events (ag-ui protocol: source, type, content, session_id, extra)
4. AgentRunService.run_agent_sync():
   - Loads/creates Playground via `_get_or_create_playground(session_id)`
   - Converts prior events to Dialog (ChatHistoryConverter)
   - Calls playground.run(task) with event_callback
5. StreamingMatMasterAgent (playground/mat_master/service/stream_agent.py):
   - Executes agent.run(task)
   - Emits via event_callback('source', 'type', content, **extra):
     - 'thought' (with stream_state='start'/'streaming'/'end' for LLM token streaming)
     - 'tool_call' (before callback modifies params)
     - 'tool_result'
     - 'finish'
6. Event Callback in AgentRunService:
   - Filters/persists events (only durable ones to DB)
   - Pushes to SSE queue (except internal events like assistant_state, streaming thoughts for Planner)
7. SSE AsyncGenerator:
   - Yields JSON-encoded ag-ui events to client
   - Client consumes and renders real-time (thoughts, actions, results)

**State Management:**

- **Session in-run mutex**: ChatSessionsService._sessions_in_run prevents concurrent runs on same session
- **Confirmation handling**: Agent pauses, emits 'confirmation_request' → client shows prompt → user replies via POST `/confirmation_reply` → StreamService puts reply in ReplyQueueLike → Agent resumes
- **Event persistence**: ChatEventsTable stores durable events; query for history via ChatHistoryConverter
- **Worker coordination** (multi-worker):
  - Worker heartbeat registered in Redis worker_registry (10s interval)
  - AgentRunService stores run owner (session → worker_id) in Redis
  - Stop signal published via Redis channel; worker polls Redis for "stop" key per session
  - Deploy/restart detection via DeployStateService comparing versions

## Key Abstractions

**Playground:**
- Purpose: Encapsulates experiment lifecycle and agent initialization
- Examples: `playground/mat_master/core/playground.py`, `evomaster/core/playground.py`
- Pattern: Subclass BasePlayground, override `_create_exp()` to customize Exp, override `setup()` for init hooks

**Agent:**
- Purpose: Autonomous entity that runs tasks via LLM + tools
- Examples: `playground/mat_master/service/stream_agent.py`, `evomaster/agent/agent.py`
- Pattern: Subclass BaseAgent, override `_on_assistant_message()`, `_on_tool_call_start()`, `_on_tool_message()` for callbacks

**Event System:**
- Purpose: Real-time progress tracking without blocking HTTP handlers
- Pattern: Callback passed to Playground.run(task) → Agent emits events → Service pushes to SSE queue → AsyncGenerator → client

**Session:**
- Purpose: Isolated execution context per user conversation
- Pattern: session_id uniquely identifies; DB stores metadata (user_id, org_id, status); memory stores runtime state (SESSIONS dict, bohrium_node_id)

**ReplyQueueLike Protocol:**
- Purpose: Abstract confirmation reply handling for in-process and cross-worker scenarios
- Examples: `InMemoryReplyQueue` (queue.Queue), `RedisReplyQueue` (Redis list)
- Pattern: put_content(str), put_cancel(), get(timeout) → Agent's confirmation logic agnostic to queue backend

## Entry Points

**Web Service:**
- Location: `app.py`
- Triggers: uvicorn listening on 0.0.0.0:8000
- Responsibilities:
  - FastAPI app setup with CORS, middleware, exception handlers
  - Lifespan: init Playground, start Redis stop subscriber, worker heartbeat task
  - Include api_router at /api/v1

**CLI Entry Point:**
- Location: `run.py` (experiment runner)
- Triggers: `python run.py --agent {name} --task {desc}`
- Responsibilities: Load Playground by name, run Exp with task, save results

**Playground Init:**
- Location: `app.py` lifespan → `agent_run_service.init_playground_sync()`
- Triggers: App startup
- Responsibilities: Preload Playground module and config (avoid cold start on first /stream)

## Error Handling

**Strategy:** Exceptions from DAO/services bubble up; global FastAPI exception handler converts to BaseResponse (code, msg, data) with appropriate HTTP status.

**Patterns:**

- **BaseErrorResponse hierarchy** (`src/utils/exceptions.py`):
  - BadRequestErrorResponse (400)
  - ForbiddenErrorResponse (403)
  - NotFoundErrorResponse (404)
  - ConflictErrorResponse (409)
  - Base handler in `app.py` converts to JSONResponse with proper status

- **DAO exceptions** flow unhandled (e.g., pymysql.Error) → caught by global handler → 500

- **Agent execution errors** (e.g., LLM failures, tool errors):
  - Emitted as 'error' event via callback
  - Persisted to DB and pushed to SSE
  - Client displays error state
  - Stream ends gracefully

- **Session state errors**:
  - 409 Conflict if session already in-run (detected via _sessions_in_run set)
  - 403 Forbidden if user lacks access (checked via can_access_session)
  - 404 Not Found if session ID invalid

## Cross-Cutting Concerns

**Logging:**
- LogContext per session_id extracted from URL path (e.g., `/api/v1/chat/sessions/{session_id}/...`)
- Request middleware binds session_id to LogContext for downstream filtering
- Logger.info/warning/error auto-includes session_id in structured logs
- Config in `src/utils/logger.py`, applied in app.py

**Validation:**
- Pydantic models in `src/models/` validate incoming requests
- SessionListQuery enforces limit ≤ 100
- ChatSendRequest validates content presence and length
- Custom validators for session_id format if needed

**Authentication:**
- UserService.require_user_id (FastAPI dependency) → check Authorization header or session cookie
- UserService.optional_user_id (FastAPI dependency) → user_id or None (for shared sessions)
- Shared sessions bypass auth (is_shared flag in DB)

**Resource Management:**
- Agent execution in ThreadPoolExecutor (max 2 workers by default, CHAT_AGENT_MAX_WORKERS env)
- Graceful shutdown in app.py lifespan: wait up to 30s for executor to finish running tasks
- Workspace cleanup after run (in _get_or_create_playground cleanup logic)
- Redis client connections reused via get_redis_dao() singleton

---

*Architecture analysis: 2026-03-21*
