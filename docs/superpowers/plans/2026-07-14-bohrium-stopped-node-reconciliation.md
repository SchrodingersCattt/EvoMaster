# Bohrium Stopped Node Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify Bohrium `status=-1` as already stopped and safely reconcile its
historical DB slot to `paused`, while renaming an absent list entry without
claiming that the provider deleted it.

**Architecture:** Keep provider response classification in the audit script. Add
one lifecycle-manager operation that uses the existing distributed slot lock,
slot identity recheck, expired-lease retirement, all-lease check, and a
`ready -> paused` database CAS without calling the provider.

**Tech Stack:** Python 3.11+, argparse, dataclasses, PyMySQL through `BaseTable`,
Redis slot locks, pytest, uv.

## Global Constraints

- Dry-run performs no provider or database writes.
- `status=2` keeps the existing fenced provider-stop behavior.
- `status=-1` never calls provider stop; apply may only reconcile
  `state='ready'` to `paused` after the slot and all leases are rechecked.
- A Node absent from `node/list` is `PROVIDER_LIST_MISSING`; it is not proof of
  deletion and causes no write.
- AccessKey, Node password, and exception messages never enter the report.
- All verification uses the repository uv environment.

---

### Task 1: Add fenced already-stopped reconciliation

**Files:**
- Modify: `src/dao/bohrium_nodes_table.py`
- Modify: `src/services/bohrium_node_lifecycle.py`
- Test: `tests/test_bohrium_nodes_table.py`
- Test: `tests/services/test_bohrium_node_lifecycle.py`

**Interfaces:**
- Produces: `BohriumNodesTable.mark_ready_paused(slot_id: int, node_id: int) -> bool`
- Produces: `BohriumNodeLeaseManager.reconcile_stopped_unleased_ready_slot(row) -> HistoricalNodeStopOutcome`
- Produces: `HistoricalNodeStopOutcome.ALREADY_STOPPED_TO_PAUSED`

- [ ] **Step 1: Write failing DAO and lifecycle tests**

Add a DAO assertion that the update is fenced by `id`, `node_id`, and
`state='ready'`. Add lifecycle tests proving an unchanged unleased slot becomes
paused with zero provider calls, while a concurrent lease or changed slot returns
the existing skip outcome and stays ready.

```python
def test_historical_already_stopped_slot_becomes_paused_without_provider_call():
    manager, nodes, leases, provider = _manager()
    handle = manager.acquire(
        NodeIdentity('u1', 'o1', 99, 456),
        session_id='session-1',
        invocation_id='inv-1',
        access_key='ak',
        creator_id=1,
    )
    leases.release(handle.invocation_id, handle.lease_token)

    outcome = manager.reconcile_stopped_unleased_ready_slot(dict(nodes.row))

    assert outcome is HistoricalNodeStopOutcome.ALREADY_STOPPED_TO_PAUSED
    assert provider.stop_count == 0
    assert nodes.row['state'] == 'paused'
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest tests/test_bohrium_nodes_table.py \
  tests/services/test_bohrium_node_lifecycle.py -q
```

Expected: failures because `mark_ready_paused`, the enum member, and the manager
method do not exist.

- [ ] **Step 3: Implement the database CAS**

```python
def mark_ready_paused(self, slot_id: int, node_id: int) -> bool:
    with self.get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {self.table_name}
                SET state = 'paused', creating_invocation_id = NULL,
                    creating_lease_token = NULL,
                    creating_lease_expires_at = NULL,
                    last_error = NULL, updated_at = NOW()
                WHERE id = %s AND node_id = %s AND state = 'ready'
                """,
                (slot_id, node_id),
            )
            conn.commit()
            return cursor.rowcount > 0
```

- [ ] **Step 4: Implement lifecycle reconciliation**

Build `NodeIdentity` from the candidate. Under `_slot_lock`, reread the slot and
require the same `id`, `node_id`, and `state='ready'`; retire only currently
expired leases, then use `count_for_slot` so a lease crossing its deadline still
blocks reconciliation. Call `mark_ready_paused`; false CAS returns
`SKIPPED_SLOT_CHANGED`, success returns `ALREADY_STOPPED_TO_PAUSED`. Do not call
any provider method.

```python
def reconcile_stopped_unleased_ready_slot(
    self, row: dict[str, Any]
) -> HistoricalNodeStopOutcome:
    identity = NodeIdentity(
        str(row['user_id']),
        str(row['org_id']),
        int(row['project_id']),
        int(row['sku_id']),
    )
    slot_id = int(row['id'])
    node_id = int(row['node_id'])
    with self._slot_lock(identity):
        current = self._nodes.find_by_id(slot_id)
        if (
            not current
            or current.get('state') != 'ready'
            or current.get('node_id') is None
            or int(current['node_id']) != node_id
        ):
            return HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
        self._leases.delete_expired_for_slot(slot_id)
        if self._leases.count_for_slot(slot_id) > 0:
            return HistoricalNodeStopOutcome.SKIPPED_CONCURRENT_LEASE
        if not self._nodes.mark_ready_paused(slot_id, node_id):
            return HistoricalNodeStopOutcome.SKIPPED_SLOT_CHANGED
    return HistoricalNodeStopOutcome.ALREADY_STOPPED_TO_PAUSED
```

- [ ] **Step 5: Run focused tests and commit**

```bash
uv run pytest tests/test_bohrium_nodes_table.py \
  tests/services/test_bohrium_node_lifecycle.py -q
git add src/dao/bohrium_nodes_table.py \
  src/services/bohrium_node_lifecycle.py \
  tests/test_bohrium_nodes_table.py \
  tests/services/test_bohrium_node_lifecycle.py
git commit -m "feat: reconcile already stopped Bohrium nodes"
```

---

### Task 2: Update audit classification and apply dispatch

**Files:**
- Modify: `scripts/audit_bohrium_node_runtime.py`
- Test: `tests/scripts/test_audit_bohrium_node_runtime.py`

**Interfaces:**
- Produces: `detail is None -> PROVIDER_LIST_MISSING`
- Produces: `status == -1 -> ALREADY_STOPPED`
- Produces: `AuditDependencies.apply_stopped(candidate, access_key)`

- [ ] **Step 1: Write failing classification and apply tests**

Update the classification expectation and add tests proving dry-run never mutates,
apply dispatches `status=2` only to `apply_stop`, dispatches `status=-1` only to
`apply_stopped`, and list-missing/unknown rows remain `NOT_ELIGIBLE`.

```python
assert [row.recommendation for row in result.rows] == [
    'VERIFY_IDLE_THEN_STOP',
    'ALREADY_STOPPED',
    'PROVIDER_LIST_MISSING',
]
assert stopped == [1]
assert reconciled == [2]
```

- [ ] **Step 2: Run focused script tests and verify RED**

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py -q
```

Expected: classification assertions fail and `AuditDependencies` rejects the new
`apply_stopped` dependency.

- [ ] **Step 3: Implement classification and dispatch**

```python
def _classify(detail):
    if detail is None:
        return None, None, 'PROVIDER_LIST_MISSING'
    status = detail.get('status')
    if status == 2:
        recommendation = 'VERIFY_IDLE_THEN_STOP'
    elif status == -1:
        recommendation = 'ALREADY_STOPPED'
    elif status is None:
        recommendation = 'MANUAL_REVIEW_STATUS_UNKNOWN'
    else:
        recommendation = f'MANUAL_REVIEW_STATUS_{status}'
    return status, detail.get('image_name'), recommendation
```

In apply mode, call `apply_stop` for status 2 and `apply_stopped` for status -1.
Convert enum outcomes to their values through the existing rendering path. Wire
the production `apply_stopped` dependency to
`get_bohrium_node_lease_manager().reconcile_stopped_unleased_ready_slot(candidate)`.

```python
apply_action = None
if provider_status == 2:
    apply_action = apply_stop
elif provider_status == -1:
    apply_action = apply_stopped
if apply and apply_action is not None:
    outcome = apply_action(candidate, access_key)
    execution = getattr(outcome, 'value', outcome)
    row = replace(row, execution=str(execution))
```

- [ ] **Step 4: Run focused and related regression tests**

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py \
  tests/services/test_bohrium_node_lifecycle.py \
  tests/services/test_bohrium_node_recycler.py \
  tests/test_bohrium_nodes_table.py -q
uv run python scripts/audit_bohrium_node_runtime.py --help
git diff --check
```

- [ ] **Step 5: Commit the audit behavior**

```bash
git add scripts/audit_bohrium_node_runtime.py \
  tests/scripts/test_audit_bohrium_node_runtime.py
git commit -m "fix: classify stopped Bohrium audit nodes"
```

Do not execute the real audit command with `--apply` during implementation.
