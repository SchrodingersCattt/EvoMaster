---
phase: 25
slug: session-playground
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-01
---

# Phase 25 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/matmaster/sessions/ -x -q` |
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

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 25-01-01 | 01 | 1 | PLAY-01 | unit | `uv run pytest tests/matmaster/sessions/test_local_session.py -x` | ❌ W0 | ⬜ pending |
| 25-01-02 | 01 | 1 | PLAY-01 | unit | `uv run pytest tests/matmaster/sessions/test_session_protocol.py -x` | ❌ W0 | ⬜ pending |
| 25-02-01 | 02 | 1 | PLAY-02 | unit | `uv run pytest tests/matmaster/sessions/test_session_factory.py -x` | ❌ W0 | ⬜ pending |
| 25-02-02 | 02 | 1 | PLAY-02 | unit | `uv run pytest tests/matmaster/sessions/test_docker_session.py -x` | ❌ W0 | ⬜ pending |
| 25-02-03 | 02 | 1 | PLAY-02 | unit | `uv run pytest tests/matmaster/sessions/test_ssh_session.py -x` | ❌ W0 | ⬜ pending |
| 25-03-01 | 03 | 2 | PLAY-03 | integration | `uv run pytest tests/matmaster/core/test_playground_native.py -x` | ❌ W0 | ⬜ pending |
| 25-03-02 | 03 | 2 | PLAY-03 | integration | `uv run pytest tests/matmaster/tools/test_tools_native_session.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/matmaster/sessions/` — directory structure for session tests
- [ ] `tests/matmaster/sessions/test_local_session.py` — stubs for PLAY-01
- [ ] `tests/matmaster/sessions/test_session_protocol.py` — protocol compliance tests
- [ ] `tests/matmaster/sessions/test_session_factory.py` — factory tests for PLAY-02
- [ ] `tests/matmaster/sessions/conftest.py` — shared fixtures (temp dirs, mock configs)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SSH session connects to Bohrium | PLAY-02 | Requires live SSH credentials | Start devshell, create SSHSession with test host, run `session.execute("echo ok")` |
| Docker session container lifecycle | PLAY-02 | Requires Docker daemon | Run `uv run pytest tests/matmaster/sessions/test_docker_session.py` with Docker running |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
