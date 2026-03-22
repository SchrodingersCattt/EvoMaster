---
phase: 5
slug: integration-quality
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-03-22
---

# Phase 5 -- Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (version in .venv) |
| **Config file** | pytest.ini |
| **Quick run command** | `.venv/bin/python -m pytest tests/matmaster/ -x -q` |
| **Full suite command** | `.venv/bin/python -m pytest tests/ -x` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `.venv/bin/python -m pytest tests/matmaster/ -x -q`
- **After every plan wave:** Run `.venv/bin/python -m pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------|-------------------|--------|
| 5-01-01 | 01 | 1 | MIGR-01 | unit | `.venv/bin/python -m pytest tests/matmaster/engine/test_agent.py tests/matmaster/types/test_context.py -x -q` | pending |
| 5-01-02 | 01 | 1 | MIGR-02 | unit | `.venv/bin/python -m pytest tests/matmaster/hooks/ -x -q` | pending |
| 5-02-01 | 02 | 1 | MIGR-01 | unit | `.venv/bin/python -m pytest tests/matmaster/integration/test_event_router.py -x -q` | pending |
| 5-02-02 | 02 | 1 | QUAL-04 | unit | `.venv/bin/python -m pytest tests/matmaster/integration/test_workspace_handler.py -x -q` | pending |
| 5-03-01 | 03 | 2 | MIGR-01 | unit + behavioral | `.venv/bin/python -m pytest tests/matmaster/integration/test_events_to_messages.py -x -q` | pending |
| 5-03-02 | 03 | 2 | MIGR-02, QUAL-05 | integration | `.venv/bin/python -m pytest tests/matmaster/ -x -q` | pending |
| 5-04-01 | 04 | 3 | QUAL-01, QUAL-02 | unit + E2E | `.venv/bin/python -m pytest tests/matmaster/types/ tests/matmaster/integration/test_e2e_mat_master.py tests/matmaster/integration/test_e2e_minimal.py tests/matmaster/integration/test_pipeline_alignment.py -x -q` | pending |
| 5-04-02 | 04 | 3 | QUAL-04, QUAL-05 | integration | `.venv/bin/python -m pytest tests/matmaster/integration/test_upstream_scenarios.py tests/matmaster/integration/test_quota_pipeline.py -x -q` | pending |
| 5-05-01 | 05 | 3 | QUAL-03 | manual-only | `test -f docs/migration-guide.md && grep -c "Architecture Changes" docs/migration-guide.md` | pending |
| 5-05-02 | 05 | 3 | QUAL-03 | checkpoint | N/A -- human-verify checkpoint | pending |

*Status: pending / green / red / flaky*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Migration document completeness | QUAL-03 | Document content requires human review | Review docs/migration-guide.md: verify old vs new architecture comparison, replaced components, pipeline flow, config changes, breaking changes, deprecation notices |
| Real LLM API E2E (D-09) | D-09 (locked decision) | D-09 specifies direct real LLM API call via CLI mode; requires live API key and config.yaml credentials. Unit/integration tests use mock LLM per D-10 | 1. Ensure config.yaml has valid API keys. 2. Run via CLI mode (not automated tests). 3. Verify mat_master completes a real agent run with LLM responses. Note: D-10 covers automated mock testing; D-09 is a manual validation step |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify commands
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
