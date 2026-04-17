# Session Directory Runtime Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect `ChatSendRequest.directory` and `evo_chat_sessions.session_directory` to the Bohrium SSH execution working directory for each run.

**Architecture:** Add a single resolver that owns request-versus-session priority and `/share` POSIX path validation. Stream preparation resolves the effective remote workdir before acquiring a run, persists the resolved metadata with the user query, and passes the validated value through Redis, Worker, `AgentRunService`, and `BohriumSetupService`. SSH uses the resolved workdir as `SSHSessionConfig.working_dir` and `SSHSessionConfig.workspace_path`; local `Playground.prepare()` workspaces, `WorkspaceHandler` skip-on-SSH behavior, and skill sync remote root remain unchanged.

**Tech Stack:** FastAPI, Pydantic v2 models, Redis-backed worker queue, pytest, unittest.mock, Bohrium SSH runtime, `uv run` for all Python commands.

---

## Source Spec

Approved spec: `docs/superpowers/specs/2026-04-17-session-directory-runtime-connection-design.md`

The implementation must preserve these decisions:

- Priority is `POST /stream.directory > evo_chat_sessions.session_directory > none`.
- `directory` means a Bohrium remote POSIX workdir only.
- Valid selected paths are `/share` or descendants of `/share`.
- Blank request directory falls through to the persistent session default.
- `POST /stream.directory` never updates the DB default.
- Invalid request directory fails before run acquisition or Redis enqueue.
- Invalid persistent session directory fails when selected.
- If an effective directory exists, Bohrium is required and local fallback is forbidden.
- Missing remote directories are created through SSH setup.
- `WorkspaceHandler` stays unchanged: it uses local `pg_ctx.workdir` for snapshots and skips upload when SSH is attached.
- The SSH session config `workspace_path` follows the selected execution directory. With a selected directory it is `remote_workdir`; without one it remains the default `/share`.
- `workspace_paths` remain prompt/history metadata. Absolute remote paths such as `/share/project/a.cif` and `/personal/1.cif` keep their original meaning and are not re-rooted under `remote_workdir`.
- Skill sync stays unchanged: remote skill root remains `remote_project_root`, currently `/share/.matmaster`.
- No new `session_directory` SSE system event is added in this version.
- Restart/retry consumers are not implemented in this plan. This plan only preserves `session_directory` metadata in `get_last_user_query()` so a later restart PR can feed it back into `ChatSendRequest.directory`.

## File Structure

Create:

- `src/services/session_directory_service.py` - resolver, normalizer, structured error, storage helper.
- `tests/test_session_directory_service.py` - pure unit tests for path normalization and resolver priority.
- `tests/test_chat_session_directory_api.py` - API tests for `PUT /session-directory` validation and normalization.
- `tests/test_chat_stream_session_directory.py` - stream-service tests for resolved workdir, history payload, Redis job payload, and invalid-directory pre-enqueue failure.
- `tests/test_chat_events_session_directory.py` - history persistence/replay tests for `session_directory` and `session_directory_source`.

Modify:

- `src/apis/chat_api.py` - translate `SessionDirectoryError`, validate `PUT /session-directory`, and catch stream preparation errors.
- `src/models/chat.py` - update descriptions so `directory` is documented as a Bohrium `/share` remote workdir.
- `src/services/stream_service.py` - add resolved directory fields to `SendStreamContext`, call resolver before run acquisition, persist resolved metadata, and enqueue job fields.
- `src/services/events_service.py` - persist user-query metadata when directory fields are present, even without files/images/workspace paths.
- `src/dao/chat_events_table.py` - unpack directory fields in replay rows and return them from `get_last_user_query()`.
- `src/worker/agent_worker.py` - read `remote_workdir` from Redis job and pass it to `AgentRunService.run_agent()`.
- `src/services/agent_run_service.py` - accept `remote_workdir`, force Bohrium setup when present, and pass it to `BohriumSetupService.run_setup()`.
- `src/services/agent_run_bohrium.py` - accept `remote_workdir` and use it as SSH `working_dir`/`workspace_path` and runtime `execution_workdir`.
- `matmaster/sessions/ssh.py` - make `open()` fail if its configured working directory cannot be created and verified as a directory.
- Existing focused tests under `tests/matmaster/worker/`, `tests/matmaster/services/`, `tests/matmaster/integration/`, and `tests/matmaster/sessions/` - add assertions to existing runtime contract tests.

Line-count guard:

- Do not add new tests to `tests/test_chat_stream_direct.py`; it is already 975 lines.
- Keep changes to `src/services/agent_run_bohrium.py` concise; it is already 989 lines. After Task 6 run `wc -l src/services/agent_run_bohrium.py` and keep the result at 1000 or below.
- If `src/services/agent_run_bohrium.py` exceeds 1000 lines, move the workdir derivation helper to `src/services/bohrium_run_support.py` as `derive_ssh_working_dir(remote_workdir: str | None, default_root: str) -> str`.

## Runtime Contract

New internal data shape:

```python
@dataclass(frozen=True)
class ResolvedSessionDirectory:
    remote_workdir: str | None
    source: Literal["request", "session", "none"]
    bohrium_required: bool
```

New stream context fields:

```python
remote_workdir: str | None = None
session_directory_source: Literal["request", "session", "none"] = "none"
```

Redis job fields:

```python
"remote_workdir": ctx.remote_workdir,
"session_directory_source": ctx.session_directory_source,
```

History payload fields for selected directories:

```python
user_msg["session_directory"] = resolved.remote_workdir
user_msg["session_directory_source"] = resolved.source
```

When `resolved.source == "none"`, do not write either field into `user_msg`.

SSH config fields after setup:

```python
ssh_working_dir = (remote_workdir or remote_workspace_root).rstrip("/") or "/"
SSHSessionConfig(
    working_dir=ssh_working_dir,
    workspace_path=ssh_working_dir,
)
```

## Task 1: Resolver And `/share` Path Normalization

**Files:**

- Create: `src/services/session_directory_service.py`
- Create: `tests/test_session_directory_service.py`

- [ ] **Step 1: Write failing normalizer tests**

Create `tests/test_session_directory_service.py` with these tests:

```python
import pytest

from src.services.session_directory_service import (
    SessionDirectoryError,
    normalize_remote_share_path,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/share", "/share"),
        ("/share/foo", "/share/foo"),
        ("/share/foo/./bar/", "/share/foo/bar"),
        ("/share/foo/../bar", "/share/bar"),
        ("  /share/run-1  ", "/share/run-1"),
    ],
)
def test_normalize_remote_share_path_accepts_share_descendants(raw, expected):
    assert normalize_remote_share_path(raw) == expected


@pytest.mark.parametrize(
    ("raw", "error_code"),
    [
        (123, "directory_invalid_type"),
        ("relative/path", "directory_must_be_absolute"),
        ("/tmp/foo", "directory_outside_share"),
        ("/share2/foo", "directory_outside_share"),
        ("/share/../root", "directory_outside_share"),
        ("/share/foo/../../root", "directory_outside_share"),
        ("/share/bad\0path", "directory_invalid_chars"),
    ],
)
def test_normalize_remote_share_path_rejects_invalid_inputs(raw, error_code):
    with pytest.raises(SessionDirectoryError) as exc:
        normalize_remote_share_path(raw)

    assert exc.value.error_code == error_code
    assert exc.value.http_status == 400
```

- [ ] **Step 2: Run the normalizer tests and confirm they fail**

Run:

```bash
uv run pytest tests/test_session_directory_service.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'src.services.session_directory_service'
```

- [ ] **Step 3: Implement the normalizer and structured error**

Create `src/services/session_directory_service.py` with this core content:

```python
"""Resolve per-run Bohrium remote working directories for chat sessions."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Literal

from src.services.sessions_service import ChatSessionsService

SessionDirectorySource = Literal["request", "session", "none"]


class SessionDirectoryError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.http_status = http_status


@dataclass(frozen=True)
class ResolvedSessionDirectory:
    remote_workdir: str | None
    source: SessionDirectorySource
    bohrium_required: bool


def _blank_to_none(raw: str | None) -> str | None:
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def normalize_remote_share_path(raw: object) -> str:
    if not isinstance(raw, str):
        raise SessionDirectoryError(
            "directory must be a string",
            error_code="directory_invalid_type",
        )

    stripped = raw.strip()
    if "\0" in stripped:
        raise SessionDirectoryError(
            "directory contains invalid characters",
            error_code="directory_invalid_chars",
        )
    if not stripped.startswith("/"):
        raise SessionDirectoryError(
            "directory must be an absolute POSIX path",
            error_code="directory_must_be_absolute",
        )

    normalized = posixpath.normpath(stripped)
    if normalized != "/share" and not normalized.startswith("/share/"):
        raise SessionDirectoryError(
            "directory must be /share or a descendant of /share",
            error_code="directory_outside_share",
        )
    return normalized


def normalize_session_directory_for_storage(raw: str | None) -> str | None:
    selected = _blank_to_none(raw)
    if selected is None:
        return None
    return normalize_remote_share_path(selected)
```

- [ ] **Step 4: Run the normalizer tests and confirm they pass**

Run:

```bash
uv run pytest tests/test_session_directory_service.py -q
```

Expected:

```text
10 passed
```

- [ ] **Step 5: Write failing resolver priority tests**

Append these tests to `tests/test_session_directory_service.py`:

```python
from unittest.mock import MagicMock

from src.services.session_directory_service import SessionDirectoryResolver


def _sessions_service(session_directory):
    svc = MagicMock()
    svc.get_session.return_value = {"session_directory": session_directory}
    return svc


def test_resolver_uses_request_directory_before_session_default():
    resolver = SessionDirectoryResolver(_sessions_service("/share/default"))

    result = resolver.resolve(
        session_id="sess-1",
        request_directory="/share/request/../run",
        request_directory_provided=True,
    )

    assert result.remote_workdir == "/share/run"
    assert result.source == "request"
    assert result.bohrium_required is True


def test_resolver_blank_request_falls_through_to_session_default():
    svc = _sessions_service("/share/default")
    resolver = SessionDirectoryResolver(svc)

    result = resolver.resolve(
        session_id="sess-1",
        request_directory="   ",
        request_directory_provided=True,
    )

    assert result.remote_workdir == "/share/default"
    assert result.source == "session"
    assert result.bohrium_required is True
    svc.get_session.assert_called_once_with("sess-1")


def test_resolver_without_request_uses_session_default():
    resolver = SessionDirectoryResolver(_sessions_service("/share/default"))

    result = resolver.resolve(
        session_id="sess-1",
        request_directory=None,
        request_directory_provided=False,
    )

    assert result.remote_workdir == "/share/default"
    assert result.source == "session"
    assert result.bohrium_required is True


def test_resolver_without_any_directory_returns_none_source():
    resolver = SessionDirectoryResolver(_sessions_service(None))

    result = resolver.resolve(
        session_id="sess-1",
        request_directory=None,
        request_directory_provided=False,
    )

    assert result.remote_workdir is None
    assert result.source == "none"
    assert result.bohrium_required is False


def test_resolver_relabels_invalid_session_default():
    resolver = SessionDirectoryResolver(_sessions_service("/tmp/bad"))

    with pytest.raises(SessionDirectoryError) as exc:
        resolver.resolve(
            session_id="sess-1",
            request_directory=None,
            request_directory_provided=False,
        )

    assert exc.value.error_code == "session_directory_invalid"
```

- [ ] **Step 6: Run resolver tests and confirm they fail on missing class**

Run:

```bash
uv run pytest tests/test_session_directory_service.py -q
```

Expected:

```text
ImportError: cannot import name 'SessionDirectoryResolver'
```

- [ ] **Step 7: Implement the resolver**

Add this class to `src/services/session_directory_service.py`:

```python
class SessionDirectoryResolver:
    def __init__(self, sessions_service: ChatSessionsService) -> None:
        self._sessions_service = sessions_service

    def resolve(
        self,
        *,
        session_id: str,
        request_directory: str | None,
        request_directory_provided: bool,
    ) -> ResolvedSessionDirectory:
        if request_directory_provided:
            selected_request = _blank_to_none(request_directory)
            if selected_request is not None:
                return ResolvedSessionDirectory(
                    remote_workdir=normalize_remote_share_path(selected_request),
                    source="request",
                    bohrium_required=True,
                )

        row = self._sessions_service.get_session(session_id)
        session_directory = None
        if row:
            session_directory = _blank_to_none(row.get("session_directory"))
        if session_directory is None:
            return ResolvedSessionDirectory(
                remote_workdir=None,
                source="none",
                bohrium_required=False,
            )

        try:
            remote_workdir = normalize_remote_share_path(session_directory)
        except SessionDirectoryError as exc:
            raise SessionDirectoryError(
                "persistent session directory is invalid",
                error_code="session_directory_invalid",
                http_status=exc.http_status,
            ) from exc

        return ResolvedSessionDirectory(
            remote_workdir=remote_workdir,
            source="session",
            bohrium_required=True,
        )
```

- [ ] **Step 8: Run resolver tests and commit**

Run:

```bash
uv run pytest tests/test_session_directory_service.py -q
git add src/services/session_directory_service.py tests/test_session_directory_service.py
git commit -m "feat: add session directory resolver"
```

Expected:

```text
15 passed
```

## Task 2: Validate `PUT /session-directory` At The API Boundary

**Files:**

- Modify: `src/apis/chat_api.py`
- Modify: `src/models/chat.py`
- Create: `tests/test_chat_session_directory_api.py`

- [ ] **Step 1: Write failing API tests for valid, invalid, and clear requests**

Create `tests/test_chat_session_directory_api.py`:

```python
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app import app
from src.services.sessions_service import get_sessions_service


def _install_sessions_service(mock_svc):
    app.dependency_overrides[get_sessions_service] = lambda: mock_svc


def _clear_overrides():
    app.dependency_overrides.pop(get_sessions_service, None)


def test_put_session_directory_normalizes_share_path_before_storage():
    mock_svc = MagicMock()
    mock_svc.set_session_directory.return_value = True
    mock_svc.get_session.return_value = {"session_directory": "/share/bar"}
    _install_sessions_service(mock_svc)

    try:
        client = TestClient(app)
        response = client.put(
            "/api/v1/chat/sessions/sess-1/session-directory",
            json={"directory": " /share/foo/../bar/ "},
            headers={"X-User-Id": "user-1"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert response.json()["data"]["directory"] == "/share/bar"
    mock_svc.set_session_directory.assert_called_once_with(
        "sess-1",
        "/share/bar",
        "user-1",
    )


def test_put_session_directory_rejects_outside_share_without_writing():
    mock_svc = MagicMock()
    _install_sessions_service(mock_svc)

    try:
        client = TestClient(app)
        response = client.put(
            "/api/v1/chat/sessions/sess-1/session-directory",
            json={"directory": "/tmp/foo"},
            headers={"X-User-Id": "user-1"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["data"]["error_code"] == "directory_outside_share"
    mock_svc.set_session_directory.assert_not_called()


def test_put_session_directory_blank_clears_storage_value():
    mock_svc = MagicMock()
    mock_svc.set_session_directory.return_value = True
    mock_svc.get_session.return_value = {"session_directory": None}
    _install_sessions_service(mock_svc)

    try:
        client = TestClient(app)
        response = client.put(
            "/api/v1/chat/sessions/sess-1/session-directory",
            json={"directory": "   "},
            headers={"X-User-Id": "user-1"},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert response.json()["data"]["directory"] is None
    mock_svc.set_session_directory.assert_called_once_with(
        "sess-1",
        None,
        "user-1",
    )
```

- [ ] **Step 2: Run API tests and confirm invalid request currently writes**

Run:

```bash
uv run pytest tests/test_chat_session_directory_api.py -q
```

Expected:

```text
1 failed, 2 passed
```

The failing test is `test_put_session_directory_rejects_outside_share_without_writing`.

- [ ] **Step 3: Add API error translation and storage normalization**

In `src/apis/chat_api.py`, import the service and translate its error:

```python
from src.services.session_directory_service import (
    SessionDirectoryError,
    normalize_session_directory_for_storage,
)
```

Add this helper near the chat API route helpers:

```python
def _session_directory_error(exc: SessionDirectoryError) -> BaseErrorResponse:
    return BaseErrorResponse(
        http_status=exc.http_status,
        code=exc.http_status,
        msg=exc.message,
        data={"error_code": exc.error_code},
    )
```

Change `set_session_directory()` to normalize before calling the service:

```python
    try:
        normalized_directory = normalize_session_directory_for_storage(body.directory)
    except SessionDirectoryError as exc:
        raise _session_directory_error(exc) from exc

    if not chat_svc.set_session_directory(sid, normalized_directory, user_id):
        raise NotFoundErrorResponse(
            msg="Session not found or you are not the owner",
        )
```

Use `code=exc.http_status` to match nearby generic `BaseErrorResponse` usage in this API. Directory-specific branching belongs in `data.error_code`.

- [ ] **Step 4: Update public request descriptions**

In `src/models/chat.py`, adjust both descriptions so the OpenAPI schema documents the runtime rule:

```python
description="绑定 Bohrium 远端 /share 工作目录；传 null 或空字符串表示清除",
```

and:

```python
description="可选，本轮 Bohrium 远端 /share 工作目录；不会更新会话持久化目录",
```

In `src/apis/chat_api.py`, update the stream and session-directory route descriptions to list the public directory error codes:

```python
directory_invalid_type, directory_invalid_chars, directory_must_be_absolute,
directory_outside_share, session_directory_invalid
```

- [ ] **Step 5: Run focused API and OpenAPI tests, then commit**

Run:

```bash
uv run pytest tests/test_chat_session_directory_api.py tests/test_openapi_chat_docs.py -q
git add src/apis/chat_api.py src/models/chat.py tests/test_chat_session_directory_api.py
git commit -m "feat: validate session directory API input"
```

Expected:

```text
4 passed
```

## Task 3: Resolve Directory During Stream Preparation And Enqueue It

**Files:**

- Modify: `src/apis/chat_api.py`
- Modify: `src/services/stream_service.py`
- Create: `tests/test_chat_stream_session_directory.py`

- [ ] **Step 1: Write failing stream preparation tests**

Create `tests/test_chat_stream_session_directory.py`:

```python
import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from src.models.chat import ChatSendRequest
from src.services.stream_service import ChatStreamService, SendStreamContext


def _service(session_directory=None):
    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {
        "session_directory": session_directory,
    }
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    deploy_state_service = MagicMock()
    return ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=deploy_state_service,
    ), sessions_service, events_service


def test_prepare_send_message_uses_request_directory_and_marks_bohrium_required():
    service, sessions_service, events_service = _service("/share/default")
    req = ChatSendRequest(content="run", directory="/share/request/../case")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.remote_workdir == "/share/case"
    assert ctx.session_directory_source == "request"
    assert ctx.bohrium_required is True
    assert ctx.user_msg["session_directory"] == "/share/case"
    assert ctx.user_msg["session_directory_source"] == "request"
    events_service.add_history_event.assert_called_once()
    stored = events_service.add_history_event.call_args.args[1]
    assert stored["session_directory"] == "/share/case"
    assert stored["session_directory_source"] == "request"
    sessions_service.try_acquire_session_run.assert_called_once_with("sess-1")


def test_prepare_send_message_blank_request_falls_through_to_session_directory():
    service, _sessions_service, _events_service = _service("/share/default")
    req = ChatSendRequest(content="run", directory="   ")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.remote_workdir == "/share/default"
    assert ctx.session_directory_source == "session"
    assert ctx.user_msg["session_directory"] == "/share/default"
    assert ctx.user_msg["session_directory_source"] == "session"


def test_prepare_send_message_without_directory_keeps_existing_no_bohrium_behavior():
    service, _sessions_service, events_service = _service(None)
    req = ChatSendRequest(content="run")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx is not None
    assert ctx.remote_workdir is None
    assert ctx.session_directory_source == "none"
    assert ctx.bohrium_required is False
    stored = events_service.add_history_event.call_args.args[1]
    assert "session_directory" not in stored
    assert "session_directory_source" not in stored


def test_prepare_send_message_invalid_request_directory_does_not_acquire_run():
    service, sessions_service, events_service = _service("/share/default")
    req = ChatSendRequest(content="run", directory="/tmp/bad")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        with pytest.raises(Exception) as exc:
            service.prepare_send_message("sess-1", req, user_id="user-1")

    assert getattr(exc.value, "error_code") == "directory_outside_share"
    sessions_service.try_acquire_session_run.assert_not_called()
    events_service.add_history_event.assert_not_called()


def test_prepare_send_message_invalid_session_directory_does_not_acquire_run():
    service, sessions_service, events_service = _service("/tmp/bad-default")
    req = ChatSendRequest(content="run")

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        with pytest.raises(Exception) as exc:
            service.prepare_send_message("sess-1", req, user_id="user-1")

    assert getattr(exc.value, "error_code") == "session_directory_invalid"
    sessions_service.try_acquire_session_run.assert_not_called()
    events_service.add_history_event.assert_not_called()
```

- [ ] **Step 2: Run stream preparation tests and confirm fields are missing**

Run:

```bash
uv run pytest tests/test_chat_stream_session_directory.py -q
```

Expected:

```text
5 failed
```

At least one failure mentions that `SendStreamContext` has no `remote_workdir`.

- [ ] **Step 3: Add fields to `SendStreamContext`**

In `src/services/stream_service.py`, import the literal type and resolver:

```python
from src.services.session_directory_service import (
    SessionDirectoryError,
    SessionDirectoryResolver,
    SessionDirectorySource,
)
```

Update `SendStreamContext`:

```python
    remote_workdir: str | None = None
    session_directory_source: SessionDirectorySource = "none"
```

- [ ] **Step 4: Resolve before acquiring the run**

In `ChatStreamService.prepare_send_message()`, compute request fields once and resolve the directory after `ensure_session()` and before `try_acquire_session_run()`:

```python
        req_fields = req.model_dump(exclude_unset=True)
        self._sessions_service.ensure_session(sid, user_id=user_id)

        resolved_directory = SessionDirectoryResolver(
            self._sessions_service
        ).resolve(
            session_id=sid,
            request_directory=req.directory,
            request_directory_provided="directory" in req_fields,
        )

        acquired_ok, _ = self._sessions_service.try_acquire_session_run(sid)
        if not acquired_ok:
            return None
```

When computing `bohrium_required`, include the resolved decision:

```python
        bohrium_required = bool(
            (org_id_val and project_id_val is not None)
            or resolved_directory.bohrium_required
        )
```

When building `user_msg`, only persist selected directories:

```python
        if resolved_directory.source != "none":
            user_msg["session_directory"] = resolved_directory.remote_workdir
            user_msg["session_directory_source"] = resolved_directory.source
```

When returning `SendStreamContext`, include:

```python
            remote_workdir=resolved_directory.remote_workdir,
            session_directory_source=resolved_directory.source,
```

- [ ] **Step 5: Catch stream preparation errors in `chat_api.py`**

In the stream endpoint, wrap `prepare_send_message()`:

```python
    try:
        ctx = stream_svc.prepare_send_message(sid, req, user_id, org_id=org_id)
    except SessionDirectoryError as exc:
        raise _session_directory_error(exc) from exc
```

This keeps invalid request directories and invalid selected session defaults as normal `BaseErrorResponse` JSON with `data.error_code`.

- [ ] **Step 6: Write failing Redis job payload test**

Append this test to `tests/test_chat_stream_session_directory.py`:

```python
@pytest.mark.asyncio
async def test_generate_send_stream_enqueues_remote_workdir_and_source():
    service = ChatStreamService(
        sessions_service=MagicMock(
            get_session_status_payload=MagicMock(
                return_value={
                    "source": "System",
                    "type": "status",
                    "content": "",
                    "session_id": "sess-1",
                }
            )
        ),
        events_service=MagicMock(get_session_events=MagicMock(return_value=[])),
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )
    ctx = SendStreamContext(
        task_id="task-1",
        invocation_id="inv-1",
        mode="direct",
        user_msg={"source": "User", "type": "query", "content": "run"},
        request_event_queue=asyncio.Queue(),
        reply_queue=MagicMock(),
        remote_workdir="/share/case",
        session_directory_source="request",
        bohrium_required=True,
    )
    fake_redis = MagicMock()
    fake_redis.create_client.return_value = None
    fake_redis.set_session_run_queued.return_value = True
    fake_redis.llen_agent_run_queue.return_value = 0
    fake_redis.lpush_agent_run_job.return_value = True

    async def _stream_closed_immediately(awaitable, timeout):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        return {
            "source": "System",
            "type": "stream_closed",
            "content": "",
            "session_id": "sess-1",
        }

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=fake_redis),
        patch(
            "src.services.stream_service.asyncio.wait_for",
            side_effect=_stream_closed_immediately,
        ),
    ):
        gen = service.generate_send_stream("sess-1", "run", ctx)
        await gen.__anext__()
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()

    pushed_job = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed_job["remote_workdir"] == "/share/case"
    assert pushed_job["session_directory_source"] == "request"
    assert pushed_job["bohrium_required"] is True
```

- [ ] **Step 7: Add Redis job fields**

In `ChatStreamService.generate_send_stream()`, add these keys to `job`:

```python
                "remote_workdir": ctx.remote_workdir,
                "session_directory_source": ctx.session_directory_source,
```

- [ ] **Step 8: Run stream tests and commit**

Run:

```bash
uv run pytest tests/test_chat_stream_session_directory.py tests/test_chat_stream_direct.py::test_generate_send_stream_enqueues_bohrium_required_flag -q
git add src/apis/chat_api.py src/services/stream_service.py tests/test_chat_stream_session_directory.py
git commit -m "feat: resolve session directory during stream enqueue"
```

Expected:

```text
7 passed
```

## Task 4: Persist Directory Metadata In User Query History

**Files:**

- Modify: `src/services/events_service.py`
- Modify: `src/dao/chat_events_table.py`
- Create: `tests/test_chat_events_session_directory.py`

- [ ] **Step 1: Write failing service and DAO tests**

Create `tests/test_chat_events_session_directory.py`:

```python
import json
from unittest.mock import MagicMock

from src.dao.chat_events_table import ChatEventsTable
from src.services.events_service import ChatEventsService


def test_add_history_event_persists_session_directory_metadata_without_files():
    table = MagicMock()
    sessions = MagicMock()
    service = ChatEventsService(events_table=table, sessions_service=sessions)

    service.add_history_event(
        "sess-1",
        {
            "source": "User",
            "type": "query",
            "content": "run",
            "mode": "direct",
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "session_directory": "/share/case",
            "session_directory_source": "request",
        },
        user_id="user-1",
    )

    stored_content = table.add_event.call_args.args[3]
    assert stored_content == {
        "content": "run",
        "session_directory": "/share/case",
        "session_directory_source": "request",
    }


def test_row_to_event_unpacks_session_directory_metadata():
    row = {
        "id": 1,
        "session_id": "sess-1",
        "source": "User",
        "type": "query",
        "content": json.dumps(
            {
                "content": "run",
                "session_directory": "/share/case",
                "session_directory_source": "session",
            }
        ),
        "task_id": "task-1",
        "invocation_id": "inv-1",
        "spawn_id": None,
        "created_at": None,
    }

    event = ChatEventsTable._row_to_event(row)

    assert event["content"] == "run"
    assert event["session_directory"] == "/share/case"
    assert event["session_directory_source"] == "session"
```

- [ ] **Step 2: Run history tests and confirm metadata is missing**

Run:

```bash
uv run pytest tests/test_chat_events_session_directory.py -q
```

Expected:

```text
2 failed
```

- [ ] **Step 3: Persist directory metadata from `ChatEventsService`**

In `src/services/events_service.py`, replace the existing user-query metadata condition with a metadata key check:

```python
        query_metadata_keys = (
            "files",
            "images",
            "workspace_paths",
            "session_directory",
            "session_directory_source",
        )
        if (
            source == "User"
            and event_type == "query"
            and any(payload.get(key) for key in query_metadata_keys)
        ):
            content = {"content": content}
            if payload.get("files"):
                content["files"] = list(payload["files"])
            if payload.get("images"):
                content["images"] = list(payload["images"])
            if payload.get("workspace_paths"):
                content["workspace_paths"] = list(payload["workspace_paths"])
            if payload.get("session_directory"):
                content["session_directory"] = payload["session_directory"]
            if payload.get("session_directory_source"):
                content["session_directory_source"] = payload[
                    "session_directory_source"
                ]
```

- [ ] **Step 4: Unpack directory metadata in `_row_to_event()` and `get_session_events()`**

In both unpacking blocks in `src/dao/chat_events_table.py`, add:

```python
            if content.get("session_directory"):
                ev["session_directory"] = content.get("session_directory")
            if content.get("session_directory_source"):
                ev["session_directory_source"] = content.get(
                    "session_directory_source"
                )
```

Keep `files`, `images`, and `workspace_paths` defaults as lists.

- [ ] **Step 5: Write failing `get_last_user_query()` test with a fake cursor**

Append this test to `tests/test_chat_events_session_directory.py`:

```python
class _Cursor:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _Cursor(self._row)


def test_get_last_user_query_returns_session_directory_metadata():
    table = ChatEventsTable.__new__(ChatEventsTable)
    table.get_connection = lambda: _Connection(
        {
            "session_id": "sess-1",
            "source": "User",
            "type": "query",
            "content": json.dumps(
                {
                    "content": "run",
                    "session_directory": "/share/case",
                    "session_directory_source": "request",
                }
            ),
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "created_at": None,
        }
    )

    last = table.get_last_user_query("sess-1")

    assert last["content"] == "run"
    assert last["session_directory"] == "/share/case"
    assert last["session_directory_source"] == "request"
```

- [ ] **Step 6: Return directory metadata from `get_last_user_query()`**

In the dict-content branch of `ChatEventsTable.get_last_user_query()`, include:

```python
                        "session_directory": content.get("session_directory"),
                        "session_directory_source": content.get(
                            "session_directory_source"
                        )
                        or "none",
```

In the string-content branch, include:

```python
                    "session_directory": None,
                    "session_directory_source": "none",
```

Update the docstrings in `src/services/events_service.py` and `src/dao/chat_events_table.py` to mention `images`, `session_directory`, and `session_directory_source`.

Do not add new `mode` persistence in this task. `get_last_user_query()` already reads `mode` when old rows contain it and defaults to `"direct"` when they do not; changing mode persistence should stay in a separate PR.

- [ ] **Step 7: Run history tests and commit**

Run:

```bash
uv run pytest tests/test_chat_events_session_directory.py -q
git add src/services/events_service.py src/dao/chat_events_table.py tests/test_chat_events_session_directory.py
git commit -m "feat: persist session directory in query history"
```

Expected:

```text
3 passed
```

## Task 5: Pass Remote Workdir Through Redis Worker

**Files:**

- Modify: `src/worker/agent_worker.py`
- Modify: `tests/matmaster/worker/test_redis_bridge.py`

- [ ] **Step 1: Extend the existing worker test with remote workdir assertions**

In `tests/matmaster/worker/test_redis_bridge.py`, update the `payload` inside `test_run_worker_loop_passes_cancel_token_and_cleans_up_controller`:

```python
        payload = {
            "session_id": "sid-1",
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "user_prompt": "hello",
            "mode": "direct",
            "remote_workdir": "/share/case",
            "session_directory_source": "request",
            "bohrium_required": True,
        }
```

Add these assertions after the existing cancel-token assertion:

```python
        assert observed["remote_workdir"] == "/share/case"
        assert observed["bohrium_required"] is True
```

- [ ] **Step 2: Run the worker test and confirm remote workdir is missing**

Run:

```bash
uv run pytest tests/matmaster/worker/test_redis_bridge.py::TestAgentWorkerCancellationIntegration::test_run_worker_loop_passes_cancel_token_and_cleans_up_controller -q
```

Expected:

```text
KeyError: 'remote_workdir'
```

- [ ] **Step 3: Read and pass `remote_workdir` in `agent_worker.py`**

After the `bohrium_required` extraction in `src/worker/agent_worker.py`, add:

```python
        raw_remote_workdir = payload.get("remote_workdir")
        remote_workdir = (
            raw_remote_workdir.strip()
            if isinstance(raw_remote_workdir, str) and raw_remote_workdir.strip()
            else None
        )
```

Pass it to `run_agent()`:

```python
                        remote_workdir=remote_workdir,
                        bohrium_required=bohrium_required,
```

Do not re-normalize here. The worker consumes only values already validated by the API process.

- [ ] **Step 4: Run worker tests and commit**

Run:

```bash
uv run pytest tests/matmaster/worker/test_redis_bridge.py -q
git add src/worker/agent_worker.py tests/matmaster/worker/test_redis_bridge.py
git commit -m "feat: pass session directory through worker jobs"
```

Expected:

```text
5 passed
```

## Task 6: Wire Remote Workdir Into Agent And Bohrium SSH Setup

**Files:**

- Modify: `src/services/agent_run_service.py`
- Modify: `src/services/agent_run_bohrium.py`
- Modify: `matmaster/sessions/ssh.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`
- Modify: `tests/matmaster/integration/test_bohrium_execution_contract.py`
- Modify: `tests/matmaster/sessions/test_ssh_session.py`

- [ ] **Step 1: Write failing `AgentRunService` pass-through test**

Append this test to `tests/matmaster/services/test_agent_run_stream.py`:

```python
@pytest.mark.asyncio
async def test_run_agent_passes_remote_workdir_to_bohrium_setup():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        await svc.run_agent(
            session_id="sess-1",
            user_prompt="hello",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            remote_workdir="/share/case",
        )

        bohrium_svc = svc._test_bohrium_svc
        call_kwargs = bohrium_svc.run_setup.call_args.kwargs

    assert call_kwargs["remote_workdir"] == "/share/case"
    assert call_kwargs["bohrium_required"] is True
```

In `_patched_service()`, after `svc._test_events_table = events_table_fn.return_value`, store the instance for assertions:

```python
        svc._test_bohrium_svc = bohrium_inst
```

- [ ] **Step 2: Run the service test and confirm signature failure**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream.py::test_run_agent_passes_remote_workdir_to_bohrium_setup -q
```

Expected:

```text
TypeError: AgentRunService.run_agent() got an unexpected keyword argument 'remote_workdir'
```

- [ ] **Step 3: Add `remote_workdir` to `AgentRunService.run_agent()`**

In `src/services/agent_run_service.py`, add the parameter:

```python
        remote_workdir: str | None = None,
```

Before the `bohrium_svc.run_setup` call, compute:

```python
            effective_bohrium_required = bool(bohrium_required or remote_workdir)
```

Pass both values:

```python
                bohrium_required=effective_bohrium_required,
                remote_workdir=remote_workdir,
```

Keep `WorkspaceHandler(workspace_path=pg_ctx.workdir, ssh_attached=ssh_attached)` unchanged.

- [ ] **Step 4: Write failing Bohrium setup tests**

Append this test to `tests/matmaster/integration/test_bohrium_execution_contract.py`:

```python
@patch.object(arb, "_sync_skills_to_ssh_session", MagicMock())
@patch.object(arb, "_run_clear_remote_proxy", MagicMock())
@patch.object(arb, "_remote_session_workspace_root", return_value="/share")
@patch("src.services.agent_run_bohrium.get_bohrium_nodes_table")
@patch("src.services.agent_run_bohrium.get_bohrium_node_service")
def test_setup_uses_remote_workdir_for_ssh_and_execution_context(
    mock_node_svc_factory: MagicMock,
    mock_nodes_table_factory: MagicMock,
    mock_remote_workspace_root: MagicMock,
) -> None:
    node_svc = MagicMock()
    mock_node_svc_factory.return_value = node_svc
    nodes_table = MagicMock()
    mock_nodes_table_factory.return_value = nodes_table
    nodes_table.find_one_for_reuse.return_value = None
    nodes_table.list_node_ids_for_user_org.return_value = []
    node_svc.create_node.return_value = {"node_id": 42}
    node_svc.wait_until_ready.return_value = {
        "ip": "10.0.0.1",
        "password": "secret",
    }

    original_session = MagicMock()
    original_session.is_open = True
    pg = _make_pg(original_session)
    mock_ssh = MagicMock()
    mock_ssh.is_open = True
    mock_ssh.remote_project_root = "/remote/proj"

    with patch.object(arb, "SSHSession", return_value=mock_ssh) as mock_ssh_cls:
        svc = _make_bohrium_service()
        result = svc._setup_bohrium_for_run(
            session_id="sess-dir",
            pg=pg,
            skill_sync_spec=SkillSyncSpec(
                project_skill_roots=["/tmp/proj_skills"],
                remote_project_root="/remote/proj",
            ),
            run_creds={"access_key": "ak", "project_id": 99},
            user_id_for_ak="u1",
            org_id="o1",
            event_callback=MagicMock(),
            run_started_at=0.0,
            remote_workdir="/share/case",
        )

    cfg = mock_ssh_cls.call_args.args[0]
    assert cfg.working_dir == "/share/case"
    assert cfg.workspace_path == "/share/case"
    assert result.execution_workdir == "/share/case"
    assert result.runtime_snapshot is not None
    assert result.runtime_snapshot.execution_workdir == "/share/case"
    assert result.runtime_snapshot.remote_workspace_root == "/share"
    assert result.runtime_snapshot.remote_project_root == "/remote/proj"
    mock_ssh.open.assert_called_once()
```

Add this test for `run_setup()` pass-through:

```python
def test_run_setup_forwards_remote_workdir_to_setup() -> None:
    svc = _make_bohrium_service()
    expected = BohriumSetupResult(True, None, MagicMock(), "/share/case", "ssh", None)
    access_key_result = BohriumAccessKeyFetchResult(
        status="success",
        access_key="ak",
        retryable=False,
        attempts=1,
    )

    with (
        patch.object(
            svc,
            "_load_run_credentials",
            return_value=({"project_id": 99}, "u1", "o1"),
        ),
        patch.object(svc, "_make_event_bridge", return_value=MagicMock()),
        patch.object(
            svc, "_setup_bohrium_for_run", return_value=expected
        ) as mock_setup,
        patch(
            "src.services.agent_run_bohrium.UserService.fetch_bohrium_access_key_result",
            return_value=access_key_result,
        ),
    ):
        result = asyncio.run(
            svc.run_setup(
                session_id="sess-dir",
                playground=MagicMock(),
                skill_sync_spec=None,
                run_started_at=1.0,
                bohrium_required=True,
                remote_workdir="/share/case",
            )
        )

    assert result is expected
    assert mock_setup.call_args.kwargs["remote_workdir"] == "/share/case"
```

- [ ] **Step 5: Run Bohrium tests and confirm signature failures**

Run:

```bash
uv run pytest tests/matmaster/integration/test_bohrium_execution_contract.py::test_setup_uses_remote_workdir_for_ssh_and_execution_context tests/matmaster/integration/test_bohrium_execution_contract.py::test_run_setup_forwards_remote_workdir_to_setup -q
```

Expected:

```text
TypeError
```

- [ ] **Step 6: Add `remote_workdir` through `BohriumSetupService`**

In `src/services/agent_run_bohrium.py`, add `remote_workdir: str | None = None` to all four call sites:

- `BohriumSetupService.run_setup()`
- `BohriumSetupService._run_setup_sync()`
- `BohriumSetupService._setup_bohrium_for_run()` instance wrapper
- module-level `_setup_bohrium_for_run()`

Pass it through each call.

In the `BohriumSetupService._setup_bohrium_for_run()` instance wrapper, forward the value to the module function:

```python
            remote_workdir=remote_workdir,
```

Inside the module-level `_setup_bohrium_for_run()`, replace the workdir derivation with:

```python
            remote_workspace_root = _remote_session_workspace_root()
            ssh_working_dir = (remote_workdir or remote_workspace_root).rstrip("/") or "/"
```

Keep `remote_workspace_root` as `_remote_session_workspace_root()` so runtime metadata still records the project-scoped root separately from the selected execution directory.

If adding signatures pushes `src/services/agent_run_bohrium.py` over 1000 lines, move this exact helper to `src/services/bohrium_run_support.py` and import it:

```python
def derive_ssh_working_dir(remote_workdir: str | None, default_root: str) -> str:
    selected = remote_workdir or default_root
    return selected.rstrip("/") or "/"
```

Then use:

```python
            ssh_working_dir = derive_ssh_working_dir(
                remote_workdir,
                remote_workspace_root,
            )
```

Keep skill sync target unchanged:

```python
            remote_project_root = getattr(ssh_session, "remote_project_root", "")
```

- [ ] **Step 7: Make SSH `open()` verify the working directory exists**

In `matmaster/sessions/ssh.py`, replace the current `mkdir -p` call in `open()`:

```python
        quoted_workdir = shlex.quote(self._workdir)
        init_result = self._ssh_exec(
            f"mkdir -p {quoted_workdir} && test -d {quoted_workdir}"
        )
        if init_result.get("exit_code") != 0:
            detail = init_result.get("stderr") or init_result.get("stdout") or ""
            try:
                if self._sftp_pool is not None:
                    self._sftp_pool.close_all()
            finally:
                self._sftp_pool = None
                if self._client is not None:
                    try:
                        self._client.close()
                    except Exception:
                        pass
                self._client = None
            raise RuntimeError(
                f"Failed to initialize SSH working directory {self._workdir}: {detail}"
            )
```

This guarantees that the first later tool command does not discover a missing or non-directory workdir.

- [ ] **Step 8: Write SSH verification tests**

Append these tests to `tests/matmaster/sessions/test_ssh_session.py`:

```python
def test_open_creates_and_verifies_configured_workdir(mock_paramiko):
    from matmaster.sessions.ssh import SSHSession

    config = SSHSessionConfig(
        host="test-host",
        port=22,
        username="root",
        password="test-pass",
        workspace_path="/share/case",
        working_dir="/share/case",
    )
    session = SSHSession(config)

    with patch.object(
        session,
        "_ssh_exec",
        return_value={"exit_code": 0, "stdout": "", "stderr": ""},
    ) as ssh_exec:
        session.open()

    ssh_exec.assert_called_once_with("mkdir -p /share/case && test -d /share/case")


def test_open_raises_when_workdir_initialization_fails(ssh_config, mock_paramiko):
    from matmaster.sessions.ssh import SSHSession

    session = SSHSession(ssh_config)

    with patch.object(
        session,
        "_ssh_exec",
        return_value={"exit_code": 1, "stdout": "", "stderr": "not a directory"},
    ):
        with pytest.raises(
            RuntimeError,
            match="Failed to initialize SSH working directory",
        ):
            session.open()

    assert session.is_open is False
    assert session._sftp_pool is None
    assert session._client is None
    mock_paramiko["client"].close.assert_called_once()
```

- [ ] **Step 9: Run focused runtime tests and line-count check**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream.py::test_run_agent_passes_remote_workdir_to_bohrium_setup tests/matmaster/integration/test_bohrium_execution_contract.py::test_setup_uses_remote_workdir_for_ssh_and_execution_context tests/matmaster/integration/test_bohrium_execution_contract.py::test_run_setup_forwards_remote_workdir_to_setup tests/matmaster/sessions/test_ssh_session.py::test_open_creates_and_verifies_configured_workdir tests/matmaster/sessions/test_ssh_session.py::test_open_raises_when_workdir_initialization_fails -q
wc -l src/services/agent_run_bohrium.py
```

Expected:

```text
5 passed
```

The `wc -l` output for `src/services/agent_run_bohrium.py` must be `1000` or lower.

- [ ] **Step 10: Commit runtime wiring**

Run:

```bash
git add src/services/agent_run_service.py src/services/agent_run_bohrium.py matmaster/sessions/ssh.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/integration/test_bohrium_execution_contract.py tests/matmaster/sessions/test_ssh_session.py
git commit -m "feat: apply session directory to bohrium execution"
```

## Task 7: Contract Regression And Full Focused Verification

**Files:**

- Read only: all files modified in Tasks 1-6.

- [ ] **Step 1: Run the complete focused pytest set**

Run:

```bash
uv run pytest \
  tests/test_session_directory_service.py \
  tests/test_chat_session_directory_api.py \
  tests/test_chat_stream_session_directory.py \
  tests/test_chat_events_session_directory.py \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/integration/test_bohrium_execution_contract.py \
  tests/matmaster/sessions/test_ssh_session.py \
  tests/test_openapi_chat_docs.py \
  -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Run the touched-file pre-commit set**

Run:

```bash
uv run pre-commit run --files \
  src/services/session_directory_service.py \
  src/apis/chat_api.py \
  src/models/chat.py \
  src/services/stream_service.py \
  src/services/events_service.py \
  src/dao/chat_events_table.py \
  src/worker/agent_worker.py \
  src/services/agent_run_service.py \
  src/services/agent_run_bohrium.py \
  matmaster/sessions/ssh.py \
  tests/test_session_directory_service.py \
  tests/test_chat_session_directory_api.py \
  tests/test_chat_stream_session_directory.py \
  tests/test_chat_events_session_directory.py \
  tests/matmaster/worker/test_redis_bridge.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/integration/test_bohrium_execution_contract.py \
  tests/matmaster/sessions/test_ssh_session.py
```

Expected:

```text
all hooks pass
```

- [ ] **Step 3: Verify source files stay below the repository line limit**

Run:

```bash
wc -l \
  src/services/stream_service.py \
  src/services/agent_run_bohrium.py \
  tests/test_chat_stream_direct.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/integration/test_bohrium_execution_contract.py
```

Expected:

```text
each listed file is 1000 lines or lower
```

- [ ] **Step 4: Inspect data-flow diff manually**

Run:

```bash
git diff --stat HEAD~6..HEAD
git diff HEAD~6..HEAD -- src/services/stream_service.py src/worker/agent_worker.py src/services/agent_run_service.py src/services/agent_run_bohrium.py
```

Confirm these facts in the diff:

- `remote_workdir` appears in stream context, Redis job, worker payload parsing, `run_agent()`, `run_setup()`, and SSH config.
- `WorkspaceHandler(workspace_path=pg_ctx.workdir)` is unchanged and still skips upload when `ssh_attached=True`.
- `SSHSessionConfig.workspace_path` follows the selected SSH execution directory; this is session config, not a `WorkspaceHandler` local snapshot path.
- `_sync_skills_to_ssh_session()` still receives `remote_project_root`, not `remote_workdir`.
- No new `session_directory` SSE event type exists.
- `POST /stream.directory` is not written to `evo_chat_sessions.session_directory`.
- No restart/retry caller consumes `session_directory` in this plan; the metadata is only returned by `get_last_user_query()` for a follow-up PR.

- [ ] **Step 5: Commit verification fixes if hooks changed files**

If pre-commit rewrites files, run:

```bash
git status --short
git add -u
git commit -m "style: apply session directory runtime formatting"
```

Expected:

```text
git status --short shows no unstaged modified source files after the commit
```

## Completion Criteria

Implementation is complete only when all of these are true:

- Invalid request directory returns HTTP 400 with `data.error_code` and does not acquire a run.
- Invalid persistent session directory returns HTTP 400 when selected by a run.
- Public directory error codes include `directory_invalid_type`, `directory_invalid_chars`, `directory_must_be_absolute`, `directory_outside_share`, and `session_directory_invalid`.
- `PUT /session-directory` rejects non-empty paths outside `/share` and does not write them.
- Blank `POST /stream.directory` falls through to the persistent session default.
- Selected request/session directory is written to query history as `session_directory` plus `session_directory_source`.
- `get_session_events()` and `get_last_user_query()` return directory metadata.
- `get_last_user_query()` metadata is restart-ready, but no restart/retry re-enqueue consumer is implemented in this plan.
- Redis job carries `remote_workdir`.
- Worker passes `remote_workdir` to `AgentRunService`.
- `AgentRunService` forces Bohrium setup when `remote_workdir` is present.
- When `remote_workdir` is selected, `BohriumSetupService` constructs `SSHSessionConfig(working_dir=remote_workdir, workspace_path=remote_workdir)`.
- `SSHSession.open()` creates and verifies the configured workdir before marking the session open.
- `BohriumExecutionContext.execution_workdir` equals the selected remote directory.
- `BohriumExecutionContext.remote_workspace_root` remains `/share`.
- Skill sync target remains `remote_project_root`.
- Local `Playground.prepare()` and `WorkspaceHandler` semantics stay unchanged: local `pg_ctx.workdir` remains the snapshot path, and `WorkspaceHandler` does not upload when SSH is attached.
- Focused pytest commands and touched-file pre-commit pass.
