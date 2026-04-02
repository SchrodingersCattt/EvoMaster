---
phase: 33-toolrunner-toolscheduler
plan: 05
subsystem: tools
tags: [tool-runtime, tool-compiler, session-capabilities, tool-catalog, gap-closure]

requires:
  - phase: 33-03
    provides: FullToolRunner + ToolCatalog builtin metadata injection
  - phase: 33-04
    provides: Canonical effect_level enum needed by ToolCompiler rules
provides:
  - ToolCompiler that compiles Tool + RuntimeTopology into ToolInstance
  - Session.capabilities contract on Session Protocol, LocalSession, and SSHSession
  - ToolCatalog delegation to ToolCompiler with backward-compatible optional injection
affects: [phase-33-verification, phase-34-exp-integration, phase-35-tool-registry-degradation]

tech-stack:
  added: []
  patterns:
    - "Registration-time style compilation via ToolCompiler with cached ToolInstance reuse"
    - "Session capability data exposed as explicit protocol surface instead of comments/spec notes"

key-files:
  created:
    - matmaster/tools/tool_compiler.py
    - tests/matmaster/sessions/test_session_capabilities.py
    - tests/matmaster/tools/test_tool_compiler.py
  modified:
    - matmaster/tools/tool_catalog.py
    - matmaster/types/session.py
    - matmaster/sessions/local.py
    - matmaster/sessions/ssh.py
    - tests/matmaster/types/test_session_protocol.py

key-decisions:
  - "Keep ToolCatalog(registry) backward compatible by making compiler/topology optional keyword injections"
  - "Store compiled ToolInstance objects in ToolCatalog cache, but preserve lazy on-demand compilation for tools already present in the registry"
  - "Update the existing session protocol test to the new 9-member contract instead of treating the protocol expansion as an implementation failure"

patterns-established:
  - "Tool compiler owns builtin binding rules; ToolCatalog re-exports metadata for compatibility but no longer hardcodes compilation logic"
  - "Session capability exposure is a first-class runtime contract and should be asserted via protocol tests"

requirements-completed: [TCON-01, TRUN-03]

duration: 2min
completed: 2026-04-02
---

# Phase 33 Plan 05: ToolCompiler + Session Capabilities Summary

**Session implementations now expose explicit runtime capabilities, and ToolCatalog compiles tools through a dedicated ToolCompiler instead of hand-building ToolInstance metadata inline**

## Performance

- **Duration:** 2 min
- **Started:** 2026-04-02T13:03:09Z
- **Completed:** 2026-04-02T13:05:13Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- Added `Session.capabilities` to the protocol and implemented concrete capability reports for both `LocalSession` and `SSHSession`
- Introduced `ToolCompiler` as the structured compiler from `Tool` + `RuntimeTopology` to `ToolInstance`
- Refactored `ToolCatalog` to delegate compilation while preserving the existing `ToolCatalog(registry)` call shape used across tests and runtime code

## Task Commits

Each task was committed atomically:

1. **Task 1: Session.capabilities 声明 + LocalSession/SSHSession 实现 (TDD RED)** - `357a94e1` (test)
2. **Task 1: Session.capabilities 声明 + LocalSession/SSHSession 实现 (TDD GREEN)** - `e0c60a4a` (feat)
3. **Task 2: ToolCompiler 实现 + ToolCatalog 委托重构 (TDD RED)** - `5be1ba5c` (test)
4. **Task 2: ToolCompiler 实现 + ToolCatalog 委托重构 (TDD GREEN)** - `55c5736c` (feat)

## Files Created/Modified
- `matmaster/types/session.py` - Promoted `capabilities` from deferred comment to formal Session Protocol member
- `matmaster/sessions/local.py` - Added native/stateless LocalSession capabilities
- `matmaster/sessions/ssh.py` - Added sftp/upload-capable SSHSession capabilities
- `matmaster/tools/tool_compiler.py` - Centralized builtin binding/effect/resource compilation rules
- `matmaster/tools/tool_catalog.py` - Delegated ToolInstance construction to ToolCompiler with cached compiled instances
- `tests/matmaster/sessions/test_session_capabilities.py` - Added direct coverage for session capability values and protocol conformance
- `tests/matmaster/tools/test_tool_compiler.py` - Added ToolCompiler contract tests and ToolCatalog delegation checks
- `tests/matmaster/types/test_session_protocol.py` - Updated the legacy fake session to satisfy the expanded Session contract

## Decisions Made
- Used optional keyword injection (`compiler=...`, `topology=...`) for `ToolCatalog` so existing runtime assembly and tests did not need sweeping constructor rewrites
- Kept ToolCompiler rules topology-aware at the API level even though current builtin mappings are static, which leaves room for later topology-sensitive compilation without another signature break
- Treated the old `FakeSession` protocol test as stale test debt and updated it in-place because the protocol itself legitimately grew a new required member

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated stale Session protocol test to include capabilities**
- **Found during:** Task 1 (Session.capabilities 声明 + LocalSession/SSHSession 实现)
- **Issue:** `tests/matmaster/types/test_session_protocol.py` still encoded the old 8-member Session Protocol, so it reported a failure even after the new contract was implemented correctly
- **Fix:** Added `capabilities` to the fake protocol implementation and removed the outdated xfail on LocalSession protocol conformance
- **Files modified:** tests/matmaster/types/test_session_protocol.py
- **Verification:** `uv run pytest tests/matmaster/sessions/test_session_capabilities.py tests/matmaster/types/test_session_protocol.py -q`
- **Committed in:** 357a94e1 (Task 1 RED commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** No scope creep. The change only synchronized an outdated protocol test with the new required interface.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness

- Phase 33 verification can now assert Session capability availability and ToolCompiler-based catalog delegation directly
- Phase 34 can inject ToolCatalog/ToolCompiler into Exp runtime assembly without inventing a new binding model
- Phase 35 can degrade ToolRegistry responsibilities further because ToolInstance compilation is no longer hardcoded inside ToolCatalog

## Self-Check: PASSED

- `matmaster/tools/tool_compiler.py` exists and is 89 lines (>= 60)
- `tests/matmaster/tools/test_tool_compiler.py` exists and is 134 lines (>= 80)
- `tests/matmaster/sessions/test_session_capabilities.py` exists and is 59 lines (>= 30)
- `uv run pytest tests/matmaster/sessions/test_session_capabilities.py tests/matmaster/types/test_session_protocol.py tests/matmaster/tools/test_tool_compiler.py tests/matmaster/tools/test_tool_catalog.py tests/matmaster/core/test_builtin_claims.py tests/matmaster/core/test_full_tool_runner.py -x -q` passed (`65 passed`)
- All four task commits (`357a94e1`, `e0c60a4a`, `5be1ba5c`, `55c5736c`) exist in git history

---
*Phase: 33-toolrunner-toolscheduler*
*Completed: 2026-04-02*
