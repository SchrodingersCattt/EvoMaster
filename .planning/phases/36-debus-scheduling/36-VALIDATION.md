---
phase: 36
slug: debus-scheduling
status: revised
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-03
updated: 2026-04-03
---

# Phase 36 — Validation Strategy

> Per-phase validation contract for the actual 4-plan, 4-wave execution shape.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest>=9.0.2` + `pytest-asyncio>=0.24.0` |
| **Config file** | `pyproject.toml` |
| **Fastest critical smoke** | `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q` |
| **Static audit** | `rg -n "MessageBus|EventRouter" matmaster src tests --glob '*.py'` |
| **Phase gate policy** | Run the four plan-level gate commands below in wave order, then run the static audit |

---

## Wave / Plan Map

| Wave | Plan | Requirements | Focus | Plan Gate |
|------|------|--------------|-------|-----------|
| 1 | `36-01` | `DBUS-01`, `DBUS-02` | fanout owner, Bohrium bridge, worker live-stream parity | `uv run pytest tests/matmaster/integration/test_event_fanout.py tests/matmaster/integration/test_workspace_handler.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q` |
| 2 | `36-02` | `DBUS-03` | single `run_agent()` entrypoint, legacy transport/runtime deletion | `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/integration/test_event_fanout.py tests/matmaster/test_bohrium_setup_injection.py tests/matmaster/integration/test_upstream_scenarios.py -q` |
| 3 | `36-03` | `DBUS-03` | bus-free `Exp`/spawn/compactor APIs and stream-based regression rewrites | `uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/integration/test_subagent_spawn.py tests/matmaster/integration/test_compaction_real_api.py tests/matmaster/integration/test_e2e_mat_master.py tests/matmaster/integration/test_bohrium_execution_contract.py tests/matmaster/integration/test_pipeline_alignment.py tests/matmaster/integration/test_e2e_minimal.py -q` |
| 4 | `36-04` | `DBUS-01` | DevShell observer replacement, repo-wide audit, stateless scheduling boundary lock | `uv run pytest tests/matmaster/devshell/test_integration.py tests/matmaster/devshell/test_compaction_via_devshell.py tests/matmaster/core/test_tool_scheduler.py tests/matmaster/tools/test_tool_compiler.py -q` |

---

## Critical Commands By Plan

### 36-01

- Task 1: `uv run pytest tests/matmaster/integration/test_event_fanout.py tests/matmaster/integration/test_workspace_handler.py -q`
- Task 2: `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q`
- Plan gate: `uv run pytest tests/matmaster/integration/test_event_fanout.py tests/matmaster/integration/test_workspace_handler.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q`

### 36-02

- Task 1: `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/integration/test_event_fanout.py tests/matmaster/test_bohrium_setup_injection.py -q`
- Task 2: `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py tests/matmaster/services/test_agent_run_stream.py -q`
- Plan gate: `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/integration/test_event_fanout.py tests/matmaster/test_bohrium_setup_injection.py tests/matmaster/integration/test_upstream_scenarios.py -q`

### 36-03

- Task 1: `uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/integration/test_subagent_spawn.py tests/matmaster/integration/test_e2e_mat_master.py tests/matmaster/integration/test_bohrium_execution_contract.py -q`
- Task 2: `uv run pytest tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/integration/test_compaction_real_api.py tests/matmaster/integration/test_pipeline_alignment.py tests/matmaster/integration/test_e2e_minimal.py -q`
- Plan gate: `uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/integration/test_subagent_spawn.py tests/matmaster/integration/test_compaction_real_api.py tests/matmaster/integration/test_e2e_mat_master.py tests/matmaster/integration/test_bohrium_execution_contract.py tests/matmaster/integration/test_pipeline_alignment.py tests/matmaster/integration/test_e2e_minimal.py -q`

### 36-04

- Task 1: `uv run pytest tests/matmaster/devshell/test_integration.py tests/matmaster/devshell/test_compaction_via_devshell.py -q`
- Task 2: `uv run pytest tests/matmaster/core/test_tool_scheduler.py tests/matmaster/tools/test_tool_compiler.py -q`
- Plan gate: `uv run pytest tests/matmaster/devshell/test_integration.py tests/matmaster/devshell/test_compaction_via_devshell.py tests/matmaster/core/test_tool_scheduler.py tests/matmaster/tools/test_tool_compiler.py -q`

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement(s) | Automated Command | Status |
|---------|------|------|----------------|-------------------|--------|
| `36-01-01` | `36-01` | 1 | `DBUS-02` | `uv run pytest tests/matmaster/integration/test_event_fanout.py tests/matmaster/integration/test_workspace_handler.py -q` | ⬜ pending |
| `36-01-02` | `36-01` | 1 | `DBUS-01`, `DBUS-02` | `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q` | ⬜ pending |
| `36-02-01` | `36-02` | 2 | `DBUS-03` | `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/integration/test_event_fanout.py tests/matmaster/test_bohrium_setup_injection.py -q` | ⬜ pending |
| `36-02-02` | `36-02` | 2 | `DBUS-03` | `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py tests/matmaster/services/test_agent_run_stream.py -q` | ⬜ pending |
| `36-03-01` | `36-03` | 3 | `DBUS-03` | `uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/integration/test_subagent_spawn.py tests/matmaster/integration/test_e2e_mat_master.py tests/matmaster/integration/test_bohrium_execution_contract.py -q` | ⬜ pending |
| `36-03-02` | `36-03` | 3 | `DBUS-03` | `uv run pytest tests/matmaster/core/test_context_compactor.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/integration/test_compaction_real_api.py tests/matmaster/integration/test_pipeline_alignment.py tests/matmaster/integration/test_e2e_minimal.py -q` | ⬜ pending |
| `36-04-01` | `36-04` | 4 | `DBUS-01` | `uv run pytest tests/matmaster/devshell/test_integration.py tests/matmaster/devshell/test_compaction_via_devshell.py -q` | ⬜ pending |
| `36-04-02` | `36-04` | 4 | `DBUS-01` | `uv run pytest tests/matmaster/core/test_tool_scheduler.py tests/matmaster/tools/test_tool_compiler.py -q` | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Sampling Rate

- After each task: run that task's `<automated>` command before moving on.
- After each plan: run the matching plan gate from the wave map.
- After wave 4: rerun the static audit `rg -n "MessageBus|EventRouter" matmaster src tests --glob '*.py'`.
- There is no Wave 0 in the revised breakdown; every planned task already has an explicit automated verification command.

---

## Manual Live-Stream Verification

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Worker `send_cb -> Redis publish -> active SSE subscriber` parity stays intact for live generator events plus terminal/system events (`run_result`, `cancelled`/`error`, `stream_closed`) after bus removal | `DBUS-02`, `DBUS-03` | Requires a real API + Worker + Redis topology with an active SSE subscriber; unit tests can prove callback contracts, but not multi-process live delivery end to end | Start API and Worker with `REDIS_URL`, open an SSE subscriber for one session, run one successful request and one cancelled or failing request, and confirm the active subscriber sees live progress plus final/system events before replay. |

---

## Validation Sign-Off

- [x] All 8 planned tasks have `<automated>` verification commands
- [x] No Wave 0 scaffolding remains in the validation contract
- [ ] Wave 1 gate green
- [ ] Wave 2 gate green
- [ ] Wave 3 gate green
- [ ] Wave 4 gate green
- [ ] Repo-wide `MessageBus` / `EventRouter` static audit green
- [ ] Manual worker live-stream parity check completed

**Approval:** pending execution
