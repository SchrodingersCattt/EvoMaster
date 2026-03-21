# Pitfalls Research

**Domain:** AI Agent Framework Refactoring (callback-to-event-bus, untyped-to-typed, monolith-to-kernel, brownfield compatibility adapters)
**Researched:** 2026-03-21
**Confidence:** HIGH (based on codebase analysis + established refactoring literature)

## Critical Pitfalls

### Pitfall 1: Event Ordering Loss During Callback-to-MessageBus Migration

**What goes wrong:**
The current system guarantees strict ordering because callbacks execute synchronously inline with the agent step loop: `_on_assistant_message` fires, then `_on_tool_call_start`, then `_on_tool_message`, all within the same call stack. When migrating to a MessageBus with async queue semantics, this implicit ordering breaks. Specifically, `StreamingMatMasterAgent._emit()` currently calls `event_callback(source, event_type, content, **extra)` synchronously -- the caller in `AgentRunService` receives events in exactly the order they were emitted. A MessageBus with asyncio.Queue or any buffered channel can reorder events if multiple producers exist (e.g., the Planner solver emitting status events concurrently with the agent emitting tool events).

The matmaster system has a concrete dependency on event ordering: the frontend SSE stream expects `thought(start) -> thought(streaming)* -> thought(end) -> tool_call -> tool_result` per step. If `tool_result` arrives before `thought(end)`, the UI stream-state machine breaks. The `_current_stream_id` tracking in `StreamingMatMasterAgent` (lines 31, 46-55) assumes this ordering is preserved.

**Why it happens:**
Developers assume "event bus = just decouple the callback" without analyzing which ordering guarantees the consumers depend on. The callback-based system provides ordering for free (same call stack), which becomes invisible infrastructure that nobody documents until it breaks.

**How to avoid:**
1. Before writing any MessageBus code, enumerate all consumer ordering assumptions. In matmaster this is at minimum: (a) stream thought lifecycle `start->streaming->end` must be atomic per stream_id, (b) `tool_call` must precede its corresponding `tool_result`, (c) `finish` must be the terminal event for a run.
2. Use a single-producer-per-agent-run channel. The MessageBus should have one queue per agent execution, not one shared global bus. This preserves FIFO ordering within a run while allowing cross-run decoupling.
3. If the ResearchPlanner solver needs its own event stream (it currently has its own `_emit` method in `ResearchPlannerRuntimeMixin`), give it a separate channel that the consuming service merges with ordering metadata (sequence numbers or timestamps).
4. Write an ordering-invariant integration test before touching any callback code: record event sequences from current production, then verify the MessageBus reproduces identical sequences.

**Warning signs:**
- Frontend shows tool results without preceding tool_call events
- Thought stream `end` markers appear after the next `start` marker
- `_is_streaming_thought_event` filter in `agent_run_service.py` (line 71) starts producing false negatives
- Duplicate stream_id values appearing in SSE output (the bug documented in CONCERNS.md "Streaming Thought Events Create Duplicates" will get worse under reordering)

**Phase to address:**
MessageBus design phase -- before any consumer migration. The bus interface must encode ordering guarantees explicitly, not leave them implicit.

---

### Pitfall 2: Adapter Layer Becomes a Permanent Translation Tax

**What goes wrong:**
The compatibility adapter (bridging old `BasePlayground.run(task, event_callback=...)` entry points to new `PlaygroundContext + AgentRuntimeSpec` contracts) was meant as a temporary bridge. Instead, it accumulates special cases and nobody removes it because "it works." Six months later, every bug fix requires understanding both the old path and the new path, doubling cognitive load. The adapter becomes load-bearing infrastructure rather than a migration scaffold.

This risk is acute in matmaster because the old entry points have complex implicit contracts. For example, `MatMasterPlayground.setup()` does at least seven distinct things: config loading, session creation, skill registry init, MCP tool setup, tool registry assembly, agent construction with config_dict cascading, and solver mode routing. An adapter that wraps this into a PlaygroundContext will inevitably leak these concerns unless the decomposition is clean first.

**Why it happens:**
The adapter is the path of least resistance. When a new feature request arrives, developers can either (a) implement it in the new architecture and update the adapter, or (b) implement it in the old code and let the adapter pass it through. Option (b) is always faster short-term. Without a hard deadline for adapter removal, option (b) wins every time.

**How to avoid:**
1. Set a concrete adapter sunset milestone in the roadmap. The adapter exists for exactly one release cycle. After mat_master and minimal run on new contracts, the adapter is deleted.
2. Mark the adapter with `warnings.warn("DeprecationWarning: ...")` from day one, logging every invocation. If adapter call counts are not trending toward zero, the migration is stalled.
3. Never add new functionality to the adapter. New features go through the new contracts only. The adapter only bridges existing call sites.
4. Track adapter usage as a metric: count how many call sites go through the adapter vs. directly through new contracts. This number must monotonically decrease.

**Warning signs:**
- The adapter file grows beyond 200 lines
- Bug reports say "works through new API but fails through adapter" or vice versa
- New config keys are added to the adapter's translation logic
- The adapter starts doing business logic (validation, transformation) rather than pure interface mapping

**Phase to address:**
Adapter design phase and every subsequent phase. Build the adapter with its deletion plan.

---

### Pitfall 3: Typed Contract Explosion from Premature Modeling

**What goes wrong:**
When replacing 167 occurrences of `Dict[str, Any]` across 33 files in evomaster/ (measured from the codebase), the natural instinct is to create precise Pydantic models for every data shape. This leads to dozens of tightly-coupled models that reflect current implementation details rather than stable domain boundaries. When the implementation changes (which is the whole point of the refactor), these models need cascading updates. The "Jenga tower" problem from Pydantic migration: changing one model forces changes in all models that reference it.

The specific risk in matmaster: `config_dict` flows through `MatMasterPlayground.__init__` -> `_create_exp()` -> `MatMasterAgent.__init__` -> `ToolGuard.__init__` -> `AsyncToolRegistry.__init__`, with each consumer cherry-picking nested keys like `(config_dict or {}).get('mat_master', {}).get('planner', {}).get('quality_gates', {})`. If you model this entire tree as nested Pydantic models, you couple all consumers to the full config shape, when each actually only needs 2-3 fields.

**Why it happens:**
Developers equate "type safety" with "model everything." The 167 `Dict[str, Any]` occurrences feel like 167 bugs waiting to happen, creating urgency to model them all at once. But most of these dicts are internal implementation details, not cross-boundary contracts.

**How to avoid:**
1. Only create Pydantic models at the three contract boundaries: `PlaygroundContext`, `AgentRuntimeSpec`, and event payloads on the MessageBus. Internal implementation can stay as dicts or simple dataclasses initially.
2. For config cascading specifically, use a flat extraction pattern: each consumer declares a small typed config (e.g., `ToolGuardConfig(loop_threshold: int, auth_failure_threshold: int)`) and a factory function extracts it from the raw dict. This decouples consumers from the config tree shape.
3. Adopt the rule: if a type is only used within one module, it does not need to be a Pydantic BaseModel. Use `dataclass` or `TypedDict` for internal-only structures. Reserve Pydantic for serialization boundaries.
4. Start with `TypedDict` for read-only config extraction (it validates shape without runtime overhead), upgrade to Pydantic BaseModel only for structures that need validation, serialization, or schema generation.

**Warning signs:**
- More than 15 Pydantic models created before any agent can run on the new architecture
- Circular import errors from model interdependencies
- Models with `model_config = ConfigDict(extra='allow')` everywhere (sign that the model does not actually constrain anything)
- Config changes require updating 4+ model files

**Phase to address:**
Contract definition phase. Define the boundary contracts first, defer internal typing to the implementation phase of each component.

---

### Pitfall 4: Guard System Split Creates Behavioral Divergence

**What goes wrong:**
The plan splits guards into generic (loop detection, built into agent kernel) and business (manuscript gate, auth failure gate, injected by exp). The split sounds clean in theory but fails in practice because the guards have state coupling. The current `ToolGuard` class in `tool_guard.py` has six concerns that share state: loop detection uses a sliding window of recent tool fingerprints, the auth-failure gate increments counters based on tool observations, and the manuscript gate checks validation state. When split across kernel and exp, state synchronization between generic and business guards becomes the new source of bugs.

Concrete example: `ToolGuard.evaluate()` checks loop detection (generic) and then prepare-gate (business) and then auth-failure (business) in sequence. The auth-failure gate blocks `execute_bash` and `str_replace_editor` -- these are the same tools that loop detection monitors. If loop detection lives in the kernel and auth-failure lives in the exp-injected guard, their interaction (should auth-failure blocking also reset the loop counter? currently yes, implicitly) must be explicitly coordinated across the boundary.

**Why it happens:**
The guard concerns were developed organically as a single class precisely because they interact. Splitting them along a clean "generic vs business" boundary ignores the interaction patterns that motivated their co-location.

**How to avoid:**
1. Before splitting, map all state interactions between guard concerns. Create a dependency matrix: which guard reads/writes which state, and which guards' outcomes affect other guards' decisions.
2. Use a GuardChain pattern rather than splitting into separate objects: the kernel provides the chain infrastructure (ordered evaluation, early termination, context passing), and both generic and business guards are plugged into the same chain. Business guards are registered by the exp layer but execute within the kernel's chain, sharing a `GuardContext` that carries cross-guard state.
3. The `GuardContext` carries: recent_tool_fingerprints (for loop detection), auth_failure_counter (for auth gate), manuscript_state (for manuscript gate). This context is the shared state, owned by the kernel, populated by both generic and business guards.
4. Test the guard chain with the full set of guards first, then verify that removing business guards (for minimal playground) does not change generic guard behavior.

**Warning signs:**
- Loop detection false positives increase after the split (because auth-failure blocks no longer suppress loop counts)
- Manuscript gate fails to block finish because it cannot see the auth-failure state
- Two separate guard evaluation calls per tool step (kernel guard, then exp guard) with no shared context
- `EXEMPT_TOOL_SUFFIXES` and `EXEMPT_MANUSCRIPT_SCRIPTS` need to be duplicated in both guard locations

**Phase to address:**
Agent kernel design phase. The guard chain interface must be designed before implementing either generic or business guards separately.

---

### Pitfall 5: Kernel Extraction Hollows Out the Agent Without Transferring Behavior

**What goes wrong:**
The refactoring extracts a clean `AgentLoop` from the current `BaseAgent.run()` (lines 139-214). But the current `run()` method does more than just the loop: it initializes trajectory, catches RecursionError with special handling, manages `stop_event` from two injection paths, and handles the `_cancelled_from_step` flag. More critically, `MatMasterAgent` overrides `_step()` which contains: ToolGuard evaluation, ToolCallbackPipeline execution, async execution policy, context compaction triggering, tool observation summarization, auto-save, and execution journal tracking.

If the kernel only extracts the loop skeleton (LLM call -> tool exec -> message append -> repeat), all the real behavior stays in MatMasterAgent's overrides. The kernel becomes a trivial loop that nobody uses directly because all the complexity lives in the overrides. The refactoring achieves clean code at the kernel level but zero simplification at the application level.

**Why it happens:**
The nanobot reference architecture is a greenfield design with a clean AgentLoop. But matmaster is brownfield: the complexity in `MatMasterAgent._step()` is not accidental -- it represents real business requirements (observation summarization for context budget, auto-save for auditability, async execution for long-running DPA calculations). Extracting the loop without extracting the hook points creates a kernel that real agents cannot use.

**How to avoid:**
1. Identify the hook points that MatMasterAgent's `_step()` override needs, and design them into the kernel loop. At minimum: `pre_tool_call(tool_call) -> tool_call` (for guard evaluation and callback pipeline), `post_tool_call(tool_call, observation) -> observation` (for observation transformation, auto-save, journal), `pre_llm_call(dialog) -> dialog` (for context compaction), `should_continue(step_record) -> bool` (for custom finish logic).
2. These hook points must be the kernel's public extension API -- not abstract methods that subclasses override. Use a composition pattern: the kernel accepts a list of middleware/interceptors, and MatMasterAgent's behaviors become interceptors that plug into the kernel.
3. Validate the kernel design by implementing both mat_master and minimal agents using only the kernel's hook points, without overriding `_step()`. If either needs to override `_step()`, the hook point set is incomplete.
4. Move the tool output truncation logic (currently hardcoded `MAX_TOOL_OUTPUT = 30000` in BaseAgent._step line 344) into a configurable kernel policy rather than embedding it in the loop.

**Warning signs:**
- MatMasterAgent still overrides `_step()` after the kernel extraction
- The kernel has abstract methods instead of composition hooks
- Minimal agent works on the kernel but mat_master needs monkey-patches
- Hook points are added reactively as mat_master migration discovers missing extension points

**Phase to address:**
Kernel design phase. Design the hook point API by analyzing MatMasterAgent's `_step()` requirements first, then build the kernel to accommodate them.

---

### Pitfall 6: ContextCompactor Coupling Leaks Through the Kernel Boundary

**What goes wrong:**
Context compaction is currently initialized inside `MatMasterAgent.__init__` (lines 111-186) with deep config cascading: `config_dict -> agents -> general -> context -> compaction`. The compactor owns an LLM caller (either dedicated compaction LLM or fallback to agent LLM), interacts with the `ExecutionJournal`, and emits lifecycle events through the event callback. It is simultaneously a kernel concern (context window management) and a business concern (which LLM to use, how to configure thresholds).

If the kernel owns the compactor, it needs to know about LLM configuration (violating kernel purity). If the exp owns the compactor, the kernel cannot trigger compaction at the right moment in the step loop (between dialog preparation and LLM call, in `BaseAgent._step` line 271).

**Why it happens:**
Context compaction sits at the intersection of two concerns: execution (when to compact) and configuration (how to compact). The current code pushes both into the agent because there is no separation of "what the kernel decides" from "what the exp configures."

**How to avoid:**
1. Split compaction into policy (kernel) and implementation (exp-injected). The kernel defines a `ContextPolicy` interface with one method: `prepare_for_query(dialog) -> dialog`. The exp injects a concrete implementation that wraps the `ContextCompactor` with its LLM and config.
2. The kernel calls `self.context_policy.prepare_for_query(dialog)` at the right moment in the loop. It does not know or care whether the implementation does token counting, LLM-based summarization, or nothing at all.
3. For `AgentRuntimeSpec`, the compaction config becomes part of the spec as an already-constructed `ContextPolicy` instance, not raw config dict values that need parsing.
4. The `ExecutionJournal` dependency should flow through the policy object, not through the kernel. The kernel passes execution metadata (turn count, tool names) to the policy, and the policy decides what to journal.

**Warning signs:**
- The kernel imports `CompactionConfig` or `ContextCompactor` directly
- `AgentRuntimeSpec` contains raw compaction threshold values instead of a pre-built policy object
- The compaction LLM creation logic (currently 40 lines of try/except/fallback in MatMasterAgent.__init__) gets duplicated in the kernel

**Phase to address:**
AgentRuntimeSpec design phase. The ContextPolicy interface must be defined as part of the spec, before kernel implementation.

---

### Pitfall 7: Solver-to-Exp Absorption Breaks ResearchPlanner's Agent Lifecycle

**What goes wrong:**
The plan moves solver logic into the exp layer as "a higher-order assembly pattern." But `ResearchPlanner` is not just an assembler -- it manages a multi-phase lifecycle (planning -> execution -> evaluation -> replan) with its own state machine (`_research_planner_runtime.py`, `_research_planner_phases.py`). It creates and destroys multiple `DirectSolver` instances, each of which creates its own agent run. The exp layer, as currently designed in `BaseExp`, expects one agent and one run per exp invocation.

Absorbing the planner into exp means either: (a) the exp creates multiple agents internally (breaking the "exp = one agent assembly" contract), or (b) the planner orchestrates multiple exp instances (which makes the planner a layer above exp, not inside it).

**Why it happens:**
The desire to eliminate the "solver" abstraction as a separate concept. But solvers emerged precisely because some workflows need multi-agent orchestration within a single user request. Removing the concept does not remove the requirement.

**How to avoid:**
1. Recognize that ResearchPlanner is a multi-agent orchestrator, not an exp variant. The refactored architecture should support this as a first-class pattern: an exp can compose multiple agent runs, each with its own kernel execution.
2. Model this as `CompositeExp` or `OrchestratedExp` that holds multiple `AgentRuntimeSpec` instances and a state machine. The exp layer provides the composition, the kernel layer provides individual agent runs.
3. The planner's event emission (its own `_emit` method in `ResearchPlannerRuntimeMixin`) should go through the same MessageBus, with a source identifier that distinguishes Planner events from Agent events. Do not create a separate event system for the planner.
4. Test the planner lifecycle independently: create a test that runs the full planning->execution->evaluation cycle on the new architecture, before migrating mat_master's main entry point.

**Warning signs:**
- ResearchPlanner needs to reach into kernel internals to manage agent lifecycle
- Planner events bypass the MessageBus and use direct callbacks
- The exp layer has special-case code for "is this a planner exp?"
- Turn budget management (`_turn_budget_lock`, `_turn_budget_remaining`) cannot be expressed through AgentRuntimeSpec

**Phase to address:**
Exp layer design phase. The exp's ability to compose multiple agent runs must be a design goal, not an afterthought discovered during planner migration.

---

### Pitfall 8: MatToolCallbacks Coupling Breaks Under ToolRegistry Extraction

**What goes wrong:**
`MatToolCallbacks` (in `callback/base.py`) holds a direct reference to the agent instance (`self.agent`) and reaches deep into it: `self.agent.session`, `self.agent.current_dialog`, `self.agent.logger`, `self.agent.session.config.workspace_path`. There are 13 occurrences of `self.agent.` across 8 files in the callback subsystem. When the tool system is extracted into a standalone `ToolRegistry` with JSON Schema self-describing tools, these callbacks cannot follow because they depend on agent state that the registry does not (and should not) know about.

If the callbacks are moved outside the registry, they lose access to tool execution. If they stay inside, they create circular dependencies (registry -> agent -> registry).

**Why it happens:**
The callbacks were designed as agent extensions, not as tool-level concerns. They do things like auto-downloading OSS results (needs session), normalizing skill script arguments (needs config), and tracking async job submissions (needs dialog history). These are legitimate business requirements that cannot be removed, but they are wired through agent internals rather than through a clean interface.

**How to avoid:**
1. Introduce a `ToolExecutionContext` that the kernel passes to tool execution and callbacks. This context contains: session handle, workspace path, current dialog reference (read-only), logger, and config subset. Callbacks receive the context, not the agent.
2. Callbacks become `ToolMiddleware` in the new architecture. The kernel's tool execution pipeline is: `pre_middleware(context, tool_call) -> tool.execute(args) -> post_middleware(context, tool_call, result) -> result`. Middleware is registered by the exp, not by the agent.
3. The `ToolExecutionContext` is constructed per-step by the kernel from `AgentRuntimeSpec` components. It is a value object, not a reference to the agent. This breaks the circular dependency.
4. Migrate one callback at a time. Start with the simplest (e.g., `before_normalize_skill_script_args` which only reads tool_call arguments) and end with the most coupled (e.g., `after_autodownload_oss_results` which needs session and workspace path).

**Warning signs:**
- Callbacks still import from `..agent` or reference `self.agent` after migration
- The ToolExecutionContext grows to have 10+ fields (sign of leaking too much agent state)
- Callback ordering changes because middleware registration order differs from pipeline.register_before/after order
- After callbacks that modify observation text (`after_clean_sn_response`, `after_survey_reminder`) stop being called, causing context bloat

**Phase to address:**
ToolRegistry design phase. The ToolExecutionContext interface must be designed before implementing any tool middleware.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Adapter passes raw `config_dict` to new contracts instead of typed extraction | Fast bridge, zero config parsing rework | New code depends on old config shape; any config schema change requires adapter updates | Never -- adapter must translate to typed contracts from day one |
| Using `extra='allow'` on all Pydantic models during migration | No breakage from unexpected fields | Models lose their validation purpose; type safety is illusory | Only in the adapter layer, never in new contracts |
| Keeping `_on_llm_token` callback alongside MessageBus events | Streaming works immediately without bus integration | Two parallel event delivery paths that can diverge | During migration only, with a tracked removal deadline |
| Copy-pasting ToolGuard into both kernel and exp | Each location has exactly the guards it needs | Bug fixes must be applied in two places; guard interaction bugs surface | Never -- use the GuardChain pattern |
| Injecting agent reference into tool middleware (instead of ToolExecutionContext) | Immediate access to all agent state | Circular dependency; cannot test middleware without a full agent | Never -- breaks testability guarantee |
| Leaving Solver as a separate layer instead of absorbing into exp | No planner refactoring needed in v1 | Three-and-a-half layers (playground, exp, solver, agent) instead of the target three | Acceptable only if documented as intentional deferral with tracked cleanup |

## Integration Gotchas

Common mistakes when connecting components during migration.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| MessageBus + AgentRunService SSE | Assuming bus.subscribe() returns events in agent emission order when the bus has multiple channels | Use per-run ordered channels; AgentRunService subscribes to the specific run channel, not a global topic |
| ToolRegistry + MCP tools | Registering MCP tools eagerly at playground init (current pattern), causing asyncio event loop conflicts when ToolRegistry is now kernel-owned | Defer MCP tool registration to agent start time; use lazy initialization with the kernel's event loop |
| AgentRuntimeSpec + CompactionConfig | Passing compaction config as raw dict values in the spec, requiring the kernel to construct CompactionConfig internally | Pass a pre-built ContextPolicy instance in the spec; the exp is responsible for LLM selection and config parsing |
| PlaygroundContext + session workspace sync | Assuming workspace path is immutable after context creation (current code in MatMasterPlayground.set_run_dir dynamically updates session.config.workspace_path) | PlaygroundContext must carry a workspace_path_provider (callable) or the session must be reconfigured through the context interface |
| Compatibility adapter + event_callback | Adapter translates old `event_callback(source, type, content, **extra)` to MessageBus.publish(), but loses `**extra` kwargs that consumers depend on (stream_state, stream_id, token_count) | Define event payload types that include all current extra fields as named attributes |
| New ToolRegistry + enabled_tool_names filtering | The current agent filters tools via `enabled_tool_names` and `AsyncExecutionPolicy`; new ToolRegistry may not preserve this filtering | ToolRegistry must support view/projection: `registry.filtered_view(enabled_names, policy)` returns a read-only subset |

## Performance Traps

Patterns that work at small scale but fail as usage grows.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| MessageBus per-event publish overhead (event object creation + serialization per token during streaming) | Streaming thought latency increases from ~5ms to ~50ms per token because each delta creates an Event object | Use raw channel writes for streaming tokens; only wrap in Event objects for durable/structured events | At 20+ concurrent agents streaming simultaneously |
| GuardChain evaluating all guards for every tool call (6 guards x N tool calls per step) | Tool execution latency increases 10-30ms per call | Short-circuit evaluation: if loop detection blocks, skip manuscript gate. Order guards by rejection probability (loop detection first, manuscript gate last) | At 50+ tool calls per agent run |
| Typed contract validation on hot path (Pydantic model_validate on every tool result) | Context compaction triggers more frequently because validation overhead increases effective turn time | Use model_construct (skip validation) for internal passes; validate only at boundary crossings (API input, config load) | At 200+ turns per run |
| PlaygroundContext re-creation per session when playground is cached | Current code caches playground instance across sessions; if PlaygroundContext is re-created each time, config loading overhead repeats | Cache PlaygroundContext alongside playground; invalidate only on config changes | At 100+ sessions per hour on the same worker |

## Security Mistakes

Domain-specific security issues during migration.

| Mistake | Risk | Prevention |
|---------|------|------------|
| Adapter exposes old unvalidated config_dict paths that bypass new typed contract validation | Attacker could inject unexpected config keys through adapter that new guards do not check | Adapter must validate input against new contract types before forwarding; never pass raw dicts through |
| MessageBus event payloads leak internal state (tool observations with credentials, session workspace paths) | Cross-run event leakage if bus channels are not properly scoped per session | Scope bus channels to session_id; sanitize event payloads at publish time; never include raw tool observations in bus events |
| Guard split leaves auth-failure gate as "business guard" that exp can choose not to inject | Agent runs without auth-failure protection if exp forgets to register the guard | Auth-failure gate is a security guard, not a business guard; it belongs in the kernel alongside loop detection |
| ToolExecutionContext carries session handle that middleware can use to execute arbitrary commands | Malicious or buggy middleware could use session.exec_bash to run commands outside tool scope | ToolExecutionContext provides a restricted session interface (read workspace, download files) not full exec_bash access |

## UX Pitfalls

User experience mistakes during framework migration.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Breaking config.yaml format during typed migration | Users' existing configs stop working with cryptic Pydantic validation errors | Provide config migration script that converts old YAML to new schema; show diff before applying |
| Changing event types or payload shapes without frontend coordination | Frontend SSE consumers break silently (events arrive but render incorrectly) | Version event payloads; adapter emits old-format events alongside new-format during migration |
| Removing implicit behaviors that users depend on (e.g., auto-save tool outputs, observation summarization) | Users lose functionality they never explicitly configured but relied on | Document all implicit behaviors before migration; make them explicit opt-in in new config with defaults matching old behavior |
| Guard rejection messages change format after split | Users see different error messages for the same guard blocking, causing confusion in support channels | Maintain a guard message catalog; both generic and business guards use the same message templates |

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- [ ] **MessageBus "works":** Often missing backpressure handling -- verify what happens when consumer is slower than producer (e.g., SSE client on slow network). Current callback blocks producer; new bus may drop events silently.
- [ ] **ToolRegistry "works":** Often missing tool filtering (enabled_tool_names, AsyncExecutionPolicy) -- verify that `_get_tool_specs()` in the new registry respects the same filtering as current `MatMasterAgent._get_tool_specs()`.
- [ ] **PlaygroundContext "works":** Often missing dynamic workspace path updates -- verify that `set_run_dir()` behavior (updating session.config.workspace_path at runtime) is supported through the context interface.
- [ ] **Guard chain "works":** Often missing cross-guard state sharing -- verify that auth-failure count reset after guard blocks a tool (currently implicit) still works when guards are separate chain links.
- [ ] **Adapter "works":** Often missing streaming thought protocol -- verify that the adapter correctly translates `_on_llm_token` callback into bus events with stream_state lifecycle (start/streaming/end).
- [ ] **Typed contracts "work":** Often missing `model_dump()` compatibility -- verify that code using `config.model_dump()` (used in 4+ places in mat_master playground) still produces the same dict structure after re-modeling.
- [ ] **Kernel "works":** Often missing RecursionError handling -- verify that the kernel's error handling matches current BaseAgent.run() (lines 191-204) which has special RecursionError treatment with `format_stack(limit=60)`.
- [ ] **Exp layer "works":** Often missing solver lifecycle -- verify that ResearchPlanner's multi-phase state machine (planning/execution/evaluation/replan) can run through the exp's composition interface.

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Event ordering broken in production | MEDIUM | Revert to callback mode via adapter; add sequence numbers to events; re-implement bus with ordered channel; redeploy |
| Adapter becomes permanent | HIGH | Audit all adapter call sites; create migration tickets per call site; set hard deadline with code review enforcement; delete adapter file on deadline (forces migration) |
| Typed contract explosion | MEDIUM | Consolidate models: delete internal-only Pydantic models, replace with TypedDict or dataclass; keep only boundary contracts; adjust imports |
| Guard behavioral divergence | HIGH | Reunify guards temporarily (revert split); re-analyze state interactions; redesign GuardChain with explicit shared context; re-split with tests |
| Kernel too thin (no hook points) | HIGH | Add hook points iteratively from MatMasterAgent._step() requirements; each hook point addition is a kernel release; consumers migrate one hook at a time |
| Compaction leaks through kernel | LOW | Extract ContextPolicy interface; move compaction behind it; kernel references only the interface; one-file change |
| Solver absorption breaks planner | HIGH | Revert solver to standalone layer; redesign exp composition API to support multi-agent orchestration; re-absorb after API is proven |
| Callback coupling breaks tool middleware | MEDIUM | Introduce ToolExecutionContext as intermediate step; migrate callbacks one at a time; keep agent reference as fallback during transition |

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Event ordering loss | MessageBus design | Integration test: record old event sequences, verify new bus reproduces them |
| Adapter permanence | Adapter design + every subsequent phase | Adapter call site count must decrease each phase; zero at final phase |
| Typed contract explosion | Contract definition (PlaygroundContext, AgentRuntimeSpec) | No more than 8 Pydantic BaseModel classes for boundary contracts; internal types use dataclass/TypedDict |
| Guard behavioral divergence | Agent kernel design (GuardChain interface) | Test matrix: all 6 guard concerns with shared context; removing business guards does not affect generic guards |
| Kernel too thin | Kernel design (hook point API) | Both mat_master and minimal agents use kernel without overriding _step() |
| Compaction coupling | AgentRuntimeSpec design (ContextPolicy interface) | Kernel imports zero compaction-related types; ContextPolicy is the only interface |
| Solver absorption breaks planner | Exp layer design (composition API) | ResearchPlanner runs full lifecycle through exp composition; no solver-layer imports in kernel or playground |
| Callback coupling breaks middleware | ToolRegistry design (ToolExecutionContext) | All 13 callback functions work with ToolExecutionContext; zero self.agent references in callbacks |

## Sources

- Codebase analysis: `evomaster/agent/agent.py`, `playground/mat_master/core/agent.py`, `playground/mat_master/service/stream_agent.py`, `playground/mat_master/core/tool_guard.py`, `playground/mat_master/core/callback/base.py`, `evomaster/core/playground.py`, `evomaster/core/exp.py`
- [Cosmic Python: Events and the Message Bus](https://www.cosmicpython.com/book/chapter_08_events_and_message_bus.html) -- event ordering, handler failure, implicit coupling through events
- [Shopify Engineering: Refactoring Legacy Code with the Strangler Fig Pattern](https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern) -- adapter sunset, incremental migration, dual-write risks
- [Pydantic Migration Guide](https://docs.pydantic.dev/latest/migration/) -- V1/V2 compatibility layer, model equality changes, Jenga tower dependency chains
- [Pydantic V1 to V2 Migration: Real-World Experience](https://potapov.me/en/make/pydantic-advanced-guide-1) -- hidden runtime issues, field metadata breaking changes
- [bubus: Production Event Bus](https://github.com/browser-use/bubus) -- ordering guarantees with parallel handlers, FIFO options, loop prevention
- Project concerns audit: `.planning/codebase/CONCERNS.md` (2026-03-21)
- Project architecture: `.planning/codebase/ARCHITECTURE.md` (2026-03-21)

---
*Pitfalls research for: AI Agent Framework Refactoring (matmaster-evo)*
*Researched: 2026-03-21*
