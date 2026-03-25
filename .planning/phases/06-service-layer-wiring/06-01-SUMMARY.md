---
phase: 06-service-layer-wiring
plan: 01
subsystem: api
tags: [openai, llm-factory, pydantic, reasoning, provider-routing]

# Dependency graph
requires:
  - phase: 04-playground-layer
    provides: Playground.prepare() returning PlaygroundContext
  - phase: 02-agent-kernel
    provides: OpenAIProvider with LLMProvider Protocol
provides:
  - PlaygroundContext with session and config_dir fields for downstream Exp access
  - OpenAIProvider extra_kwargs passthrough for reasoning parameters
  - Config-driven _build_llm_provider factory replacing NotImplementedError stub
  - Model family routing with reasoning parameter application
affects: [06-service-layer-wiring, service-layer, llm-provider]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Config-driven LLM factory with profile resolution chain (model match > key match > default fallback)"
    - "Model family defaults dict mapping family name to reasoning protocol and temperature policy"
    - "extra_kwargs merge pattern in OpenAIProvider for provider-specific reasoning parameters"

key-files:
  created:
    - tests/matmaster/integration/test_llm_factory.py
  modified:
    - matmaster/types/context.py
    - matmaster/providers/openai_provider.py
    - matmaster/playground/playground.py
    - src/services/agent_run_service.py
    - tests/matmaster/types/test_context.py

key-decisions:
  - "PlaygroundContext uses arbitrary_types_allowed=True for session field accepting BaseSession instances"
  - "extra_kwargs merged via dict.update() after tools check, before SDK create() call"
  - "Model family resolution: explicit config model_family takes precedence over _infer_model_family substring matching"
  - "Profile resolution chain: model name match > profile key match > default profile fallback with model override"
  - "Temperature policy force_one_when_reasoning applied after reasoning protocol resolution"

patterns-established:
  - "LLM factory pattern: _resolve_llm_profile -> extract params -> resolve family -> build reasoning kwargs -> apply temp policy -> instantiate provider"
  - "Module-level helper functions for model family logic, importable for unit testing"

requirements-completed: [MIGR-01, MIGR-02]

# Metrics
duration: 5min
completed: 2026-03-22
---

# Phase 6 Plan 1: LLM Factory + PlaygroundContext Wiring Summary

**Config-driven _build_llm_provider replacing stub, with PlaygroundContext session/config_dir extension and OpenAIProvider reasoning passthrough via extra_kwargs**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-22T13:19:23Z
- **Completed:** 2026-03-22T13:24:30Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- PlaygroundContext extended with session (Any) and config_dir (Path|None) fields, enabling Exp layer to access session and config without hacky hasattr checks
- OpenAIProvider supports extra_kwargs parameter merged into all SDK calls (chat and chat_stream), enabling reasoning protocol passthrough
- _build_llm_provider is now a working config-driven factory with model family routing: Claude 4.6 gets anthropic_adaptive_thinking + temp=1, GPT-5 gets reasoning_effort, Gemini gets no reasoning params
- All 398 matmaster tests pass including 30 new tests

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend PlaygroundContext + OpenAIProvider extra_kwargs** - `6c49f6e` (feat)
2. **Task 2: Wire _build_llm_provider factory with config-driven provider routing** - `284bb1d` (feat)

_Note: TDD tasks had combined RED+GREEN commits per task._

## Files Created/Modified
- `matmaster/types/context.py` - Added session (Any) and config_dir (Path|None) fields with arbitrary_types_allowed
- `matmaster/providers/openai_provider.py` - Added extra_kwargs parameter merged into chat() and chat_stream()
- `matmaster/playground/playground.py` - Updated prepare() to populate session and config_dir on PlaygroundContext
- `src/services/agent_run_service.py` - Replaced _build_llm_provider stub with config-driven factory, added module-level helpers
- `tests/matmaster/types/test_context.py` - 5 new tests for session/config_dir fields
- `tests/matmaster/integration/test_llm_factory.py` - 25 new tests for LLM factory

## Decisions Made
- PlaygroundContext uses arbitrary_types_allowed=True because session field may receive BaseSession instances that Pydantic cannot validate by default
- extra_kwargs merged via dict.update() -- placed after tools check and before create() call to allow reasoning parameters to override base kwargs
- Model family resolution prioritizes explicit config model_family over _infer_model_family substring matching
- Profile resolution chain follows D-02/D-03: model name match > profile key match > default profile fallback with model override
- _build_llm_provider signature changed from (pg_ctx, llm_override, model_override) to (playground, model_override) per D-02 (llm_override deprecated)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- LLM factory is operational; production pipeline can now instantiate correct LLM providers from config
- PlaygroundContext carries session and config_dir for Phase 6 Plan 2 (DirectExp cleanup, builtin tool wiring)
- OpenAIProvider reasoning passthrough enables all model families (Claude, GPT-5, Gemini, DeepSeek)

## Self-Check: PASSED

All 7 files verified present. Both task commits (6c49f6e, 284bb1d) confirmed in git log. 398/398 tests passing.

---
*Phase: 06-service-layer-wiring*
*Completed: 2026-03-22*
