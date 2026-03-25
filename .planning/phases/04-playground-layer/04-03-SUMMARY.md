---
phase: 04-playground-layer
plan: 03
subsystem: assembly
tags: [adapter-pattern, tool-protocol, cleanup-lifecycle, capability-ownership]

# Dependency graph
requires:
  - phase: 03-exp-assembly-layer
    provides: "ToolRegistry, Tool Protocol, DirectExp, Exp base class, ContextBuilder"
  - phase: 04-playground-layer/01
    provides: "PlaygroundContext without mcp_manager/skill_registry fields"
provides:
  - "EvoToolAdapter: EvoMaster BaseTool -> matmaster Tool Protocol adapter"
  - "DirectExp capability ownership: mcp_config, skill_config, factory-based init"
  - "Exp.run() try/finally cleanup guarantee via _cleanup_callbacks"
affects: [05-integration-quality]

# Tech tracking
tech-stack:
  added: []
  patterns: [adapter-pattern, factory-callback-injection, try-finally-cleanup]

key-files:
  created:
    - matmaster/assembly/evomaster_tool_adapter.py
    - tests/matmaster/assembly/test_evomaster_tool_adapter.py
  modified:
    - matmaster/assembly/__init__.py
    - matmaster/assembly/direct_exp.py
    - matmaster/assembly/exp.py
    - tests/matmaster/assembly/test_direct_exp.py
    - tests/matmaster/assembly/test_exp.py

key-decisions:
  - "EvoToolAdapter is a standalone class (not inheriting BaseTool) -- cleanly wraps any EvoMaster tool without coupling"
  - "Factory callback pattern for skill/MCP init -- DirectExp receives callables, not concrete instances, enabling test isolation"
  - "All cleanup callbacks execute independently -- one failing callback does not prevent others from running"
  - "Structured observation (dict/list) JSON-serialized by adapter -- matmaster Tool Protocol contract remains str-only"

patterns-established:
  - "Adapter pattern: EvoToolAdapter bridges EvoMaster BaseTool to matmaster Tool Protocol"
  - "Factory injection: DirectExp accepts callable factories for capability initialization"
  - "Finally cleanup: Exp.run() guarantees cleanup via _cleanup_callbacks even on kernel error"

requirements-completed: [WKSP-01]

# Metrics
duration: 4min
completed: 2026-03-22
---

# Phase 4 Plan 3: Exp Capability Ownership Summary

**EvoToolAdapter bridges EvoMaster tools to matmaster Tool Protocol; DirectExp owns MCP/Skill init via factory injection with Exp.run() finally-based cleanup**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-22T06:14:11Z
- **Completed:** 2026-03-22T06:18:45Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 7

## Accomplishments
- EvoToolAdapter wraps any EvoMaster BaseTool (SkillTool, MCPTool) into matmaster Tool Protocol without weakening the contract
- DirectExp owns capability initialization: accepts mcp_config, skill_config, and factory callables; assembles tools in assemble() not from PlaygroundContext
- Exp.run() guarantees cleanup via try/finally _cleanup_callbacks, even when kernel.run() raises
- PlaygroundContext is now purely environment-only -- no capability object access anywhere in matmaster assembly

## Task Commits

Each task was committed atomically:

1. **Task 1: Add EvoMaster-to-matmaster tool adapter** - `0aa8529` (feat)
2. **Task 2: Move capability initialization and cleanup into Exp/DirectExp** - `20c8505` (feat)

_Both tasks followed TDD (RED -> GREEN) flow._

## Files Created/Modified
- `matmaster/assembly/evomaster_tool_adapter.py` - EvoToolAdapter class bridging EvoMaster BaseTool to matmaster Tool Protocol
- `matmaster/assembly/__init__.py` - Added EvoToolAdapter export
- `matmaster/assembly/exp.py` - Added _cleanup_callbacks, _register_cleanup(), _run_cleanup_callbacks(), try/finally in run()
- `matmaster/assembly/direct_exp.py` - Capability ownership: session, mcp_config, skill_config, factory params; _init_skill_tools(), _init_mcp_tools()
- `tests/matmaster/assembly/test_evomaster_tool_adapter.py` - 8 tests for adapter behavior and protocol compliance
- `tests/matmaster/assembly/test_direct_exp.py` - 5 new tests for capability ownership (skill factory, MCP factory, cleanup registration)
- `tests/matmaster/assembly/test_exp.py` - 5 new tests for cleanup callback lifecycle (init, register, success, error, multiple)

## Decisions Made
- EvoToolAdapter wraps BaseTool without inheritance -- clean adapter pattern, no EvoMaster coupling in matmaster types
- Factory callback injection (skill_registry_factory, mcp_manager_factory) enables test isolation with fake factories
- Cleanup callbacks execute independently with exception logging -- one broken callback does not prevent others
- Structured observation (dict/list) from EvoMaster is JSON-serialized -- matmaster Tool Protocol contract stays str-only

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 4 boundary correction is complete: Playground is pure environment, Exp owns all capability init and cleanup
- Phase 5 (integration/quality) can proceed with full matmaster stack: contracts, kernel, assembly, playground all aligned
- Service layer integration in Phase 5 has clear interfaces: Playground.prepare() -> PlaygroundContext, Exp(mcp_config, skill_config) -> assemble(ctx) -> run()

---
*Phase: 04-playground-layer*
*Completed: 2026-03-22*
