# Bohrium Node Lifecycle Preferences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support `run_end`, allowlisted idle shutdown, and keep-running Node policies through persistent settings, per-send confirmation, and a queue snapshot.

**Architecture:** tools-server owns user preferences, evo validates and snapshots per-send policy before the independent Worker acquires a shared Node lease, and the Node slot stores the latest desired policy used by last-release/recycler transitions. The frontend has a settings entry and a pre-send prompt; all missing or invalid legacy values fail safe to `run_end`.

**Tech Stack:** FastAPI, Pydantic, MySQL, Redis, pytest, React 19, TypeScript, Ant Design, Node test runner.

## Global Constraints

- API and Worker communicate only through serializable Redis jobs; no process-local callback or SDK object crosses the boundary.
- Allowed idle timeouts are exactly 900, 1800, and 7200 seconds.
- Default lifecycle policy is `run_end`; `keep_running` means no MatMaster automatic stop, not a provider uptime guarantee.
- Shared slots use latest successful acquire policy; live leases always fence stop operations.
- Existing user changes and unrelated dirty files must be preserved.

---

### Task 1: tools-server preference contract

**Files:**
- Create: `migrations/add_bohrium_node_lifecycle_preference.sql`
- Create: `src/apis/bohrium_node_lifecycle_preference_api.py`
- Create: `src/services/bohrium_node_lifecycle_preference_service.py`
- Create: `tests/test_bohrium_node_lifecycle_preference.py`
- Modify: `src/models/preference.py`
- Modify: `src/dao/user_prefence_db.py`
- Modify: `src/apis/api_router.py`
- Modify: `tests/test_user_runtime_preference.py`

- [ ] Add failing model/API/DB tests for valid policies, timeout combinations, prompt default, and aggregate response.
- [ ] Run the focused tests and confirm failures are caused by missing lifecycle fields and endpoints.
- [ ] Add the three-column migration, strict Pydantic request model, atomic multi-column DB update, service, router, and aggregate mapping.
- [ ] Run focused tests, full pytest, and pre-commit for modified files.
- [ ] Commit the tools-server change.

### Task 2: evo lifecycle state machine

**Files:**
- Modify: `src/services/bohrium_node_lifecycle.py`
- Modify: `src/dao/bohrium_nodes_table.py`
- Modify: `src/services/bohrium_node_recycler.py`
- Modify: `tests/services/test_bohrium_node_lifecycle.py`
- Modify: `tests/services/test_bohrium_node_recycler.py`
- Modify: `tests/test_bohrium_nodes_table.py`

- [ ] Add failing tests for policy validation, `ready -> idle`, idle reuse, due-idle stop, keep-running exclusion, and latest-policy behavior.
- [ ] Run focused tests and confirm expected red failures.
- [ ] Implement typed policy resolution, slot-policy CAS helpers, idle transitions/scans, and recycler dispatch under the existing Redis slot lock.
- [ ] Run focused tests and lifecycle regression tests.

### Task 3: evo request and Worker snapshot

**Files:**
- Modify: `src/models/chat.py`
- Modify: `src/services/stream_service.py`
- Modify: `src/worker/agent_worker.py`
- Modify: `src/services/agent_run_service.py`
- Modify: `src/services/agent_run_bohrium_stage.py`
- Modify: `src/services/agent_run_bohrium.py`
- Modify: `clients/matmaster_platform/runtime_preference.py`
- Modify: `src/services/user_runtime_preference_service.py`
- Modify: `src/services/feishu_inbound_service.py`
- Modify relevant stream, worker, run-stage, and preference tests.

- [ ] Add failing tests that the validated policy snapshot reaches the Redis job and Worker acquire call, with legacy fallback to `run_end`.
- [ ] Run focused tests and confirm red failures.
- [ ] Thread only the two primitive snapshot fields through the existing API/Worker call chain and resolve non-Web preferences safely.
- [ ] Run focused tests and API/Worker regressions.

### Task 4: frontend preference contract and settings entry

**Files:**
- Modify: `src/api/account.ts`
- Modify: `src/pages/settings/runtime-preferences-section.tsx`
- Modify: `src/pages/settings/runtime-preferences-section.module.less`
- Modify: `src/locales/zh.json`
- Modify: `src/locales/en.json`
- Add pure lifecycle option/normalization module and Node-runner tests under `tests/chat/`.

- [ ] Add failing tests for lifecycle normalization and request serialization.
- [ ] Add typed account GET/POST helpers, lifecycle select, prompt switch, and keep-running warning.
- [ ] Run chat tests, typecheck, lint, and build.

### Task 5: frontend per-send prompt and snapshot

**Files:**
- Create a focused lifecycle preference/prompt hook and modal component under `src/features/chat/`.
- Modify: `src/features/chat/page-shell/EvoChatCore.tsx`
- Modify: `src/features/chat/page-shell/useEvoRuntimeWiring.ts`
- Modify: `src/features/chat/runtime/useEvoHandleSendMessage.ts`
- Modify: `src/features/chat/runtime/evo-stream-request-options.ts`
- Modify: `src/api/chat-runtime-stream-request.ts`
- Add focused Node-runner tests under `tests/chat/`.

- [ ] Add failing tests for one-shot selection, remembered selection, prompt-disabled snapshot, and body serialization.
- [ ] Implement the Promise-based pre-send modal and pass its resolved primitives to the stream body before opening SSE.
- [ ] Ensure cancelled modal sends nothing and save failure does not silently disable future prompts.
- [ ] Run frontend test/typecheck/lint/build and commit frontend changes.

### Task 6: cross-repository verification

- [ ] Run all focused suites plus full pytest/typecheck/lint/build commands in each changed repository.
- [ ] Run pre-commit on changed files and confirm no non-doc file exceeds 1000 lines.
- [ ] Review migrations and deployment order; do not execute DMS or deploy from the development machine.
- [ ] Inspect final diffs/status and report commits plus test evidence.
