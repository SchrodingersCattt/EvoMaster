# Phase 6: Service Layer Wiring - Research

**Researched:** 2026-03-22
**Domain:** Service layer integration -- LLM factory, builtin tool registration, PlaygroundContext extension, WorkerRegistry adaptation
**Confidence:** HIGH

## Summary

Phase 6 closes the remaining integration gaps between the matmaster framework (Phases 1-5) and the production service layer (`agent_run_service.py`). The core challenge is replacing four stubs/hacks with real implementations: (1) `_build_llm_provider` stub needs to become a config-driven LLM factory that reads YAML config, resolves env vars, matches model family, and instantiates OpenAIProvider with reasoning parameters; (2) `_get_builtin_tools` stub needs to be eliminated, with builtin tool construction moving into DirectExp.assemble(); (3) PlaygroundContext needs session/config_dir fields so DirectExp no longer needs separate constructor parameters; (4) WorkerRegistryService needs an adapter to satisfy the WorkerRegistry Protocol.

All key patterns are already established in the codebase. The EvoMaster `evomaster/utils/llm.py` contains mature model family defaults, reasoning protocol builders, and model inference logic that can be directly reused. The EvoToolAdapter pattern for wrapping EvoMaster tools is battle-tested in skill/MCP initialization. The frozen model `model_copy(update={...})` pattern is established via `with_bohrium()`. The WorkerRegistry Protocol is defined with 4 methods that closely mirror the existing service.

**Primary recommendation:** Implement in 4 logical units: (1) LLM factory in service layer, (2) PlaygroundContext extension + DirectExp parameter cleanup, (3) builtin tool construction in DirectExp.assemble(), (4) WorkerRegistry adapter. Guard shell removal is a simple cleanup task.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** All models route through LiteLLM proxy via OpenAI-compatible interface, single base_url + api_key (`LITELLM_PROXY_API_BASE` / `LITELLM_PROXY_API_KEY`)
- **D-02:** `llm_override` parameter deprecated (frontend no longer uses it), `model_override` is the only frontend override parameter (e.g. `azure/gpt-5`)
- **D-03:** Config-driven provider routing, hardcoded model family -> parameter template mapping. Match model family from `model_override`, use corresponding template to instantiate OpenAIProvider
- **D-04:** Model family parameter templates cover provider differences:
  - **Claude 4.6** (opus-4-6 / sonnet-4-6): `anthropic_adaptive_thinking` protocol -- `thinking: {type: 'adaptive'} + output_config: {effort: ...}`, temperature forced to 1
  - **Claude 4.5** (haiku-4-5): Legacy thinking protocol -- `thinking: {type: 'enabled', budget_tokens: N}`
  - **GPT-5 / Azure**: `reasoning_effort` parameter
  - **Gemini**: No special reasoning parameters
  - **Generic OpenAI-compatible** (qwen / cds): Basic parameters
- **D-05:** Reference `evomaster/utils/llm.py` existing logic: `_MODEL_FAMILY_DEFAULTS`, `_infer_model_family_from_model`, `_build_reasoning_request_overrides`
- **D-06:** FinishTool deprecated (Phase 1 decision). Builtin tools are only 3: BashTool, EditorTool, MonitorJobTool
- **D-07:** Builtin tools use EvoMaster implementations, wrapped via EvoToolAdapter for matmaster Tool Protocol
- **D-08:** Tool construction happens inside DirectExp.assemble(ctx), using ctx.session for EvoToolAdapter binding. Service layer removes `_get_builtin_tools()` method
- **D-09:** PlaygroundContext adds `session: Any = None` field (needs `arbitrary_types_allowed=True`)
- **D-10:** PlaygroundContext adds `config_dir: Path | None = None` field
- **D-11:** DirectExp constructor removes `session` and `config_dir` parameters (reads from ctx). Removes `builtin_tools` (builds in assemble)
- **D-12:** Service layer removes hacky `playground.session if hasattr(...)` and `playground.config_path.parent if hasattr(...)` access
- **D-13:** Remove ManuscriptGateGuard and AuthFailureGateGuard shell implementations from `matmaster/assembly/guards.py`
- **D-14:** Phase 6 injects no business guards. Guard injection mechanism from Phase 2-3 remains available
- **D-15:** Guard injection mechanism (DirectExp accepts guards -> GuardPipeline chained execution) built in Phase 2-3, keep operational
- **D-16:** Existing `worker_registry_service.py` (Redis implementation) adapted to WorkerRegistry Protocol via dependency injection into Exp layer

### Claude's Discretion
- Environment variable substitution implementation approach (os.environ.get or config layer preprocessing)
- Model family matching pattern (prefix matching vs substring matching vs regex)
- OpenAIProvider extended parameter field design (reasoning_protocol, thinking_effort, etc.)
- WorkerRegistry Protocol adaptation bridging approach
- MonitorJobTool conditional registration (config control)

### Deferred Ideas (OUT OF SCOPE)
- Guard business logic migration (manuscript gate, auth failure gate, structure-retrieval gate) -- future milestone, redesign as Hook not Guard
- Multiple LLM provider implementations (native Anthropic, native Google) -- all routes through LiteLLM OpenAI-compatible, no separate providers needed
- Legacy code cleanup (evomaster/, playground/mat_master/ deprecated modules) -- Phase 7 or later
- Nanobot-style tool rewrite (decouple from EvoMaster BaseTool system) -- long-term direction, EvoToolAdapter sufficient for now
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MIGR-01 | mat_master end-to-end on new skeleton | LLM factory implementation enables real provider; builtin tools + PlaygroundContext extension complete the production pipeline |
| MIGR-02 | minimal end-to-end on new skeleton | Same LLM factory works for minimal (different config YAML, same code path) |
| ASBL-02 | ToolRegistry unified registration | Builtin tools constructed in DirectExp.assemble() and registered via same ToolRegistry path as skill/MCP |
| ASBL-06 | WorkerRegistry interface + injection | WorkerRegistryServiceAdapter bridges existing Redis implementation to Protocol; injected via PlaygroundContext.run_meta or constructor |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| openai | existing | OpenAI SDK for LLM calls via LiteLLM proxy | Already used by OpenAIProvider, all models route through OpenAI-compatible API |
| pydantic | existing (v2) | Frozen models, config validation | Already used throughout for contracts |
| PyYAML | existing | Config YAML parsing | Already used by ConfigManager |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dotenv | existing | .env file loading for API keys | Already integrated in ConfigManager.load() |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hardcoded model family map | Dynamic plugin registry | Unnecessary complexity; 5 model families are stable enough for hardcoded map |
| WorkerRegistry Protocol adapter | Direct WorkerRegistryService usage | Breaks layer isolation; adapter maintains clean Protocol boundary |
| Session in PlaygroundContext | Session passed via run_meta dict | Typed field is safer and more discoverable than dict key |

**No new dependencies required.** All changes use existing libraries.

## Architecture Patterns

### Current State (Stubs to Wire)

```
agent_run_service.py
├── _build_llm_provider()        # stub -- raises NotImplementedError
├── _get_builtin_tools()         # stub -- returns []
├── DirectExp construction       # uses playground.session via hasattr
│   ├── session=playground.session if hasattr(...)
│   ├── config_dir=playground.config_path.parent if hasattr(...)
│   └── builtin_tools=self._get_builtin_tools(pg_ctx)
└── (no WorkerRegistry injection)
```

### Target State (Wired)

```
agent_run_service.py
├── _build_llm_provider()        # config-driven LLM factory
│   ├── Load llm config from Playground config
│   ├── Resolve model_override -> model family
│   ├── Apply reasoning parameters per family
│   └── Instantiate OpenAIProvider
├── (no _get_builtin_tools)      # REMOVED
├── PlaygroundContext             # carries session + config_dir
│   ├── session: Any = None
│   └── config_dir: Path | None = None
├── DirectExp construction       # reads from ctx
│   └── assemble(ctx) builds builtin tools from ctx.session
└── WorkerRegistryServiceAdapter # bridges Protocol
```

### Pattern 1: Config-Driven LLM Factory
**What:** `_build_llm_provider` reads YAML config, resolves model family, applies reasoning parameters, instantiates OpenAIProvider
**When to use:** Every agent run -- single entry point for all LLM provider construction

The factory follows this resolution chain:
1. Determine LLM profile key: `model_override` -> match against config YAML `llm` entries by model name, or use agent config `llm` key (e.g., "litellm")
2. Load the LLM config dict from `config.llm[profile_key]`
3. Env vars already substituted by ConfigManager._substitute_env (uses `${VAR}` pattern)
4. Infer model family from model string (reuse `_infer_model_family_from_model` logic)
5. Build reasoning request overrides (reuse `_build_reasoning_request_overrides` logic)
6. Apply temperature policy (force_one_when_reasoning for Claude 4.6)
7. Instantiate OpenAIProvider with resolved parameters

```python
# Source: derived from evomaster/utils/llm.py patterns
def _build_llm_provider(self, playground, model_override):
    config = playground.config
    llm_dict = config.llm  # dict of profile_key -> config dict

    # 1. Resolve which LLM profile to use
    profile_key = self._resolve_llm_profile_key(
        llm_dict, model_override
    )
    llm_cfg = llm_dict[profile_key]

    # 2. Extract parameters
    model = model_override or llm_cfg["model"]
    api_key = llm_cfg.get("api_key", "")
    base_url = llm_cfg.get("base_url")
    temperature = llm_cfg.get("temperature", 0.7)

    # 3. Model family resolution + temperature policy
    family = llm_cfg.get("model_family") or _infer_model_family(model)
    if _should_force_temperature_one(family, llm_cfg):
        temperature = 1.0

    # 4. Build provider with extra_body for reasoning
    return OpenAIProvider(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=llm_cfg.get("max_tokens"),
        timeout=llm_cfg.get("timeout", 300.0),
        max_retries=llm_cfg.get("max_retries", 3),
        retry_delay=llm_cfg.get("retry_delay", 1.0),
    )
```

### Pattern 2: Builtin Tool Construction in assemble()
**What:** DirectExp.assemble(ctx) creates BashTool/EditorTool/MonitorJobTool, wraps with EvoToolAdapter using ctx.session
**When to use:** Every DirectExp assembly -- same pattern as existing skill/MCP tool registration

```python
# Source: matmaster/assembly/direct_exp.py existing pattern
def _init_builtin_tools(self, ctx: PlaygroundContext, registry: ToolRegistry) -> None:
    """Construct and register builtin tools using ctx.session."""
    if ctx.session is None:
        logger.warning("No session in context, skipping builtin tools")
        return

    from evomaster.agent.tools.builtin import BashTool, EditorTool, MonitorJobTool

    for evo_tool in [BashTool(), EditorTool(), MonitorJobTool()]:
        adapted = EvoToolAdapter(evo_tool, ctx.session)
        registry.register(adapted, source="builtin")
```

### Pattern 3: Frozen Model Extension (PlaygroundContext)
**What:** Add session and config_dir fields to PlaygroundContext using Pydantic v2 model_config
**When to use:** When extending an existing frozen contract with new fields

```python
# Source: matmaster/types/context.py established pattern
class PlaygroundContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # Existing fields...
    workdir: Path
    session_type: str
    cache_area: Path
    env_vars: dict[str, str] = Field(default_factory=dict)
    archival: WorkspaceArchivalConfig | None = None
    run_meta: dict[str, Any] = Field(default_factory=dict)

    # New fields (Phase 6)
    session: Any = None      # EvoMaster BaseSession instance
    config_dir: Path | None = None  # Playground config directory
```

### Pattern 4: WorkerRegistry Adapter
**What:** Thin adapter class wrapping WorkerRegistryService to satisfy WorkerRegistry Protocol
**When to use:** Bridging src/services/ implementations to matmaster Protocol interfaces

Key difference to bridge:
- Protocol: `delete_session_run_owner(session_id) -> bool`
- Service: `delete_session_run_owner(session_id) -> None`

```python
class WorkerRegistryServiceAdapter:
    """Adapts WorkerRegistryService to matmaster WorkerRegistry Protocol."""

    def __init__(self, service: WorkerRegistryService) -> None:
        self._service = service

    def set_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        return self._service.set_session_run_owner(session_id, worker_id)

    def refresh_session_run_owner(self, session_id: str, worker_id: str) -> bool:
        return self._service.refresh_session_run_owner(session_id, worker_id)

    def delete_session_run_owner(self, session_id: str) -> bool:
        self._service.delete_session_run_owner(session_id)
        return True  # Service returns None; always True for Protocol

    def get_session_run_owner(self, session_id: str) -> str | None:
        return self._service.get_session_run_owner(session_id)
```

### Anti-Patterns to Avoid
- **Accessing Playground attributes via hasattr:** Current code uses `playground.session if hasattr(playground, "session")` which is fragile. Use typed PlaygroundContext.session instead.
- **Service layer building tools:** Tool construction belongs in Exp.assemble(), not in the service orchestration layer. The service should not know about BashTool/EditorTool.
- **Hardcoding env var names in factory:** Use the config dict which already has env vars substituted by ConfigManager.
- **Extending OpenAIProvider constructor for reasoning:** Reasoning parameters (extra_body, reasoning_effort) should be passed through the existing OpenAI SDK kwargs mechanism, not new constructor params on OpenAIProvider. The factory builds the provider; reasoning overrides are applied at call time or through SDK extra_body.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Model family inference | Custom model name parser | `_infer_model_family_from_model` from evomaster/utils/llm.py | Already handles all current model patterns; tested in production |
| Reasoning request construction | Manual extra_body assembly | `_build_anthropic_adaptive_thinking_request` / `_build_openai_reasoning_effort_request` from evomaster/utils/llm.py | Handles protocol differences between Claude adaptive thinking and OpenAI reasoning effort |
| Tool wrapping | Custom BaseTool -> Tool bridge | EvoToolAdapter | Battle-tested adapter for skill/MCP tools, handles JSON serialization and observation normalization |
| Env var substitution | Manual os.environ.get in factory | ConfigManager._substitute_env | Already recursively substitutes `${VAR}` patterns in loaded config dict |
| Session creation | Manual session factory in service | Playground.prepare() creates session | Session lifecycle is Playground responsibility; service gets it via PlaygroundContext.session |

**Key insight:** The evomaster/utils/llm.py module contains all the model-family-aware logic needed for provider routing. The factory just needs to read config, call existing utility functions, and instantiate OpenAIProvider. Do not reimplement the reasoning protocol builders or model family inference.

## Common Pitfalls

### Pitfall 1: OpenAIProvider Does Not Currently Support Reasoning Parameters
**What goes wrong:** OpenAIProvider.chat() only passes model, messages, temperature, max_tokens, tools to the SDK. It has no mechanism to pass `extra_body` (for Anthropic adaptive thinking via LiteLLM) or `reasoning_effort` (for GPT-5).
**Why it happens:** OpenAIProvider was built as a minimal implementation in Phase 2. Reasoning support was deferred.
**How to avoid:** Extend OpenAIProvider to accept optional `extra_kwargs` (dict) at construction time. These get merged into every SDK call. The factory builds the extra_kwargs from the model family config.
**Warning signs:** Test with a Claude model through LiteLLM -- if thinking content is not returned, extra_body is not being passed.

### Pitfall 2: PlaygroundContext frozen=True + arbitrary_types_allowed
**What goes wrong:** Adding `session: Any = None` to a frozen Pydantic model requires `arbitrary_types_allowed=True` in model_config, otherwise Pydantic validation rejects the BaseSession instance.
**Why it happens:** Pydantic v2 frozen models validate types strictly by default.
**How to avoid:** Add `arbitrary_types_allowed=True` to PlaygroundContext's `ConfigDict`. Verify all existing tests still pass (the field is optional with default None, so backward compatibility is maintained).
**Warning signs:** `PydanticUserError: ... arbitrary types are not allowed` at PlaygroundContext construction.

### Pitfall 3: Signature Mismatch in WorkerRegistryService
**What goes wrong:** The WorkerRegistry Protocol defines `delete_session_run_owner(session_id) -> bool` but the actual WorkerRegistryService returns `-> None`. Direct isinstance check will fail at runtime.
**Why it happens:** Protocol was designed in Phase 3 before examining the existing service's exact signatures.
**How to avoid:** Use an explicit adapter class (WorkerRegistryServiceAdapter) that wraps the service and provides the correct return types. Don't try to make WorkerRegistryService directly implement the Protocol.
**Warning signs:** `isinstance(service, WorkerRegistry)` returns False; tests that check Protocol conformance fail.

### Pitfall 4: model_override Resolution Ambiguity
**What goes wrong:** `model_override` could be a full model name (e.g., "azure/gpt-5") or a profile key (e.g., "azure"). The factory needs clear resolution logic.
**Why it happens:** The parameter is passed from the frontend as a model name, not a config key.
**How to avoid:** Resolution strategy: (1) If model_override matches a model name in any llm config entry, use that entry's profile; (2) If model_override matches a profile key directly, use that profile; (3) Fall back to agents.general.llm default key. Always use model_override as the actual model name for OpenAIProvider if provided.
**Warning signs:** Wrong LLM config applied; wrong api_key/base_url used for the overridden model.

### Pitfall 5: MonitorJobTool Depends on Session Credentials
**What goes wrong:** MonitorJobTool.execute() accesses `session._bohrium_credentials` for access_key and `session._stop_event` for cancellation. If these aren't set on the session, job monitoring silently falls back to env vars.
**Why it happens:** MonitorJobTool was designed to work with the legacy playground's session which has Bohrium credentials injected.
**How to avoid:** Ensure BohriumSetupService.setup() injects credentials into the session (it already does this in the current pipeline). The EvoToolAdapter passes session directly to tool.execute(), so credentials flow naturally.
**Warning signs:** MonitorJobTool falls back to env BOHRIUM_ACCESS_KEY; credentials from BohriumSetupService are not used.

### Pitfall 6: Circular Import Between Service Layer and matmaster
**What goes wrong:** agent_run_service.py already uses lazy imports for DirectExp (`from matmaster.assembly.direct_exp import DirectExp` inside the method). Adding more matmaster imports at module level could trigger circular import chains.
**Why it happens:** The service layer (src/) and framework layer (matmaster/) have bidirectional awareness through type annotations.
**How to avoid:** Keep matmaster imports inside methods or behind TYPE_CHECKING guards. The LLM factory helper functions can be standalone functions or static methods that import OpenAIProvider lazily.
**Warning signs:** ImportError at module load time; "partially initialized module" errors.

## Code Examples

### LLM Factory: Model Family Resolution
```python
# Source: evomaster/utils/llm.py L655-665 (existing, reusable)
def _infer_model_family_from_model(model: str) -> str | None:
    model_name = (model or '').strip().lower()
    if 'claude-sonnet-4-6' in model_name or 'claude-opus-4-6' in model_name:
        return 'claude-4.6'
    if 'gpt-5' in model_name:
        return 'gpt-5'
    if 'deepseek-reasoner' in model_name:
        return 'deepseek-reasoner'
    if 'gemini-3-flash-preview' in model_name:
        return 'gemini-3-flash-preview'
    return None
```

### LLM Factory: Reasoning Protocol Builders
```python
# Source: evomaster/utils/llm.py L636-646 (existing, reusable)
def _build_anthropic_adaptive_thinking_request(effort: str) -> dict[str, Any]:
    return {
        'extra_body': {
            'thinking': {'type': 'adaptive'},
            'output_config': {'effort': effort},
        }
    }

def _build_openai_reasoning_effort_request(effort: str) -> dict[str, Any]:
    return {'reasoning_effort': effort}
```

### Config YAML LLM Section Structure
```yaml
# Source: configs/mat_master/config.yaml
llm:
  litellm:         # profile key
    provider: "openai"
    model: "claude-opus-4-6"
    model_family: "claude-4.6"
    api_key: "${LITELLM_PROXY_API_KEY}"    # env-substituted by ConfigManager
    base_url: "${LITELLM_PROXY_API_BASE}"
    thinking_effort: "high"
    reasoning_protocol: "anthropic_adaptive_thinking"
    temperature: 0.7
    timeout: 300
    max_retries: 3
    retry_delay: 1.0
  azure:           # profile key
    model: "azure/gpt-5"
    model_family: "gpt-5"
    ...
  default: "litellm"  # default profile key
```

### Builtin Tool Construction via EvoToolAdapter
```python
# Source: matmaster/assembly/direct_exp.py L131-153 (existing pattern for skill tools)
# Builtin tools follow the same pattern:
from evomaster.agent.tools.builtin import BashTool, EditorTool, MonitorJobTool

for evo_tool in [BashTool(), EditorTool(), MonitorJobTool()]:
    adapted = EvoToolAdapter(evo_tool, ctx.session)
    registry.register(adapted, source="builtin")
```

### PlaygroundContext Extension Pattern
```python
# Source: matmaster/types/context.py L56-64 (existing with_bohrium pattern)
def with_bohrium(self, result: dict[str, Any]) -> "PlaygroundContext":
    updated_meta = {**self.run_meta, "bohrium": result}
    return self.model_copy(update={"run_meta": updated_meta})
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `_build_llm_provider` raises NotImplementedError | Config-driven factory with model family routing | Phase 6 (this phase) | Enables production LLM calls |
| `_get_builtin_tools` returns empty list | Builtin tools built in DirectExp.assemble(ctx) | Phase 6 (this phase) | Tools available for agent execution |
| `playground.session if hasattr(...)` | PlaygroundContext.session typed field | Phase 6 (this phase) | Clean data flow, no hasattr hacks |
| WorkerRegistryService used directly | Adapted to WorkerRegistry Protocol | Phase 6 (this phase) | Layer isolation maintained |
| ManuscriptGateGuard/AuthFailureGateGuard shells | Removed (no business guards in Phase 6) | Phase 6 (this phase) | Guard mechanism preserved, shells cleaned up |

## Open Questions

1. **OpenAIProvider extra_body passthrough mechanism**
   - What we know: OpenAI SDK supports `extra_body` kwarg in `client.chat.completions.create()`. LiteLLM uses this for Anthropic thinking protocol passthrough.
   - What's unclear: Should OpenAIProvider store extra_kwargs at construction (fixed per provider instance) or accept them per-call? Construction-time is simpler since model family determines reasoning protocol.
   - Recommendation: Construction-time `extra_kwargs: dict[str, Any]` merged into every SDK call. This is simpler, and model family doesn't change during a run.

2. **model_override -> LLM profile key resolution order**
   - What we know: `model_override` is a model name (e.g., "azure/gpt-5"), not a profile key. Config has profile keys ("litellm", "azure") with model names inside.
   - What's unclear: What if model_override doesn't match any configured model? Should it fall back to default profile with overridden model name?
   - Recommendation: (1) Search config entries for matching model name; (2) Search for matching profile key; (3) Use default profile key but override the model field. Log a warning for case (3).

3. **MonitorJobTool conditional registration**
   - What we know: MonitorJobTool depends on Bohrium infrastructure (remote job submission). In local-only setups, it's useless.
   - What's unclear: Should registration be gated by config (e.g., `mat_master.monitor_job` section existence)?
   - Recommendation: Always register MonitorJobTool. It gracefully handles missing credentials at execution time (falls back to env vars, returns error if none found). Config gating adds complexity without clear benefit.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.x |
| Config file | pytest.ini (root) |
| Quick run command | `python -m pytest tests/matmaster/ -x -q --timeout=30` |
| Full suite command | `python -m pytest tests/matmaster/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MIGR-01 | LLM factory produces valid OpenAIProvider from config | unit | `python -m pytest tests/matmaster/integration/test_llm_factory.py -x` | Wave 0 |
| MIGR-01 | model_override routes to correct LLM profile | unit | `python -m pytest tests/matmaster/integration/test_llm_factory.py::test_model_override_routing -x` | Wave 0 |
| MIGR-02 | Minimal config path produces valid provider | unit | `python -m pytest tests/matmaster/integration/test_llm_factory.py::test_minimal_config -x` | Wave 0 |
| ASBL-02 | Builtin tools registered in assemble via ctx.session | unit | `python -m pytest tests/matmaster/assembly/test_direct_exp.py::TestDirectExpBuiltinTools -x` | Wave 0 |
| ASBL-02 | PlaygroundContext carries session and config_dir | unit | `python -m pytest tests/matmaster/types/test_context.py::test_session_field -x` | Wave 0 |
| ASBL-06 | WorkerRegistryServiceAdapter satisfies Protocol | unit | `python -m pytest tests/matmaster/assembly/test_worker_registry.py::test_adapter_isinstance -x` | Wave 0 |
| MIGR-01 | E2E pipeline with real LLM factory (mock SDK) | integration | `python -m pytest tests/matmaster/integration/test_e2e_mat_master.py -x` | Existing (update) |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/matmaster/ -x -q --timeout=30`
- **Per wave merge:** `python -m pytest tests/matmaster/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/integration/test_llm_factory.py` -- covers MIGR-01, MIGR-02 (LLM factory unit tests)
- [ ] `tests/matmaster/assembly/test_direct_exp.py::TestDirectExpBuiltinTools` -- covers ASBL-02 (new test class in existing file)
- [ ] `tests/matmaster/types/test_context.py` -- covers ASBL-02 (PlaygroundContext new fields)
- [ ] `tests/matmaster/assembly/test_worker_registry.py::test_adapter_*` -- covers ASBL-06 (adapter tests in existing file)

## Sources

### Primary (HIGH confidence)
- `src/services/agent_run_service.py` -- current stubs, DirectExp construction, pipeline flow
- `src/services/worker_registry_service.py` -- existing Redis WorkerRegistry implementation, method signatures
- `matmaster/types/context.py` -- PlaygroundContext frozen model, with_bohrium pattern
- `matmaster/providers/openai_provider.py` -- current OpenAIProvider implementation
- `matmaster/assembly/direct_exp.py` -- DirectExp constructor, assemble(), skill/MCP init patterns
- `matmaster/assembly/evomaster_tool_adapter.py` -- EvoToolAdapter wrapping pattern
- `matmaster/assembly/worker_registry.py` -- WorkerRegistry Protocol definition (4 methods)
- `matmaster/assembly/guards.py` -- shell guard implementations to remove
- `evomaster/utils/llm.py` -- LLMConfig, _MODEL_FAMILY_DEFAULTS, reasoning builders, model family inference
- `evomaster/agent/tools/builtin/` -- BashTool, EditorTool, MonitorJobTool implementations
- `configs/mat_master/config.yaml` -- LLM config structure, model families, agent tools config
- `evomaster/config.py` -- ConfigManager, _substitute_env, EvoMasterConfig
- `matmaster/playground/playground.py` -- Playground.prepare(), session/config_path exposure

### Secondary (MEDIUM confidence)
- `tests/matmaster/assembly/test_direct_exp.py` -- existing test patterns for DirectExp
- `tests/matmaster/assembly/test_worker_registry.py` -- existing Protocol conformance tests

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in use, no new dependencies
- Architecture: HIGH -- all patterns established in codebase (EvoToolAdapter, frozen model extension, Protocol adapter)
- Pitfalls: HIGH -- identified from direct code inspection of signature mismatches and missing capabilities
- LLM factory: HIGH -- evomaster/utils/llm.py contains mature reference implementation; config.yaml structure well-understood
- WorkerRegistry adapter: HIGH -- Protocol and Service both inspected, signature difference identified and solution clear

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- internal codebase, no external API changes expected)
