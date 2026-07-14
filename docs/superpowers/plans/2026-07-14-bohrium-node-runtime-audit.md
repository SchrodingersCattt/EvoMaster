# Bohrium Node Runtime Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a strictly read-only terminal audit that reconciles historical `ready` Node slots without live invocation leases against Bohrium `node/list`.

**Architecture:** Add one narrow DAO read for audit candidates, then keep classification and rendering in a standalone script with injected credential/detail readers for deterministic tests. The production entry point uses existing-only AK lookup and the read-only Node detail adapter; it never imports or invokes lifecycle mutation methods.

**Tech Stack:** Python 3.11+, argparse, dataclasses, PyMySQL through existing `BaseTable`, httpx through `BohriumNodeService`, pytest, uv.

## Global Constraints

- The command is read-only and must not expose `--apply`, stop, restart, delete, or database UPDATE behavior.
- It must call `UserService.get_existing_bohrium_access_key()`, never the AK-creating lookup.
- It must print provider status values without guessing unverified Paused status codes.
- AccessKey and Node password must never appear in terminal output, exception summaries, or `node/list` logs.
- `--limit` defaults to 1000 and accepts only integers from 1 through 1000.
- Any credential/provider lookup failure yields exit code 2 after processing remaining rows; database query failure yields exit code 1.
- Run and verify with the repository uv environment.

---

### Task 1: Add the read-only historical-slot query

**Files:**
- Modify: `src/dao/bohrium_nodes_table.py`
- Modify: `tests/test_bohrium_nodes_table.py`

**Interfaces:**
- Consumes: `BohriumNodesTable.get_connection()` and the migrated `bohrium_node_leases` table.
- Produces: `BohriumNodesTable.list_ready_without_live_leases(limit: int) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write the failing DAO test**

Add a test that calls the new method with a fake connection and asserts the returned rows, `limit` parameter, live-lease predicate, deterministic ordering, and absence of writes:

```python
def test_list_ready_without_live_leases_is_read_only_and_oldest_first(monkeypatch):
    rows = [{"id": 1, "node_id": 20079820}]
    conn = _FakeConnection(fetchall_result=rows)
    table = _make_table(monkeypatch, conn)

    assert table.list_ready_without_live_leases(1000) == rows
    assert conn.cursor_obj.params == (1000,)
    assert "n.state = 'ready'" in conn.cursor_obj.sql
    assert "l.lease_expires_at > NOW()" in conn.cursor_obj.sql
    assert "l.id IS NULL" in conn.cursor_obj.sql
    assert "ORDER BY n.last_used_at ASC, n.id ASC" in conn.cursor_obj.sql
    assert not conn.committed
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest tests/test_bohrium_nodes_table.py::test_list_ready_without_live_leases_is_read_only_and_oldest_first -q
```

Expected: FAIL because `list_ready_without_live_leases` does not exist.

- [ ] **Step 3: Add the minimal DAO method**

Implement an explicit-column SELECT with a live-lease LEFT JOIN:

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

- [ ] **Step 4: Run the DAO tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_bohrium_nodes_table.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the query**

```bash
git add src/dao/bohrium_nodes_table.py tests/test_bohrium_nodes_table.py
git commit -m "feat: query unleased historical node slots"
```

### Task 2: Implement conservative audit classification and terminal CLI

**Files:**
- Create: `scripts/audit_bohrium_node_runtime.py`
- Create: `tests/scripts/test_audit_bohrium_node_runtime.py`

**Interfaces:**
- Consumes: `list_ready_without_live_leases(limit)`, an existing-only AK loader with signature `(user_id: str, org_id: str) -> str | None`, and a detail loader with signature `(access_key: str, node_id: int) -> dict[str, Any] | None`.
- Produces: `AuditRow`, `audit_candidates(...) -> tuple[list[AuditRow], bool]`, `render_report(rows) -> str`, and `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write failing classification and redaction tests**

Load the script with `importlib.util` following `test_poc_launching_sandbox.py`. Add tests using in-memory rows and injected functions:

```python
def _candidate(node_id: int) -> dict[str, Any]:
    return {
        "id": node_id,
        "node_id": node_id,
        "user_id": "u1",
        "org_id": "o1",
        "project_id": 99,
        "sku_id": 388,
        "last_used_at": "2026-07-01 12:00:00",
    }


def test_classifies_provider_states_conservatively():
    candidates = [
        _candidate(node_id=1),
        _candidate(node_id=2),
        _candidate(node_id=3),
        _candidate(node_id=4),
    ]
    details = {
        1: {"status": 2, "image_name": "matmaster:v1"},
        2: None,
        3: {"status": 7, "image_name": "matmaster:v1"},
        4: {"status": None, "image_name": None},
    }

    rows, incomplete = audit_candidates(
        candidates,
        access_key_loader=lambda _user, _org: "secret-ak",
        node_detail_loader=lambda _ak, node_id: details[node_id],
    )

    assert incomplete is False
    assert [row.recommendation for row in rows] == [
        "VERIFY_IDLE_THEN_STOP",
        "DB_ROW_STALE_CANDIDATE",
        "MANUAL_REVIEW_STATUS_7",
        "MANUAL_REVIEW_STATUS_UNKNOWN",
    ]
    assert "secret-ak" not in render_report(rows)
```

Add a second test proving missing AK and a provider exception become `AUDIT_INCOMPLETE`, processing continues, exception text containing a secret is not rendered, and `incomplete` is true:

```python
def test_incomplete_rows_continue_without_leaking_error_text():
    def load_access_key(user_id: str, _org_id: str) -> str | None:
        return None if user_id == "u1" else "secret-ak"

    def load_detail(_ak: str, _node_id: int) -> dict[str, Any] | None:
        raise RuntimeError("provider failed with secret-ak")

    candidates = [_candidate(1), {**_candidate(2), "user_id": "u2"}]
    rows, incomplete = audit_candidates(
        candidates,
        access_key_loader=load_access_key,
        node_detail_loader=load_detail,
    )

    assert incomplete is True
    assert [row.recommendation for row in rows] == [
        "AUDIT_INCOMPLETE",
        "AUDIT_INCOMPLETE",
    ]
    output = render_report(rows)
    assert "MissingAccessKey" in output
    assert "RuntimeError" in output
    assert "secret-ak" not in output
```

- [ ] **Step 2: Run classification tests and verify RED**

Run:

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py -q
```

Expected: collection/import FAIL because the script does not exist.

- [ ] **Step 3: Implement the pure audit model and classifier**

Create a frozen dataclass with display-only fields and no AccessKey/password field:

```python
@dataclass(frozen=True)
class AuditRow:
    node_id: int
    user_id: str
    org_id: str
    project_id: int
    sku_id: int
    last_used_at: Any
    provider_status: Any
    image_name: str | None
    recommendation: str
    error_type: str | None = None
```

Implement `audit_candidates` with an AK cache keyed by `(user_id, org_id)`. Catch per-row provider exceptions, record only `type(exc).__name__`, continue processing, and never retain the AK in `AuditRow`.

The classifier must use this conservative mapping:

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

For credential failures, create an `AUDIT_INCOMPLETE` row with
`error_type="MissingAccessKey"` or the credential exception class name. For provider exceptions,
use the provider exception class name. The exception message is never retained.

- [ ] **Step 4: Implement deterministic terminal rendering**

Render a tab-separated header and rows, replacing missing values with `-`, followed by a sorted recommendation summary:

```text
NODE_ID\tUSER_ID\tORG_ID\tPROJECT_ID\tSKU_ID\tLAST_USED_AT\tPROVIDER_STATUS\tIMAGE_NAME\tRECOMMENDATION\tERROR
...
SUMMARY total=29 audit_incomplete=0
SUMMARY DB_ROW_STALE_CANDIDATE=3
```

Use `Counter(row.recommendation for row in rows)` for sorted summaries. Do not render `repr()`
of external response objects or exceptions.

- [ ] **Step 5: Write failing CLI tests**

Test `main` with injected table/loaders or patch the module factories. Assert:

```python
assert main(["--limit", "10"], deps=fake_deps) == 0
assert main([], deps=deps_with_missing_ak) == 2
assert main([], deps=deps_with_db_failure) == 1

with pytest.raises(SystemExit) as exc_info:
    main(["--limit", "0"], deps=fake_deps)
assert exc_info.value.code == 2
```

Also assert the production dependency assembly selects
`UserService.get_existing_bohrium_access_key`, not `get_bohrium_access_key`.

- [ ] **Step 6: Implement CLI dependency assembly and exit codes**

Use an `AuditDependencies` dataclass so tests do not touch network or DB:

```python
@dataclass(frozen=True)
class AuditDependencies:
    candidate_loader: Callable[[int], list[dict[str, Any]]]
    access_key_loader: Callable[[str, str], str | None]
    node_detail_loader: Callable[[str, int], dict[str, Any] | None]
```

`main(argv=None, *, deps=None)` parses the bounded positive limit, constructs production dependencies lazily, catches only whole-query errors at the command boundary, prints the report, and returns 2 when classification is incomplete.

- [ ] **Step 7: Run script tests and verify GREEN**

Run:

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit the read-only CLI**

```bash
git add scripts/audit_bohrium_node_runtime.py tests/scripts/test_audit_bohrium_node_runtime.py
git commit -m "feat: add read-only Bohrium node runtime audit"
```

### Task 3: Redact `node/list` request logging and finish verification

**Files:**
- Modify: `src/services/bohrium_node_service.py`
- Modify: `tests/services/test_bohrium_node_service.py`

**Interfaces:**
- Consumes: `BohriumNodeService._fetch_node_list(access_key)`.
- Produces: the same provider response behavior, with a terminal-safe request log containing `<redacted>` instead of the key.

- [ ] **Step 1: Write the failing log-redaction test**

Patch `httpx.Client`, call `get_node_detail("secret-ak", 123)`, capture logs, and assert both request correctness and log redaction:

```python
assert captured["headers"]["accessKey"] == "secret-ak"
assert "<redacted>" in caplog.text
assert "secret-ak" not in caplog.text
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest tests/services/test_bohrium_node_service.py::test_node_list_log_redacts_access_key -q
```

Expected: FAIL because the current curl log contains `secret-ak`.

- [ ] **Step 3: Replace the secret-bearing curl log**

Keep the URL useful while removing the credential:

```python
logger.info(
    "Bohrium node/list request: curl -v -X GET '%s' "
    "-H 'accessKey: <redacted>'",
    url,
)
```

Do not change the actual HTTP request headers.

- [ ] **Step 4: Run focused and regression tests**

Run:

```bash
uv run pytest tests/scripts/test_audit_bohrium_node_runtime.py \
  tests/services/test_bohrium_node_service.py \
  tests/test_bohrium_nodes_table.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run repository checks**

Run pre-commit for every changed file, `git diff --check`, and the relevant Node lifecycle suite:

```bash
uv run pytest tests/services/test_bohrium_node_lifecycle.py \
  tests/services/test_bohrium_node_recycler.py \
  tests/monitor/test_monitor_worker.py -q
```

- [ ] **Step 6: Commit the redaction and final verification state**

```bash
git add src/services/bohrium_node_service.py tests/services/test_bohrium_node_service.py
git commit -m "fix: redact Bohrium node list credentials"
```

- [ ] **Step 7: Run a non-network CLI import/help smoke**

```bash
uv run python scripts/audit_bohrium_node_runtime.py --help
```

Expected: exit 0, documents `--limit`, and contains no mutation option.
