---
phase: 25-session-playground
plan: 02
subsystem: session
tags: [ssh, paramiko, tmux, sftp, session-protocol]

requires:
  - phase: 25-session-playground (plan 01)
    provides: Session Protocol, SSHSessionConfig, PS1_PATTERN, BashMetadata, LocalSession
provides:
  - SSHSession native implementation (direct paramiko, no SSHEnv intermediate)
  - SSHSession unit tests with mocked paramiko
  - SSHSession exported from matmaster.sessions
affects: [25-session-playground-plan-03, 28-bohrium-setup, playground-migration]

tech-stack:
  added: [paramiko (existing dep, now used directly in matmaster)]
  patterns: [single-class SSH session (no Env intermediate), SFTP-based file ops, tmux persistent shell via PS1 parsing]

key-files:
  created:
    - matmaster/sessions/ssh.py
    - tests/matmaster/sessions/test_ssh_session.py
  modified:
    - matmaster/sessions/__init__.py

key-decisions:
  - "Merged SSHSession + SSHEnv into single class directly holding paramiko.SSHClient"
  - "Kept upload_directory_tarball/ssh_exec/ssh_bash_noninteractive as public methods for external callers"

patterns-established:
  - "SSH session owns paramiko client directly, no intermediate Env layer"
  - "SFTP operations protected by threading.Lock for concurrent safety"
  - "tmux log reading uses daemon thread with timeout + ssh_exec fallback"

requirements-completed: [PLAY-02]

duration: 4min
completed: 2026-04-01
---

# Phase 25 Plan 02: SSHSession Migration Summary

**SSHSession native implementation merging evomaster SSHSession + SSHEnv into single-class direct paramiko ownership with tmux persistent shell**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-01T09:14:55Z
- **Completed:** 2026-04-01T09:19:24Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Merged evomaster's two-layer SSHSession (307 lines) + SSHEnv (630 lines) into single matmaster/sessions/ssh.py (477 lines)
- SSHSession directly holds paramiko.SSHClient and SFTPClient -- no BaseSession/BaseEnv inheritance
- Implements all 8 Session Protocol methods via structural typing (isinstance check passes)
- Exposes ssh_exec, ssh_bash_noninteractive, upload_file, upload_directory_tarball for external callers
- 16 unit tests with mocked paramiko covering lifecycle, file ops, Protocol conformance, and not-open guards

## Task Commits

Each task was committed atomically:

1. **Task 1: Create SSHSession native implementation** - `6c2475c7` (feat)
2. **Task 2: SSHSession unit tests + __init__ export** - `077d05d3` (test)

## Files Created/Modified

- `matmaster/sessions/ssh.py` -- Complete SSHSession with inlined SSHEnv logic (477 lines)
- `tests/matmaster/sessions/test_ssh_session.py` -- 16 tests with mocked paramiko
- `matmaster/sessions/__init__.py` -- Added SSHSession export

## Decisions Made

- Merged SSHSession + SSHEnv into single class: eliminates the Env intermediate layer, SSHSession directly holds paramiko.SSHClient. This matches the plan's D-10 decision.
- Kept upload_directory_tarball, ssh_exec, ssh_bash_noninteractive as public methods: these are called by external code (agent_run_bohrium.py, script_env.py) and need to remain accessible until Phase 28 migrates the callers.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Mock fixture needed adjustment: `_get_tmux_logs` reads SFTP file via context manager in a daemon thread, so the mock SFTP `open()` had to return a proper context manager mock with `read()` returning `b""` instead of a MagicMock. Fixed in the test fixture setup.

## User Setup Required

None - no external service configuration required.

## Known Stubs

None - SSHSession is a complete implementation of all Protocol methods and public helpers.

## Next Phase Readiness

- SSHSession and LocalSession both available from matmaster.sessions
- Plan 03 (SessionFactory + Playground config-driven session creation) can proceed
- External callers (agent_run_bohrium.py) still use evomaster SSHSession; Phase 28 will migrate them

---
*Phase: 25-session-playground*
*Completed: 2026-04-01*
