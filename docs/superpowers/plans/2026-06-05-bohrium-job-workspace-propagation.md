# Bohrium Job Workspace Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the effective Bohrium SSH workspace at job submit time and propagate that same workspace into programmatic wake-up runs, so completed Bohrium jobs resume in the directory where their inputs and outputs live.

**Architecture:** Rename the internal run payload field from `remote_workdir` to `workspace` across stream enqueue, worker dequeue, `AgentRunService`, and Bohrium setup. After Bohrium setup succeeds, expose a ledger write port only when the stage produced a valid `/share` workspace; the port stores that run-level workspace on every submitted job. The DAO and DDL enforce the same non-null `/share` invariant, while scheduler/trigger wiring remains limited to accepting and carrying a `workspace` parameter.

**Tech Stack:** Python 3.11+ via `uv run`, FastAPI service code, Redis job payloads, MySQL/PyMySQL DAO, pytest, existing Bohrium stage and ledger ports.

---

## Review Gate

Spec reviewed against current checkout and approved for implementation with these execution constraints:

- `trigger_run()` currently calls `_prepare_run(..., remote_workdir=None, session_directory_source="none")`, so the wake-up path really drops the remote directory.
- User send flow already resolves `directory` / session directory before enqueue, but the Redis job still uses `remote_workdir`; this PR should migrate the internal payload to `workspace` with no runtime fallback for `remote_workdir`.
- `run_bohrium_stage()` already runs before `build_bohrium_jobs_ports()`, and `BohriumSetupService` returns the actual SSH `execution_workdir`, so the correct submit-time workspace is available before the ledger port is constructed.
- `BohriumTool._safe_ledger()` swallows ledger exceptions. Therefore workspace presence must be enforced before exposing the ledger port to the tool, not by raising inside `record_submit()` and hoping the caller notices.
- `BohriumTool` is registered for every runtime, while a Bohrium SSH workspace exists only after a successful SSH setup. Implementation must not make ordinary non-SSH/direct runs fail merely because there is no `/share` workspace; instead, pass `bohrium_job_ledger=None` when no valid SSH workspace was produced.

## File Structure

- `src/sql/migrate_add_bohrium_jobs_workspace.sql` — external/manual migration script for existing `bohrium_jobs` tables. It must not be invoked from runtime code.

- `src/services/stream_service.py` — rename internal run payload argument/key to `workspace`; make `trigger_run(workspace=...)` carry programmatic wake-up workspace.
- `src/worker/agent_worker.py` — read `payload["workspace"]` and pass `workspace` to `AgentRunService.run_agent`.
- `src/services/agent_run_service.py` — rename `run_agent(..., workspace=...)`; pass it to Bohrium stage; build a ledger write port only when stage produced a valid workspace.
- `src/services/agent_run_bohrium_stage.py` — rename stage input to `workspace`; add `BohriumStageResult.workspace`; validate SSH `execution_workdir` as `/share` before returning it.
- `src/services/agent_run_bohrium.py` — rename setup parameters from `remote_workdir` to `workspace`; keep `SSHSessionConfig.workspace_path` because it is the SSH session concept.
- `src/services/bohrium_jobs_wiring.py` — store the run-level workspace in `_BohriumJobLedger`; return `None` for the write port when no workspace exists; pass workspace to DAO on submit.
- `src/dao/bohrium_jobs_table.py` — add `workspace` to submit insert/update, claim rows, and agent-facing job rows; validate nonempty `/share` paths before SQL.
- `src/sql/create_bohrium_jobs_table.sql` — add `workspace` column and CHECK constraint.

**No Compatibility**

- Do not read both `remote_workdir` and `workspace` in the worker.
- Do not keep `remote_workdir` in Redis job payloads.
- Do not auto-fill missing workspace from current session directory or `/share`.
- Do not add runtime migration/backfill code.
- Do not change history event fields `session_directory` / `session_directory_source`; those remain display/history metadata only.

**Line-count guard:** `src/services/agent_run_service.py` and `src/services/stream_service.py` are central files. After implementation, run:

```bash
uv run python .pre-commit/check_file_lines.py \
  src/services/stream_service.py \
  src/worker/agent_worker.py \
  src/services/agent_run_service.py \
  src/services/agent_run_bohrium_stage.py \
  src/services/agent_run_bohrium.py \
  src/services/bohrium_jobs_wiring.py \
  src/dao/bohrium_jobs_table.py
```

---

### Task 1: Migrate Stream Job Payload To `workspace`

**Files:**

- Modify: `src/services/stream_service.py`
- Modify: `tests/test_chat_stream_session_directory.py`
- Modify: `tests/test_agent_run_trigger.py`
- Modify: `tests/test_chat_stream_direct.py`
- Modify: `tests/test_extension_points_smoke.py`

- [ ] **Step 1: Write failing stream payload tests**

In `tests/test_chat_stream_session_directory.py`, update the three existing payload assertions:

```python
# request directory case
assert ctx.job["workspace"] == "/share/case"
assert "remote_workdir" not in ctx.job
assert "session_directory_source" not in ctx.job
assert ctx.job["bohrium_required"] is True

# session default case
assert ctx.job["workspace"] == "/share/default"
assert "remote_workdir" not in ctx.job
assert "session_directory_source" not in ctx.job

# no directory case
assert ctx.job["workspace"] is None
assert "remote_workdir" not in ctx.job
assert "session_directory_source" not in ctx.job
```

In `tests/test_agent_run_trigger.py`, add this test after `test_trigger_run_enqueues_and_writes_system_event`:

```python
def test_trigger_run_accepts_workspace_for_programmatic_wakeup():
    service, _sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    fake_redis.dedup_key_exists.return_value = False
    fake_redis.lpush_agent_run_job.return_value = True
    p1, p2, p3, p4 = _trigger_patches(fake_redis)

    with p1, p2, p3, p4:
        res = service.trigger_run(
            "s1",
            "作业123已完成，请回到原 workspace 继续分析",
            origin="hpc_job",
            dedup_key="job:123:done",
            workspace="/share/case/../case",
            delivery=None,
        )

    assert res.status == "enqueued"
    pushed = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed["workspace"] == "/share/case"
    assert pushed["bohrium_required"] is True
    assert "remote_workdir" not in pushed
    assert "session_directory_source" not in pushed

    written = events_service.add_history_event.call_args.args[1]
    assert written["content"] == {
        "text": "作业123已完成，请回到原 workspace 继续分析",
        "origin": "hpc_job",
    }
    assert "session_directory" not in written
```

In `tests/test_agent_run_trigger.py`, add `import pytest` next to the existing imports, then add this validation test:

```python
def test_trigger_run_rejects_workspace_outside_share_before_enqueue():
    service, sessions_service, events_service = _make_trigger_service()
    fake_redis = MagicMock()
    p1, p2, p3, p4 = _trigger_patches(fake_redis)

    with p1, p2, p3, p4, pytest.raises(Exception) as exc:
        service.trigger_run(
            "s1",
            "x",
            origin="hpc_job",
            workspace="/tmp/case",
        )

    assert getattr(exc.value, "error_code", None) == "directory_outside_share"
    sessions_service.try_acquire_session_run.assert_not_called()
    events_service.add_history_event.assert_not_called()
    fake_redis.lpush_agent_run_job.assert_not_called()
```

In `tests/test_extension_points_smoke.py`, update the schedule smoke call to pass a workspace and assert it:

```python
stream_svc.trigger_run(
    row["session_id"],
    row["prompt"],
    origin="cron",
    dedup_key=f"sched:{row['id']}:{row['fire_epoch']}",
    delivery={"notify": True},
    on_busy="skip",
    workspace="/share/case",
)
```

and assert:

```python
assert kwargs["workspace"] == "/share/case"
```

In `tests/test_chat_stream_direct.py`, update `_send_stream_job()`:

```python
"workspace": None,
```

Remove the old `remote_workdir` and `session_directory_source` fixture keys.

- [ ] **Step 2: Run stream tests red**

Run:

```bash
uv run pytest \
  tests/test_chat_stream_session_directory.py \
  tests/test_agent_run_trigger.py \
  tests/test_extension_points_smoke.py \
  tests/test_chat_stream_direct.py::test_prepare_send_message_captures_turn_input_before_user_event \
  -q
```

Expected: FAIL because `ChatStreamService` still writes `remote_workdir`, `trigger_run()` has no `workspace` parameter, and worker fixtures still use the old payload shape.

- [ ] **Step 3: Implement `workspace` in `ChatStreamService`**

In `src/services/stream_service.py`, import and use the existing session directory validator:

```python
from src.services.session_directory_service import (
    SessionDirectoryResolver,
    SessionDirectorySource,
    normalize_remote_share_path,
)
```

Change `_prepare_run()` so the remote execution directory is named `workspace`:

```python
workspace: str | None = None,
```

At the top of `_prepare_run()`, before `ensure_session()` or `try_acquire_session_run()`, normalize the optional workspace and use only `workspace` in the job payload:

```python
workspace_value = normalize_remote_share_path(workspace) if workspace else None

job["bohrium_required"] = bool(bohrium_required or workspace_value)
job["workspace"] = workspace_value
```

Change `trigger_run()` signature:

```python
def trigger_run(
    self,
    session_id: str,
    prompt: str,
    *,
    origin: str,
    dedup_key: str | None = None,
    delivery: DeliverySpec | dict | None = None,
    on_busy: str = "skip",
    mode: str | None = None,
    llm: str | None = None,
    model: str | None = None,
    dedup_ttl_sec: int = DEFAULT_DEDUP_TTL_SEC,
    workspace: str | None = None,
) -> TriggerResult:
```

Pass the field through trigger:

```python
bohrium_required=bool(workspace),
workspace=workspace,
```

In `prepare_send_message()`, pass the resolved directory as workspace:

```python
workspace=resolved_directory.remote_workdir,
```

Do not include `session_directory_source` in the Redis job payload.

- [ ] **Step 4: Run stream tests green and commit**

Run:

```bash
uv run pytest \
  tests/test_chat_stream_session_directory.py \
  tests/test_agent_run_trigger.py \
  tests/test_extension_points_smoke.py \
  tests/test_chat_stream_direct.py::test_prepare_send_message_captures_turn_input_before_user_event \
  -q
git add src/services/stream_service.py \
  tests/test_chat_stream_session_directory.py \
  tests/test_agent_run_trigger.py \
  tests/test_extension_points_smoke.py \
  tests/test_chat_stream_direct.py
git commit -m "refactor(stream): use workspace in run payload"
```

Expected: pytest PASS, commit succeeds.

---

### Task 2: Propagate `workspace` Through Worker And Bohrium Setup

**Files:**

- Modify: `src/worker/agent_worker.py`
- Modify: `src/services/agent_run_service.py`
- Modify: `src/services/agent_run_bohrium_stage.py`
- Modify: `src/services/agent_run_bohrium.py`
- Modify: `tests/matmaster/worker/test_redis_bridge.py`
- Modify: `tests/matmaster/services/test_agent_run_stream_terminal.py`
- Modify: `tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py`
- Modify: `tests/matmaster/test_bohrium_setup_injection.py`
- Modify: `tests/matmaster/integration/test_bohrium_execution_contract.py`

- [ ] **Step 1: Write failing propagation assertions**

In `tests/matmaster/worker/test_redis_bridge.py`, update the queued payload fixture from:

```python
"remote_workdir": "/share/case",
```

to:

```python
"workspace": "/share/case",
```

and update the final assertion:

```python
assert observed["workspace"] == "/share/case"
assert "remote_workdir" not in observed
```

In `tests/matmaster/services/test_agent_run_stream_terminal.py`, rename `test_run_agent_passes_remote_workdir_to_bohrium_setup` to:

```python
async def test_run_agent_passes_workspace_to_bohrium_setup():
```

and update its `run_agent()` call and assertion:

```python
await agent_run_service.run_agent(
    session_id="sess-1",
    user_prompt="run",
    send_cb=lambda payload: None,
    cancel_token=CancellationToken(),
    mode="direct",
    task_id="task-1",
    workspace="/share/case",
)
```

```python
assert call_kwargs["workspace"] == "/share/case"
assert "remote_workdir" not in call_kwargs
```

Apply the same rename and assertion change in `tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py`.

In `tests/matmaster/integration/test_bohrium_execution_contract.py`, update the setup contract tests:

```python
def test_setup_uses_workspace_for_ssh_and_execution_context(
```

Call the setup helper with:

```python
workspace="/share/case",
```

and assert:

```python
assert cfg.workspace_path == "/share/case"
assert result.execution_workdir == "/share/case"
assert result.runtime_snapshot.execution_workdir == "/share/case"
```

Rename `test_run_setup_forwards_remote_workdir_to_setup` to:

```python
def test_run_setup_forwards_workspace_to_setup() -> None:
```

and assert:

```python
assert mock_setup.call_args.kwargs["workspace"] == "/share/case"
assert "remote_workdir" not in mock_setup.call_args.kwargs
```

- [ ] **Step 2: Run propagation tests red**

Run:

```bash
uv run pytest \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream_terminal.py::test_run_agent_passes_workspace_to_bohrium_setup \
  tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py::test_run_agent_passes_workspace_to_bohrium_setup \
  tests/matmaster/integration/test_bohrium_execution_contract.py::test_setup_uses_workspace_for_ssh_and_execution_context \
  tests/matmaster/integration/test_bohrium_execution_contract.py::test_run_setup_forwards_workspace_to_setup \
  -q
```

Expected: FAIL because the worker and setup layers still use `remote_workdir`.

- [ ] **Step 3: Update worker payload parsing**

In `src/worker/agent_worker.py`, replace old `remote_workdir` parsing with:

```python
raw_workspace = payload.get('workspace')
workspace = raw_workspace.strip() or None if isinstance(raw_workspace, str) else None
```

Use the parsed value in `run_agent_kwargs`:

```python
"workspace": workspace,
"bohrium_required": bool(bohrium_required or workspace),
```

Do not read `payload.get('remote_workdir')`.

- [ ] **Step 4: Rename service and stage parameters**

In `src/services/agent_run_service.py`, change `run_agent()` signature and stage call:

```python
workspace: str | None = None,
...
workspace=workspace,
```

In `src/services/agent_run_bohrium_stage.py`, update the dataclass:

```python
@dataclass(frozen=True)
class BohriumStageResult:
    """Return value of ``run_bohrium_stage``."""

    abort_result: Any | None
    bohrium_svc: BohriumSetupService
    environment: ExecutionEnvironment
    ssh_attached: bool
    user_instructions: UserInstructions
    workspace: str | None = None
```

Update `run_bohrium_stage()` signature and setup call:

```python
workspace: str | None,
effective_bohrium_required = bool(bohrium_required or workspace)
bohrium_result = await bohrium_svc.run_setup(
    session_id=session_id,
    playground=playground,
    run_started_at=run_started_at,
    bohrium_required=effective_bohrium_required,
    workspace=workspace,
)
```

When SSH setup succeeds, normalize the returned execution workdir:

```python
stage_workspace: str | None = None
if bohrium_result.execution_session is not None:
    execution_workdir = bohrium_result.execution_workdir or ''
    session_type = bohrium_result.session_type or 'ssh'
    environment = environment.with_execution(
        session=bohrium_result.execution_session,
        session_type=session_type,
        execution_workdir=execution_workdir,
    )
    if ssh_attached:
        from src.services.session_directory_service import normalize_remote_share_path

        stage_workspace = normalize_remote_share_path(execution_workdir)
```

Return `workspace=stage_workspace` in the successful `BohriumStageResult`, and `workspace=None` in abort/no-op returns.

- [ ] **Step 5: Rename Bohrium setup internals**

In `src/services/agent_run_bohrium.py`, rename parameters in `BohriumSetupService._setup_bohrium_for_run()`, `run_setup()`, `_run_setup_sync()`, and module-level `_setup_bohrium_for_run()` from `remote_workdir` to `workspace`.

The SSH path construction becomes:

```python
ssh_workspace_path = (workspace or remote_workspace_root).rstrip('/') or '/'
```

Keep `SSHSessionConfig.workspace_path` and `BohriumExecutionContext.execution_workdir` unchanged as lower-level names; only the run request parameter is renamed.

After editing, this command must return no runtime hits:

```bash
rg -n "remote_workdir" src/services/agent_run_service.py src/services/agent_run_bohrium_stage.py src/services/agent_run_bohrium.py src/worker/agent_worker.py
```

Expected: no output.

- [ ] **Step 6: Run propagation tests green and commit**

Run:

```bash
uv run pytest \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream_terminal.py::test_run_agent_passes_workspace_to_bohrium_setup \
  tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py::test_run_agent_passes_workspace_to_bohrium_setup \
  tests/matmaster/integration/test_bohrium_execution_contract.py::test_setup_uses_workspace_for_ssh_and_execution_context \
  tests/matmaster/integration/test_bohrium_execution_contract.py::test_run_setup_forwards_workspace_to_setup \
  -q
git add src/worker/agent_worker.py \
  src/services/agent_run_service.py \
  src/services/agent_run_bohrium_stage.py \
  src/services/agent_run_bohrium.py \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream_terminal.py \
  tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py \
  tests/matmaster/integration/test_bohrium_execution_contract.py
git commit -m "refactor(bohrium): propagate run workspace"
```

Expected: pytest PASS, commit succeeds.

---

### Task 3: Persist Workspace In Bohrium Job Ledger

**Files:**

- Modify: `src/services/bohrium_jobs_wiring.py`
- Modify: `src/dao/bohrium_jobs_table.py`
- Modify: `src/sql/create_bohrium_jobs_table.sql`
- Create: `src/sql/migrate_add_bohrium_jobs_workspace.sql`
- Modify: `tests/services/test_bohrium_jobs_wiring.py`
- Modify: `tests/services/test_bohrium_poller.py`

- [ ] **Step 1: Write failing ledger wiring tests**

In `tests/services/test_bohrium_jobs_wiring.py`, update `test_record_submit_passes_identity_snapshot()` to pass workspace at port construction and assert DAO receives it:

```python
ledger, _ = build_bohrium_jobs_ports(
    session_id="sess-1",
    invocation_id="inv-1",
    user_id="u1",
    org_id="o1",
    spawn_id="sp-1",
    workspace="/share/project/../project",
    table=table,
)
```

```python
assert kw["workspace"] == "/share/project"
```

Add this test after `test_record_submit_allows_null_invocation_id()`:

```python
def test_ledger_write_port_is_none_without_workspace() -> None:
    table = MagicMock()
    ledger, jobs = build_bohrium_jobs_ports(
        session_id="sess-1",
        invocation_id="inv-1",
        user_id="u1",
        org_id="o1",
        workspace=None,
        table=table,
    )

    assert ledger is None
    assert jobs is not None
    table.insert_submitted.assert_not_called()
```

Add this validation test:

```python
def test_ledger_workspace_must_be_share_path() -> None:
    table = MagicMock()
    with pytest.raises(ValueError, match="bohrium ledger workspace"):
        build_bohrium_jobs_ports(
            session_id="sess-1",
            invocation_id="inv-1",
            user_id="u1",
            org_id="o1",
            workspace="/tmp/project",
            table=table,
        )
```

For existing tests that call `build_bohrium_jobs_ports()` and need a write ledger, add `workspace="/share/project"`. For tests that only need `_RunSessionJobsPort`, use `workspace=None`.

- [ ] **Step 2: Write failing DAO and poller shape tests**

In `tests/services/test_bohrium_poller.py`, update `_submit_kwargs()`:

```python
workspace="/share/project",
```

In `test_poller_polls_due_job_and_writes_running()`, assert persisted workspace:

```python
assert row["workspace"] == "/share/project"
```

In `test_poller_first_poll_uses_initial_backoff()`, add workspace to the fake claimed row:

```python
"workspace": "/share/project",
```

Add this test after `test_poller_writes_terminal_and_stops_polling()`:

```python
def test_claim_due_batch_returns_workspace(jobs_table) -> None:
    jobs_table.insert_submitted(
        **_submit_kwargs(job_id="301", workspace="/share/project/a")
    )

    rows = jobs_table.claim_due_batch(limit=10, claim_timeout_seconds=120)

    assert rows[0]["job_id"] == "301"
    assert rows[0]["workspace"] == "/share/project/a"
```

Add this DAO validation test:

```python
def test_insert_submitted_rejects_workspace_outside_share(jobs_table) -> None:
    with pytest.raises(ValueError, match="bohrium_jobs.workspace"):
        jobs_table.insert_submitted(
            **_submit_kwargs(job_id="302", workspace="/tmp/project")
        )
```

- [ ] **Step 3: Run ledger tests red**

Run:

```bash
uv run pytest \
  tests/services/test_bohrium_jobs_wiring.py \
  tests/services/test_bohrium_poller.py \
  -q
```

Expected: FAIL because `workspace` is not accepted by the port/DAO and is absent from DDL claim rows.

- [ ] **Step 4: Implement ledger port workspace snapshot**

In `src/services/bohrium_jobs_wiring.py`, import the existing share-path validator and add a wrapper:

```python
from src.services.session_directory_service import (
    SessionDirectoryError,
    normalize_remote_share_path,
)
```

Add a local wrapper near `_FOREGROUND_POLL_BACKOFF_SECONDS`:

```python
def _normalize_ledger_workspace(workspace: str | None) -> str | None:
    if workspace is None:
        return None
    try:
        return normalize_remote_share_path(workspace)
    except SessionDirectoryError as exc:
        raise ValueError(f"bohrium ledger workspace invalid: {workspace!r}") from exc
```

Update `_BohriumJobLedger.__init__()` to require and store `workspace: str`:

```python
workspace: str,
...
self._workspace = workspace
```

Update `record_submit()`:

```python
self._table_ref.get().insert_submitted(
    session_id=self._session_id,
    invocation_id=self._invocation_id,
    spawn_id=self._spawn_id,
    user_id=self._user_id,
    org_id=self._org_id,
    job_id=str(job_id),
    job_name=job_name,
    project_id=int(project_id),
    sandbox=bool(sandbox),
    input_dir=str(input_dir),
    workspace=self._workspace,
)
```

Update `build_bohrium_jobs_ports()` so `workspace` is explicit and the write port is optional:

```python
def build_bohrium_jobs_ports(
    *,
    session_id: str,
    invocation_id: str | None,
    user_id: str,
    org_id: str,
    workspace: str | None,
    spawn_id: str | None = None,
    table: BohriumJobsTable | None = None,
    table_factory: Callable[[], BohriumJobsTable] = BohriumJobsTable,
) -> tuple[_BohriumJobLedger | None, _RunSessionJobsPort]:
    table_ref = _BohriumJobsTableRef(table=table, table_factory=table_factory)
    normalized_workspace = _normalize_ledger_workspace(workspace)
    ledger = (
        _BohriumJobLedger(
            table_ref=table_ref,
            session_id=session_id,
            invocation_id=invocation_id,
            user_id=user_id,
            org_id=org_id,
            workspace=normalized_workspace,
            spawn_id=spawn_id,
        )
        if normalized_workspace is not None
        else None
    )
    jobs = _RunSessionJobsPort(table_ref=table_ref, user_id=user_id, org_id=org_id)
    return ledger, jobs
```

This is the key invariant: no workspace means no write ledger port is exposed to `BohriumTool`.

- [ ] **Step 5: Pass stage workspace into port assembly**

In `src/services/agent_run_service.py`, update the port assembly call:

```python
bohrium_ledger_port, bohrium_jobs_port = build_bohrium_jobs_ports(
    session_id=session_id,
    invocation_id=invocation_id,
    user_id=_ledger_user_id,
    org_id=_ledger_org_id,
    workspace=stage_result.workspace,
)
```

`bohrium_ledger_port` may be `None`; `AgentRunPorts.bohrium_job_ledger` already allows `None`.

- [ ] **Step 6: Implement DAO workspace column**

In `src/dao/bohrium_jobs_table.py`, keep the DAO independent of service-layer imports and add a small local validator:

```python
import posixpath
```

Add a DAO helper near the class:

```python
def _require_workspace(workspace: str) -> str:
    if not isinstance(workspace, str):
        raise ValueError(f"bohrium_jobs.workspace must be a string, got {workspace!r}")
    stripped = workspace.strip()
    if not stripped:
        raise ValueError("bohrium_jobs.workspace must not be empty")
    if "\0" in stripped:
        raise ValueError("bohrium_jobs.workspace contains invalid characters")
    if not stripped.startswith("/"):
        raise ValueError(f"bohrium_jobs.workspace must be absolute, got {workspace!r}")
    normalized = posixpath.normpath(stripped)
    if normalized != "/share" and not normalized.startswith("/share/"):
        raise ValueError(
            f"bohrium_jobs.workspace must be /share path, got {workspace!r}"
        )
    return normalized
```

Update column constants and `insert_submitted()`:

```python
_AGENT_COLUMNS = (
    "job_id, job_name, status, sandbox, project_id, input_dir, workspace, "
    "submitted_at, last_polled_at, result_dir"
)
_CLAIM_COLUMNS = (
    "id, session_id, user_id, org_id, project_id, job_id, sandbox, "
    "workspace, status, poll_count"
)

# insert_submitted signature
workspace: str,
workspace_value = _require_workspace(workspace)
```

Update the SQL insert and duplicate update:

```python
INSERT INTO {self.table_name}
    (session_id, invocation_id, spawn_id, user_id, org_id,
     job_id, job_name, project_id, sandbox, input_dir, workspace,
     status, poll_count, submitted_at, next_poll_at)
VALUES
    (%s, %s, %s, %s, %s,
     %s, %s, %s, %s, %s, %s,
     'submitted', 0, NOW(), NOW())
ON DUPLICATE KEY UPDATE
    session_id = VALUES(session_id),
    invocation_id = VALUES(invocation_id),
    spawn_id = VALUES(spawn_id),
    job_name = VALUES(job_name),
    project_id = VALUES(project_id),
    input_dir = VALUES(input_dir),
    workspace = VALUES(workspace)
```

Add `workspace_value` to the execute tuple immediately after `input_dir`.

- [ ] **Step 7: Update create-table DDL**

In `src/sql/create_bohrium_jobs_table.sql`, add the column after `input_dir`:

```sql
    `input_dir` VARCHAR(1024) NOT NULL,
    `workspace` VARCHAR(1024) COLLATE utf8mb4_bin NOT NULL,
    `result_dir` VARCHAR(1024) NULL,
```

Add the CHECK constraint near other path/status constraints:

```sql
    CONSTRAINT `chk_workspace_share_path` CHECK (
        `workspace` = '/share' OR `workspace` LIKE '/share/%'
    ),
```

Keep table default collation unchanged.

- [ ] **Step 8: Add external migration script**

Create `src/sql/migrate_add_bohrium_jobs_workspace.sql`:

```sql
-- Add workspace to an existing bohrium_jobs table.
-- This is an external/manual migration script. Runtime code must not infer,
-- backfill, or fall back when workspace is missing.
--
-- Operator flow:
-- 1. Add the nullable column.
-- 2. Manually populate every existing row with the correct submit-time
--    /share workspace, or delete rows that cannot be recovered.
-- 3. Verify the guard SELECT returns zero rows.
-- 4. Enforce NOT NULL and the CHECK constraint.

ALTER TABLE `bohrium_jobs`
    ADD COLUMN `workspace` VARCHAR(1024) COLLATE utf8mb4_bin NULL
    AFTER `input_dir`;

-- Manual recovery step, example only:
-- UPDATE `bohrium_jobs`
-- SET `workspace` = '/share/project'
-- WHERE `id` IN (...);

SELECT `id`, `session_id`, `job_id`, `workspace`
FROM `bohrium_jobs`
WHERE `workspace` IS NULL
   OR `workspace` = ''
   OR (`workspace` <> '/share' AND `workspace` NOT LIKE '/share/%');

ALTER TABLE `bohrium_jobs`
    MODIFY COLUMN `workspace` VARCHAR(1024) COLLATE utf8mb4_bin NOT NULL,
    ADD CONSTRAINT `chk_workspace_share_path` CHECK (
        `workspace` = '/share' OR `workspace` LIKE '/share/%'
    );
```

This script intentionally includes a human verification query instead of runtime migration logic. Do not call it from Python.

- [ ] **Step 9: Run ledger tests green and commit**

Run:

```bash
uv run pytest \
  tests/services/test_bohrium_jobs_wiring.py \
  tests/services/test_bohrium_poller.py \
  -q
git add src/services/bohrium_jobs_wiring.py \
  src/services/agent_run_service.py \
  src/dao/bohrium_jobs_table.py \
  src/sql/create_bohrium_jobs_table.sql \
  src/sql/migrate_add_bohrium_jobs_workspace.sql \
  tests/services/test_bohrium_jobs_wiring.py \
  tests/services/test_bohrium_poller.py
git commit -m "feat(bohrium): persist job workspace"
```

Expected: pytest PASS, commit succeeds.

---

### Task 4: Tighten Poller/Trigger Contract Without Implementing Scheduler

**Files:**

- Modify: `tests/test_extension_points_smoke.py`

- [ ] **Step 1: Add trigger contract smoke test**

Task 3 already proves `claim_due_batch()` returns `workspace`. In `tests/test_extension_points_smoke.py`, add a scheduler-shaped example:

```python
def test_completion_dispatcher_can_pass_claimed_workspace_to_trigger_run():
    stream_svc = MagicMock()
    stream_svc.trigger_run.return_value = MagicMock(status="enqueued")
    stream_svc.trigger_run(
        "sess-1",
        "Bohrium 作业 job-1 已完成，请读取结果并继续。",
        origin="bohrium_job",
        dedup_key="bohrium_job:sess-1:job-1:done",
        delivery={"notify": False},
        on_busy="skip",
        workspace="/share/project",
    )
    kwargs = stream_svc.trigger_run.call_args.kwargs
    assert kwargs["workspace"] == "/share/project"
    assert kwargs["origin"] == "bohrium_job"
```

- [ ] **Step 2: Run contract tests**

Run:

```bash
uv run pytest \
  tests/services/test_bohrium_poller.py::test_claim_due_batch_returns_workspace \
  tests/test_extension_points_smoke.py::test_completion_dispatcher_can_pass_claimed_workspace_to_trigger_run \
  -q
```

Expected: PASS after Tasks 1-3. No production scheduler should be added in this task.

- [ ] **Step 3: Confirm poller code does not trigger runs**

Run:

```bash
rg -n "trigger_run|ChatStreamService|get_stream_service" src/services/bohrium_poller.py
```

Expected: no output. This PR carries `workspace`; the completion scheduler remains a separate design.

---

### Task 5: Clean Up Old Name And Run Focused Verification

**Files:**

- Modify as needed: any remaining test/runtime files still referencing `remote_workdir`.

- [ ] **Step 1: Remove remaining old internal payload name**

Run:

```bash
rg -n "remote_workdir" src tests matmaster
```

Expected after cleanup: no runtime/test references except `src/services/session_directory_service.py` and `tests/test_session_directory_service.py`, where `ResolvedSessionDirectory.remote_workdir` may remain because that service still models request/session directory resolution. Do not rename `session_directory`, `session_directory_source`, `workspace_paths`, `SSHSessionConfig.workspace_path`, or `ExecutionEnvironment.execution_workdir`.

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest \
  tests/test_chat_stream_session_directory.py \
  tests/test_agent_run_trigger.py \
  tests/test_extension_points_smoke.py \
  tests/test_chat_stream_direct.py::test_prepare_send_message_captures_turn_input_before_user_event \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream_terminal.py::test_run_agent_passes_workspace_to_bohrium_setup \
  tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py::test_run_agent_passes_workspace_to_bohrium_setup \
  tests/matmaster/integration/test_bohrium_execution_contract.py::test_setup_uses_workspace_for_ssh_and_execution_context \
  tests/matmaster/integration/test_bohrium_execution_contract.py::test_run_setup_forwards_workspace_to_setup \
  tests/services/test_bohrium_jobs_wiring.py \
  tests/services/test_bohrium_poller.py \
  -q
```

Expected: PASS. If `tests/services/test_bohrium_poller.py` skips because MySQL from `.env.test` is unavailable, record the skip reason and run the available non-DB poller tests plus `tests/services/test_bohrium_jobs_wiring.py`.

- [ ] **Step 3: Run file hygiene checks**

Run:

```bash
git diff --check
uv run python .pre-commit/check_file_lines.py src/services/stream_service.py src/worker/agent_worker.py src/services/agent_run_service.py src/services/agent_run_bohrium_stage.py src/services/agent_run_bohrium.py src/services/bohrium_jobs_wiring.py src/dao/bohrium_jobs_table.py tests/test_chat_stream_session_directory.py tests/test_agent_run_trigger.py tests/matmaster/worker/test_redis_bridge.py tests/services/test_bohrium_jobs_wiring.py tests/services/test_bohrium_poller.py
```

Expected: both commands PASS.

- [ ] **Step 4: Final status check**

Run:

```bash
git status --short
```

Expected: only intentional files are modified. Do not include unrelated existing untracked specs/plans in commits unless the user explicitly asks.
