# Bohrium Stopping Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile stale `stopping` Bohrium Node slots from provider state, expose useful readiness timeout diagnostics, and safely remove the confirmed stale test slot.

**Architecture:** The monitor remains the sole owner of `stopping` recovery. `retry_stopping` reads provider state after its existing slot/lease checks, uses fenced DB transitions for missing or stopped Nodes, and retains existing stop retry behavior otherwise. The provider adapter records only sanitized final readiness observations in timeout errors.

**Tech Stack:** Python 3.13 via `uv`, pytest, MySQL, Bohrium OpenAPI `node/list`.

## Global Constraints

- Do not infer terminal state from provider code `148888` or error text.
- Do not include access keys, passwords, IPs, or raw provider responses in diagnostics.
- Do not let acquire directly take over `stopping` slots.
- Do not operate on provider Node `20079903`.
- Do not use Sandbox `image_cache_status=1` as a readiness gate.
- Preserve API/Worker separation and existing Redis slot fencing.

---

### Task 1: Reconcile stopping slots from provider state

**Files:**
- Modify: `tests/services/test_bohrium_node_lifecycle.py`
- Modify: `src/services/bohrium_node_service.py`
- Modify: `src/services/bohrium_node_lifecycle.py`

**Interfaces:**
- Consumes: `BohriumNodeService.get_node_detail(access_key, node_id)` returning a provider item or `None`.
- Produces: `NODE_STATUS_STOPPED = -1`; `retry_stopping(...) -> bool` returns true when a stale slot is removed or a stopped provider Node is reconciled to paused.

- [ ] **Step 1: Add provider-state controls to the existing fake provider**

```python
class _Provider:
    def __init__(self) -> None:
        # existing fields
        self.node_detail = {"nodeId": 171, "status": 2}

    def get_node_detail(self, _access_key, _node_id):
        return self.node_detail
```

- [ ] **Step 2: Write failing missing/stopped reconciliation tests**

```python
def test_retry_stopping_removes_slot_missing_from_provider():
    manager, nodes, _leases, provider = _manager()
    handle = manager.acquire(NodeIdentity("u1", "o1", 99, 456), session_id="session-1", invocation_id="inv-1", access_key="ak")
    provider.stop_failures = 1
    with pytest.raises(TimeoutError):
        manager.release(handle, access_key="ak")
    provider.node_detail = None
    assert manager.retry_stopping(nodes.row, access_key="ak") is True
    assert nodes.row is None
    assert provider.stop_count == 0


def test_retry_stopping_reconciles_provider_stopped_to_paused():
    manager, nodes, _leases, provider = _manager()
    handle = manager.acquire(NodeIdentity("u1", "o1", 99, 456), session_id="session-1", invocation_id="inv-1", access_key="ak")
    provider.stop_failures = 1
    with pytest.raises(TimeoutError):
        manager.release(handle, access_key="ak")
    provider.node_detail = {"nodeId": 171, "status": -1}
    assert manager.retry_stopping(nodes.row, access_key="ak") is True
    assert nodes.row["state"] == "paused"
    assert provider.stop_count == 0
```

- [ ] **Step 3: Run the tests and confirm red failures**

```bash
uv run pytest -q \
  tests/services/test_bohrium_node_lifecycle.py::test_retry_stopping_removes_slot_missing_from_provider \
  tests/services/test_bohrium_node_lifecycle.py::test_retry_stopping_reconciles_provider_stopped_to_paused
```

Expected: both fail because `retry_stopping` still invokes `stop_node` without provider reconciliation.

- [ ] **Step 4: Implement minimal fenced reconciliation**

Add the stopped status constant next to `NODE_STATUS_READY`, import it into the lifecycle manager, and add this logic after the initial locked state/lease check:

```python
detail = self._node_service.get_node_detail(access_key, node_id)
if detail is None:
    with self._slot_lock(identity):
        if self._has_leases_after_expired_cleanup(slot_id):
            return False
        return self._nodes.delete_stopping_slot(slot_id, node_id)
if detail.get("status") == NODE_STATUS_STOPPED:
    with self._slot_lock(identity):
        if self._has_leases_after_expired_cleanup(slot_id):
            return False
        return self._nodes.mark_paused(slot_id, node_id)
```

Other statuses continue through the existing stop retry path.

- [ ] **Step 5: Run focused and lifecycle tests**

```bash
uv run pytest -q tests/services/test_bohrium_node_lifecycle.py tests/services/test_bohrium_node_recycler.py
```

Expected: all pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/services/bohrium_node_service.py src/services/bohrium_node_lifecycle.py tests/services/test_bohrium_node_lifecycle.py
git commit -m "fix: reconcile stale Bohrium stopping slots"
```

### Task 2: Add readiness timeout observations

**Files:**
- Modify: `tests/services/test_bohrium_node_service.py`
- Modify: `src/services/bohrium_node_service.py`

**Interfaces:**
- Consumes: provider list item fields `status`, `startingUpMsg`, and `errCode`.
- Produces: the existing `TimeoutError` type with sanitized `found`, `last_status`, `starting_up_msg`, and `error_code` fields in its message.

- [ ] **Step 1: Write the failing timeout diagnostic test**

```python
def test_wait_until_ready_timeout_reports_last_provider_observation(monkeypatch):
    response = _FakeResponse({"code": 0, "data": {"items": [{"nodeId": 123, "status": 1, "startingUpMsg": "waiting for capacity", "errCode": 203901}]}})
    monkeypatch.setattr(node_module.httpx, "Client", lambda timeout: _FakeClient(response, {}))
    times = iter((100.0, 100.1, 100.6))
    monkeypatch.setattr(node_module.time, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError) as exc_info:
        _service().wait_until_ready("secret-ak", 123, poll_interval=0, timeout=0.5)
    message = str(exc_info.value)
    assert "found=True" in message
    assert "last_status=1" in message
    assert "starting_up_msg='waiting for capacity'" in message
    assert "error_code=203901" in message
    assert "secret-ak" not in message
```

- [ ] **Step 2: Run the test and confirm the expected red failure**

```bash
uv run pytest -q tests/services/test_bohrium_node_service.py::test_wait_until_ready_timeout_reports_last_provider_observation
```

Expected: fail because the current timeout message contains no observation fields.

- [ ] **Step 3: Track and report sanitized final observations**

Initialize `found=False` and the three last-value fields before polling. Update them only for the matching Node item, then raise:

```python
raise TimeoutError(
    f"Bohrium node node_id={node_id} did not become ready within {timeout}s; "
    f"found={found} last_status={last_status!r} "
    f"starting_up_msg={last_starting_up_msg!r} error_code={last_error_code!r}"
)
```

- [ ] **Step 4: Run provider and lifecycle regressions**

```bash
uv run pytest -q tests/services/test_bohrium_node_service.py tests/services/test_bohrium_node_lifecycle.py tests/services/test_bohrium_node_recycler.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/services/bohrium_node_service.py tests/services/test_bohrium_node_service.py
git commit -m "feat: report Bohrium node readiness timeout state"
```

### Task 3: Verify and reconcile the test database

**Files:**
- Verify: all files changed in Tasks 1-2
- Database: test `evo_bohrium_nodes` and `bohrium_node_leases`

**Interfaces:**
- Consumes: confirmed slot `id=182`, Node `20079897`, state `stopping` and absence of live leases/provider item.
- Produces: no row for slot 182, allowing a future MatMaster acquire to insert a new slot.

- [ ] **Step 1: Run code quality and relevant regressions**

```bash
git diff --check
git diff --name-only --diff-filter=ACMR -z | xargs -0 uv run pre-commit run --files
uv run pytest -q \
  tests/services/test_bohrium_node_service.py \
  tests/services/test_bohrium_node_lifecycle.py \
  tests/services/test_bohrium_node_lifecycle_policies.py \
  tests/services/test_bohrium_node_recycler.py \
  tests/matmaster/integration/test_bohrium_execution_contract.py
```

Expected: pre-commit passes and pytest reports zero failures.

- [ ] **Step 2: Recheck exact provider and DB facts immediately before deletion**

Use the test access key to confirm `20079897` is absent from `node/list`, then query slot 182 and its live lease count. Abort if the provider Node appears, state/node identity changes, or the live lease count is non-zero.

- [ ] **Step 3: Execute the fenced deletion**

```sql
DELETE n
FROM evo_bohrium_nodes AS n
LEFT JOIN bohrium_node_leases AS l
  ON l.node_slot_id = n.id
 AND l.lease_expires_at > NOW()
WHERE n.id = 182
  AND n.node_id = 20079897
  AND n.state = 'stopping'
  AND l.id IS NULL;
```

Expected: affected rows is exactly 1.

- [ ] **Step 4: Verify deletion and preserve the manual provider Node**

Query slot 182 and live leases again; both must return zero rows. Query provider `node/list` and confirm no stop/delete request was issued for manual Node `20079903`.

- [ ] **Step 5: Record final repository state**

```bash
git status --short
git log -3 --oneline
```

Expected: clean worktree with the two implementation commits after the design and plan commits.
