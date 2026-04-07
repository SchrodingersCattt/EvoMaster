# Bohrium Runtime Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Bohrium helper and bridge layering with explicit Bohrium domain modules while keeping the existing credential resolver intact and preserving `from matmaster.tools.builtin.bohrium_tool import BohriumTool`.

**Architecture:** Keep `matmaster.integration.runtime_bridge` focused on credential resolution and env projection only, then move Bohrium path parsing, Open SDK upload, artifact transfer, and action orchestration into Bohrium-scoped modules under `matmaster/tools/builtin/bohrium_tool/`. Land the new modules first, rewire `BohriumTool` to consume them, then remove the legacy bridge and helper files in one cleanup pass.

**Tech Stack:** Python 3.13, pytest, requests, Bohrium Open SDK, Session protocol, `uv`

---

## File Map

### Create

- `matmaster/tools/builtin/bohrium_tool/errors.py`
- `matmaster/tools/builtin/bohrium_tool/models.py`
- `matmaster/tools/builtin/bohrium_tool/paths.py`
- `matmaster/tools/builtin/bohrium_tool/open_sdk.py`
- `matmaster/tools/builtin/bohrium_tool/transfers.py`
- `matmaster/tools/builtin/bohrium_tool/api.py`
- `matmaster/tools/builtin/bohrium_tool/tool.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_models.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_paths.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_open_sdk.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_api.py`
- `tests/matmaster/integration/test_bohrium_job_skill_submit.py`

### Modify

- `matmaster/tools/builtin/bohrium_tool/__init__.py`
- `matmaster/tools/builtin/bohrium_tool/_api.py`
- `matmaster/tools/builtin/bohrium_tool/_helpers.py`
- `matmaster/tools/builtin/bohrium_tool/_results.py`
- `matmaster/integration/runtime_bridge/__init__.py`
- `matmaster/integration/runtime_bridge/models.py`
- `tests/matmaster/integration/test_runtime_bridge.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_download.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_poll.py`
- `tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py`
- `matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py`

### Delete

- `matmaster/integration/runtime_bridge/bridge.py`
- `matmaster/integration/runtime_bridge/path_policy.py`
- `matmaster/tools/builtin/bohrium_tool/_api.py`
- `matmaster/tools/builtin/bohrium_tool/_helpers.py`
- `matmaster/tools/builtin/bohrium_tool/_results.py`

## Task 1: Add Bohrium Domain Errors And Context Model

**Files:**
- Create: `matmaster/tools/builtin/bohrium_tool/errors.py`
- Create: `matmaster/tools/builtin/bohrium_tool/models.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_models.py`

- [ ] **Step 1: Write the failing model tests**

```python
from __future__ import annotations

import pytest

from matmaster.integration.runtime_bridge.models import ResolvedCredential
from matmaster.tools.builtin.bohrium_tool.errors import BohriumCredentialError
from matmaster.tools.builtin.bohrium_tool.models import BohriumContext


def test_context_from_resolved_credential_captures_source_and_sandbox():
    cred = ResolvedCredential(
        service="bohrium",
        source="session",
        values={
            "access_key": "ak-123",
            "project_id": 42,
            "base_url": "https://openapi.test.dp.tech",
            "user_id": 7,
            "user_no": "U001",
        },
    )

    ctx = BohriumContext.from_resolved_credential(cred, sandbox=True)

    assert ctx.access_key == "ak-123"
    assert ctx.project_id == 42
    assert ctx.base_url == "https://openapi.test.dp.tech"
    assert ctx.credential_source == "session"
    assert ctx.sandbox is True
    assert ctx.user_id == 7
    assert ctx.user_no == "U001"


def test_context_from_resolved_credential_rejects_missing_access_key():
    cred = ResolvedCredential(service="bohrium", source="none", values={})

    with pytest.raises(BohriumCredentialError, match="BOHRIUM_ACCESS_KEY"):
        BohriumContext.from_resolved_credential(cred, sandbox=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_models.py -v`
Expected: FAIL with `ModuleNotFoundError` for `matmaster.tools.builtin.bohrium_tool.models`

- [ ] **Step 3: Write the minimal domain model and error hierarchy**

```python
# matmaster/tools/builtin/bohrium_tool/errors.py
class BohriumError(RuntimeError):
    """Base class for Bohrium runtime failures."""


class BohriumCredentialError(BohriumError):
    """Raised when required Bohrium credentials are unavailable."""


class BohriumPathError(BohriumError):
    """Raised when Bohrium path resolution fails."""


class BohriumTransferError(BohriumError):
    """Raised when archive upload, download, or publish fails."""


class BohriumAPIError(BohriumError):
    """Raised when Bohrium OpenAPI returns an error."""


class BohriumJobStateError(BohriumError):
    """Raised when a Bohrium job is not in the expected state."""
```

```python
# matmaster/tools/builtin/bohrium_tool/models.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from matmaster.integration.runtime_bridge.models import ResolvedCredential

from .errors import BohriumCredentialError


@dataclass(frozen=True)
class BohriumContext:
    access_key: str
    project_id: int
    base_url: str
    credential_source: str
    sandbox: bool
    user_id: int | None = None
    user_no: str = ""

    @classmethod
    def from_resolved_credential(
        cls, cred: ResolvedCredential, *, sandbox: bool
    ) -> "BohriumContext":
        values = cred.values
        access_key = str(values.get("access_key") or "").strip()
        if not access_key:
            raise BohriumCredentialError(
                "Bohrium credentials unavailable. Provide via session or BOHRIUM_ACCESS_KEY."
            )

        raw_project_id = values.get("project_id", -1)
        project_id = raw_project_id if isinstance(raw_project_id, int) else -1
        return cls(
            access_key=access_key,
            project_id=project_id,
            base_url=str(values.get("base_url") or "").strip(),
            credential_source=cred.source,
            sandbox=sandbox,
            user_id=values.get("user_id"),
            user_no=str(values.get("user_no") or "").strip(),
        )


@dataclass(frozen=True)
class BohriumInputSource:
    kind: str
    raw_path: str
    resolved_path: str


@dataclass(frozen=True)
class BohriumDownloadTarget:
    kind: str
    raw_path: str
    resolved_path: str
    staging_dir: Path
    publish_mode: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_models.py -v`
Expected: PASS with 2 passed

- [ ] **Step 5: Commit**

```bash
git add \
  matmaster/tools/builtin/bohrium_tool/errors.py \
  matmaster/tools/builtin/bohrium_tool/models.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_models.py
git commit -m "feat: add Bohrium domain models and errors"
```

## Task 2: Add Bohrium Path Resolution And Local Or Remote Target Models

**Files:**
- Create: `matmaster/tools/builtin/bohrium_tool/paths.py`
- Create: `tests/matmaster/tools/builtin/test_bohrium_tool_paths.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py`

- [ ] **Step 1: Write the failing path tests**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.tools.builtin.bohrium_tool.errors import BohriumPathError
from matmaster.tools.builtin.bohrium_tool.paths import (
    resolve_download_target,
    resolve_input_source,
)
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import FakeRemoteSession


def test_resolve_input_source_collapses_relative_local_path(tmp_path: Path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()

    source = resolve_input_source(
        raw_path="inputs",
        workdir=tmp_path,
        session=None,
    )

    assert source.kind == "local_dir"
    assert source.resolved_path == str(input_dir)


def test_resolve_input_source_rejects_missing_remote_session():
    with pytest.raises(BohriumPathError, match="remote session"):
        resolve_input_source(
            raw_path="/share/job-inputs",
            workdir=Path("/tmp"),
            session=None,
        )


def test_resolve_download_target_uses_staged_upload_for_remote_share(tmp_path: Path):
    session = FakeRemoteSession(existing_paths={"/share/results"}, is_open=True)

    target = resolve_download_target(
        raw_path="/share/results",
        workdir=tmp_path,
        session=session,
    )

    assert target.kind == "remote_share_dir"
    assert target.resolved_path == "/share/results"
    assert target.publish_mode == "staged_upload"
    assert target.staging_dir != Path("/share/results")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_paths.py -v`
Expected: FAIL with `ModuleNotFoundError` for `matmaster.tools.builtin.bohrium_tool.paths`

- [ ] **Step 3: Implement explicit Bohrium path resolution**

```python
# matmaster/tools/builtin/bohrium_tool/paths.py
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .errors import BohriumPathError
from .models import BohriumDownloadTarget, BohriumInputSource

_REMOTE_SHARE_PREFIXES = ("/share/", "/personal/")


def _is_remote_share(path: str) -> bool:
    return any(path.startswith(prefix) or path == prefix.rstrip("/") for prefix in _REMOTE_SHARE_PREFIXES)


def _normalize_local_path(raw_path: str, workdir: Path | None) -> str:
    stripped = raw_path.strip()
    if Path(stripped).is_absolute():
        return str(Path(stripped))
    base = workdir or Path(".")
    return str((base / stripped).resolve())


def _require_open_session(session: Any | None, raw_path: str) -> Any:
    if session is None or not getattr(session, "is_open", False):
        raise BohriumPathError(
            f"path '{raw_path}' requires an active remote session but none is available"
        )
    return session


def resolve_input_source(
    *, raw_path: str, workdir: Path | None, session: Any | None
) -> BohriumInputSource:
    stripped = raw_path.strip()
    if _is_remote_share(stripped):
        active_session = _require_open_session(session, stripped)
        if not active_session.path_exists(stripped):
            raise BohriumPathError(f"Remote input_dir not found: {stripped}")
        if active_session.is_file(stripped):
            raise BohriumPathError(f"Remote input_dir is not a directory: {stripped}")
        return BohriumInputSource(
            kind="remote_share_dir",
            raw_path=raw_path,
            resolved_path=stripped,
        )

    local_path = Path(_normalize_local_path(stripped, workdir))
    if not local_path.exists():
        raise BohriumPathError(f"input_dir not found: {raw_path}")
    if not local_path.is_dir():
        raise BohriumPathError(f"input_dir is not a directory: {raw_path}")
    return BohriumInputSource(
        kind="local_dir",
        raw_path=raw_path,
        resolved_path=str(local_path),
    )


def resolve_download_target(
    *, raw_path: str, workdir: Path | None, session: Any | None
) -> BohriumDownloadTarget:
    stripped = raw_path.strip()
    if _is_remote_share(stripped):
        _require_open_session(session, stripped)
        return BohriumDownloadTarget(
            kind="remote_share_dir",
            raw_path=raw_path,
            resolved_path=stripped,
            staging_dir=Path(tempfile.mkdtemp(prefix="bohrium-download-")),
            publish_mode="staged_upload",
        )

    local_path = Path(_normalize_local_path(stripped, workdir))
    return BohriumDownloadTarget(
        kind="local_dir",
        raw_path=raw_path,
        resolved_path=str(local_path),
        staging_dir=local_path,
        publish_mode="direct",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_paths.py -v`
Expected: PASS with 3 passed

- [ ] **Step 5: Commit**

```bash
git add \
  matmaster/tools/builtin/bohrium_tool/paths.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_paths.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py
git commit -m "feat: add Bohrium path resolution module"
```

## Task 3: Extract Bohrium Open SDK Upload Logic

**Files:**
- Create: `matmaster/tools/builtin/bohrium_tool/open_sdk.py`
- Create: `tests/matmaster/tools/builtin/test_bohrium_tool_open_sdk.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py`

- [ ] **Step 1: Write the failing Open SDK tests**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.tools.builtin.bohrium_tool.errors import BohriumTransferError
from matmaster.tools.builtin.bohrium_tool.open_sdk import upload_input_archive
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import _install_fake_tiefblue


def test_upload_input_archive_returns_oss_key_and_download_url(tmp_path, monkeypatch):
    upload_calls: list[tuple[str, str, dict]] = []
    _install_fake_tiefblue(monkeypatch, upload_calls)
    zip_path = tmp_path / "input.zip"
    zip_path.write_bytes(b"zip-bytes")

    uploaded = upload_input_archive(
        create_data={
            "storePath": "sandbox/jobs/run-1/",
            "storeHost": "https://store.example.com",
            "token": "token-123",
        },
        zip_path=zip_path,
    )

    assert uploaded.oss_key == "sandbox/jobs/run-1/input.zip"
    assert uploaded.download_url.startswith("https://store.example.com/api/download/")
    assert upload_calls[0][2]["Authorization"] == "Bearer token-123"


def test_upload_input_archive_surfaces_missing_sdk(tmp_path, monkeypatch):
    monkeypatch.delitem(__import__("sys").modules, "bohrium_open_sdk", raising=False)
    monkeypatch.delitem(__import__("sys").modules, "bohrium_open_sdk.opensdk", raising=False)
    monkeypatch.delitem(
        __import__("sys").modules,
        "bohrium_open_sdk.opensdk._tiefblue_client",
        raising=False,
    )
    zip_path = tmp_path / "input.zip"
    zip_path.write_bytes(b"zip-bytes")

    with pytest.raises(BohriumTransferError, match="bohrium_open_sdk"):
        upload_input_archive(
            create_data={
                "storePath": "sandbox/jobs/run-2/",
                "storeHost": "https://store.example.com",
                "token": "token-456",
            },
            zip_path=zip_path,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_open_sdk.py -v`
Expected: FAIL with `ModuleNotFoundError` for `matmaster.tools.builtin.bohrium_tool.open_sdk`

- [ ] **Step 3: Implement Bohrium Open SDK upload wrapper**

```python
# matmaster/tools/builtin/bohrium_tool/open_sdk.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .errors import BohriumTransferError


@dataclass(frozen=True)
class UploadedArchive:
    oss_key: str
    download_url: str


def _build_download_url(store_host: str, oss_key: str, token: str) -> str:
    encoded_key = quote(oss_key, safe="/")
    return (
        f"{store_host}/api/download/{encoded_key}?token={token}"
        "&Response-Content-Type=application/octet-stream"
    )


def upload_input_archive(*, create_data: dict, zip_path: Path) -> UploadedArchive:
    try:
        from bohrium_open_sdk.opensdk._tiefblue_client import Tiefblue as TiefblueClient
    except ImportError as exc:
        raise BohriumTransferError(
            "bohrium_open_sdk not installed. Run: pip install bohrium_open_sdk"
        ) from exc

    store_path = str(create_data["storePath"]).strip()
    if not store_path.endswith("/"):
        store_path += "/"
    store_host = str(create_data["storeHost"]).rstrip("/")
    token = str(create_data["token"]).strip()
    oss_key = f"{store_path}input.zip"

    client = TiefblueClient(base_url=store_host)
    response = client.upload_from_file_multi_part(
        object_key=oss_key,
        file_path=str(zip_path),
        custom_headers={"Authorization": f"Bearer {token}"},
        progress_bar=False,
    )
    if isinstance(response, dict) and response.get("code") not in (0, None):
        raise BohriumTransferError(f"Upload failed: {response}")

    return UploadedArchive(
        oss_key=oss_key,
        download_url=_build_download_url(store_host, oss_key, token),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_open_sdk.py -v`
Expected: PASS with 2 passed

- [ ] **Step 5: Commit**

```bash
git add \
  matmaster/tools/builtin/bohrium_tool/open_sdk.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_open_sdk.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py
git commit -m "feat: extract Bohrium Open SDK upload wrapper"
```

## Task 4: Add Input And Output Transfer Workflows

**Files:**
- Create: `matmaster/tools/builtin/bohrium_tool/transfers.py`
- Create: `tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py`

- [ ] **Step 1: Write the failing transfer tests**

```python
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from matmaster.tools.builtin.bohrium_tool.models import (
    BohriumDownloadTarget,
    BohriumInputSource,
)
from matmaster.tools.builtin.bohrium_tool.transfers import (
    prepare_input_archive,
    publish_download_target,
)
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import FakeRemoteSession


def test_prepare_input_archive_downloads_remote_share_zip(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("INPUT", "data")

    session = FakeRemoteSession(downloads={"/tmp/remote.zip": buffer.getvalue()})
    source = BohriumInputSource(
        kind="remote_share_dir",
        raw_path="/share/input",
        resolved_path="/share/input",
    )

    with prepare_input_archive(source, session=session) as zip_path:
        assert zip_path.name == "input.zip"
        assert session.exec_calls
        assert session.download_calls == ["/tmp/remote.zip"]


def test_publish_download_target_uploads_remote_share_and_returns_remote_dir(tmp_path):
    session = FakeRemoteSession(is_open=True)
    staging_dir = tmp_path / "download-stage"
    staging_dir.mkdir()
    (staging_dir / "log").write_text("done\n", encoding="utf-8")
    target = BohriumDownloadTarget(
        kind="remote_share_dir",
        raw_path="/share/results",
        resolved_path="/share/results",
        staging_dir=staging_dir,
        publish_mode="staged_upload",
    )

    result_dir = publish_download_target(target, session=session)

    assert result_dir == "/share/results"
    assert session.upload_calls[0][1] == "/share/results"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py -v`
Expected: FAIL with `ModuleNotFoundError` for `matmaster.tools.builtin.bohrium_tool.transfers`

- [ ] **Step 3: Implement transfer utilities for archive preparation and publish**

```python
# matmaster/tools/builtin/bohrium_tool/transfers.py
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import requests

from .errors import BohriumTransferError
from .models import BohriumDownloadTarget, BohriumInputSource

logger = logging.getLogger(__name__)


def _zip_local_dir(input_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in input_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(input_dir))


def _prepare_remote_input_zip(*, source: BohriumInputSource, session, zip_path: Path) -> None:
    remote_zip_path = "/tmp/remote.zip"
    script = (
        "python3 - <<'PY'\n"
        "import pathlib, zipfile\n"
        f"source = pathlib.Path({json.dumps(source.resolved_path)})\n"
        f"archive = pathlib.Path({json.dumps(remote_zip_path)})\n"
        "with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:\n"
        "    for path in source.rglob('*'):\n"
        "        if path.is_file():\n"
        "            zf.write(path, path.relative_to(source))\n"
        "PY"
    )
    result = session.exec_bash(script)
    if result.get("exit_code") != 0:
        raise BohriumTransferError("Failed to package remote input_dir")
    zip_path.write_bytes(session.download(remote_zip_path))


@contextmanager
def prepare_input_archive(source: BohriumInputSource, *, session):
    with tempfile.TemporaryDirectory(prefix="bohrium_submit_") as tmp_dir:
        zip_path = Path(tmp_dir) / "input.zip"
        if source.kind == "remote_share_dir":
            _prepare_remote_input_zip(source=source, session=session, zip_path=zip_path)
        else:
            _zip_local_dir(Path(source.resolved_path), zip_path)
        yield zip_path


def publish_download_target(target: BohriumDownloadTarget, *, session) -> str:
    if target.publish_mode == "direct":
        target.staging_dir.mkdir(parents=True, exist_ok=True)
        return target.resolved_path
    try:
        session.upload_directory(str(target.staging_dir), target.resolved_path)
        shutil.rmtree(target.staging_dir, ignore_errors=True)
        return target.resolved_path
    except Exception:
        logger.warning(
            "Failed to upload results to remote share %s",
            target.resolved_path,
            exc_info=True,
        )
        return str(target.staging_dir)


def _download_to_file(url: str, dest: Path, *, timeout: int = 300) -> None:
    response = requests.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in response.iter_content(chunk_size=65536):
            fh.write(chunk)


def _extract_zip(zip_path: Path, extract_dir: Path) -> list[str]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
        return zf.namelist()


def _read_log(result_dir: Path, *, max_chars: int = 4000) -> str:
    for name in ("log", "STDOUTERR"):
        file_path = result_dir / name
        if file_path.exists():
            return file_path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
    return "(no log file found in result directory)"


def download_job_artifacts(
    *, job_id: int | str, detail_data: dict, target: BohriumDownloadTarget, ctx
) -> tuple[list[str], str]:
    result_dir = target.staging_dir
    result_dir.mkdir(parents=True, exist_ok=True)
    result_url = str(detail_data.get("resultUrl") or detail_data.get("result") or "")
    if not result_url:
        return [], "(no resultUrl in job detail)"

    zip_path = result_dir / "out.zip"
    _download_to_file(result_url, zip_path)
    files = _extract_zip(zip_path, result_dir)
    return files, _read_log(result_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py -v`
Expected: PASS with 2 passed

- [ ] **Step 5: Commit**

```bash
git add \
  matmaster/tools/builtin/bohrium_tool/transfers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py
git commit -m "feat: add Bohrium transfer workflows"
```

## Task 5: Consolidate Bohrium OpenAPI Logic In `api.py`

**Files:**
- Create: `matmaster/tools/builtin/bohrium_tool/api.py`
- Create: `tests/matmaster/tools/builtin/test_bohrium_tool_api.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/models.py`

- [ ] **Step 1: Write the failing API tests**

```python
from __future__ import annotations

from matmaster.tools.builtin.bohrium_tool.api import (
    add_job,
    confirm_terminal_status,
    create_job,
    use_sandbox,
)
from matmaster.tools.builtin.bohrium_tool.models import BohriumContext
from matmaster.tools.builtin.bohrium_tool.open_sdk import UploadedArchive


def test_create_job_uses_sandbox_endpoint(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_post(base_url, path, access_key, payload, timeout=30):
        del base_url, access_key, timeout
        calls.append((path, payload))
        return {"code": 0, "data": {"jobId": "job-1"}}

    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.api._post",
        fake_post,
    )
    ctx = BohriumContext(
        access_key="ak",
        project_id=42,
        base_url="https://openapi.test.dp.tech",
        credential_source="env",
        sandbox=True,
    )

    create_job(ctx, job_name="demo")

    assert calls == [("/openapi/v1/sandbox/job/create", {"projectId": 42, "name": "demo"})]


def test_add_job_uses_uploaded_download_url_for_sandbox(monkeypatch):
    calls: list[dict] = []

    def fake_post(base_url, path, access_key, payload, timeout=30):
        del base_url, access_key, timeout
        assert path == "/openapi/v1/sandbox/job/add"
        calls.append(payload)
        return {"code": 0, "data": {"jobId": "job-2", "bohrJobId": "bohr-2"}}

    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.api._post",
        fake_post,
    )
    ctx = BohriumContext(
        access_key="ak",
        project_id=42,
        base_url="https://openapi.test.dp.tech",
        credential_source="env",
        sandbox=True,
    )

    add_job(
        ctx,
        create_data={"jobId": "job-create"},
        upload=UploadedArchive(
            oss_key="sandbox/jobs/run-1/input.zip",
            download_url="https://store.example.com/api/download/input.zip?token=abc",
        ),
        image="demo:latest",
        cmd="python run.py > log 2>&1",
        machine="c32_m128_cpu",
        job_name="demo",
        disk_size=50,
    )

    assert calls[0]["ossPath"] == ["https://store.example.com/api/download/input.zip?token=abc"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_api.py -v`
Expected: FAIL with `ModuleNotFoundError` for `matmaster.tools.builtin.bohrium_tool.api`

- [ ] **Step 3: Move API behavior behind a Bohrium-scoped client module**

```python
# matmaster/tools/builtin/bohrium_tool/api.py
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from .errors import BohriumAPIError
from .models import BohriumContext
from .open_sdk import UploadedArchive

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    -10: "Prepared",
    -2: "Deleted",
    -1: "Failed",
    0: "Pending",
    1: "Running",
    2: "Finished",
    3: "Scheduling",
    6: "Unknown",
}
_FAILURE_CODES = {-2, -1}


def _get(
    base_url: str,
    path: str,
    access_key: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    response = requests.get(
        f"{base_url}{path}",
        headers={"accessKey": access_key, "Accept": "application/json"},
        params=params or {},
        timeout=timeout,
    )
    if not response.ok:
        logger.warning("Bohrium GET failed path=%s status=%s", path, response.status_code)
    response.raise_for_status()
    return response.json()


def _post(
    base_url: str,
    path: str,
    access_key: str,
    payload: dict[str, Any],
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    response = requests.post(
        f"{base_url}{path}",
        headers={"accessKey": access_key, "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if not response.ok:
        logger.warning("Bohrium POST failed path=%s status=%s", path, response.status_code)
    response.raise_for_status()
    return response.json()


def use_sandbox() -> bool:
    return os.environ.get("BOHRIUM_USE_SANDBOX", "1").strip() == "1"


def create_job(ctx: BohriumContext, *, job_name: str) -> dict[str, Any]:
    path = "/openapi/v1/sandbox/job/create" if ctx.sandbox else "/openapi/v1/job/create"
    payload = {"projectId": ctx.project_id, "name": job_name} if ctx.sandbox else {
        "projectId": ctx.project_id,
        "jobName": job_name,
    }
    response = _post(ctx.base_url, path, ctx.access_key, payload)
    if response.get("code") != 0:
        raise BohriumAPIError(f"job/create failed: {response}")
    return response["data"]


def add_job(
    ctx: BohriumContext,
    *,
    create_data: dict[str, Any],
    upload: UploadedArchive,
    image: str,
    cmd: str,
    machine: str,
    job_name: str,
    disk_size: int,
) -> dict[str, Any]:
    if ctx.sandbox:
        payload = {
            "imageName": image,
            "scassType": machine,
            "jobName": job_name,
            "cmd": cmd,
            "jobId": str(create_data["jobId"]).strip(),
            "ossPath": [upload.download_url],
        }
        path = "/openapi/v1/sandbox/job/add"
    else:
        payload = {
            "projectId": ctx.project_id,
            "jobName": job_name,
            "jobType": "indicate",
            "scassType": machine,
            "cmd": cmd,
            "imageName": image,
            "ossPath": [upload.oss_key],
            "inputFileMethod": 1,
            "inputFileType": 3,
            "diskSize": disk_size,
            "logFiles": ["log"],
        }
        path = "/openapi/v2/job/add"
    response = _post(ctx.base_url, path, ctx.access_key, payload)
    if response.get("code") != 0:
        raise BohriumAPIError(f"job/add failed: {response}")
    return response["data"]


def get_job_detail(ctx: BohriumContext, *, job_id: int | str) -> dict[str, Any]:
    path = (
        f"/openapi/v1/sandbox/job/{job_id}"
        if ctx.sandbox
        else f"/openapi/v1/job/{job_id}"
    )
    return (_get(ctx.base_url, path, ctx.access_key).get("data") or {})


def confirm_terminal_status(
    ctx: BohriumContext,
    *,
    job_id: int | str,
    detail_data: dict[str, Any],
    attempts: int = 3,
    sleep_seconds: int = 3,
) -> tuple[int, str, dict[str, Any]]:
    code = detail_data.get("status", 0)
    status_name = _STATUS_MAP.get(code, f"Unknown({code})")
    if code not in _FAILURE_CODES:
        return code, status_name, detail_data

    latest = detail_data
    for attempt in range(1, attempts):
        time.sleep(sleep_seconds)
        latest = get_job_detail(ctx, job_id=job_id)
        code = latest.get("status", 0)
        status_name = _STATUS_MAP.get(code, f"Unknown({code})")
        if code not in _FAILURE_CODES:
            break
    return code, status_name, latest


def list_public_images(ctx: BohriumContext, *, keyword: str, max_results: int) -> list[dict[str, Any]]:
    response = _get(
        ctx.base_url,
        "/openapi/v2/image/public",
        ctx.access_key,
        params={"page": 1, "pageSize": 1000},
    )
    items = (response.get("data") or {}).get("items") or []
    lowered = keyword.lower()
    filtered = [
        item
        for item in items
        if not lowered
        or lowered in str(item.get("name") or item.get("imageName") or "").lower()
        or lowered in str(item.get("description") or "").lower()
    ]
    return filtered[:max_results]


def list_machine_types(
    ctx: BohriumContext, *, machine_type: str, keyword: str, max_results: int
) -> list[dict[str, Any]]:
    response = _get(
        ctx.base_url,
        "/openapi/v1/calc/list",
        ctx.access_key,
        params={
            "page": 1,
            "pageSize": 512,
            "scene": "job",
            "isVirtualNode": "false",
            "chooseType": machine_type,
            "productLine": "bohrium",
        },
    )
    items = (response.get("data") or {}).get("items") or []
    lowered = keyword.lower()
    filtered = [
        item
        for item in items
        if not lowered
        or lowered in str(item.get("skuEnName") or item.get("skuName") or "").lower()
    ]
    return filtered[:max_results]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_api.py -v`
Expected: PASS with 2 passed

- [ ] **Step 5: Commit**

```bash
git add \
  matmaster/tools/builtin/bohrium_tool/api.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_api.py \
  matmaster/tools/builtin/bohrium_tool/models.py
git commit -m "feat: consolidate Bohrium OpenAPI logic"
```

## Task 6: Rewire `BohriumTool` To Use The New Domain Modules

**Files:**
- Create: `matmaster/tools/builtin/bohrium_tool/tool.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/__init__.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_download.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_poll.py`

- [ ] **Step 1: Rewrite one representative tool test to patch the new seams**

```python
def test_download_finished_job_returns_files(tmp_path, monkeypatch):
    tool = BohriumTool(workdir=tmp_path)

    class FakeTarget:
        resolved_path = str(tmp_path / "results")
        staging_dir = tmp_path / "results"
        publish_mode = "direct"

    monkeypatch.setattr("matmaster.tools.builtin.bohrium_tool.tool.resolve_download_target", lambda **_: FakeTarget())
    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.tool.get_job_detail",
        lambda ctx, job_id: {"status": 2, "resultUrl": "https://store.example/out.zip"},
    )
    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.tool.confirm_terminal_status",
        lambda ctx, job_id, detail_data: (2, "Finished", detail_data),
    )
    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.tool.download_job_artifacts",
        lambda **_: (["log"], "done\n"),
    )
    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.tool.publish_download_target",
        lambda target, session: str(target.staging_dir),
    )

    result = asyncio.run(
        tool.execute(
            {"action": "download", "job_id": "job-finished", "result_dir": str(tmp_path / "results")}
        )
    )

    payload = json.loads(result.content)
    assert payload["status"] == "Finished"
    assert payload["files"] == ["log"]
```

- [ ] **Step 2: Run the existing Bohrium tool tests to verify the old patch points now fail**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool_poll.py tests/matmaster/tools/builtin/test_bohrium_tool_download.py -v`
Expected: FAIL in tests that patch `_get`, `_resolve_download_target_dir`, `download_bohrium_results`, or package-level `_use_sandbox`

- [ ] **Step 3: Implement the new orchestrator and package export**

```python
# matmaster/tools/builtin/bohrium_tool/tool.py
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from matmaster.integration.runtime_bridge.adapters.bohrium import (
    resolve_bohrium_credentials,
)
from matmaster.integration.bohrium_api import get_bohrium_base_url, get_bohrium_service_env
from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult

from .api import (
    add_job,
    confirm_terminal_status,
    create_job,
    get_job_detail,
    list_machine_types,
    list_public_images,
    use_sandbox,
)
from .errors import BohriumError
from .models import BohriumContext
from .open_sdk import upload_input_archive
from .paths import resolve_download_target, resolve_input_source
from .transfers import (
    download_job_artifacts,
    prepare_input_archive,
    publish_download_target,
)

logger = logging.getLogger(__name__)


class BohriumTool(BuiltinTool):
    def _build_context(self, *, require_project: bool = False) -> BohriumContext:
        cred = resolve_bohrium_credentials(session=self._session)
        ctx = BohriumContext.from_resolved_credential(cred, sandbox=use_sandbox())
        if require_project and ctx.project_id <= 0:
            raise BohriumError(
                "Bohrium project ID unavailable. Provide via session or BOHRIUM_PROJECT_ID."
            )
        if not ctx.base_url:
            ctx = BohriumContext(
                access_key=ctx.access_key,
                project_id=ctx.project_id,
                base_url=get_bohrium_base_url(),
                credential_source=ctx.credential_source,
                sandbox=ctx.sandbox,
                user_id=ctx.user_id,
                user_no=ctx.user_no,
            )
        return ctx

    def _submit(self, args: dict[str, Any]) -> ToolResult:
        try:
            ctx = self._build_context(require_project=True)
            source = resolve_input_source(
                raw_path=str(args["input_dir"]),
                workdir=self._workdir,
                session=self._session,
            )
            cmd = str(args["cmd"]).rstrip()
            if not cmd.endswith("> log 2>&1"):
                cmd = cmd + " > log 2>&1"
            with prepare_input_archive(source, session=self._session) as zip_path:
                create_data = create_job(ctx, job_name=str(args.get("job_name", "matmaster-job")))
                upload = upload_input_archive(create_data=create_data, zip_path=zip_path)
                add_data = add_job(
                    ctx,
                    create_data=create_data,
                    upload=upload,
                    image=str(args["image"]),
                    cmd=cmd,
                    machine=str(args.get("machine", "c32_m128_cpu")),
                    job_name=str(args.get("job_name", "matmaster-job")),
                    disk_size=int(args.get("disk_size", 50)),
                )
            return ToolResult(status="success", content=json.dumps({"success": True, "job_id": add_data["jobId"], "status": "Submitted"}, ensure_ascii=False))
        except BohriumError as exc:
            return ToolResult(status="error", content=str(exc))

    def _download(self, args: dict[str, Any]) -> ToolResult:
        try:
            ctx = self._build_context()
            job_id = str(args["job_id"]).strip() if ctx.sandbox else int(args["job_id"])
            target = resolve_download_target(
                raw_path=str(args["result_dir"]),
                workdir=self._workdir,
                session=self._session,
            )
            detail_data = get_job_detail(ctx, job_id=job_id)
            code, status_name, detail_data = confirm_terminal_status(
                ctx,
                job_id=job_id,
                detail_data=detail_data,
            )
            files, log_tail = download_job_artifacts(
                job_id=job_id,
                detail_data=detail_data,
                target=target,
                ctx=ctx,
            )
            report_dir = publish_download_target(target, session=self._session)
            return ToolResult(status="success", content=json.dumps({"success": code == 2, "job_id": job_id, "status": status_name, "result_dir": report_dir, "files": files, "log_tail": log_tail}, ensure_ascii=False))
        except BohriumError as exc:
            return ToolResult(status="error", content=str(exc))
```

```python
# matmaster/tools/builtin/bohrium_tool/__init__.py
from .tool import BohriumTool

__all__ = ["BohriumTool"]
```

- [ ] **Step 4: Run the Bohrium tool tests against the new module seams**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool_poll.py tests/matmaster/tools/builtin/test_bohrium_tool_download.py -v`
Expected: PASS after replacing old package-level patches with `tool.py`, `api.py`, `paths.py`, and `transfers.py` patch points

- [ ] **Step 5: Commit**

```bash
git add \
  matmaster/tools/builtin/bohrium_tool/tool.py \
  matmaster/tools/builtin/bohrium_tool/__init__.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_poll.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_download.py
git commit -m "refactor: rewire Bohrium tool around domain modules"
```

## Task 7: Remove Legacy Bridge And Helper Files, Then Reuse Shared Submit Workflow In Skill Script

**Files:**
- Modify: `matmaster/integration/runtime_bridge/__init__.py`
- Modify: `tests/matmaster/integration/test_runtime_bridge.py`
- Modify: `matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py`
- Create: `tests/matmaster/integration/test_bohrium_job_skill_submit.py`
- Delete: `matmaster/integration/runtime_bridge/bridge.py`
- Delete: `matmaster/integration/runtime_bridge/path_policy.py`
- Delete: `matmaster/tools/builtin/bohrium_tool/_api.py`
- Delete: `matmaster/tools/builtin/bohrium_tool/_helpers.py`
- Delete: `matmaster/tools/builtin/bohrium_tool/_results.py`

- [ ] **Step 1: Write the failing cleanup and script reuse tests**

```python
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from matmaster.integration import runtime_bridge

SCRIPT_PATH = Path("matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py")


def test_runtime_bridge_no_longer_exports_path_resolution():
    assert not hasattr(runtime_bridge, "resolve_output_path")
    assert not hasattr(runtime_bridge, "OutputPathDecision")


def test_submit_job_script_delegates_to_shared_upload_workflow(monkeypatch):
    spec = importlib.util.spec_from_file_location("bohrium_submit_job_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    called = {}

    def fake_submit_job(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        return ("job-123", "bohr-456")

    monkeypatch.setattr(module, "submit_job_via_runtime", fake_submit_job)

    job_id, bohr_job_id = module.submit_job(
        input_dir=Path("/tmp/inputs"),
        image="demo:latest",
        cmd="python run.py > log 2>&1",
        machine="c32_m128_cpu",
        job_name="demo",
        disk_size=50,
    )

    assert (job_id, bohr_job_id) == ("job-123", "bohr-456")
    assert called["kwargs"]["image"] == "demo:latest"
```

- [ ] **Step 2: Run the cleanup tests to verify they fail**

Run: `uv run pytest tests/matmaster/integration/test_runtime_bridge.py tests/matmaster/integration/test_bohrium_job_skill_submit.py -v`
Expected: FAIL because `runtime_bridge` still exports path helpers and `submit_job.py` still owns its own create/upload/add workflow

- [ ] **Step 3: Remove the legacy bridge and thin the script to the shared workflow**

```python
# matmaster/integration/runtime_bridge/__init__.py
from matmaster.integration.runtime_bridge.adapters.bohrium import (
    build_bohrium_env,
    inject_bohrium_mcp_args,
    resolve_bohrium_credentials,
)
from matmaster.integration.runtime_bridge.models import ResolvedCredential
from matmaster.integration.runtime_bridge.resolver import resolve_credentials

__all__ = [
    "ResolvedCredential",
    "build_bohrium_env",
    "inject_bohrium_mcp_args",
    "resolve_bohrium_credentials",
    "resolve_credentials",
]
```

```python
# matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py
from __future__ import annotations

from pathlib import Path

from matmaster.integration.runtime_bridge.adapters.bohrium import (
    resolve_bohrium_credentials,
)
from matmaster.tools.builtin.bohrium_tool.api import add_job, create_job, use_sandbox
from matmaster.tools.builtin.bohrium_tool.models import BohriumContext
from matmaster.tools.builtin.bohrium_tool.open_sdk import upload_input_archive
from matmaster.tools.builtin.bohrium_tool.paths import resolve_input_source
from matmaster.tools.builtin.bohrium_tool.transfers import prepare_input_archive


def submit_job_via_runtime(
    *,
    input_dir: Path,
    image: str,
    cmd: str,
    machine: str,
    job_name: str,
    disk_size: int,
):
    cred = resolve_bohrium_credentials(session=None, explicit=None)
    ctx = BohriumContext.from_resolved_credential(cred, sandbox=use_sandbox())
    source = resolve_input_source(raw_path=str(input_dir), workdir=input_dir.parent, session=None)
    with prepare_input_archive(source, session=None) as zip_path:
        create_data = create_job(ctx, job_name=job_name)
        upload = upload_input_archive(create_data=create_data, zip_path=zip_path)
        add_data = add_job(
            ctx,
            create_data=create_data,
            upload=upload,
            image=image,
            cmd=cmd,
            machine=machine,
            job_name=job_name,
            disk_size=disk_size,
        )
    return add_data["jobId"], add_data.get("bohrJobId") or add_data["jobId"]
```

- [ ] **Step 4: Run the focused cleanup suite and one full Bohrium regression sweep**

Run: `uv run pytest tests/matmaster/integration/test_runtime_bridge.py tests/matmaster/integration/test_bohrium_job_skill_submit.py tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool_poll.py tests/matmaster/tools/builtin/test_bohrium_tool_download.py tests/matmaster/tools/builtin/test_bohrium_tool_api.py tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py tests/matmaster/tools/builtin/test_bohrium_tool_paths.py tests/matmaster/tools/builtin/test_bohrium_tool_open_sdk.py tests/matmaster/tools/builtin/test_bohrium_tool_models.py -v`
Expected: PASS with all Bohrium-related tests green and no import of `resolve_output_path`

- [ ] **Step 5: Commit**

```bash
git add \
  matmaster/integration/runtime_bridge/__init__.py \
  matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py \
  tests/matmaster/integration/test_runtime_bridge.py \
  tests/matmaster/integration/test_bohrium_job_skill_submit.py
git rm \
  matmaster/integration/runtime_bridge/bridge.py \
  matmaster/integration/runtime_bridge/path_policy.py \
  matmaster/tools/builtin/bohrium_tool/_api.py \
  matmaster/tools/builtin/bohrium_tool/_helpers.py \
  matmaster/tools/builtin/bohrium_tool/_results.py
git commit -m "refactor: remove legacy Bohrium bridge and helpers"
```
