# Remove Bohrium Node Manual Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the public and UI-facing “shut down now” capability while preserving all automatic Bohrium Node lifecycle policies.

**Architecture:** evo will no longer expose a manual-stop route, request model, or lease-manager method. The frontend will retain lifecycle preference controls and the pre-send prompt, but will remove the immediate-stop card and API client. tools-server remains unchanged.

**Tech Stack:** FastAPI, Pydantic, pytest, React 19, TypeScript, Node test runner, ESLint, Vite.

## Global Constraints

- Preserve `run_end`, `idle_timeout`, and `keep_running` behavior unchanged.
- Preserve API/Worker separation and Redis job lifecycle snapshots unchanged.
- Do not modify tools-server or its user-preference migration.
- Do not add a replacement manual-stop entry.

---

### Task 1: Remove evo manual-stop contract

**Files:**
- Modify: `tests/apis/test_bohrium_node_manual_stop.py`
- Delete: `src/apis/bohrium_node_api.py`
- Modify: `src/apis/api_router.py`
- Modify: `src/models/chat.py`
- Modify: `src/services/bohrium_node_lifecycle.py`
- Modify: `tests/services/test_bohrium_node_lifecycle_policies.py`
- Modify: `docs/superpowers/specs/2026-07-14-bohrium-node-lifecycle-preferences-design.md`
- Modify: `docs/superpowers/plans/2026-07-14-bohrium-node-lifecycle-preferences.md`

**Interfaces:**
- Consumes: existing automatic lifecycle state machine and FastAPI app router.
- Produces: no `/chat/sessions/runtime/bohrium-node/stop` path and no `manual_stop` business method.

- [ ] **Step 1: Replace the positive endpoint tests with a failing absence contract**

```python
def test_bohrium_node_manual_stop_route_is_not_registered():
    from app import app

    paths = app.openapi()["paths"]
    assert "/api/v1/chat/sessions/runtime/bohrium-node/stop" not in paths
```

- [ ] **Step 2: Run the absence test and verify RED**

Run: `uv run pytest -q tests/apis/test_bohrium_node_manual_stop.py`

Expected: FAIL because the route is still registered.

- [ ] **Step 3: Remove the endpoint, request model, manager method, positive tests, and old design references**

Delete `src/apis/bohrium_node_api.py`; remove its `api_router` registration; remove
`BohriumNodeStopRequest`, `BohriumNodeLeaseManager.manual_stop`, and the two manual-stop
lease tests. Remove the “手动关机” section and Task 4 from the original design/plan, and
remove manual-stop wording from their test checklists.

- [ ] **Step 4: Run evo verification**

Run:

```bash
uv run pytest -q \
  tests/apis/test_bohrium_node_manual_stop.py \
  tests/services/test_bohrium_node_lifecycle.py \
  tests/services/test_bohrium_node_lifecycle_policies.py \
  tests/services/test_bohrium_node_recycler.py \
  tests/test_agent_worker_snapshot_confirm.py \
  tests/test_chat_stream_bohrium_node_sku.py
git ls-files --modified --others --exclude-standard -z | \
  xargs -0 uv run pre-commit run --files
```

Expected: all tests and hooks pass.

### Task 2: Remove frontend manual-stop entry

**Files:**
- Modify: `tests/chat/bohrium-node-lifecycle.test.ts`
- Modify: `src/pages/settings/runtime-preferences-section.tsx`
- Modify: `src/api/chat-interaction.ts`
- Modify: `src/api/chat-runtime.ts`
- Modify: `src/locales/zh.json`
- Modify: `src/locales/en.json`

**Interfaces:**
- Consumes: lifecycle preference API and automatic lifecycle selection UI.
- Produces: settings UI without an immediate-stop card and frontend API surface without `stopEvoBohriumNode`.

- [ ] **Step 1: Add a failing frontend absence contract**

```typescript
import * as chatInteraction from '../../src/api/chat-interaction.ts';

test('does not expose an immediate Bohrium Node stop action', () => {
    assert.equal('stopEvoBohriumNode' in chatInteraction, false);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --test tests/chat/bohrium-node-lifecycle.test.ts`

Expected: FAIL because `stopEvoBohriumNode` is still exported.

- [ ] **Step 3: Remove the frontend entry and client**

Remove `stopEvoBohriumNode`, its barrel export, the settings component import/state/handler/card,
and all `nodeStop*` locale keys. Keep lifecycle select, prompt switch, warnings, and save logic unchanged.

- [ ] **Step 4: Run frontend verification**

Run:

```bash
npm run test:chat
git ls-files --modified --others --exclude-standard '*.ts' '*.tsx' -z | \
  xargs -0 npx eslint
npm run build
git diff --check
```

Expected: 222 or more chat tests pass, ESLint has no errors, and Vite build exits 0.

### Task 3: Cross-repository cleanup and commit

**Files:**
- Verify all modified/deleted files in `matmaster-evo` and `scimaster-bohr-chat`.

**Interfaces:**
- Consumes: Task 1 and Task 2 commits.
- Produces: clean branches with no manual-stop implementation or UI strings.

- [ ] **Step 1: Verify static absence**

Run in evo:

```bash
rg -n "manual_stop|bohrium-node/stop|BohriumNodeStopRequest" src tests
```

Expected: only the explicit negative contract test may mention the removed route.

Run in frontend:

```bash
rg -n "stopEvoBohriumNode|nodeStopTitle|nodeStopNow|立即关闭 Bohrium" src tests
```

Expected: only the explicit negative contract test may mention the removed export.

- [ ] **Step 2: Commit repository changes**

```bash
git commit -m "refactor: remove Bohrium node manual stop"
```

Use the same commit subject independently in evo and frontend. Do not push.
