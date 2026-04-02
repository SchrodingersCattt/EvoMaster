# Codebase Concerns

**Analysis Date:** 2026-04-02

## Tech Debt

**evomaster Legacy References (Comments Only):**
- Issue: The `evomaster/` directory has been deleted, `EvoToolAdapter` eliminated, and no runtime imports from evomaster remain in `matmaster/`. However, docstrings and comments still reference evomaster as origin context (e.g., "migrated from evomaster/env/docker.py", "originally from evomaster"). Tests explicitly guard against evomaster re-introduction.
- Files: `matmaster/sessions/tmux.py`, `matmaster/tools/builtin/edit_tool.py`, `matmaster/tools/builtin/monitor_job/__init__.py`, `matmaster/adaptors/calculation/path_adaptor.py`, `matmaster/tools/skill_tool.py`, `matmaster/config/loader.py`
- Guard tests: `tests/matmaster/core/test_playground_no_evomaster.py`, `tests/matmaster/mcp/test_manager.py`, `tests/matmaster/tools/test_lazy_mcp.py`, `tests/matmaster/tools/test_cache_mcp_schemas.py`
- Impact: Low -- no functional debt, only cognitive noise. The guard tests are valuable and should be kept.
- Fix approach: Batch-clean docstrings/comments referencing evomaster origin. Keep the guard tests.

**_tmp/MatMaster References in job_service.py:**
- Issue: `matmaster/adaptors/calculation/job_service.py` module docstring and inline comments reference `_tmp/MatMaster/agents/matmaster_agent/services/job.py` as design origin. This path does not exist in the repo.
- Files: `matmaster/adaptors/calculation/job_service.py` (lines 5, 14-15, 35, 78, 257)
- Impact: Low -- misleading documentation for future contributors.
- Fix approach: Remove _tmp/MatMaster references; document the Bohrium OpenAPI contract directly.

**Evaluation Framework References Dead Config Path:**
- Issue: `evaluation/core/schemas.py` defaults `mat_config_path` to `'configs/mat_master/config.yaml'` -- a directory that no longer exists. The actual config lives at `config/`.
- Files: `evaluation/core/schemas.py` (line 295)
- Impact: Medium -- evaluation runs will fail if they rely on the default path without override.
- Fix approach: Update default to `'config/config.yaml'` or make it relative to project root.

**config/loader.py Docstring Still Mentions ConfigManager Coexistence:**
- Issue: The module docstring in `matmaster/config/loader.py` states these are "counterpart to evomaster.config.ConfigManager -- both can coexist during migration." The migration is complete.
- Files: `matmaster/config/loader.py` (lines 3-4, 12)
- Impact: Low -- misleading documentation.
- Fix approach: Remove the evomaster.config.ConfigManager reference from docstring.

**Phase 1 / Phase 2 Dual-Path in AgentKernel:**
- Issue: `_resolve_tool_definitions()` in `matmaster/core/agent.py` maintains a Phase 1 fallback path alongside the Phase 2 tool_catalog path. The `_call_llm_with_retry()` method (line 456-463) also has a separate legacy fallback for tool definition resolution. Both use `hasattr()` duck-typing checks on `spec.tool_registry`.
- Files: `matmaster/core/agent.py` (lines 110-133, 456-463)
- Impact: Medium -- duplicated resolution logic, hasattr checks bypass type safety. Dead code if Phase 1 path is no longer used.
- Fix approach: Audit whether any caller still constructs `AgentRuntimeSpec` without `tool_catalog`. If not, remove the Phase 1 fallback and the `hasattr` checks.

**AgentRuntimeSpec Uses `Any` for v2 Fields to Avoid Circular Import:**
- Issue: `tool_runner`, `tool_catalog`, `runtime_topology`, `capability_policy`, `structural_validation` are all typed as `Any | None` with a model_validator doing runtime checks via lazy import.
- Files: `matmaster/types/runtime.py` (lines 80-107)
- Impact: Medium -- IDE autocompletion and static type checking are lost for these critical fields. The lazy-import model_validator is a clever workaround but fragile.
- Fix approach: Restructure imports to break the circular dependency (e.g., move Protocol definitions to a dedicated `matmaster/types/protocols.py` that has no downstream imports from `matmaster/core/`).

**Phase 33 Placeholders in AgentRuntimeSpec:**
- Issue: `capability_policy` and `structural_validation` fields are declared with comments "Phase 33 defines..." but are not yet implemented.
- Files: `matmaster/types/runtime.py` (lines 88-89)
- Impact: Low -- unused placeholder fields consuming model space.
- Fix approach: Implement or remove when Phase 33 scope is finalized.

**tool_runner.py Docstring References Unimplemented Plan:**
- Issue: Module docstring states "Phase 2 (Plan 33) will implement the full ToolRunner with ToolCatalog lookup -> StructuralValidation -> RunStateGuard -> CapabilityPolicy -> ToolScheduler -> executor -> release." This plan has not been executed.
- Files: `matmaster/core/tool_runner.py` (lines 10-12)
- Impact: Low -- misleading future-looking documentation.
- Fix approach: Update docstring to reflect current state.

## Security Considerations

**BaseTable SQL Column Name Interpolation:**
- Risk: `src/base/base_table.py` constructs SQL via f-strings for column names, table names, and WHERE clause keys. While values are parameterized (using `%s` placeholders with `cursor.execute(sql, values)`), column names and table name are directly interpolated from dict keys.
- Files: `src/base/base_table.py` (lines 100-104, 134, 176, 206, 233)
- Current mitigation: Column names come from internal code (dict keys), not user input. Table names are class-level constants.
- Recommendations: Add a whitelist of allowed column names per table class. If `where` dict keys ever originate from external input (API params), this becomes a SQL injection vector via column name manipulation.

**Broad Exception Swallowing in Credential Resolution:**
- Risk: Bohrium access key resolution uses a bare `except Exception` that silently catches all errors from the primary credential source, falling back to environment variable.
- Files: `matmaster/adaptors/calculation/job_service.py` (lines 62-68)
- Current mitigation: The fallback is reasonable but errors from `get_bohrium_credentials()` are silently swallowed.
- Recommendations: Log the exception at warning level before falling back. Narrow the except clause to expected failures (ImportError, KeyError).

**BaseTable Catches BaseException:**
- Risk: `src/base/base_table.py` `get_connection()` catches `BaseException` (line 41), which includes `KeyboardInterrupt` and `SystemExit`. This can prevent clean process shutdown.
- Files: `src/base/base_table.py` (lines 41, 76)
- Current mitigation: The exception is re-raised after rollback/logging.
- Recommendations: Change to `except Exception` -- `BaseException` catch should only be used in cleanup-guaranteed contexts, not for general error handling.

## Performance Bottlenecks

**No MySQL Connection Pooling:**
- Problem: `src/base/base_table.py` creates a new `pymysql.connect()` for every database operation via the `get_connection()` context manager. Every single query opens and closes a TCP connection.
- Files: `src/base/base_table.py` (lines 34-48)
- Cause: No connection pool layer. Each DAO method (`find_one`, `insert`, `update`, `delete`) pays full connection setup cost.
- Improvement path: Introduce `DBUtils.PooledDB` or SQLAlchemy connection pool. The per-operation connection pattern is the most impactful performance concern for the `src/` layer.

**Synchronous DB in Async Event Persistence:**
- Problem: `PersistenceHandler.handle()` wraps synchronous `events_table.add_event()` in `asyncio.to_thread()`. Every persisted event spawns a thread-pool task. Combined with the per-operation connection pattern above, this means each event = 1 thread + 1 new MySQL connection.
- Files: `matmaster/integration/persistence_handler.py` (line 75), `src/base/base_table.py`
- Cause: Sync DB client + `to_thread()` per event + no connection pool.
- Improvement path: Connection pooling alone would significantly reduce overhead. For further gains, batch event persistence writes or use an async MySQL driver.

**threading.Event for Stop Signal in Async Kernel:**
- Problem: `AgentKernel` accepts `threading.Event` for cancellation, requiring periodic polling (`is_set()` checks every N chunks). In an async context, `asyncio.Event` would allow `await`-based cancellation with zero polling overhead.
- Files: `matmaster/core/agent.py` (lines 64-66: `_STOP_CHECK_EVERY_N_STREAM_CHUNKS = 8`, `_STOP_RETRY_SLEEP_SLICE_SEC = 0.25`), `matmaster/core/tool_runner.py` (line 51)
- Cause: The Worker (`src/worker/agent_worker.py`) provides `RedisBackedStopEvent` which mimics `threading.Event.is_set()`. The kernel is async but receives a sync signal primitive.
- Improvement path: Define a `StopSignal` Protocol with both sync and async interfaces. The kernel could `await stop_signal.wait()` in retry backoff instead of sliced sleep polling.

**stream_service.py Approaching Refactoring Threshold:**
- Problem: `src/services/stream_service.py` is 986 lines, close to the project's 1000-line threshold.
- Files: `src/services/stream_service.py`
- Cause: Combines SSE queue management, subscription logic, message dispatch, Redis pub/sub bridging, and Feishu notification.
- Improvement path: Extract Redis subscription bridging and SSE event formatting into separate modules.

**agent_run_bohrium.py Exceeds Complexity Threshold:**
- Problem: `src/services/agent_run_bohrium.py` is 951 lines and contains 17+ `except Exception` blocks in cleanup/teardown chains.
- Files: `src/services/agent_run_bohrium.py`
- Cause: Handles SSH session lifecycle, workspace sync, Bohrium node management, skill sync, and post-run cleanup all in one file.
- Improvement path: Extract into BohriumSSHManager, BohriumWorkspaceSync, and BohriumCleanupChain classes.

## Fragile Areas

**Playground Session Injection Pattern:**
- Files: `matmaster/core/playground.py` (lines 64-68)
- Why fragile: `Playground.session` and `Playground._owns_session` are kept as writable attributes specifically so `src/services/agent_run_bohrium.py` can inject SSH sessions via `pg.session = ssh_session` and `pg._owns_session = False`. This bypasses the normal `prepare()` flow.
- Safe modification: Any change to Playground's session lifecycle must account for the Bohrium injection path. The `_setup_session()` method (line 350) also handles session restoration after Bohrium detach -- test this path explicitly.
- Test coverage: `tests/matmaster/core/test_playground_no_evomaster.py` tests import isolation but not session injection.

**Bohrium Cleanup Exception Cascade:**
- Files: `src/services/agent_run_bohrium.py`
- Why fragile: Cleanup logic uses nested try/except chains where each block catches `Exception` broadly. If an early cleanup step fails (e.g., SSH disconnect), later steps may operate on invalid state but exceptions are logged and swallowed.
- Safe modification: Test cleanup paths independently with mock SSH failures. Each except block should be reviewed for whether a narrower exception type is appropriate.
- Test coverage: No tests exist for `src/services/agent_run_bohrium.py`.

**lru_cache Singletons for All Services:**
- Files: `src/services/stream_service.py` (line 979), `src/services/agent_run_service.py` (line 477), `src/services/sessions_service.py` (line 384), `src/services/user_service.py` (line 321), `src/services/events_service.py` (line 71), `src/services/bohrium_node_service.py` (line 457), `src/services/deploy_state_service.py` (line 97), `src/services/worker_registry_service.py` (line 142), `src/dao/chat_events_table.py` (line 172), `src/dao/bohrium_nodes_table.py` (line 119)
- Why fragile: All 10+ service/DAO singletons use `@lru_cache` with no way to clear or reset them. In testing, services hold stale state across test cases. In production, if a MySQL connection error surfaces inside a cached DAO's `__init__` (which calls `init_table()`), the broken instance persists for the process lifetime.
- Safe modification: Consider a service registry pattern with explicit lifecycle management, or use `lru_cache` with a documented cache-clear strategy for testing (`get_xxx.cache_clear()`).

**hasattr Duck-Typing in AgentKernel:**
- Files: `matmaster/core/agent.py` (lines 130, 191, 245, 461)
- Why fragile: Four `hasattr()` checks are used to probe `spec.tool_registry` and `spec.compactor` for methods. This bypasses Protocol type checking and breaks if method names are renamed.
- Safe modification: Replace with Protocol-based isinstance checks or ensure the types are properly annotated (removing the need for hasattr probing).

## Scaling Limits

**Per-Query MySQL Connections:**
- Current capacity: Sufficient for single-worker deployment with low concurrency.
- Limit: Under concurrent agent runs, each event persistence call creates a new MySQL connection. With N concurrent runs and M events per run, peak connection rate = N * M (per event, not per run).
- Scaling path: Connection pool with bounded size. The pool can be shared across all DAO instances via a module-level factory.

**Single-Process MessageBus:**
- Current capacity: `matmaster/core/bus.py` uses `asyncio.Queue` -- single-process only.
- Limit: Cannot distribute event processing across multiple machines for a single run.
- Scaling path: The Worker architecture already uses Redis for cross-process communication. The MessageBus is scoped per-run, so this is not a current bottleneck.

## Dependencies at Risk

**pymysql (Sync-Only MySQL Driver):**
- Risk: pymysql is synchronous. In an async-first architecture (FastAPI + asyncio kernel), every DB call requires `asyncio.to_thread()` wrapping or blocks the event loop.
- Impact: Performance ceiling on DB-heavy paths (event persistence, session management). Every `to_thread` call consumes a thread-pool slot.
- Migration plan: Consider `aiomysql` or `asyncmy` for async-native MySQL access. Alternatively, adopt SQLAlchemy async engine which provides both pooling and async support.

**LLMConfig Legacy Format Support:**
- Risk: `matmaster/config/llm.py` `_normalize_legacy_or_explicit_schema()` (line 180) supports both a "legacy flat YAML format" and the normalized `profiles` format. This dual-schema support adds complexity and validation surface.
- Impact: Low currently, but if new config fields are added only to the normalized format, the legacy path silently produces incomplete configs.
- Migration plan: Set a deprecation timeline for the flat YAML format. Add a warning log when legacy format is detected.

## Test Coverage Gaps

**src/ Service Layer (Zero Unit Tests):**
- What's not tested: The entire `src/` directory (services, DAO, worker, API routes) has no unit tests. No `tests/src/` directory exists.
- Files: `src/services/agent_run_service.py` (485 lines), `src/services/stream_service.py` (986 lines), `src/services/agent_run_bohrium.py` (951 lines), `src/worker/agent_worker.py` (498 lines), `src/base/base_table.py` (241 lines), `src/services/chat_history.py` (589 lines)
- Risk: Changes to the service orchestration layer, cleanup logic, or SSE streaming cannot be verified automatically. The Bohrium cleanup chain in `agent_run_bohrium.py` is especially risky.
- Priority: High -- this layer orchestrates everything and totals ~4,000+ lines of untested code.

**Async Tests Nearly Absent:**
- What's not tested: Only 1 async test function exists across 132 test files. The kernel, event router, tool_runner, and bus are all async but tested only through sync wrappers or mocks.
- Files: All `tests/matmaster/` directories
- Risk: Async-specific bugs (race conditions, cancellation edge cases, event loop issues) are not caught by the test suite.
- Priority: Medium -- the matmaster core layer has good sync test coverage (132 test files) but async behavior is assumed correct.

**Multi-Turn Dialog History Conversion:**
- What's not tested: `ChatHistoryConverter` (589 lines at `src/services/chat_history.py`) handles dialog history reconstruction from DB events into LLM message format.
- Files: `src/services/chat_history.py`
- Risk: Multi-turn conversation history could silently corrupt, truncate, or misorder messages, causing LLM context errors.
- Priority: Medium

**path_adaptor.py Complex Path Resolution:**
- What's not tested: `matmaster/adaptors/calculation/path_adaptor.py` (891 lines) handles local/remote path translation for Bohrium execution. Multiple `except Exception: pass` blocks silently eat path resolution failures.
- Files: `matmaster/adaptors/calculation/path_adaptor.py`
- Risk: Path resolution bugs cause tools to read/write wrong files on remote Bohrium nodes. Silent failures make debugging difficult.
- Priority: Medium

---

*Concerns audit: 2026-04-02*
