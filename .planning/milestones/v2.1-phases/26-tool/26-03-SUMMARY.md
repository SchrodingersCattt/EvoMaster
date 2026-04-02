---
phase: 26-tool
plan: 03
subsystem: tools
tags: [evomaster-decoupling, tool-registration, builtin-tool, exp-assembly]

# Dependency graph
requires:
  - phase: 26-01
    provides: Inlined bash_safety and editor helpers (no evomaster.agent.tools.builtin imports in bash_tool/edit_tool)
  - phase: 26-02
    provides: MonitorJobTool as matmaster native BuiltinTool subclass in matmaster/tools/builtin/monitor_job/
provides:
  - EvoToolAdapter eliminated from matmaster package (file deleted, exports cleaned, tests removed)
  - exp.py tool registration fully native (MonitorJobTool direct, no adapter wrapping)
  - playground.mat_master.tools.web_search dependency removed from exp.py
  - source='builtin_evo' eliminated from registration path
affects: [25-session-playground, 27-calculation, matmaster-independence]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "All builtin tools registered with source='builtin' (no dual-source builtin_evo)"
    - "MonitorJobTool constructed with session= and workdir= kwargs (BuiltinTool contract)"

key-files:
  created: []
  modified:
    - matmaster/core/exp.py
    - matmaster/tools/__init__.py
    - matmaster/tools/lazy_mcp.py
    - tests/matmaster/core/test_exp.py
  deleted:
    - matmaster/tools/evomaster_tool_adapter.py
    - tests/matmaster/tools/test_evomaster_tool_adapter.py

key-decisions:
  - "WebSearchTool already in native_tools list (line 379), no need to re-register in additional block -- removed playground web_search import entirely"
  - "MonitorJobTool registered via direct construction (session=ctx.session, workdir=exec_wd), not through adapter wrapping"
  - "Cleaned stale EvoToolAdapter reference in lazy_mcp.py docstring (comment-only, not runtime)"

patterns-established:
  - "All tool registration in exp.py uses source='builtin' uniformly (no adapter-specific sources)"
  - "Science-specific builtin tools (MonitorJobTool) follow same BuiltinTool contract as core tools"

requirements-completed: [TOOL-07, TOOL-10]

# Metrics
duration: 8min
completed: 2026-04-01
---

# Phase 26 Plan 03: EvoToolAdapter Elimination Summary

**Eliminated EvoToolAdapter from matmaster, replaced evo adapter tool registration with native BuiltinTool direct registration in exp.py, removed playground web_search dependency**

## Performance

- **Duration:** 8 min
- **Started:** 2026-04-01T08:53:51Z
- **Completed:** 2026-04-01T09:01:29Z
- **Tasks:** 3
- **Files modified:** 4
- **Files deleted:** 2

## Accomplishments
- exp.py tool registration path fully native: MonitorJobTool constructed as BuiltinTool with session/workdir kwargs, registered with source='builtin'
- EvoToolAdapter file deleted from matmaster/tools/, along with its test file (169 lines of tests for now-deleted adapter)
- matmaster/tools/__init__.py cleaned: only exports Tool and ToolRegistry (no adapter)
- playground.mat_master.tools.web_search import removed from exp.py (WebSearchTool already in native tools list)

## Task Commits

Each task was committed atomically:

1. **Task 1: Replace evo adapter registration with native builtin in exp.py** - `2d40b3db` (feat)
2. **Task 2: Delete EvoToolAdapter file, clean exports, delete test** - `a2a2e04a` (chore)
3. **Task 3: Update exp builtin tool tests for native registration** - `40bf1ce9` (test)

## Files Created/Modified
- `matmaster/core/exp.py` - Removed EvoToolAdapter import, replaced evo adapter tool block with native MonitorJobTool registration, updated docstring
- `matmaster/tools/__init__.py` - Removed EvoToolAdapter export, cleaned docstring
- `matmaster/tools/lazy_mcp.py` - Removed stale EvoToolAdapter comment reference
- `tests/matmaster/core/test_exp.py` - Updated tool counts (15 native, 0 evo), updated test assertions for native registration

**Deleted:**
- `matmaster/tools/evomaster_tool_adapter.py` - Adapter class no longer needed
- `tests/matmaster/tools/test_evomaster_tool_adapter.py` - Tests for deleted adapter

## Decisions Made
- WebSearchTool was already registered as a native builtin in the native_tools list (line 379 as `WebSearchTool()`), so removing the playground `get_web_search_tool()` import does not lose functionality -- it only removes the duplicate evo adapter registration
- MonitorJobTool uses the standard BuiltinTool construction pattern (session=, workdir=) matching all other native tools
- The stale EvoToolAdapter mention in lazy_mcp.py docstring was cleaned as a comment-only change (no runtime impact)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated exp builtin tool test assertions**
- **Found during:** Task 3 (regression test)
- **Issue:** test_exp.py had hardcoded counts (14 native + 2 evo = 16 total) and assertions checking source='builtin_evo' which no longer exists
- **Fix:** Updated counts to 15 native + 0 evo = 15 total; replaced evo source assertions with native builtin assertions; renamed test methods to reflect new behavior
- **Files modified:** tests/matmaster/core/test_exp.py
- **Verification:** 1075 passed, 3 skipped (excluding 1 pre-existing external API failure)
- **Committed in:** 40bf1ce9 (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - test assertion update)
**Impact on plan:** Necessary test update for correctness. No scope creep.

## Issues Encountered
- Pre-existing test failure in `tests/matmaster/integration/test_compaction_real_api.py` -- LiteLLM proxy returning 400 for claude-haiku-4-5 model (external service issue, not related to our changes)
- Pre-existing evomaster module loading through PlaygroundContext import chain (71 evomaster modules loaded via session/playground dependency) -- this is Phase 25 scope, not caused by Plan 26-03 changes

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all registration paths are fully functional.

## Next Phase Readiness
- matmaster/tools/ package is now clean: no EvoToolAdapter, no evomaster.agent.tools.builtin imports
- exp.py tool registration path is fully native (all builtin tools use source='builtin')
- The remaining evomaster dependency in exp.py is `evomaster.adaptors.calculation` in skill tools (lazy import, Phase 27 scope)
- The remaining evomaster dependencies through PlaygroundContext import chain are Phase 25 scope
- Ready for Phase 27 (calculation/MCP path internalization)

## Self-Check: PASSED

- matmaster/core/exp.py exists and contains native MonitorJobTool registration
- matmaster/tools/__init__.py exists and exports only Tool, ToolRegistry
- matmaster/tools/evomaster_tool_adapter.py does NOT exist (deleted)
- tests/matmaster/tools/test_evomaster_tool_adapter.py does NOT exist (deleted)
- Commit 2d40b3db (Task 1) found
- Commit a2a2e04a (Task 2) found
- Commit 40bf1ce9 (Task 3) found
- 1075 tests pass (excluding 1 pre-existing external API failure)

---
*Phase: 26-tool*
*Completed: 2026-04-01*
