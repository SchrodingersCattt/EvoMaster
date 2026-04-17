# Session Directory Runtime Connection Design

## Context

Recent remote commits introduced the beginning of a session directory feature:

- `evo_chat_sessions.session_directory` stores a persistent session-level
  directory.
- `GET /api/v1/chat/sessions/{session_id}/session-directory` and
  `PUT /api/v1/chat/sessions/{session_id}/session-directory` read and write
  that value.
- `ChatSendRequest.directory` lets one `POST /stream` request carry a directory
  value.

The existing implementation does not yet connect these fields to the actual
agent runtime working directory. The current runtime path still flows through:

```text
AgentRunService.run_agent()
  -> Playground.prepare({"run_dir", "task_id"})
  -> local workdir: runs/<RUN_ID>/workspaces/<task_id>
  -> optional Bohrium SSH attach
  -> execution_workdir defaults to /share
```

This design connects `directory` and `session_directory` to the Bohrium
execution working directory while preserving the existing local workspace
semantics.

Implementation should account for the current branch state. If the working
branch does not yet contain the `feat/session-dir` commits from `origin/main`,
those schema and API changes must be merged or ported before this runtime
connection is implemented.

## Goals

- Resolve the effective run directory using this priority:

```text
POST /stream.directory > evo_chat_sessions.session_directory > no specified directory
```

- Treat the directory as a Bohrium remote working directory only.
- Accept only POSIX absolute paths inside `/share`.
- Force Bohrium execution when an effective directory exists.
- Automatically create a missing remote directory after SSH attach.
- Preserve local `Playground.prepare()` workspaces for logs, cache, local
  archival, and workspace upload.
- Persist directory metadata in user query history so replay and restart flows
  do not lose the chosen directory.

## Non-Goals

- Do not add a new `session_directory` SSE system event in the first version.
- Do not support local filesystem paths.
- Do not support relative paths that map implicitly into `/share`.
- Do not support remote paths outside `/share`.
- Do not let `POST /stream.directory` update the persistent
  `evo_chat_sessions.session_directory` value.
- Do not change local workspace path generation in `Playground.prepare()`.
- Do not silently fall back to local execution when an effective Bohrium
  directory exists.

## Architecture

Introduce a small resolver, for example
`src/services/session_directory_service.py`, with one responsibility:
resolve and validate the effective Bohrium remote working directory for one
agent run.

The resolver should not start SSH, mutate sessions, enqueue jobs, or run the
agent. It only translates request and session metadata into a structured runtime
decision.

Suggested result model:

```python
@dataclass(frozen=True)
class ResolvedSessionDirectory:
    remote_workdir: str | None
    source: Literal["request", "session", "none"]
    bohrium_required: bool
```

Suggested resolver API:

```python
class SessionDirectoryResolver:
    def resolve(
        self,
        *,
        session_id: str,
        request_directory: str | None,
        request_directory_provided: bool,
    ) -> ResolvedSessionDirectory:
        ...
```

The `request_directory_provided` flag is important because Pydantic defaults
make it hard to distinguish an omitted field from an explicitly provided `null`
unless the caller checks `req.model_dump(exclude_unset=True)`.

## Directory Semantics

The selected behavior is:

- If `POST /stream` provides a non-empty `directory`, use it for this run.
- If `POST /stream.directory` is omitted, `null`, or blank, fall back to the
  session's persistent `session_directory`.
- If neither request nor session has a directory, preserve existing behavior.
- `POST /stream.directory` never updates `evo_chat_sessions.session_directory`.
- Clearing the persistent default directory remains the responsibility of
  `PUT /session-directory` with `null` or an empty string.

This means an explicitly blank per-run `directory` is not a command to clear or
bypass the session default. It simply means this request has no per-run override.

## Path Validation

Validation should be centralized in a helper such as:

```python
def normalize_remote_share_path(raw: str) -> str:
    ...
```

The helper should treat the input as a remote POSIX path, not as a local macOS
path. It should use POSIX path semantics, for example via `PurePosixPath` or
manual segment handling, and must not call local `Path.resolve()`.

Valid examples:

```text
/share -> /share
/share/foo -> /share/foo
/share/foo/./bar/ -> /share/foo/bar
/share/foo/../bar -> /share/bar
```

Invalid examples:

```text
relative/path
/tmp/foo
/share2/foo
/share/../root
/share/foo/../../root
path with NUL
non-string values
```

Rules:

- The path must be a string after request validation.
- The stripped path must be non-empty when selected.
- The path must be absolute.
- The normalized path must be `/share` or a descendant of `/share`.
- `..` may normalize inside `/share`, but may not escape above `/share`.
- NUL characters are rejected.

Invalid request directory should fail before the run is enqueued. Invalid
persistent session directory should also fail, because it is now the selected
default for the run and ignoring it would hide a bad configuration.

## Error Handling

Introduce a small structured exception, for example:

```python
class SessionDirectoryError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        http_status: int = 400,
    ) -> None:
        ...
```

Suggested error codes:

- `directory_invalid_type`
- `directory_invalid_chars`
- `directory_must_be_absolute`
- `directory_outside_share`

`chat_api.py` or `ChatStreamService.prepare_send_message()` should translate
this exception into the existing `BaseErrorResponse` shape. Invalid directory
input should not acquire a run, enqueue a Redis job, or write partial run state.

If an effective directory exists, `bohrium_required` must be true. Missing
Bohrium credentials, missing project information, access key failure, node
creation failure, SSH failure, or remote directory creation failure should fail
the run with existing `error` and `stream_closed` behavior. There must be no
fallback to local execution or plain `/share`.

## Runtime Data Flow

The runtime data flow should be explicit:

```text
ChatSendRequest.directory
  -> ChatStreamService.prepare_send_message()
  -> SessionDirectoryResolver.resolve()
  -> SendStreamContext.remote_workdir/source
  -> Redis job.remote_workdir/source
  -> agent_worker
  -> AgentRunService.run_agent(remote_workdir=...)
  -> BohriumSetupService.run_setup(remote_workdir=...)
  -> SSHSessionConfig.working_dir/workspace_path
  -> pg_ctx.with_execution(execution_workdir=remote_workdir)
  -> Exp tools use ctx.execution_workdir
```

### Stream Service

`ChatStreamService.prepare_send_message()` should call the resolver after the
session is known and before the Redis job context is finalized.

`SendStreamContext` should gain:

```python
remote_workdir: str | None
session_directory_source: Literal["request", "session", "none"]
```

The final `bohrium_required` should be:

```python
bohrium_required = explicit_bohrium_required_from_org_project or remote_workdir is not None
```

The user query payload should include directory metadata when a directory is
selected:

```python
user_msg["session_directory"] = remote_workdir
user_msg["session_directory_source"] = source
```

No new system event is required in this version.

### Redis Job

`generate_send_stream()` should include the resolved values in the job:

```python
job = {
    ...
    "remote_workdir": ctx.remote_workdir,
    "session_directory_source": ctx.session_directory_source,
    "bohrium_required": ctx.bohrium_required,
}
```

### Worker

`agent_worker` should not re-read the session row or re-interpret paths. It
should only type-narrow and pass through:

```python
remote_workdir = payload.get("remote_workdir")
session_directory_source = payload.get("session_directory_source") or "none"
```

Then call:

```python
agent_run_service.run_agent(
    ...,
    remote_workdir=remote_workdir,
    session_directory_source=session_directory_source,
    bohrium_required=bohrium_required,
)
```

### AgentRunService

`AgentRunService.run_agent()` should preserve existing local workspace
preparation:

```python
pg_ctx = playground.prepare({"run_dir": run_dir, "task_id": task_id})
```

It should then pass `remote_workdir` to Bohrium setup:

```python
bohrium_svc.run_setup(
    ...,
    remote_workdir=remote_workdir,
    bohrium_required=bohrium_required,
)
```

When Bohrium returns an execution session, existing behavior should continue:

```python
pg_ctx = pg_ctx.with_execution(
    session=bohrium_result.execution_session,
    session_type=session_type,
    execution_workdir=bohrium_result.execution_workdir,
)
```

This keeps `pg_ctx.workdir` as the local service workspace and makes
`pg_ctx.execution_workdir` the remote Bohrium working directory used by tools.

### Bohrium Setup

`BohriumSetupService.run_setup()` should accept `remote_workdir`.

Behavior:

- If `remote_workdir` is `None` and there is no other Bohrium requirement,
  preserve existing behavior.
- If `remote_workdir` is not `None`, setup must complete Bohrium credential
  resolution, node creation or reuse, and SSH attach.
- After SSH attach, create the directory safely:

```python
mkdir -p -- <shlex.quote(remote_workdir)>
```

- Verify that it exists and is a directory:

```python
test -d <shlex.quote(remote_workdir)>
```

- Use `remote_workdir` for `SSHSessionConfig.working_dir` and
  `SSHSessionConfig.workspace_path`.
- Return `execution_workdir=remote_workdir` in `BohriumSetupResult`.

Even though paths are restricted to `/share`, shell quoting is still required.

## History Persistence

The current remote implementation places `session_directory` on the live
`user_msg`, but the history persistence path only stores `files`, `images`, and
`workspace_paths` inside the `User/query` content object. This design requires
closing that gap.

`ChatEventsService.add_history_event()` should include:

```python
content["session_directory"] = payload["session_directory"]
content["session_directory_source"] = payload["session_directory_source"]
```

for `User/query` events when those fields are present.

`ChatEventsTable.get_session_events()` and `_row_to_event()` should unpack them
back to top-level event fields:

```python
ev["session_directory"] = content.get("session_directory")
ev["session_directory_source"] = content.get("session_directory_source")
```

`get_last_user_query()` should also return them so deploy or restart recovery
does not lose the directory context.

## Testing Strategy

### Resolver Tests

Cover normalization:

```text
/share -> /share
/share/foo -> /share/foo
/share/foo/./bar/ -> /share/foo/bar
/share/foo/../bar -> /share/bar
```

Cover invalid values:

```text
relative/path
/tmp/foo
/share2/foo
/share/../root
/share/foo/../../root
NUL-containing path
non-string values
```

Cover source priority:

- Request directory wins over DB directory.
- Omitted request directory uses DB directory.
- Blank or null request directory uses DB directory.
- No request directory and no DB directory returns source `none`.
- Invalid request directory fails instead of falling back.
- Invalid DB directory fails when selected.

### Stream Service Tests

Verify:

- Request directory sets `ctx.remote_workdir`, source `request`, and
  `bohrium_required=True`.
- DB directory sets source `session` when no request override exists.
- No directory preserves existing `bohrium_required` behavior.
- Invalid directory produces a 400 path and does not enqueue a run.
- `POST /stream.directory` does not update
  `evo_chat_sessions.session_directory`.

### History Tests

Verify:

- `session_directory` and `session_directory_source` persist into
  `evo_chat_events.content` for user query events.
- `get_session_events()` replays them as top-level event fields.
- `get_last_user_query()` returns them.
- Existing `files`, `images`, and `workspace_paths` behavior is unchanged.

### Redis And Worker Tests

Verify:

- Redis job contains `remote_workdir` and `session_directory_source`.
- Worker passes both values into `AgentRunService.run_agent()`.
- Worker does not re-interpret or revalidate the path.

### AgentRunService And Bohrium Tests

Verify:

- `AgentRunService.run_agent(remote_workdir="/share/foo")` calls
  `BohriumSetupService.run_setup(remote_workdir="/share/foo",
  bohrium_required=True)`.
- Bohrium setup creates and verifies the remote directory after SSH attach.
- Returned `execution_workdir` is `/share/foo`.
- `pg_ctx.with_execution()` receives `execution_workdir="/share/foo"`.
- Tool construction continues to rely on `ctx.execution_workdir`.

### Regression Tests

Verify:

- No directory preserves local workspace generation.
- Bohrium without `remote_workdir` still uses existing default `/share`.
- Existing attachments, image inputs, and workspace path history replay are not
  regressed.

## Rollout Notes

This feature touches API request handling, session DB metadata, history
persistence, Redis job payloads, worker dispatch, Bohrium setup, and runtime
context construction. The implementation should keep changes small and
explicit, with the resolver as the only place that understands request versus
session priority and `/share` path validation.

Because the local branch may not include the remote session-directory commits,
implementation should first reconcile the base branch or port those commits.
After that, implement the resolver and then wire the result through the runtime
chain.
