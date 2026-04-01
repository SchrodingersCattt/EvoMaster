---
phase: 25
slug: session-playground
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-01
updated: 2026-04-01
validated: 2026-04-01
---

# Phase 25 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/matmaster/sessions/ tests/matmaster/types/test_session_protocol.py -x -q` |
| **Full suite command** | `uv run pytest tests/ -x -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/matmaster/sessions/ -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------|-------------------|--------|
| 25-01-01 | 01 | 1 | PLAY-01 | unit | `uv run pytest tests/matmaster/types/test_session_protocol.py -x` | green |
| 25-01-02 | 01 | 1 | PLAY-01 | unit | `uv run pytest tests/matmaster/sessions/test_local.py -x` | green |
| 25-02-01 | 02 | 2 | PLAY-02 | unit | `uv run python -c "from matmaster.sessions.ssh import SSHSession; print('OK')"` | green |
| 25-02-02 | 02 | 2 | PLAY-02 | unit | `uv run pytest tests/matmaster/sessions/test_ssh_session.py -x` | green |
| 25-03-01 | 03 | 3 | PLAY-02, PLAY-03 | integration | `uv run python -c "from matmaster.core.playground import Playground; pg = Playground(session_type='local', session_config={'workspace_path': '/tmp'}); assert pg._session_type == 'local'; print('OK')"` | green |
| 25-03-02 | 03 | 3 | PLAY-03 | integration | `uv run pytest tests/matmaster/core/test_playground_no_evomaster.py -x` | green |
| 25-03-03 | 03 | 3 | PLAY-03 | integration | `uv run pytest tests/matmaster/core/test_playground.py tests/matmaster/core/test_playground_manager.py tests/matmaster/core/test_playground_config_paths.py -x` | green |
| 25-03-04 | 03 | 3 | PLAY-03 | integration | `uv run python -c "from src.services.agent_run_service import AgentRunService; print('OK')"` | green |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

All test files are created by their respective plan tasks (TDD pattern: tests are written as part of the task). No separate Wave 0 scaffolding needed.

- Plan 01 Task 1 creates `tests/matmaster/types/test_session_protocol.py`
- Plan 01 Task 2 extends `tests/matmaster/sessions/test_local.py` (existing file)
- Plan 02 Task 2 creates `tests/matmaster/sessions/test_ssh_session.py`
- Plan 03 Task 2 creates `tests/matmaster/core/test_playground_no_evomaster.py` and updates existing playground tests

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SSH session connects to Bohrium | PLAY-02 | Requires live SSH credentials | Start devshell, create SSHSession with test host, run `session.exec_bash("echo ok")` |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify commands
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 test files created by task actions (TDD)
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated 2026-04-01

---

## Validation Audit 2026-04-01

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

All 8 task verification commands executed and passed. 105 tests green across 7 test files. 3 inline import checks passed.
