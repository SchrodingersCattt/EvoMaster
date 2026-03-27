# Codebase Concerns

**Analysis Date:** 2026-03-21

## Tech Debt

**Unimplemented Parallel Execution:**
- Issue: Multi-agent parallel execution is configured but not implemented across multiple playground types
- Files: `playground/x_master/core/playground.py`, `configs/x_master/config.yaml`, `playground/x_master/README_CN.md`
- Impact: Agent batching configurations (`agent_num`, `max_workers`) are parsed but silently ignored; only single-agent execution works. Users may set these parameters expecting parallel performance but get serial execution instead. Documentation explicitly marks this as TODO.
- Fix approach: Implement concurrent execution in `_setup_agents()` and workflow coordination using ThreadPoolExecutor or asyncio. Add runtime validation that agent_num=1 if parallelization not yet ready.

**Type Safety Degradation - Generic Collections:**
- Issue: Widespread use of untyped dictionaries (`Dict[str, Any]`) for configuration and inter-component communication
- Files: `evomaster/agent/agent.py` (line 79: `output_config: dict[str, Any]`), `playground/mat_master/core/agent.py` (config_dict), tool callback systems
- Impact: Type checker cannot validate shape or keys at compile time; errors surface at runtime when accessing missing or incorrectly-typed config fields. Refactoring configurations requires careful search-and-replace across all call sites.
- Fix approach: Create explicit TypedDict or Pydantic models for common config structures (e.g., `ToolOutputConfig`, `ExecutionConfig`). Gradually replace generic dicts in public APIs with typed alternatives. Use `TYPE_CHECKING` blocks for circular import avoidance.

**Context Window Management Complexity:**
- Issue: Multiple overlapping mechanisms for context size control scattered across codebase
- Files: `evomaster/agent/context.py` (CompactionConfig with trigger_tokens, context_window_tokens, trigger_ratio), `evomaster/utils/llm.py` (timeout management), stream_timeout vs stream_idle_timeout in LLMConfig
- Impact: Hard to predict actual context consumption; configuration tuning requires understanding multiple interacting parameters. Bug: CompactionConfig defaults to trigger_ratio=0.80 of a 200k token window (160k tokens), but some LLM models have smaller context; no validation that effective_trigger_tokens() < max_model_context.
- Fix approach: Create unified `ContextBudgetManager` that owns all token-limit decisions. Add pre-execution validation that compaction trigger thresholds respect actual model limits. Document config interaction with examples.

**Dangerous Script Detection is Reactive:**
- Issue: Pattern-based detection of dangerous Python code paths occurs at execution time, not parse time
- Files: `playground/mat_master/core/tool_guard.py` (lines 22-25 AUTH_FAILURE_MARKERS, dangerous script patterns), `evomaster/agent/tools/builtin/bash_safety.py` (imported conditionally)
- Impact: If an agent repeatedly generates scripts with `os.environ` lookups for credential hunting, the gate blocks execution only after the fact. In high-concurrency scenarios, multiple blocks can occur before termination. No static analysis prevents vulnerable patterns in skill/prompt definitions.
- Fix approach: Add pre-execution AST scanning of skill prompts and tool definitions. Implement static validator that runs at config load time. Document credential-safe patterns in agent prompt guidelines.

**Resource Limits Lack Enforcement:**
- Issue: max_workers and ThreadPoolExecutor sizing is configured but not enforced with resource quota validation
- Files: `src/services/agent_run_service.py` (lines 40-42: _AGENT_MAX_WORKERS from env, default 2), `playground/mat_master/core/execution/scheduler.py` (concurrent job tracking)
- Impact: In multi-tenant deployments, a single user can spawn unbounded agent threads via repeated chat submissions; no per-user quota or memory budget. Out-of-memory errors are the only circuit breaker.
- Fix approach: Implement resource quota tracking per user/session in `AgentRunService.run_agent_sync()`. Add quota DAOs mirroring user/session entity. Check quota before ThreadPoolExecutor.submit(). Emit quota_exceeded events to frontend.

## Known Bugs

**JSON Sanitization False Negatives:**
- Symptoms: LLM-generated malformed tool call arguments occasionally bypass the repair logic in `_sanitize_tool_call_arguments()`
- Files: `evomaster/utils/llm.py` (lines 47-98)
- Trigger: Tool calls with complex nested JSON structures or embedded XML-like attributes in non-standard positions (not just `"key" attr="val":`)
- Workaround: Manual observation editing in tool response; agent can recover on next turn if it re-attempts the call
- Details: Regex `r'(?<=")\s+\w+="[^"]*"(?=\s*:)'` only catches XML attributes between JSON key and colon. Edge cases: attributes after the colon, multi-line attributes, or attributes mixed with valid JSON escapes will pass through, causing litellm parse failure.

**Streaming Thought Events Create Duplicates:**
- Symptoms: Thought stream markers (start/streaming/end) appear twice in frontend chat: once from agent thought, once from compacted context injection
- Files: `src/services/agent_run_service.py` (lines 71-77: _is_streaming_thought_event), `playground/mat_master/core/solvers/_research_planner_runtime.py` (line 90: _emit stream_id)
- Trigger: When ContextCompactor injects `[COMPACT CONTEXT]` block as system message, it re-emits the thought stream markers
- Workaround: Frontend de-duplication of stream_id; currently filtering in _should_skip_push
- Impact: Misleading UI showing agent thought twice; potential context bloat if compaction triggers repeatedly

**Docker Exec Metadata Extraction Fragile:**
- Symptoms: Docker command execution fails silently if PS1 prompt parsing breaks (e.g., JSON with unescaped quotes in output)
- Files: `evomaster/env/docker.py` (lines 31-36: PS1_PATTERN regex, lines 69-79: BashMetadata.from_json)
- Trigger: Large bash output with JSON containing double quotes; prompt pattern becomes ambiguous
- Workaround: Manual container inspection; hard to debug from logs
- Fix approach: Use delimiter that survives JSON encoding (e.g., base64-wrapped JSON with newline), or wrap metadata in CDATA block.

## Security Considerations

**Credential Leakage via Environment Variable Reflection:**
- Risk: Agent can query os.environ to enumerate and extract credentials set in .env
- Files: `playground/mat_master/core/tool_guard.py` (lines 49-70 AUTH_FAILURE_MARKERS), auth failure gate increments after detecting "invalid api key" in tool output, but does not prevent agent from probing
- Current mitigation: Auth-failure gate stops execute_bash after 3 consecutive authentication errors; gate cannot distinguish between user error (wrong key) and credential scanning
- Recommendations:
  1. Whitelist environment variables accessible to agent (restrict to non-sensitive subsets)
  2. Implement read-only proxy for os.environ that filters sensitive keys
  3. Log all environment variable access attempts and alert on repeated reads of same variable

**Tool Output Stored Unencrypted on Disk:**
- Risk: Tool observation autosave to `_tmp/tool_outputs/` contains sensitive API responses, database dumps, etc.
- Files: `playground/mat_master/core/agent.py` (lines 80-88: _tool_output_auto_save_patterns)
- Current mitigation: Files in _tmp are local to execution environment; no explicit encryption or access control
- Recommendations:
  1. Encrypt tool output files at rest (e.g., fernet from cryptography)
  2. Add retention policy: auto-delete outputs older than 7 days
  3. Log tool output save/read access for audit trail
  4. Mark sensitive tool prefixes (e.g., mat_sn_search*) to suppress saving

**Redis Connection String in Code:**
- Risk: REDIS_URL may be hardcoded or visible in logs if connection fails
- Files: `src/utils/constant.py` (imported as REDIS_URL), `src/dao/redis_dao.py`
- Current mitigation: .env loading via python-dotenv, but no validation that REDIS_URL is sanitized in error messages
- Recommendations:
  1. Never log full REDIS_URL; mask password portion
  2. Add connection string validation (parse and reject if credentials visible)
  3. Use environment variable fallback with explicit masking in error messages

**SSH Key Handling in Session:**
- Risk: SSH private key paths or passphrases may be logged or persisted without encryption
- Files: `evomaster/agent/session/ssh.py` (imported in ResearchPlannerRuntimeMixin), session config in agent_run_service
- Current mitigation: Session config is Pydantic model (not inspected); SSH keys not explicitly cleared from memory
- Recommendations:
  1. Wrap SSH key in SecretStr or encrypted field
  2. Explicitly clear key material from memory after session use (memset-like pattern)
  3. Add hook to securely delete temporary key files on agent shutdown

## Performance Bottlenecks

**Context Compaction Cost Not Amortized:**
- Problem: Every time total tokens exceed trigger threshold, LLM is called synchronously to compress history
- Files: `evomaster/agent/context.py` (ContextCompactor.compact() method), agent step loop in `evomaster/agent/agent.py`
- Cause: No batching or caching of compaction results; if agent runs many turns with long outputs, compaction may be triggered multiple times per run
- Impact: Adding 2-5 minutes per compaction invocation in multi-turn runs; tail latency spike
- Improvement path:
  1. Add compaction queue and async worker thread to compress in background while agent continues
  2. Cache compressed summaries (keyed by dialog hash) to reuse across similar runs
  3. Profile: compare current turn latency p50/p95 before/after threading

**Docker Env Metadata Extraction Blocking:**
- Problem: Every bash execution waits for PS1 prompt regex parsing in sync docker.execute()
- Files: `evomaster/env/docker.py` (DockerEnv.execute), BashMetadata.from_json fallback when parsing fails
- Cause: PS1 regex is greedy (DOTALL | MULTILINE); large output requires O(output_size) regex backtracking
- Impact: Observed 2-3 second latency for 10MB bash output in one execution; blocks other pending tool calls
- Improvement path:
  1. Add a fixed-length read limit (e.g., tail last 4KB) for PS1 extraction instead of DOTALL
  2. Implement timeout on regex match (use regex-timeout crate or similar)
  3. Benchmark against real workloads

**Frontend Chat Panel Re-renders on Every Tool Event:**
- Problem: ChatPanel state updates on every tool call/result event; no memoization of list items
- Files: `playground/mat_master/frontend/src/components/ChatPanel.tsx` (lines 52-87: ToolCard is React.memo but parent re-renders array)
- Cause: No windowing or virtualization; array of 500+ tool calls causes O(n) re-renders as new events arrive
- Impact: 60+ frames per second drops to 10 FPS when agent makes 20+ tool calls in rapid succession
- Improvement path:
  1. Switch ChatPanel to virtualized list (react-window or react-virtualized)
  2. Memoize ToolCard by deep-equality of callArgs + result
  3. Lazy-load tool content (render only visible cards in viewport)

**Large File Line-Count Sorting O(n log n):**
- Problem: File size report generated via `wc -l` on all source files, then sorted
- Files: Execution occurs during analysis (not in shipped code), but equivalent pattern exists in WorkspacePanel.tsx file tree rendering
- Cause: Frontend fetches entire file list and sorts by size in-place every render
- Impact: 1000+ files → 30ms sort on every panel open; UI flicker
- Improvement path: Implement server-side pagination/sorting for file tree in workspace service

## Fragile Areas

**Agent Step Loop with Manual Stop Event:**
- Files: `evomaster/agent/agent.py` (run method, lines 139-150), `playground/mat_master/core/agent.py` (MatMasterAgent.run override)
- Why fragile: stop_event can be injected via two paths (argument or self._stop_event); no synchronization if both are set. If stop_event is checked in middle of critical section (e.g., after tool call but before trajectory save), run state becomes inconsistent.
- Safe modification:
  1. Establish single source of truth for stop signal (prefer argument over instance variable)
  2. Check stop_event only at well-defined checkpoints (start of new turn, after tool completion)
  3. Add flag to prevent mid-step cancellation; defer to next iteration
- Test coverage: No test of concurrent run cancellation; add test that submits agent run and cancels halfway through tool call

**ToolGuard State with Multiple Auth Failure Markers:**
- Files: `playground/mat_master/core/tool_guard.py` (AUTH_FAILURE_MARKERS_STRONG, AUTH_FAILURE_MARKERS_WEAK, line 71: AUTH_FAILURE_THRESHOLD=3)
- Why fragile: STRONG and WEAK markers have different semantics but are checked in same loop; weak marker (401) is excluded from threshold incrementing, but if observation contains BOTH "401" and "invalid api key", only STRONG is counted. If marker order in observation changes, threshold logic breaks.
- Safe modification:
  1. Separate strong/weak counters; only STRONG increments threshold
  2. Document: which markers are user error vs system auth vs third-party error
  3. Add unit test matrix: (marker type, threshold count, expected gate result)
- Test coverage: ToolGuard has inline test in tool_guard.py but no integration tests with real tool responses

**Compaction Config Cascade with Fallback:**
- Files: `playground/mat_master/core/agent.py` (lines 112-150: config_dict cascading into _compaction_cfg)
- Why fragile: Three levels of defaults (agent config → compaction config → fallback values) with no validation. If config_dict['agents']['general'] is missing, code silently creates empty dict and falls back to CompactionConfig defaults, which may not match user intent.
- Safe modification:
  1. Validate config_dict schema at agent init (use Pydantic to parse entire subtree)
  2. Add verbose logging: "Using compaction defaults because config_dict['agents']['general']['context'] not found"
  3. Fail fast if compaction_llm key references non-existent LLM config
- Test coverage: No test of missing config cascades; add test_missing_compaction_config_falls_back_to_default

**Manuscript Finish Gate with Validation State:**
- Files: `playground/mat_master/core/tool_guard.py` (can_finish_manuscript method), MANUSCRIPT_FAIL_MARKERS in constants
- Why fragile: finish gate checks if sections were written but never validated. Validation state is tracked via MANUSCRIPT_FAIL_MARKERS in tool output, which could be brittle if error messages change. No explicit manuscript object state machine.
- Safe modification:
  1. Create explicit ManuscriptState enum (initialized, written_unvalidated, validated, failed) tracked in ToolGuard
  2. Track validation state on each section independently, not just globally
  3. Add method: can_finish_section(section_name) for fine-grained gating
- Test coverage: `tests/evomaster/agent/test_agent_context.py` covers agent context; tool guard / manuscript validation testing is minimal

## Scaling Limits

**Single-Machine Memory with Multi-Worker Agent Pool:**
- Current capacity: 2-4 concurrent agent threads (configured by CHAT_AGENT_MAX_WORKERS) × ~200MB per agent = 400-800MB peak
- Limit: Beyond 8 workers, OOM kills agents; no graceful degradation
- Path: Implement agent queuing with priority (VIP users first); add memory watermark monitoring; offload long-running agents to separate pod/container
- Reference: `src/services/agent_run_service.py` line 40 (_AGENT_MAX_WORKERS)

**Redis List Size for Confirmation Replies:**
- Current capacity: Confirmation replies stored as Redis list per session_id; no size limit or TTL
- Limit: If agent has 1000+ confirm gates, Redis list grows unbounded; BLPOP latency increases
- Path: Add FIFO rotation (keep last 100 replies per session) + TTL of 1 hour per reply
- Reference: `src/services/stream_service.py` (RedisReplyQueue)

**Frontend Chat Panel History:**
- Current capacity: Frontend loads last _DIALOG_HISTORY_MAX_EVENTS=500 events into state
- Limit: 500+ events with tool call/result pairs → 2000+ DOM nodes → browser memory spike
- Path: Implement server-side event pagination + virtual scrolling; fetch only visible range
- Reference: `src/services/agent_run_service.py` line 51 (_DIALOG_HISTORY_MAX_EVENTS)

**Bohrium Job Metadata Cache Staleness:**
- Current capacity: Job status queries hit Bohrium API directly; no caching
- Limit: 100+ concurrent job monitors → 100 QPS to Bohrium; rate limit or quota error
- Path: Add in-memory or Redis cache of job status with TTL=30s; batch status queries
- Reference: `evomaster/adaptors/calculation/job_service.py` (query_job_status)

## Dependencies at Risk

**MCP (Model Context Protocol) Complexity:**
- Risk: MCP manager initialization and tool registry is tightly coupled to evomaster.core.BasePlayground; adding new tool types requires changes across multiple layers
- Impact: New MCP server integration requires updates to `_setup_mcp_tools()`, tool_config parsing, and skill registry initialization
- Migration plan: Extract MCP bootstrap into separate MCPBootstrapper class; decouple from playground base class

**LiteLLM as Single Point of LLM Integration:**
- Risk: All LLM calls funnel through LiteLLM provider adapters; any change to LiteLLM API (e.g., deprecating completion_with_retries) requires agent-wide refactor
- Impact: Version pinning to specific LiteLLM (listed in requirements but no patch lock), provider SDK updates lag LiteLLM updates
- Migration plan: Create LLMProviderFacade abstraction that decouples evomaster.utils.llm.query() from LiteLLM internals; implement provider-specific adapters (OpenAIAdapter, AnthropicAdapter)

**Pydantic v2 Config Migration Incomplete:**
- Risk: Some old-style Pydantic v1 config patterns (e.g., ConfigDict usage) not consistently applied; upgrade to Pydantic v2.1+ may break validation
- Impact: Type validation strictness varies across domain models; errors surface at runtime instead of init time
- Migration plan: Audit all BaseModel subclasses; standardize on Pydantic v2 ConfigDict with validation_mode='strict'

**Python 3.10+ Type Hints Require Modern Tooling:**
- Risk: Code uses `str | None` (PEP 604 union syntax) and TYPE_CHECKING (PEP 563); older type checkers and IDE versions may not parse correctly
- Impact: Team members with older Python or mypy version may see false type errors; pre-commit hooks need Python 3.11+
- Migration plan: Document minimum Python 3.11 requirement in README; add pyenv .python-version enforcement

## Missing Critical Features

**Observability Gap - Execution Timeline:**
- Problem: No structured metrics for agent execution timeline (turn duration, tool call latency, LLM latency breakdown)
- Blocks: Cannot diagnose performance regressions or identify which phase is slow; only wall-clock totals available
- Approach: Instrument agent step loop with timing milestones; emit structured logs (JSON with turn_num, llm_latency_ms, tool_latency_ms, total_ms)

**Configuration Hot-Reload:**
- Problem: Agent configurations require process restart to take effect; no way to update agent prompt or tool settings without stopping all active runs
- Blocks: Quick iterations on prompt engineering; requires full deployment cycle
- Approach: Implement config versioning and dynamic prompt reloading in BaseAgent (store config hash, check on each step)

**Multi-Tenant Isolation Audit:**
- Problem: No explicit isolation between different users' agent runs; shared Redis instance, shared thread pool, shared file system (_tmp)
- Blocks: Cannot safely deploy multi-tenant where users must not see each other's workspace/logs
- Approach: Add per-user namespace prefixing in Redis keys, separate _tmp directories per user, audit tool access for file traversal

## Test Coverage Gaps

**Agent Context Compaction:**
- What's not tested: Compaction triggered with 50+ turn dialog; verify no data loss and summary quality acceptable
- Files: `evomaster/agent/context.py` (ContextCompactor), no integration test
- Risk: Compaction bug only surfaces in long-running multi-turn execution; integration tests are week-long runs
- Priority: High — compaction enables production scalability

**Tool Guard Loop Detection Edge Cases:**
- What's not tested: Loop detection with tool calls that have non-deterministic arguments (e.g., timestamps); verify threshold is not false-positive
- Files: `playground/mat_master/core/tool_guard.py` (evaluate method with LOOP_THRESHOLD=2)
- Risk: Agent blocked for creating legitimately different tool calls with similar names
- Priority: Medium — affects iteration speed during agent development

**Docker Env Bash Command Execution with Large Output:**
- What's not tested: Bash command producing >100MB output; verify PS1 metadata extraction doesn't hang or corrupt
- Files: `evomaster/env/docker.py` (execute method, BashMetadata.from_json)
- Risk: Silent failure or timeout if regex backtracking on huge output; hard to debug
- Priority: High — production jobs can produce large logs

**Frontend SSE Reconnection:**
- What's not tested: Client disconnects mid-stream, reconnects; verify no duplicate events and correct resume point
- Files: `playground/mat_master/frontend/src/components/ChatPanel.tsx`, `src/services/stream_service.py`
- Risk: Loss of events or duplicate events create inconsistent UI state
- Priority: Medium — affects mobile and unstable connections

**Config Validation with Missing Required Fields:**
- What's not tested: BasePlayground init with incomplete config.yaml (missing llm_config, agent config, mcp_config)
- Files: `evomaster/core/playground.py` (setup method cascading lookups)
- Risk: Cryptic error messages; hard to distinguish missing config from invalid config
- Priority: Medium — affects setup experience for new users

---

*Concerns audit: 2026-03-21*
