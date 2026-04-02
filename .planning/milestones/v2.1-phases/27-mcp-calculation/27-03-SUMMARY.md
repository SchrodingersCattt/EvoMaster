---
phase: 27-mcp-calculation
plan: 03
subsystem: mcp
tags: [mcp, lazy-mcp, direct-call, import-migration, decoupling]

# Dependency graph
requires:
  - phase: 27-mcp-calculation plan 01
    provides: MCPConnection ABC + MCPToolManager (matmaster/mcp/)
  - phase: 27-mcp-calculation plan 02
    provides: matmaster/adaptors/calculation/ (path_adaptor, job_service, env_config)
provides:
  - LazyMCPTool direct MCPConnection.call_tool execution (no MCPTool intermediate layer)
  - LazyMCPConnector with matmaster-native MCPToolManager
  - All 8 matmaster files migrated from evomaster MCP/calculation imports
  - cache_mcp_schemas adapted for dict-based tools_by_server
affects: [phase-28, evomaster-removal, matmaster-independence]

# Tech tracking
tech-stack:
  added: []
  patterns: [direct-call-mcp, format-result-content-list, async-ensure-connection]

key-files:
  modified:
    - matmaster/tools/lazy_mcp.py
    - matmaster/tools/cache_mcp_schemas.py
    - matmaster/core/exp.py
    - matmaster/eval_tooling_snapshot.py
    - matmaster/tools/builtin/monitor_job/_lifecycle.py
    - matmaster/tools/builtin/monitor_job/_llm.py
    - matmaster/tools/builtin/monitor_job/_logs.py
    - matmaster/tools/builtin/monitor_job/_download.py
    - tests/matmaster/tools/test_lazy_mcp.py

key-decisions:
  - "LazyMCPTool.execute calls MCPConnection.call_tool directly, eliminating MCPTool intermediate layer and asyncio.to_thread bridging"
  - "LazyMCPConnector.ensure_connection returns {connection, path_adaptor} dict instead of MCPTool instance"
  - "cache_mcp_schemas adapted to dict-based tools_by_server (tool_info dict instead of MCPTool.get_tool_spec)"
  - "connect_and_get_tool kept as legacy sync method for backward compatibility"

patterns-established:
  - "Direct MCP call pattern: LazyMCPTool -> MCPConnection.call_tool (no intermediate)"
  - "_format_result handles MCP SDK content list (TextContent.text, dict text, str fallback)"

requirements-completed: [MCP-01, CALC-01, CALC-02]

# Metrics
duration: 8min
completed: 2026-04-01
---

# Phase 27 Plan 03: LazyMCPTool Direct-Call Rewrite + Import Migration Summary

**LazyMCPTool rewritten to call MCPConnection.call_tool directly (no MCPTool intermediate), all 8 matmaster files migrated from evomaster MCP/calculation imports to matmaster-native paths**

## Performance

- **Duration:** 8 min
- **Started:** 2026-04-01T11:04:18Z
- **Completed:** 2026-04-01T11:12:33Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments
- LazyMCPTool.execute now awaits MCPConnection.call_tool directly, eliminating MCPTool intermediate layer and asyncio.to_thread bridging
- Added _format_result method to handle MCP SDK content list formatting (TextContent, dict, str fallback, JSON parsing)
- LazyMCPConnector.ensure_connection returns {connection, path_adaptor} for the new direct-call path
- All 7 production files and 1 test file switched from evomaster to matmaster-native imports
- cache_mcp_schemas adapted to dict-based tools_by_server (tool_info["name"] instead of tool.get_tool_spec())
- 31 tests passing covering direct-call execution, _format_result edge cases, path_adaptor integration, and no-evomaster audit

## Task Commits

Each task was committed atomically:

1. **Task 1: Rewrite LazyMCPTool + LazyMCPConnector** - `82d07e07` (feat)
2. **Task 2: Batch modify import paths (7 files)** - `c2ee5100` (refactor)
3. **Task 3: Update test_lazy_mcp.py** - `b25d55b4` (test)

## Files Created/Modified
- `matmaster/tools/lazy_mcp.py` - LazyMCPTool direct MCPConnection.call_tool + _format_result + LazyMCPConnector.ensure_connection
- `matmaster/tools/cache_mcp_schemas.py` - MCPToolManager from matmaster.mcp.manager + dict-based tool access
- `matmaster/core/exp.py` - resolve_mcp_config_path from matmaster.adaptors.calculation
- `matmaster/eval_tooling_snapshot.py` - resolve_mcp_config_path from matmaster.adaptors.calculation
- `matmaster/tools/builtin/monitor_job/_lifecycle.py` - job_service from matmaster.adaptors.calculation
- `matmaster/tools/builtin/monitor_job/_llm.py` - terminate_job from matmaster.adaptors.calculation
- `matmaster/tools/builtin/monitor_job/_logs.py` - iterate_job_files + query/download from matmaster.adaptors.calculation
- `matmaster/tools/builtin/monitor_job/_download.py` - download/iterate/token from matmaster.adaptors.calculation
- `tests/matmaster/tools/test_lazy_mcp.py` - 31 tests adapted for direct-call architecture

## Decisions Made
- LazyMCPTool.execute calls MCPConnection.call_tool directly -- eliminates MCPTool as unnecessary intermediate layer, removes asyncio.to_thread since MCPConnection.call_tool is already async
- LazyMCPConnector.ensure_connection returns {connection, path_adaptor} dict -- cleaner than the old connect_and_get_tool returning MCPTool instances
- connect_and_get_tool kept as legacy sync method for any remaining callers (backward compat)
- cache_mcp_schemas tool loop adapted to read from dict (tool_info["remote_tool_name"]) instead of MCPTool attribute access (tool.get_tool_spec())

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## Known Stubs
None - all production code is fully implemented.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 27 (mcp-calculation) is now complete: matmaster/mcp/ package, matmaster/adaptors/calculation/, and all import migrations done
- matmaster/ runtime path no longer imports evomaster.agent.tools.mcp or evomaster.adaptors.calculation
- Remaining evomaster imports in matmaster/ are: evomaster.env.bohrium (3 lazy imports in path_adaptor/job_service, per D-08), evomaster.config/utils (_llm.py), evomaster.agent.session.local (bash_tool.py)
- Ready for Phase 28 which targets the remaining evomaster dependencies

## Self-Check: PASSED

All 10 files verified present. All 3 commits (82d07e07, c2ee5100, b25d55b4) verified in git log.

---
*Phase: 27-mcp-calculation*
*Completed: 2026-04-01*
