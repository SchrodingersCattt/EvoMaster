# Bohrium Node Runtime Audit and Batch Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reconcile historical `ready` Node slots without live invocation leases
against Bohrium `node/list`, and optionally stop every confirmed `status=2` candidate
after an explicit double confirmation.

**Architecture:** Add one narrow DAO query for candidates. Keep classification,
reporting, and CLI dependency assembly in a standalone script. Reuse the existing
Node lifecycle manager for mutations: it rechecks slot identity and live leases
under the distributed slot lock before transitioning `ready -> stopping`, performs
the provider stop outside the lock, then publishes `paused` under the lock.

**Tech Stack:** Python 3.11+, argparse, dataclasses, PyMySQL through existing
`BaseTable`, Redis slot locks, httpx through `BohriumNodeService`, pytest, uv.

## Global Constraints

- Dry-run is the default and must perform no provider or database writes.
- Execution requires both `--apply` and
  `--confirm-stop-all-unleased-ready`; either flag alone is invalid.
- Apply handles only candidates whose first provider lookup returned `status == 2`.
- It must use `UserService.get_existing_bohrium_access_key()`, never the
  AK-creating lookup.
- It must print raw provider status values without guessing undocumented meanings.
- AccessKey, Node password, and exception messages must never enter reports or logs.
- `--limit` defaults to 1000 and accepts integers from 1 through 1000.
- Exit codes are: 0 success, 1 whole candidate-query failure, 2 incomplete audit,
  and 3 at least one apply failure. Apply failure takes precedence over incomplete
  audit.
- Run and verify with the repository uv environment.

---

### Task 1: Add the historical-slot candidate query

**Files:**
- Modify: `src/dao/bohrium_nodes_table.py`
- Modify: `tests/test_bohrium_nodes_table.py`

**Interface:**
- `BohriumNodesTable.list_ready_without_live_leases(limit: int) -> list[dict[str, Any]]`

- [ ] **Step 1: Write the failing DAO test**

Assert the new method returns fake rows, passes `(limit,)`, selects only
`state='ready'` slots with non-null Node IDs and no unexpired joined lease, orders by
oldest `last_used_at` and then `id`, and does not commit.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
uv run pytest tests/test_bohrium_nodes_table.py::test_list_ready_without_live_leases_is_read_only_and_oldest_first -q
```

Expected: fail because the method is absent.

- [ ] **Step 3: Implement the explicit-column SELECT**

```python
def list_ready_without_live_leases(self, limit: int) -> list[dict[str, Any]]:
    with self.get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT n.id, n.user_id, n.org_id, n.project_id, n.sku_id,
                       n.node_id, n.state, n.last_used_at, n.updated_at
                FROM {self.table_name} AS n
                LEFT JOIN bohrium_node_leases AS l
                  ON l.node_slot_id = n.id
                 AND l.lease_expires_at > NOW()
                WHERE n.state = 'ready' AND n.node_id IS NOT NULL
                  AND l.id IS NULL
                ORDER BY n.last_used_at ASC, n.id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return cursor.fetchall() or []
```

- [ ] **Step 4: Run all DAO tests and commit**

```bash
uv run pytest tests/test_bohrium_nodes_table.py -q
git add src/dao/bohrium_nodes_table.py tests/test_bohrium_nodes_table.py
git commit -m "feat: query unleased historical node slots"
```

---

### Task 2: Implement conservative dry-run classification and CLI reporting

**Files:**
- Create: `scripts/audit_bohrium_node_runtime.py`
- Create: `tests/scripts/test_audit_bohrium_node_runtime.py`

**Interfaces:**
- `AuditRow`: display-only fields, including `recommendation`, `execution`, and
  optional `error_type`; never contains an AccessKey or password.
- `AuditResult`: rows plus `incomplete` and `apply_failed` flags.
- `AuditDependencies`: candidate, existing-AK, detail, and apply-stop callables.
- `audit_candidates(..., apply: bool = False) -> AuditResult`
- `render_report(rows) -> str`
- `main(argv: list[str] | None = None, *, deps=None) -> int`

- [ ] **Step 1: Write failing classification tests**

Load the script with `importlib.util` as in `test_poc_launching_sandbox.py`.
Use injected candidates and loaders to cover:

- `status=2 -> VERIFY_IDLE_THEN_STOP`;
- missing provider row -> `DB_ROW_STALE_CANDIDATE`;
- other status -> `MANUAL_REVIEW_STATUS_<value>`;
- missing status -> `MANUAL_REVIEW_STATUS_UNKNOWN`;
- missing existing AK and provider exceptions -> `AUDIT_INCOMPLETE` while later rows
  still run;
- AK caching by `(user_id, org_id)`;
- reports contain only exception class names, never exception messages or secrets.

- [ ] **Step 2: Run the script tests and verify RED**

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py -q
```

Expected: import failure because the script does not exist.

- [ ] **Step 3: Implement pure classification and deterministic rendering**

The classifier is deliberately conservative:

```python
def _classify(detail: dict[str, Any] | None) -> tuple[Any, str | None, str]:
    if detail is None:
        return None, None, "DB_ROW_STALE_CANDIDATE"
    status = detail.get("status")
    image_name = detail.get("image_name")
    if status == 2:
        recommendation = "VERIFY_IDLE_THEN_STOP"
    elif status is None:
        recommendation = "MANUAL_REVIEW_STATUS_UNKNOWN"
    else:
        recommendation = f"MANUAL_REVIEW_STATUS_{status}"
    return status, image_name, recommendation
```

Render tab-separated fields:

```text
NODE_ID USER_ID ORG_ID PROJECT_ID SKU_ID LAST_USED_AT PROVIDER_STATUS IMAGE_NAME RECOMMENDATION EXECUTION ERROR
```

Use `-` for missing fields and append deterministic sorted summary lines. In dry-run,
all successfully classified rows use `execution=DRY_RUN`; incomplete rows also avoid
all mutations.

- [ ] **Step 4: Write and pass CLI boundary tests**

Cover bounded limit parsing, empty results, database error exit 1, incomplete exit 2,
and production dependency assembly using existing-only AK lookup. Add parser tests
proving that either execution flag alone exits 2 and that both together are accepted.
At this task, inject a no-op apply callable; the lifecycle mutation is added next.

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py -q
```

- [ ] **Step 5: Commit the dry-run CLI foundation**

```bash
git add scripts/audit_bohrium_node_runtime.py tests/scripts/test_audit_bohrium_node_runtime.py
git commit -m "feat: add Bohrium node runtime audit"
```

---

### Task 3: Add fenced stopping for unleased historical slots

**Files:**
- Modify: `src/dao/bohrium_node_leases_table.py`
- Modify: `src/dao/bohrium_nodes_table.py`
- Modify: `src/services/bohrium_node_lifecycle.py`
- Modify: `tests/test_bohrium_nodes_table.py`
- Modify: `tests/services/test_bohrium_node_lifecycle.py`

**Interface:**
- `HistoricalNodeStopOutcome` values:
  `STOPPED_TO_PAUSED`, `SKIPPED_SLOT_CHANGED`,
  `SKIPPED_CONCURRENT_LEASE`, `PROVIDER_MISSING_SLOT_REMOVED`, and
  `PROVIDER_MISSING_SLOT_ALREADY_ABSENT`.
- `BohriumNodeLeaseManager.stop_unleased_ready_slot(row, *, access_key,
  creator_id=0) -> HistoricalNodeStopOutcome`

- [ ] **Step 1: Write failing lifecycle tests**

Cover seven cases with the existing in-memory fakes:

1. unchanged ready slot without leases stops once and becomes paused;
2. a live lease produces `SKIPPED_CONCURRENT_LEASE` and no provider call;
3. an expired lease racing heartbeat is either retired or renewed and skipped;
4. a lease crossing its deadline after cleanup still conservatively skips;
5. changed/missing slot or Node ID produces `SKIPPED_SLOT_CHANGED`;
6. stop timeout leaves `stopping`, records an error, and re-raises;
7. provider not-found uses a precise stopping-slot delete CAS and distinguishes
   removed, already-absent, and changed slots.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest tests/services/test_bohrium_node_lifecycle.py -q
```

Expected: import/attribute failure for the new outcome and method.

- [ ] **Step 3: Implement lock, recheck, CAS, provider stop, and publish**

Build `NodeIdentity` from the candidate. Under `_slot_lock`:

- re-read by slot ID;
- require `state == 'ready'` and the same `node_id`;
- delete only leases for the slot whose deadline is still expired;
- count all remaining lease rows and skip if any exist, including a row that crossed
  its deadline between cleanup and claim;
- call `mark_stopping(slot_id, node_id)` and treat a false CAS as slot changed.

Release the lock before `stop_node`. On not-found, reacquire the lock, reread the
exact slot, and delete only `id + node_id + state='stopping'`; return distinct
outcomes for removed, already-absent, and changed slots. On other exceptions, call
`record_stop_error` and re-raise. After a successful provider stop, reacquire the lock and require
`mark_paused(slot_id, node_id)`; a false result raises a fenced-state error.

- [ ] **Step 4: Run lifecycle regression tests and commit**

```bash
uv run pytest tests/services/test_bohrium_node_lifecycle.py \
  tests/services/test_bohrium_node_recycler.py -q
git add src/services/bohrium_node_lifecycle.py \
  tests/services/test_bohrium_node_lifecycle.py
git commit -m "feat: stop unleased historical node slots safely"
```

---

### Task 4: Wire automatic apply into the audit command

**Files:**
- Modify: `scripts/audit_bohrium_node_runtime.py`
- Modify: `tests/scripts/test_audit_bohrium_node_runtime.py`

**Interfaces:**
- `AuditDependencies.apply_stop(candidate, access_key) -> str`
- Production implementation calls
  `get_bohrium_node_lease_manager().stop_unleased_ready_slot(...)`, passing
  `_creator_id_from_user(candidate['user_id'])`.

- [ ] **Step 1: Write failing apply tests**

Cover:

- dry-run never invokes `apply_stop`;
- apply invokes it for every `status=2` row;
- missing provider rows, unknown statuses, and audit-incomplete rows use
  `execution=NOT_ELIGIBLE` and never invoke stop;
- lifecycle skip/success/not-found outcomes are rendered unchanged;
- an apply exception becomes `FAILED_<ExceptionClass>`, later rows continue, and
  `apply_failed` is true without retaining the exception message;
- CLI returns 3 for any apply failure, even when another row is audit-incomplete.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py -q
```

- [ ] **Step 3: Implement apply selection and production wiring**

Call `apply_stop` only after successful classification with `status == 2` and only
when `apply=True`. Convert enum outcomes to their string values. For apply mode,
non-status-2 rows use `NOT_ELIGIBLE`. Catch per-row apply exceptions, retain only the
exception class name, continue, and give exit code 3 priority over exit code 2.

- [ ] **Step 4: Run script and lifecycle tests and commit**

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py \
  tests/services/test_bohrium_node_lifecycle.py -q
git add scripts/audit_bohrium_node_runtime.py \
  tests/scripts/test_audit_bohrium_node_runtime.py
git commit -m "feat: batch stop audited Bohrium nodes"
```

---

### Task 5: Redact `node/list` logs and verify the completed command

**Files:**
- Modify: `src/base/base_table.py`
- Modify: `src/services/bohrium_node_service.py`
- Modify: `src/services/user_service.py`
- Modify: `tests/dao/test_base_table_db_config.py`
- Modify: `tests/services/test_bohrium_node_service.py`
- Modify: `tests/matmaster/services/test_user_service.py`

- [ ] **Step 1: Write the failing redaction test**

Patch `httpx.Client`, call `get_node_detail('secret-ak', 123)`, capture logs, and
assert the actual HTTP header still contains the key while logs contain
`accessKey: <redacted>` and never contain `secret-ak`.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
uv run pytest tests/services/test_bohrium_node_service.py::test_node_list_log_redacts_access_key -q
```

- [ ] **Step 3: Replace only the secret-bearing request log**

Keep the useful URL and method in the log but render the header as
`accessKey: <redacted>`. Do not change actual request headers.
Also keep existing-AK request failure logs useful by recording only the exception
class, never the exception message. Guard production dependency construction with
the same redacted exit-1 boundary as the candidate query.

- [ ] **Step 4: Run focused and regression verification**

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py \
  tests/services/test_bohrium_node_lifecycle.py \
  tests/services/test_bohrium_node_recycler.py \
  tests/services/test_bohrium_node_service.py \
  tests/test_bohrium_nodes_table.py \
  tests/monitor/test_monitor_worker.py -q
uv run pre-commit run --all-files
git diff --check
uv run python scripts/audit_bohrium_node_runtime.py --help
```

The help smoke must be non-network and document `--limit`, `--apply`, and the full
confirmation flag.

- [ ] **Step 5: Commit the redaction and final verified state**

```bash
git add src/services/bohrium_node_service.py \
  tests/services/test_bohrium_node_service.py
git commit -m "fix: redact Bohrium node list credentials"
```

Do not run the real command with `--apply` during implementation or verification.
