# Bohrium Runtime Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Bohrium integration around a single runtime handle registered during `agent_run_bohrium`, migrate every downstream consumer to that handle, and delete `runtime_bridge`, `bohrium_env`, and `adaptors/calculation`.

**Architecture:** Introduce a focused `matmaster/bohrium/` package as the only production implementation of the calculation runtime, with a thin `matmaster/calculation_runtimes/` contract layer for future non-Bohrium backends. Keep calculation MCP request understanding inside `matmaster/mcp/calculation/`, but make it consume a runtime handle for env, submission building, and path materialization. Preserve two compatibility boundaries while refactoring: `src/utils/constant.py` remains the home for service/startup-only Bohrium defaults such as image IDs and core-service URLs, and `bohrium_tool` keeps its existing sandbox-vs-HPC OpenAPI protocol split instead of abstracting it into the runtime contract. Migrate in startup-first order: core contracts, runtime registration, shell/script consumers, Bohrium builtin tool, MCP preflight and `lazy_mcp`, then remove legacy modules and add structure guards.

**Tech Stack:** Python 3.13 via `uv run`, existing `Session` / `PlaygroundContext` abstractions, pytest, unittest.mock, pathlib, dataclasses / Protocols, Bohrium SSH + OpenAPI integration

**Spec:** `docs/superpowers/specs/2026-04-07-bohrium-runtime-reorg-design.md`

---

## File Map

### New files

- `matmaster/bohrium/__init__.py`
  Purpose: export the stable Bohrium runtime entrypoints used by startup code and consumers.
- `matmaster/bohrium/types.py`
  Purpose: define `BohriumCredentials`, `BohriumExecutionContext`, `BohriumRuntimeSnapshot`, and `BohriumSubmissionSpec`.
- `matmaster/bohrium/errors.py`
  Purpose: centralize typed Bohrium runtime exceptions.
- `matmaster/bohrium/endpoints.py`
  Purpose: own Bohrium host and service-env resolution previously in `integration/bohrium_api.py`.
- `matmaster/bohrium/credentials.py`
  Purpose: host credential normalization and environment fallback helpers that build `BohriumCredentials`.
- `matmaster/bohrium/env.py`
  Purpose: project runtime credentials into `BOHRIUM_*` environment variables.
- `matmaster/bohrium/executor.py`
  Purpose: inject normalized credentials into dispatcher/local executor templates.
- `matmaster/bohrium/storage.py`
  Purpose: build HTTPS storage payloads for calculation submissions.
- `matmaster/bohrium/paths.py`
  Purpose: own path classification, remote-session download, model-alias rewrite helpers, and OSS-backed input materialization.
- `matmaster/bohrium/runtime.py`
  Purpose: define `BohriumRuntimeHandle`, session attach/get/detach helpers, snapshot export, and submission construction.
- `matmaster/bohrium/oss.py`
  Purpose: move Bohrium-specific OSS upload/download helpers out of the old adaptor package.
- `matmaster/bohrium/jobs.py`
  Purpose: move Bohrium OpenAPI job helpers out of the old adaptor package.
- `matmaster/calculation_runtimes/__init__.py`
  Purpose: export the thin runtime contract layer and registry.
- `matmaster/calculation_runtimes/base.py`
  Purpose: define the minimal `CalculationRuntime` Protocol.
- `matmaster/calculation_runtimes/types.py`
  Purpose: define small request/response carrier types and execution-context Protocols shared by preflight and runtime implementations.
- `matmaster/calculation_runtimes/registry.py`
  Purpose: register and resolve calculation runtime factories by name.
- `matmaster/mcp/calculation/__init__.py`
  Purpose: export calculation client-side preflight entrypoints.
- `matmaster/mcp/calculation/errors.py`
  Purpose: host `CalculationPreflightError`.
- `matmaster/mcp/calculation/selectors.py`
  Purpose: move schema selector discovery and path rewriting out of `adaptors/calculation`.
- `matmaster/mcp/calculation/config_env.py`
  Purpose: move calculation MCP config file environment switching into the MCP client namespace.
- `matmaster/mcp/calculation/preflight.py`
  Purpose: replace the old path adaptor with an explicit calculation preflight object.
- `tests/matmaster/bohrium/test_types.py`
  Purpose: cover credential normalization, endpoint trimming, and snapshot contracts.
- `tests/matmaster/bohrium/test_runtime.py`
  Purpose: cover runtime handle attach/get/detach, env projection, and submission builders.
- `tests/matmaster/bohrium/test_jobs.py`
  Purpose: preserve Bohrium job-service parsing and access-key resolution behavior after moving the module.
- `tests/matmaster/calculation_runtimes/test_registry.py`
  Purpose: verify runtime Protocol compatibility and registry resolution.
- `tests/matmaster/mcp/calculation/test_selectors.py`
  Purpose: preserve and extend nested selector coverage after moving selector helpers.
- `tests/matmaster/mcp/calculation/test_config_env.py`
  Purpose: preserve `resolve_mcp_config_path` behavior after namespace move.
- `tests/matmaster/mcp/calculation/test_preflight.py`
  Purpose: verify client-side preflight builds submission requests, delegates leaf materialization, and raises `CalculationPreflightError`.
- `tests/matmaster/architecture/test_bohrium_runtime_boundaries.py`
  Purpose: guard against reintroducing `runtime_bridge`, `bohrium_env`, `matmaster.adaptors.calculation`, or direct `_bohrium_credentials` consumers.

### Modified files

- `src/services/agent_run_bohrium.py`
  Purpose: become the only runtime composition root and temporarily dual-write `_bohrium_runtime` plus `_bohrium_credentials`.
- `src/services/agent_run_service.py`
  Purpose: stop hand-assembling Bohrium meta dicts and write `BohriumRuntimeSnapshot`-derived data into `PlaygroundContext`.
- `matmaster/types/context.py`
  Purpose: keep `with_bohrium()` aligned with `BohriumRuntimeSnapshot`.
- `matmaster/mcp/manager.py`
  Purpose: rename `path_adaptor` plumbing to calculation preflight metadata and factories so LazyMCP no longer exposes legacy adaptor concepts.
- `matmaster/tools/script_env.py`
  Purpose: switch shell env injection from `build_service_env()` to runtime-backed env projection via `get_runtime(session)`.
- `matmaster/tools/builtin/bash_tool.py`
  Purpose: use runtime env projection instead of the old runtime bridge.
- `matmaster/tools/builtin/glob_tool.py`
  Purpose: use runtime env projection instead of the old runtime bridge.
- `matmaster/tools/builtin/grep_tool.py`
  Purpose: use runtime env projection instead of the old runtime bridge.
- `matmaster/tools/builtin/bohrium_tool/tool.py`
  Purpose: resolve Bohrium credentials through the runtime handle instead of `resolve_bohrium_credentials()`.
- `matmaster/tools/builtin/bohrium_tool/models.py`
  Purpose: replace `ResolvedCredential` dependence with `BohriumCredentials`.
- `matmaster/tools/lazy_mcp.py`
  Purpose: replace `path_adaptor` imports and object plumbing with calculation preflight plumbing.
- `matmaster/core/exp.py`
  Purpose: import calculation config resolution from the new MCP client namespace.
- `matmaster/tools/cache_mcp_schemas.py`
  Purpose: import calculation config resolution from the new MCP client namespace.
- `evaluation/eval_tooling_snapshot.py`
  Purpose: import calculation config resolution from the new MCP client namespace.
- `tests/matmaster/test_bohrium_setup_injection.py`
  Purpose: extend the existing `BohriumSetupService` orchestration tests with startup registration behavior and the migration-period dual-write.
- `tests/matmaster/tools/test_script_env.py`
  Purpose: align shell env tests with runtime handle usage.
- `tests/matmaster/tools/builtin/test_bash_tool.py`
  Purpose: assert `bash_tool` uses runtime env projection.
- `tests/matmaster/tools/builtin/test_glob_tool.py`
  Purpose: assert `glob_tool` uses runtime env projection.
- `tests/matmaster/tools/builtin/test_grep_tool.py`
  Purpose: assert `grep_tool` uses runtime env projection.
- `tests/matmaster/tools/builtin/test_bohrium_tool_models.py`
  Purpose: assert Bohrium tool models use `BohriumCredentials` or runtime-derived data.
- `tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py`
  Purpose: replace `resolve_bohrium_credentials` patching with runtime handle patching.
- `tests/matmaster/tools/builtin/test_bohrium_tool.py`
  Purpose: keep end-to-end builtin tool orchestration aligned with runtime handle usage.
- `tests/matmaster/tools/builtin/test_bohrium_tool_api.py`
  Purpose: preserve sandbox-vs-HPC endpoint and payload contracts while credential sourcing changes underneath.
- `tests/matmaster/tools/builtin/test_bohrium_tool_poll.py`
  Purpose: preserve sandbox-vs-HPC job ID typing and poll endpoint behavior.
- `tests/matmaster/tools/builtin/test_bohrium_tool_download.py`
  Purpose: preserve sandbox-vs-HPC download endpoint and artifact resolution behavior.
- `tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py`
  Purpose: preserve sandbox resultUrl/token download behavior while runtime integration changes.
- `tests/matmaster/tools/test_lazy_mcp.py`
  Purpose: verify new calculation preflight factory and call path.
- `tests/matmaster/tools/test_lazy_mcp_actor_routing.py`
  Purpose: verify connector preflight lookup still happens per server.
- `tests/matmaster/mcp/test_manager.py`
  Purpose: rename manager-side adaptor state to calculation preflight state and preserve tool metadata filtering behavior.
- `tests/matmaster/integration/test_runtime_credential_bridge_e2e.py`
  Purpose: rewrite bridge-oriented end-to-end coverage so Bohrium tool, preflight, and jobs all resolve credentials through the runtime handle.
- `tests/matmaster/integration/test_bohrium_job_skill_submit.py`
  Purpose: preserve skill-facing submit output, sandbox flag propagation, and machine/HPC payload compatibility.

### Existing files to reference while implementing

- `src/utils/constant.py`
- `src/services/user_service.py`
- `matmaster/core/playground.py`
- `matmaster/types/session.py`
- `matmaster/tools/tool_result.py`
- `matmaster/mcp/connection.py`
- `matmaster/mcp/manager.py`
- `matmaster/tools/builtin/bohrium_tool/api.py`
- `matmaster/tools/builtin/bohrium_tool/open_sdk.py`
- `matmaster/tools/builtin/bohrium_tool/paths.py`
- `matmaster/tools/builtin/bohrium_tool/transfers.py`

---

## Compatibility Guardrails

- `src/utils/constant.py` is not a blind migration target. Keep `BOHRIUM_DEFAULT_IMAGE_ID`, `BOHRIUM_DEFAULT_IMAGE_NAME`, and `BOHRIUM_CORE_BASE_URL` in `src/` because they are consumed by startup/service orchestration.
- Runtime-side Bohrium endpoint resolution must preserve today's `matmaster/integration/bohrium_api.py` behavior: prod defaults to `https://openapi.dp.tech`, non-prod defaults to `https://openapi.{env}.dp.tech`, and `BOHRIUM_BASE_URL` remains the explicit override.
- `src/services/bohrium_node_service.py` currently follows `src/utils/constant.py::BOHRIUM_OPENAPI_HOST`, whose prod fallback differs from `bohrium_api.py`. This refactor should not silently collapse those two rules into one helper. Keep node-lifecycle host selection unchanged unless a dedicated follow-up validates both domains are intentionally equivalent in production.
- `BOHRIUM_USE_SANDBOX` remains a Bohrium job-protocol switch, not a runtime-contract concern. The refactor must preserve the current API split between sandbox paths such as `/openapi/v1/sandbox/job/create` and standard HPC paths such as `/openapi/v1/job/create` plus `/openapi/v2/job/add`, along with their different payloads, job ID typing, and download flows.
- `machine`, `scassType`, `diskSize`, `ossPath`, `download_url`, and sandbox `resultUrl` handling are compatibility-sensitive. Refactoring credential sourcing must not rewrite these protocol-level behaviors unless a dedicated follow-up spec covers it.

---

### Task 1: Introduce runtime contracts, Bohrium value types, and endpoint helpers

**Files:**
- Create: `matmaster/calculation_runtimes/__init__.py`
- Create: `matmaster/calculation_runtimes/base.py`
- Create: `matmaster/calculation_runtimes/types.py`
- Create: `matmaster/calculation_runtimes/registry.py`
- Create: `matmaster/bohrium/__init__.py`
- Create: `matmaster/bohrium/types.py`
- Create: `matmaster/bohrium/errors.py`
- Create: `matmaster/bohrium/endpoints.py`
- Test: `tests/matmaster/calculation_runtimes/test_registry.py`
- Test: `tests/matmaster/bohrium/test_types.py`

- [ ] **Step 1: Write the failing contract and normalization tests**

```python
"""tests/matmaster/bohrium/test_types.py"""
from matmaster.bohrium.endpoints import get_bohrium_base_url, get_bohrium_service_env
from matmaster.bohrium.types import BohriumCredentials, BohriumRuntimeSnapshot


def test_bohrium_credentials_normalize_raw_mapping() -> None:
    cred = BohriumCredentials.from_mapping(
        {
            "access_key": "  ak-123  ",
            "project_id": "42",
            "user_id": "7",
            "user_no": "  U001  ",
            "base_url": "https://openapi.test.dp.tech/",
        }
    )

    assert cred.access_key == "ak-123"
    assert cred.project_id == 42
    assert cred.user_id == 7
    assert cred.user_no == "U001"
    assert cred.base_url == "https://openapi.test.dp.tech"


def test_runtime_snapshot_is_plain_data() -> None:
    snap = BohriumRuntimeSnapshot(
        session_type="ssh",
        execution_workdir="/share",
        remote_workspace_root="/share",
        remote_project_root="/share/.matmaster",
        node_id=12,
        node_ip="10.0.0.8",
        ssh_attached=True,
    )

    assert snap.model_dump()["node_id"] == 12


def test_bohrium_base_url_prefers_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_BASE_URL", "https://openapi.custom.dp.tech/")
    assert get_bohrium_base_url() == "https://openapi.custom.dp.tech"


def test_bohrium_base_url_keeps_existing_runtime_prod_default(monkeypatch) -> None:
    monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)
    monkeypatch.setenv("SERVICE_ENV", "prod")
    assert get_bohrium_base_url() == "https://openapi.dp.tech"


def test_bohrium_service_env_defaults_to_test(monkeypatch) -> None:
    monkeypatch.delenv("SERVICE_ENV", raising=False)
    assert get_bohrium_service_env() == "test"
```

```python
"""matmaster/bohrium/errors.py"""
class BohriumCredentialError(RuntimeError):
    """Raised when Bohrium credentials cannot be resolved."""


class BohriumRuntimeNotInitialized(RuntimeError):
    """Raised when the current session has no attached Bohrium runtime."""


class BohriumSubmissionBuildError(RuntimeError):
    """Raised when a submission spec cannot be built from runtime state."""


class BohriumPathMaterializationError(RuntimeError):
    """Raised when a calculation input path cannot be converted to a remote URL."""
```

```python
"""matmaster/bohrium/__init__.py"""
from .credentials import credentials_from_env, normalize_bohrium_credentials
from .runtime import BohriumRuntimeHandle, attach_runtime, detach_runtime, get_runtime
from .types import (
    BohriumCredentials,
    BohriumExecutionContext,
    BohriumRuntimeSnapshot,
    BohriumSubmissionSpec,
)

__all__ = [
    "BohriumCredentials",
    "BohriumExecutionContext",
    "BohriumRuntimeHandle",
    "BohriumRuntimeSnapshot",
    "BohriumSubmissionSpec",
    "attach_runtime",
    "credentials_from_env",
    "detach_runtime",
    "get_runtime",
    "normalize_bohrium_credentials",
]
```

```python
"""tests/matmaster/calculation_runtimes/test_registry.py"""
from typing import cast

from matmaster.calculation_runtimes.base import CalculationRuntime
from matmaster.calculation_runtimes.registry import get_runtime_factory, register_runtime


class _FakeRuntime:
    def build_env(self) -> dict[str, str]:
        return {"A": "1"}

    def execution(self):
        return "execution"

    def build_submission(self, request):
        return request

    def materialize_input_path(self, *args, **kwargs):
        return "https://example.invalid/input"


def test_register_and_resolve_runtime_factory() -> None:
    register_runtime("fake", lambda session=None: cast(CalculationRuntime, _FakeRuntime()))

    factory = get_runtime_factory("fake")
    runtime = factory(None)

    assert runtime.build_env() == {"A": "1"}
```

- [ ] **Step 2: Run tests to verify the new modules do not exist yet**

Run:

```bash
uv run pytest \
  tests/matmaster/bohrium/test_types.py \
  tests/matmaster/calculation_runtimes/test_registry.py -v
```

Expected:

- `ModuleNotFoundError: No module named 'matmaster.bohrium'`
- `ModuleNotFoundError: No module named 'matmaster.calculation_runtimes'`

- [ ] **Step 3: Implement the foundational contracts and type files**

```python
"""matmaster/bohrium/types.py"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class BohriumCredentials:
    access_key: str
    project_id: int
    user_id: int | None
    user_no: str
    base_url: str

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "BohriumCredentials":
        access_key = str(values.get("access_key") or "").strip()
        raw_project_id = values.get("project_id", -1)
        raw_user_id = values.get("user_id", None)
        try:
            project_id = int(raw_project_id)
        except (TypeError, ValueError):
            project_id = -1
        try:
            user_id = int(raw_user_id) if raw_user_id not in (None, "", -1) else None
        except (TypeError, ValueError):
            user_id = None
        user_no = str(values.get("user_no") or "").strip()
        base_url = str(values.get("base_url") or "").strip().rstrip("/")
        return cls(
            access_key=access_key,
            project_id=project_id,
            user_id=user_id,
            user_no=user_no,
            base_url=base_url,
        )


class BohriumRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_type: str
    execution_workdir: str
    remote_workspace_root: str
    remote_project_root: str
    node_id: int | None = None
    node_ip: str | None = None
    ssh_attached: bool = False


@dataclass(frozen=True)
class BohriumExecutionContext:
    session_type: str
    execution_workdir: str
    remote_workspace_root: str
    remote_project_root: str
    node_id: int | None
    node_ip: str | None
    ssh_attached: bool


@dataclass(frozen=True)
class BohriumSubmissionSpec:
    executor: dict[str, Any] | None
    storage: dict[str, Any] | None
    submission_mode: str
```

```python
"""matmaster/calculation_runtimes/base.py"""
from __future__ import annotations

from typing import Any, Protocol

from .types import ExecutionContextLike, SubmissionRequest, SubmissionSpecLike


class CalculationRuntime(Protocol):
    def build_env(self) -> dict[str, str]: ...

    def execution(self) -> ExecutionContextLike: ...

    def build_submission(self, request: SubmissionRequest) -> SubmissionSpecLike: ...

    def materialize_input_path(self, *args: Any, **kwargs: Any) -> str: ...
```

```python
"""matmaster/calculation_runtimes/types.py"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ExecutionContextLike(Protocol):
    session_type: str
    execution_workdir: str
    remote_workspace_root: str
    remote_project_root: str
    node_id: int | None
    node_ip: str | None
    ssh_attached: bool


class SubmissionSpecLike(Protocol):
    executor: dict[str, Any] | None
    storage: dict[str, Any] | None
    submission_mode: str


@dataclass(frozen=True)
class SubmissionRequest:
    executor_template: dict[str, Any] | None
    needs_storage: bool
    submission_mode: str
```

```python
"""matmaster/calculation_runtimes/registry.py"""
from __future__ import annotations

from collections.abc import Callable

from .base import CalculationRuntime

_RUNTIME_FACTORIES: dict[str, Callable[[object | None], CalculationRuntime]] = {}


def register_runtime(
    name: str, factory: Callable[[object | None], CalculationRuntime]
) -> None:
    _RUNTIME_FACTORIES[name] = factory


def get_runtime_factory(
    name: str,
) -> Callable[[object | None], CalculationRuntime]:
    try:
        return _RUNTIME_FACTORIES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown calculation runtime: {name!r}") from exc
```

```python
"""matmaster/bohrium/endpoints.py"""
from __future__ import annotations

import os


def get_bohrium_service_env() -> str:
    raw = (os.getenv("SERVICE_ENV", "test") or "").strip().lower()
    return raw or "test"


def get_bohrium_base_url() -> str:
    override = (os.getenv("BOHRIUM_BASE_URL", "") or "").strip().rstrip("/")
    if override:
        return override
    env = get_bohrium_service_env()
    if env == "prod":
        return "https://openapi.dp.tech"
    return f"https://openapi.{env}.dp.tech"
```

- [ ] **Step 4: Run the new foundational tests**

Run:

```bash
uv run pytest \
  tests/matmaster/bohrium/test_types.py \
  tests/matmaster/calculation_runtimes/test_registry.py -v
```

Expected:

- all selected tests `PASS`
- no import from `matmaster.integration.runtime_bridge`
- `get_bohrium_base_url()` stays aligned with the current prod/test fallback
  semantics used by `matmaster.integration.bohrium_api`

- [ ] **Step 5: Commit the foundational contracts**

```bash
git add \
  matmaster/bohrium/__init__.py \
  matmaster/bohrium/types.py \
  matmaster/bohrium/errors.py \
  matmaster/bohrium/endpoints.py \
  matmaster/calculation_runtimes/__init__.py \
  matmaster/calculation_runtimes/base.py \
  matmaster/calculation_runtimes/types.py \
  matmaster/calculation_runtimes/registry.py \
  tests/matmaster/bohrium/test_types.py \
  tests/matmaster/calculation_runtimes/test_registry.py
git commit -m "feat: add Bohrium runtime contracts"
```

### Task 2: Implement the runtime handle, env projection, and submission builders

**Files:**
- Create: `matmaster/bohrium/credentials.py`
- Create: `matmaster/bohrium/env.py`
- Create: `matmaster/bohrium/executor.py`
- Create: `matmaster/bohrium/storage.py`
- Create: `matmaster/bohrium/paths.py`
- Create: `matmaster/bohrium/runtime.py`
- Test: `tests/matmaster/bohrium/test_runtime.py`

- [ ] **Step 1: Write the failing runtime-handle tests**

```python
"""tests/matmaster/bohrium/test_runtime.py"""
from types import SimpleNamespace

import pytest

from matmaster.bohrium.runtime import (
    BohriumRuntimeHandle,
    attach_runtime,
    detach_runtime,
    get_runtime,
    require_runtime,
)
from matmaster.bohrium.types import (
    BohriumCredentials,
    BohriumExecutionContext,
)
from matmaster.calculation_runtimes.types import SubmissionRequest


def _runtime() -> BohriumRuntimeHandle:
    credentials = BohriumCredentials(
        access_key="ak",
        project_id=42,
        user_id=7,
        user_no="U001",
        base_url="https://openapi.test.dp.tech",
    )
    execution = BohriumExecutionContext(
        session_type="ssh",
        execution_workdir="/share",
        remote_workspace_root="/share",
        remote_project_root="/share/.matmaster",
        node_id=8,
        node_ip="10.0.0.8",
        ssh_attached=True,
    )
    return BohriumRuntimeHandle(credentials=credentials, execution=execution)


def test_attach_and_require_runtime_round_trip() -> None:
    session = SimpleNamespace()
    runtime = _runtime()

    attach_runtime(session, runtime)

    assert get_runtime(session) is runtime
    assert require_runtime(session) is runtime


def test_detach_runtime_clears_session() -> None:
    session = SimpleNamespace()
    attach_runtime(session, _runtime())

    detach_runtime(session)

    assert get_runtime(session) is None


def test_build_env_projects_runtime_credentials() -> None:
    env = _runtime().build_env()
    assert env["BOHRIUM_ACCESS_KEY"] == "ak"
    assert env["BOHRIUM_PROJECT_ID"] == "42"
    assert env["BOHRIUM_BASE_URL"] == "https://openapi.test.dp.tech"


def test_build_submission_injects_dispatcher_credentials() -> None:
    submission = _runtime().build_submission(
        SubmissionRequest(
            executor_template={
                "type": "dispatcher",
                "machine": {"remote_profile": {"machine_type": "c2_m8_cpu", "image_address": "repo/image:latest"}},
            },
            needs_storage=True,
            submission_mode="async",
        )
    )

    assert submission.executor["machine"]["remote_profile"]["access_key"] == "ak"
    assert submission.storage["plugin"]["project_id"] == 42


def test_require_runtime_raises_for_missing_runtime() -> None:
    with pytest.raises(RuntimeError):
        require_runtime(SimpleNamespace())


def test_snapshot_maps_execution_fields_explicitly() -> None:
    snap = _runtime().snapshot()

    assert snap.session_type == "ssh"
    assert snap.execution_workdir == "/share"


def test_materialize_input_path_uploads_local_files(tmp_path, monkeypatch) -> None:
    input_file = tmp_path / "input.in"
    input_file.write_text("data", encoding="utf-8")

    monkeypatch.setattr(
        "matmaster.bohrium.paths.upload_file_to_oss",
        lambda path, workspace_root, **kwargs: f"https://oss/{path.name}",
    )

    url = _runtime().materialize_input_path(
        str(input_file),
        workspace_root=tmp_path,
        session=None,
    )

    assert url == "https://oss/input.in"


def test_materialize_input_path_downloads_remote_files_before_upload(
    tmp_path, monkeypatch
) -> None:
    session = SimpleNamespace(
        is_file=lambda path: True,
        download=lambda path: b"remote-data",
    )

    monkeypatch.setattr(
        "matmaster.bohrium.paths.upload_file_to_oss",
        lambda path, workspace_root, **kwargs: f"https://oss/{kwargs['object_basename']}",
    )

    url = _runtime().materialize_input_path(
        "inputs/job.in",
        workspace_root=tmp_path,
        session=session,
    )

    assert url == "https://oss/job.in"
```

- [ ] **Step 2: Run tests to confirm runtime helpers do not exist yet**

Run:

```bash
uv run pytest tests/matmaster/bohrium/test_runtime.py -v
```

Expected:

- import errors for `matmaster.bohrium.runtime`
- missing runtime helpers and builders in `matmaster.bohrium`

- [ ] **Step 3: Implement runtime data flow and submission helpers**

```python
"""matmaster/bohrium/credentials.py"""
from __future__ import annotations

import os
from typing import Any

from .types import BohriumCredentials


def normalize_bohrium_credentials(values: dict[str, Any]) -> BohriumCredentials:
    return BohriumCredentials.from_mapping(values)


def credentials_from_env() -> BohriumCredentials | None:
    values = {
        "access_key": os.getenv("BOHRIUM_ACCESS_KEY"),
        "project_id": os.getenv("BOHRIUM_PROJECT_ID"),
        "user_id": os.getenv("BOHRIUM_USER_ID"),
        "user_no": os.getenv("BOHRIUM_USER_NO"),
        "base_url": os.getenv("BOHRIUM_BASE_URL"),
    }
    cred = normalize_bohrium_credentials(values)
    return cred if cred.access_key else None
```

```python
"""matmaster/bohrium/env.py"""
from __future__ import annotations

from .types import BohriumCredentials


def build_bohrium_env(credentials: BohriumCredentials) -> dict[str, str]:
    env: dict[str, str] = {}
    if credentials.access_key:
        env["BOHRIUM_ACCESS_KEY"] = credentials.access_key
    if credentials.project_id != -1:
        env["BOHRIUM_PROJECT_ID"] = str(credentials.project_id)
    if credentials.user_id is not None:
        env["BOHRIUM_USER_ID"] = str(credentials.user_id)
    if credentials.user_no:
        env["BOHRIUM_USER_NO"] = credentials.user_no
    if credentials.base_url:
        env["BOHRIUM_BASE_URL"] = credentials.base_url
    return env
```

```python
"""matmaster/bohrium/executor.py"""
from __future__ import annotations

import copy
from typing import Any

from .types import BohriumCredentials


def build_executor(
    template: dict[str, Any] | None,
    credentials: BohriumCredentials,
) -> dict[str, Any] | None:
    if template is None:
        return None
    executor = copy.deepcopy(template)
    if executor.get("type") == "dispatcher":
        remote_profile = executor.setdefault("machine", {}).setdefault("remote_profile", {})
        remote_profile["access_key"] = credentials.access_key
        remote_profile["project_id"] = credentials.project_id
        remote_profile["real_user_id"] = credentials.user_id or -1
        resources = executor.setdefault("resources", {})
        envs = resources.setdefault("envs", {})
        envs["BOHRIUM_PROJECT_ID"] = credentials.project_id
    elif executor.get("type") == "local":
        env = executor.setdefault("env", {})
        env["BOHRIUM_PROJECT_ID"] = str(credentials.project_id)
        env["BOHRIUM_ACCESS_KEY"] = credentials.access_key
    return executor
```

```python
"""matmaster/bohrium/storage.py"""
from __future__ import annotations

from .types import BohriumCredentials


def build_storage(credentials: BohriumCredentials) -> dict[str, object]:
    return {
        "type": "https",
        "plugin": {
            "type": "bohrium",
            "access_key": credentials.access_key,
            "project_id": credentials.project_id,
            "app_key": "agent",
        },
    }
```

```python
"""matmaster/bohrium/paths.py"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import BohriumPathMaterializationError
from .oss import upload_file_to_oss

_URL_RE = re.compile(r'https?://[^\s,\'"<>)}\]]+')


def is_local_path(value: Any) -> bool:
    if not value or not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme not in ("http", "https")


def workspace_path_to_local(value: str, workspace_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (workspace_root / path).resolve()


def materialize_input_path(
    value: str,
    *,
    workspace_root: Path,
    session: Any = None,
) -> str:
    if not is_local_path(value):
        return value

    resolved = workspace_path_to_local(value, workspace_root)
    if session is not None and hasattr(session, "download") and hasattr(session, "is_file"):
        remote_path = str(resolved).replace("\\", "/")
        if not session.is_file(remote_path):
            raise BohriumPathMaterializationError(
                f"Remote input file not found: {remote_path}"
            )
        data = session.download(remote_path)
        suffix = Path(remote_path).suffix or ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            return upload_file_to_oss(
                tmp_path,
                tmp_path.parent,
                object_basename=Path(remote_path).name,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    if not resolved.exists() or not resolved.is_file():
        raise BohriumPathMaterializationError(f"Local input file not found: {resolved}")
    return upload_file_to_oss(resolved, workspace_root)
```

```python
"""matmaster/bohrium/runtime.py"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from matmaster.calculation_runtimes.types import SubmissionRequest

from .env import build_bohrium_env
from .executor import build_executor
from .paths import materialize_input_path as materialize_bohrium_input_path
from .storage import build_storage
from .types import (
    BohriumCredentials,
    BohriumExecutionContext,
    BohriumRuntimeSnapshot,
    BohriumSubmissionSpec,
)


class SupportsBohriumRuntimeSlot(Protocol):
    _bohrium_runtime: "BohriumRuntimeHandle | None"


class BohriumRuntimeHandle:
    def __init__(
        self,
        *,
        credentials: BohriumCredentials,
        execution: BohriumExecutionContext,
        execution_session: Any | None = None,
    ) -> None:
        self._credentials = credentials
        self._execution = execution
        self._execution_session = execution_session

    def credentials(self) -> BohriumCredentials:
        return self._credentials

    def execution(self) -> BohriumExecutionContext:
        return self._execution

    def snapshot(self) -> BohriumRuntimeSnapshot:
        return BohriumRuntimeSnapshot(
            session_type=self._execution.session_type,
            execution_workdir=self._execution.execution_workdir,
            remote_workspace_root=self._execution.remote_workspace_root,
            remote_project_root=self._execution.remote_project_root,
            node_id=self._execution.node_id,
            node_ip=self._execution.node_ip,
            ssh_attached=self._execution.ssh_attached,
        )

    def execution_session(self) -> Any | None:
        return self._execution_session

    def build_env(self) -> dict[str, str]:
        return build_bohrium_env(self.credentials())

    def build_submission(self, request: SubmissionRequest) -> BohriumSubmissionSpec:
        return BohriumSubmissionSpec(
            executor=build_executor(request.executor_template, self.credentials()),
            storage=build_storage(self.credentials()) if request.needs_storage else None,
            submission_mode=request.submission_mode,
        )

    def materialize_input_path(
        self,
        value: str,
        *,
        workspace_root: Path,
        session: Any = None,
        **_: Any,
    ) -> str:
        return materialize_bohrium_input_path(
            value,
            workspace_root=workspace_root,
            session=session,
        )


def attach_runtime(session: SupportsBohriumRuntimeSlot, runtime: BohriumRuntimeHandle) -> None:
    session._bohrium_runtime = runtime


def get_runtime(
    session: SupportsBohriumRuntimeSlot | None,
) -> BohriumRuntimeHandle | None:
    if session is None:
        return None
    return getattr(session, "_bohrium_runtime", None)


def require_runtime(session: SupportsBohriumRuntimeSlot) -> BohriumRuntimeHandle:
    runtime = get_runtime(session)
    if runtime is None:
        raise RuntimeError("Bohrium runtime is not initialized for this session.")
    return runtime


def detach_runtime(session: SupportsBohriumRuntimeSlot) -> None:
    if hasattr(session, "_bohrium_runtime"):
        delattr(session, "_bohrium_runtime")
```

- [ ] **Step 4: Run the Bohrium runtime tests**

Run:

```bash
uv run pytest tests/matmaster/bohrium/test_runtime.py -v
```

Expected:

- all selected tests `PASS`
- `build_submission()` returns injected dispatcher/local structures

- [ ] **Step 5: Commit the runtime handle foundation**

```bash
git add \
  matmaster/bohrium/credentials.py \
  matmaster/bohrium/env.py \
  matmaster/bohrium/executor.py \
  matmaster/bohrium/paths.py \
  matmaster/bohrium/storage.py \
  matmaster/bohrium/runtime.py \
  tests/matmaster/bohrium/test_runtime.py
git commit -m "feat: add Bohrium runtime handle"
```

### Task 3: Register the runtime during `agent_run_bohrium` startup and export snapshots

**Files:**
- Modify: `src/services/agent_run_bohrium.py`
- Modify: `src/services/agent_run_service.py`
- Modify: `matmaster/types/context.py`
- Modify: `tests/matmaster/test_bohrium_setup_injection.py`

Task 3 only changes the runtime registration lifecycle inside
`src/services/agent_run_bohrium.py`. The existing node provisioning, SSH swap,
skill sync, and abort flow stay in place. The concrete methods to touch are:

- `_apply_run_credentials_to_session()`
- `_setup_bohrium_for_run()`
- `_store_bohrium_runtime()` only as cleanup bookkeeping, not as a second
  runtime registry
- `_restore_bohrium_runtime_state()`
- `_cleanup_bohrium_after_run()`

`BohriumSetupService` is already a thin delegator over these module-level
functions. Keep that delegation intact and extend the existing
`tests/matmaster/test_bohrium_setup_injection.py` coverage instead of replacing
its current service-orchestration assertions.

The first runtime attached by `_apply_run_credentials_to_session()` is a
credential-only placeholder for pre-SSH phases. Its empty execution fields are
acceptable only if `build_env()` keeps projecting credentials without depending
on execution metadata. Consumers that need `execution()` must observe the
rebuilt SSH runtime after `_setup_bohrium_for_run()` finishes.

Switching `pg_ctx.with_bohrium()` from `bohrium_result._asdict()` to
`runtime_snapshot.model_dump()` is an intentional metadata-shape change. Before
landing this task, grep the repo for `run_meta["bohrium"]` field consumers and
confirm that only generic storage/tests depend on the old key set.

- [ ] **Step 1: Write the failing startup registration tests**

```python
"""tests/matmaster/test_bohrium_setup_injection.py"""
from types import SimpleNamespace

from src.services.agent_run_bohrium import (
    BohriumSetupResult,
    _apply_run_credentials_to_session,
)
from matmaster.bohrium.runtime import get_runtime


def test_apply_run_credentials_registers_runtime_and_keeps_dual_write() -> None:
    session = SimpleNamespace()
    run_creds = {
        "access_key": "ak",
        "project_id": 42,
        "user_id": "7",
        "user_no": "U001",
        "base_url": "https://openapi.test.dp.tech/",
    }

    _apply_run_credentials_to_session(session, run_creds)

    runtime = get_runtime(session)
    assert runtime is not None
    assert runtime.credentials().access_key == "ak"
    assert session._bohrium_credentials["project_id"] == 42


def test_playground_context_with_bohrium_uses_snapshot_dict() -> None:
    from pathlib import Path

    from matmaster.types.context import PlaygroundContext

    ctx = PlaygroundContext(
        workdir=Path("/tmp/work"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    )

    updated = ctx.with_bohrium(
        {
            "session_type": "ssh",
            "execution_workdir": "/share",
            "remote_workspace_root": "/share",
            "remote_project_root": "/share/.matmaster",
            "node_id": 9,
            "node_ip": "10.0.0.9",
            "ssh_attached": True,
        }
    )

    assert updated.run_meta["bohrium"]["node_id"] == 9
```

- [ ] **Step 2: Run the startup tests and verify registration is missing**

Run:

```bash
uv run pytest tests/matmaster/test_bohrium_setup_injection.py -v
```

Expected:

- runtime assertion fails because `get_runtime(session)` returns `None`

- [ ] **Step 3: Wire `agent_run_bohrium.py` to build and attach the runtime**

```python
"""replace _apply_run_credentials_to_session in src/services/agent_run_bohrium.py"""
from matmaster.bohrium.endpoints import get_bohrium_base_url
from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime, detach_runtime
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext


def _apply_run_credentials_to_session(session: Any, run_creds: dict[str, Any]) -> None:
    """Attach transient Bohrium runtime to the active session object."""
    if not run_creds or session is None:
        return

    normalized = {
        **run_creds,
        "base_url": run_creds.get("base_url") or get_bohrium_base_url(),
    }
    credentials = BohriumCredentials.from_mapping(normalized)
    execution = BohriumExecutionContext(
        session_type="local",
        execution_workdir="",
        remote_workspace_root="",
        remote_project_root="",
        node_id=None,
        node_ip=None,
        ssh_attached=False,
    )
    runtime = BohriumRuntimeHandle(
        credentials=credentials,
        execution=execution,
        execution_session=session,
    )
    attach_runtime(session, runtime)
    session._bohrium_credentials = dict(run_creds)


def _restore_bohrium_runtime_state(session_id: str, pg: Any | None) -> None:
    """Close the transient SSH session and clear its runtime slot before restore."""
    sess = SESSIONS.get(session_id)
    if not sess:
        return
    runtime_state = sess.pop("bohrium_runtime", None)
    if not runtime_state:
        return
    ssh = runtime_state.get("ssh_session")
    if ssh is not None:
        detach_runtime(ssh)
        if getattr(ssh, "is_open", False):
            ssh.close()
    if pg is not None:
        _restore_playground_session(
            pg,
            runtime_state.get("original_session"),
            runtime_state.get("original_owns_session", True),
        )


def _cleanup_bohrium_after_run(
    *,
    session_id: str,
    sessions_service: Any,
    event_callback: Callable[..., None],
    pg_for_run: Any,
    ssh_attached: bool,
) -> None:
    """Run-final cleanup removes runtime slots from the active session objects."""
    _restore_bohrium_runtime_state(session_id, pg_for_run)
```

```python
"""extend BohriumSetupResult and rebuild the runtime after SSH attach"""
from matmaster.bohrium.types import BohriumRuntimeSnapshot


class BohriumSetupResult(NamedTuple):
    ssh_attached: bool
    abort_result: tuple[Any, int] | None
    execution_session: Any | None
    execution_workdir: str | None
    session_type: str | None
    runtime_snapshot: BohriumRuntimeSnapshot | None


# Update every return site in _setup_bohrium_for_run(), not just the SSH happy path:
return BohriumSetupResult(False, None, None, None, None, None)
return BohriumSetupResult(False, ((False, reason), elapsed_ms), None, None, None, None)

# Audit command before leaving Task 3:
# rg -n "return BohriumSetupResult\\(" src/services/agent_run_bohrium.py


execution = BohriumExecutionContext(
    session_type="ssh",
    execution_workdir=ssh_working_dir,
    remote_workspace_root=remote_workspace_root,
    remote_project_root=getattr(ssh_session, "remote_project_root", ""),
    node_id=node_id,
    node_ip=node_ip,
    ssh_attached=True,
)
runtime = BohriumRuntimeHandle(
    credentials=BohriumCredentials.from_mapping(
        {**run_creds, "base_url": run_creds.get("base_url") or get_bohrium_base_url()}
    ),
    execution=execution,
    execution_session=ssh_session,
)
attach_runtime(ssh_session, runtime)

return BohriumSetupResult(
    True,
    None,
    ssh_session,
    ssh_working_dir,
    "ssh",
    runtime.snapshot(),
)
```

```python
"""src/services/agent_run_service.py"""
bohrium_meta = (
    bohrium_result.runtime_snapshot.model_dump()
    if bohrium_result.runtime_snapshot is not None
    else {}
)
pg_ctx = pg_ctx.with_bohrium(bohrium_meta)

# Repo audit before landing:
# rg -n 'run_meta\\[.?["'\"']bohrium["'\"']|with_bohrium\\(' src matmaster tests
```

- [ ] **Step 4: Run the startup-focused tests plus one live-path smoke test**

Run:

```bash
uv run pytest \
  tests/matmaster/test_bohrium_setup_injection.py \
  tests/matmaster/integration/test_bohrium_execution_contract.py -v
```

Expected:

- startup injection tests `PASS`
- no breakage in the existing Bohrium execution contract smoke test

- [ ] **Step 5: Commit the startup registration change**

```bash
git add \
  src/services/agent_run_bohrium.py \
  src/services/agent_run_service.py \
  matmaster/types/context.py \
  tests/matmaster/test_bohrium_setup_injection.py
git commit -m "refactor: register Bohrium runtime during startup"
```

### Task 4: Migrate shell and script env consumers to `get_runtime(session)`-based env projection

**Files:**
- Modify: `matmaster/tools/script_env.py`
- Modify: `matmaster/tools/builtin/bash_tool.py`
- Modify: `matmaster/tools/builtin/glob_tool.py`
- Modify: `matmaster/tools/builtin/grep_tool.py`
- Modify: `tests/matmaster/tools/test_script_env.py`
- Modify: `tests/matmaster/tools/builtin/test_bash_tool.py`
- Modify: `tests/matmaster/tools/builtin/test_glob_tool.py`
- Modify: `tests/matmaster/tools/builtin/test_grep_tool.py`

The concrete call sites in current code are:

- `script_env.inject()` as the shared shell wrapper
- `bash_tool.BashTool._execute()` after `plan_shell_command()`
- `glob_tool.GlobTool._execute()` before `session.exec_bash()`
- `grep_tool.GrepTool._execute_internal()` before `session.exec_bash()`
- `grep_tool.GrepTool._list_candidate_files()` in the semantic-search fallback path

- [ ] **Step 1: Write the failing consumer tests around runtime env usage**

```python
"""tests/matmaster/tools/test_script_env.py"""
from types import SimpleNamespace

from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext
from matmaster.tools.script_env import inject


def _attach_runtime(session: SimpleNamespace) -> None:
    runtime = BohriumRuntimeHandle(
        credentials=BohriumCredentials(
            access_key="ak",
            project_id=42,
            user_id=7,
            user_no="U001",
            base_url="https://openapi.test.dp.tech",
        ),
        execution=BohriumExecutionContext(
            session_type="ssh",
            execution_workdir="/share",
            remote_workspace_root="/share",
            remote_project_root="/share/.matmaster",
            node_id=1,
            node_ip="10.0.0.1",
            ssh_attached=True,
        ),
        execution_session=session,
    )
    attach_runtime(session, runtime)


def test_inject_reads_env_from_runtime_handle() -> None:
    session = SimpleNamespace(write_file=lambda *a, **k: None, exec_bash=lambda *a, **k: None)
    _attach_runtime(session)

    wrapped = inject("python tool.py", session)

    assert "BOHRIUM_ACCESS_KEY" in wrapped
```

```python
"""tests/matmaster/tools/builtin/test_bash_tool.py"""
from unittest.mock import MagicMock

from matmaster.tools.builtin.bash_tool import BashTool


def test_bash_tool_reads_runtime_env(monkeypatch):
    session = MagicMock()
    session.exec_bash.return_value = {"output": "ok", "exit_code": 0}
    runtime = MagicMock()
    runtime.build_env.return_value = {"BOHRIUM_ACCESS_KEY": "ak"}
    monkeypatch.setattr(
        "matmaster.tools.builtin.bash_tool.get_runtime",
        lambda _session: runtime,
    )

    tool = BashTool(session=session)
    result = tool._execute({"command": "echo ok"})

    assert result.endswith("[Command finished with exit code 0]")
```

Also extend `tests/matmaster/tools/builtin/test_grep_tool.py` with a case that
forces `_list_candidate_files()` through the semantic-search fallback path, so
both legacy `build_service_env()` call sites are covered before Task 8 deletes
`runtime_bridge`.

- [ ] **Step 2: Run the shell/script tests to expose the old bridge dependency**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/test_script_env.py \
  tests/matmaster/tools/builtin/test_bash_tool.py \
  tests/matmaster/tools/builtin/test_glob_tool.py \
  tests/matmaster/tools/builtin/test_grep_tool.py -v
```

Expected:

- tests fail because production code still imports `build_service_env`

- [ ] **Step 3: Replace `build_service_env()` calls with runtime handle calls**

```python
"""matmaster/tools/script_env.py"""
from matmaster.bohrium.runtime import get_runtime


def inject(cmd: str, session: Any) -> str:
    runtime = get_runtime(session)
    if runtime is None:
        return cmd
    env = runtime.build_env()
    return inject_env(cmd, env, session)
```

```python
"""matmaster/tools/builtin/bash_tool.py"""
from matmaster.bohrium.runtime import get_runtime


runtime = get_runtime(session)
env = runtime.build_env() if runtime is not None else {}
plan = plan_shell_command(command)
if plan.mode == "script":
    command = prepare_script_command(command, env, session, shell_path="bash")
else:
    command = prepare_inline_command(command, env, session)
```

```python
"""matmaster/tools/builtin/glob_tool.py"""
from matmaster.bohrium.runtime import get_runtime


runtime = get_runtime(session)
env = runtime.build_env() if runtime is not None else {}
command = inject_env(command, env, session)
```

```python
"""matmaster/tools/builtin/grep_tool.py"""
from matmaster.bohrium.runtime import get_runtime


runtime = get_runtime(session)
env = runtime.build_env() if runtime is not None else {}
cmd = inject_env(cmd, env, session)

# also update _list_candidate_files()
runtime = get_runtime(session)
env = runtime.build_env() if runtime is not None else {}
result = session.exec_bash(
    command=inject_env(find_cmd, env, session),
    timeout=30,
    cancel_token=self._cancel_token_for_exec(),
)
```

- [ ] **Step 4: Run the updated shell/script test slice**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/test_script_env.py \
  tests/matmaster/tools/builtin/test_bash_tool.py \
  tests/matmaster/tools/builtin/test_glob_tool.py \
  tests/matmaster/tools/builtin/test_grep_tool.py -v
```

Expected:

- all selected tests `PASS`
- no imports from `matmaster.integration.runtime_bridge` remain in these files

- [ ] **Step 5: Commit the env-consumer migration**

```bash
git add \
  matmaster/tools/script_env.py \
  matmaster/tools/builtin/bash_tool.py \
  matmaster/tools/builtin/glob_tool.py \
  matmaster/tools/builtin/grep_tool.py \
  tests/matmaster/tools/test_script_env.py \
  tests/matmaster/tools/builtin/test_bash_tool.py \
  tests/matmaster/tools/builtin/test_glob_tool.py \
  tests/matmaster/tools/builtin/test_grep_tool.py
git commit -m "refactor: read Bohrium env from runtime handle"
```

### Task 5: Migrate `bohrium_tool` to runtime-derived credentials and context

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/models.py`
- Reference: `matmaster/tools/builtin/bohrium_tool/api.py`
- Reference: `matmaster/tools/builtin/bohrium_tool/transfers.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_models.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Reference: `tests/matmaster/tools/builtin/test_bohrium_tool_api.py`
- Reference: `tests/matmaster/tools/builtin/test_bohrium_tool_poll.py`
- Reference: `tests/matmaster/tools/builtin/test_bohrium_tool_download.py`
- Reference: `tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py`
- Reference: `tests/matmaster/integration/test_bohrium_job_skill_submit.py`

`BohriumInputSource` and `BohriumDownloadTarget` stay unchanged in this task.
Task 5 only changes how `BohriumContext` gets its credentials and how
`build_bohrium_context()` preserves the existing runtime-or-env fallback.

- [ ] **Step 1: Write the failing Bohrium builtin tool tests against runtime-derived credentials**

```python
"""tests/matmaster/tools/builtin/test_bohrium_tool_models.py"""
from matmaster.bohrium.types import BohriumCredentials
from matmaster.tools.builtin.bohrium_tool.models import BohriumContext


def test_bohrium_context_builds_from_bohrium_credentials() -> None:
    cred = BohriumCredentials(
        access_key="ak",
        project_id=42,
        user_id=7,
        user_no="U001",
        base_url="https://openapi.test.dp.tech",
    )

    ctx = BohriumContext.from_credentials(cred, sandbox=False)

    assert ctx.access_key == "ak"
    assert ctx.project_id == 42
    assert ctx.credential_source == "runtime"
```

```python
"""tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py"""
from types import SimpleNamespace

from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext
from matmaster.tools.builtin.bohrium_tool.tool import build_bohrium_context


def test_build_bohrium_context_reads_runtime_handle() -> None:
    session = SimpleNamespace()
    attach_runtime(
        session,
        BohriumRuntimeHandle(
            credentials=BohriumCredentials(
                access_key="ak",
                project_id=42,
                user_id=7,
                user_no="U001",
                base_url="https://openapi.test.dp.tech",
            ),
            execution=BohriumExecutionContext(
                session_type="ssh",
                execution_workdir="/share",
                remote_workspace_root="/share",
                remote_project_root="/share/.matmaster",
                node_id=1,
                node_ip="10.0.0.1",
                ssh_attached=True,
            ),
            execution_session=session,
        ),
    )

    ctx = build_bohrium_context(session=session, require_project=True)

    assert ctx.project_id == 42


def test_build_bohrium_context_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "9")
    monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)

    ctx = build_bohrium_context(session=None, require_project=True)

    assert ctx.access_key == "env-ak"
    assert ctx.project_id == 9
    assert ctx.credential_source == "env"
```

- [ ] **Step 2: Run the Bohrium builtin tool tests to expose bridge/model coupling**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/builtin/test_bohrium_tool_models.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_api.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_poll.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_download.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py \
  tests/matmaster/integration/test_bohrium_job_skill_submit.py -v
```

Expected:

- model test fails because `BohriumContext.from_credentials()` does not exist
- helper test fails because `build_bohrium_context()` still imports `resolve_bohrium_credentials`
- sandbox/HPC protocol tests remain green or fail only because credential resolution still points at the old bridge

- [ ] **Step 3: Replace bridge-facing code with runtime-facing code**

Keep these behaviors unchanged while editing:

- `use_sandbox()` remains the only switch for sandbox-vs-HPC job API selection.
- `create_job()` / `add_job()` / `get_job_detail()` keep their current endpoint split and payload differences.
- sandbox uses string job IDs and `download_url`; standard HPC keeps integer job IDs and `oss_key`.
- download/result transfer code in `transfers.py` is compatibility-sensitive and should only change if the credential-source refactor forces a minimal signature update.

```python
"""matmaster/tools/builtin/bohrium_tool/models.py"""
from dataclasses import dataclass
from pathlib import Path

from matmaster.bohrium.types import BohriumCredentials

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
    def from_credentials(
        cls, cred: BohriumCredentials, *, sandbox: bool, source: str = "runtime"
    ) -> "BohriumContext":
        if not cred.access_key:
            raise BohriumCredentialError(
                "Bohrium credentials unavailable. Provide via session or BOHRIUM_ACCESS_KEY."
            )
        return cls(
            access_key=cred.access_key,
            project_id=cred.project_id,
            base_url=cred.base_url,
            credential_source=source,
            sandbox=sandbox,
            user_id=cred.user_id,
            user_no=cred.user_no,
        )
```

```python
"""matmaster/tools/builtin/bohrium_tool/tool.py"""
from matmaster.bohrium.credentials import credentials_from_env
from matmaster.bohrium.endpoints import get_bohrium_base_url
from matmaster.bohrium.runtime import get_runtime


def build_bohrium_context(*, session, require_project: bool = False) -> BohriumContext:
    runtime = get_runtime(session) if session is not None else None
    if runtime is not None:
        cred = runtime.credentials()
        source = "runtime"
    else:
        cred = credentials_from_env()
        if cred is None:
            raise BohriumError(
                "Bohrium credentials unavailable. Provide via session or BOHRIUM_ACCESS_KEY."
            )
        source = "env"
    ctx = BohriumContext.from_credentials(
        cred,
        sandbox=use_sandbox(),
        source=source,
    )
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
```

- [ ] **Step 4: Run the Bohrium builtin tool test slice**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/builtin/test_bohrium_tool_models.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_api.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_poll.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_download.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py \
  tests/matmaster/integration/test_bohrium_job_skill_submit.py -v
```

Expected:

- the selected Bohrium builtin tool tests `PASS`
- no production import remains from `matmaster.integration.runtime_bridge.models`
- sandbox/HPC endpoint, payload, machine, and artifact-download compatibility all remain unchanged

- [ ] **Step 5: Commit the Bohrium builtin tool migration**

```bash
git add \
  matmaster/tools/builtin/bohrium_tool/tool.py \
  matmaster/tools/builtin/bohrium_tool/models.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_models.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py
git commit -m "refactor: route Bohrium tool through runtime handle"
```

### Task 6: Extract calculation preflight and rewire `lazy_mcp.py`

**Files:**
- Create: `matmaster/mcp/calculation/__init__.py`
- Create: `matmaster/mcp/calculation/errors.py`
- Create: `matmaster/mcp/calculation/selectors.py`
- Create: `matmaster/mcp/calculation/config_env.py`
- Create: `matmaster/mcp/calculation/preflight.py`
- Modify: `matmaster/mcp/manager.py`
- Modify: `matmaster/tools/lazy_mcp.py`
- Modify: `tests/matmaster/mcp/test_manager.py`
- Modify: `tests/matmaster/tools/test_cache_mcp_schemas.py`
- Modify: `tests/matmaster/tools/test_lazy_mcp.py`
- Modify: `tests/matmaster/tools/test_lazy_mcp_actor_routing.py`

This task replaces the old adaptor plumbing rather than layering a second
registry on top of it:

- `manager.path_adaptor_servers` -> `manager.calculation_preflight_servers`
- `manager.path_adaptor_factory` -> `manager.calculation_preflight_factory`
- `tool_info["has_path_adaptor"]` -> `tool_info["has_calculation_preflight"]`
- `LazyMCPConnector._path_adaptors` -> `_calculation_preflights`
- `get_path_adaptor()` -> `get_calculation_preflight()`

Because `lazy_mcp.py` currently imports `CalculationPreflightError` at module
import time, Task 6 must land before Task 8 deletes
`matmaster.adaptors.calculation.path_adaptor`. Do not split those two tasks
across a partial rollout boundary.

- [ ] **Step 1: Write the failing preflight and `lazy_mcp` tests**

```python
"""tests/matmaster/mcp/calculation/test_preflight.py"""
from unittest.mock import MagicMock

from matmaster.bohrium.runtime import BohriumRuntimeHandle
from matmaster.mcp.calculation.errors import CalculationPreflightError
from matmaster.mcp.calculation.preflight import CalculationPreflight


def test_prepare_call_builds_submission_and_materializes_selected_inputs(monkeypatch):
    runtime = MagicMock(spec=BohriumRuntimeHandle)
    runtime.build_submission.return_value = MagicMock(
        executor={"type": "local"},
        storage={"type": "https"},
        submission_mode="sync",
    )
    runtime.materialize_input_path.side_effect = lambda value, **_: f"https://oss/{value}"

    preflight = CalculationPreflight(calculation_executors={"mat_sg": {"sync_tools": ["run"]}})
    args = {"input_path": "a.in"}
    schema = {"type": "object", "properties": {"input_path": {"type": "string", "format": "path"}}}

    resolved = preflight.prepare_call(
        workspace_path="/tmp/work",
        args=args,
        tool_name="mat_sg_run",
        remote_tool_name="run",
        server_name="mat_sg",
        input_schema=schema,
        tool_description="Args:\\n    input_path (Path): input file",
        runtime=runtime,
        session=None,
    )

    assert resolved["executor"] == {"type": "local"}
    assert resolved["storage"] == {"type": "https"}
    assert resolved["input_path"] == "https://oss/a.in"


def test_prepare_call_resolves_model_alias_before_path_materialization():
    runtime = MagicMock(spec=BohriumRuntimeHandle)
    runtime.build_submission.return_value = MagicMock(
        executor={"type": "dispatcher"},
        storage={"type": "https"},
        submission_mode="async",
    )
    runtime.materialize_input_path.side_effect = lambda value, **_: value

    preflight = CalculationPreflight(
        calculation_executors={
            "mat_sg": {
                "executor_map": {
                    "submit_run": {"type": "dispatcher", "machine": {"remote_profile": {}}}
                }
            }
        }
    )
    resolved = preflight.prepare_call(
        workspace_path="/tmp/work",
        args={"model_path": "DPA2.4-7M"},
        tool_name="mat_sg_submit_run",
        remote_tool_name="submit_run",
        server_name="mat_sg",
        input_schema={
            "type": "object",
            "properties": {"model_path": {"type": "string", "format": "path"}},
        },
        tool_description=(
            "Args:\\n"
            "    model_path (Path): model file. Aliases: "
            "{'DPA2.4-7M': 'https://oss/models/dpa-2.4-7M.pt'}"
        ),
        runtime=runtime,
        session=None,
    )

    assert resolved["model_path"] == "https://oss/models/dpa-2.4-7M.pt"
```

```python
"""tests/matmaster/tools/test_lazy_mcp.py"""
from unittest.mock import AsyncMock, MagicMock

from matmaster.tools.lazy_mcp import LazyMCPTool


async def test_lazy_mcp_uses_calculation_preflight_before_call():
    connector = MagicMock()
    preflight = MagicMock()
    preflight.prepare_call.return_value = {"executor": {"type": "local"}}
    connector.get_calculation_preflight = AsyncMock(return_value=preflight)
    connector.call_tool = AsyncMock(return_value=[{"text": "ok"}])
    tool = LazyMCPTool(
        server_name="mat_sg",
        tool_name="mat_sg_run",
        remote_tool_name="run",
        description="desc",
        input_schema={"type": "object"},
        connector=connector,
    )

    await tool.execute({})

    connector.get_calculation_preflight.assert_awaited_once_with("mat_sg")
    preflight.prepare_call.assert_called_once()
```

```python
"""tests/matmaster/mcp/test_manager.py"""
from unittest.mock import MagicMock

from matmaster.mcp.manager import MCPToolManager


def test_build_tools_marks_calculation_preflight_when_configured() -> None:
    manager = MCPToolManager()
    manager.calculation_preflight_servers = {"srv"}
    manager.calculation_preflight_factory = lambda: MagicMock()

    manager._build_tools(
        "srv",
        MagicMock(),
        [{"name": "run", "description": "desc", "input_schema": {"type": "object"}}],
    )

    tool_info = manager.tools_by_server["srv"]["srv_run"]
    assert tool_info["has_calculation_preflight"] is True
```

- [ ] **Step 2: Run the new preflight slice and expose the old adaptor coupling**

Run:

```bash
uv run pytest \
  tests/matmaster/mcp/calculation/test_preflight.py \
  tests/matmaster/mcp/test_manager.py \
  tests/matmaster/tools/test_lazy_mcp.py \
  tests/matmaster/tools/test_lazy_mcp_actor_routing.py -v
```

Expected:

- import errors for `matmaster.mcp.calculation`
- `LazyMCPTool` and `MCPToolManager` tests still refer to `path_adaptor`

- [ ] **Step 3: Move selector/config logic and introduce `CalculationPreflight`**

Move these helper families into the new client-side preflight layer without
changing their current behavior:

- sync-vs-async classification via `_effective_sync_tools()`
- per-tool executor lookup via `executor_map` fallback to server executor
- `path_params_by_tool` validation against dereferenced schema
- description-driven model alias resolution via `_resolve_model_aliases()`
- leaf-level path upload delegated to `runtime.materialize_input_path()`

```python
"""matmaster/mcp/calculation/errors.py"""
class CalculationPreflightError(RuntimeError):
    """Raised when calculation tool arguments cannot be prepared safely."""
```

```python
"""matmaster/mcp/calculation/preflight.py"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from matmaster.calculation_runtimes.types import SubmissionRequest
from matmaster.mcp.calculation.errors import CalculationPreflightError
from matmaster.mcp.calculation.selectors import collect_path_selectors, rewrite_selected_paths


class CalculationPreflight:
    def __init__(self, calculation_executors: dict[str, Any] | None = None) -> None:
        self.calculation_executors = calculation_executors or {}

    def prepare_call(
        self,
        *,
        workspace_path: str,
        args: dict[str, Any],
        tool_name: str,
        remote_tool_name: str,
        server_name: str,
        input_schema: dict[str, Any] | None,
        tool_description: str | None,
        runtime,
        session: Any,
    ) -> dict[str, Any]:
        if runtime is None:
            raise CalculationPreflightError(
                f"Calculation runtime unavailable for server {server_name}."
            )
        server_cfg = self.calculation_executors.get(server_name) or {}
        sync_tools = _effective_sync_tools(server_cfg)
        if remote_tool_name.startswith("submit_"):
            base_name = remote_tool_name[len("submit_") :]
            if base_name in sync_tools:
                raise CalculationPreflightError(
                    f"Tool '{tool_name}' is blocked: '{base_name}' is a sync tool."
                )
        is_async_tool = self._is_async_remote_tool(server_name, remote_tool_name)
        if is_async_tool and not remote_tool_name.startswith("submit_"):
            raise CalculationPreflightError(
                f"Async tool '{tool_name}' is blocked for LLM runtime. "
                f"Use submit interface: '{server_name}_submit_*'."
            )

        schema_selectors = collect_path_selectors(input_schema or {})
        desc_selectors = _path_keys_from_description(tool_description)
        config_selectors = self._path_selectors_from_tool_config(
            server_name, remote_tool_name, input_schema
        )
        path_selectors = schema_selectors | desc_selectors | config_selectors
        request = SubmissionRequest(
            executor_template=self._resolve_executor_template(
                server_name,
                remote_tool_name,
            ),
            needs_storage=True,
            submission_mode="async" if is_async_tool else "sync",
        )
        submission = runtime.build_submission(request)
        resolved = dict(args)
        resolved["executor"] = submission.executor
        resolved["storage"] = submission.storage
        top_level_path_keys = _top_level_path_keys(path_selectors)
        if top_level_path_keys and tool_description:
            resolved = _resolve_model_aliases(
                resolved,
                tool_description,
                top_level_path_keys,
            )
        if not path_selectors:
            return resolved
        workspace_root = Path(workspace_path).resolve()
        return rewrite_selected_paths(
            resolved,
            selectors=path_selectors,
            schema=input_schema,
            rewrite_leaf=lambda selector, value, schema_leaf: runtime.materialize_input_path(
                str(value),
                selector=selector,
                workspace_root=workspace_root,
                session=session,
            ),
        )
```

```python
"""matmaster/mcp/manager.py"""
self.calculation_preflight_servers: set[str] = set()
self.calculation_preflight_factory: Callable[[], Any] | None = None

needs_calculation_preflight = (
    self.calculation_preflight_servers
    and self.calculation_preflight_factory
    and name in self.calculation_preflight_servers
)

tool_dict: dict[str, Any] = {
    "name": prefixed_name,
    "description": description,
    "input_schema": tool_info.get("input_schema", {}),
    "remote_tool_name": original_name,
    "connection": conn,
    "has_calculation_preflight": bool(needs_calculation_preflight),
}
```

```python
"""matmaster/tools/lazy_mcp.py"""
from matmaster.bohrium.runtime import get_runtime
from matmaster.mcp.calculation.errors import CalculationPreflightError


preflight = await self._connector.get_calculation_preflight(self._server_name)
if preflight:
    session = getattr(self._connector, "session", None)
    runtime = get_runtime(session) if session is not None else None
    resolved_args = preflight.prepare_call(
        workspace_path=self._connector.workspace_path,
        args=arguments,
        tool_name=self._name,
        remote_tool_name=self._remote_tool_name,
        server_name=self._server_name,
        tool_description=self._static_description,
        input_schema=self._input_schema,
        runtime=runtime,
        session=session,
    )
```

```python
"""configure_mcp_manager() / LazyMCPConnector in matmaster/tools/lazy_mcp.py"""
if mcp_config.get("path_adaptor") == "calculation":
    calc_servers = mcp_config.get("calculation_servers")
    manager.calculation_preflight_servers = (
        set(calc_servers) if calc_servers else set(all_server_names or ())
    )
    from matmaster.mcp.calculation.preflight import CalculationPreflight

    manager.calculation_preflight_factory = lambda: CalculationPreflight(
        mcp_config.get("calculation_executors") or {}
    )

self._calculation_preflights: dict[str, Any] = {}

async def get_calculation_preflight(self, server_name: str) -> Any | None:
    manager = self._ensure_manager()
    if not (
        manager.calculation_preflight_factory
        and server_name in manager.calculation_preflight_servers
    ):
        return None
    if server_name not in self._calculation_preflights:
        self._calculation_preflights[server_name] = (
            manager.calculation_preflight_factory()
        )
    return self._calculation_preflights[server_name]


async def ensure_connection(self, server_name: str) -> dict[str, Any]:
    await self.ensure_actor(server_name)
    calculation_preflight = await self.get_calculation_preflight(server_name)
    return {"calculation_preflight": calculation_preflight}
```

- [ ] **Step 4: Run the selector, preflight, and `lazy_mcp` tests**

Run:

```bash
uv run pytest \
  tests/matmaster/mcp/calculation/test_preflight.py \
  tests/matmaster/mcp/calculation/test_selectors.py \
  tests/matmaster/mcp/calculation/test_config_env.py \
  tests/matmaster/mcp/test_manager.py \
  tests/matmaster/tools/test_cache_mcp_schemas.py \
  tests/matmaster/tools/test_lazy_mcp.py \
  tests/matmaster/tools/test_lazy_mcp_actor_routing.py -v
```

Expected:

- new tests `PASS`
- manager and cache-schema tests pass with `has_calculation_preflight`
- existing `lazy_mcp` actor-routing tests still pass with the preflight lookup rename

- [ ] **Step 5: Commit the preflight extraction**

```bash
git add \
  matmaster/mcp/calculation/__init__.py \
  matmaster/mcp/calculation/errors.py \
  matmaster/mcp/calculation/selectors.py \
  matmaster/mcp/calculation/config_env.py \
  matmaster/mcp/calculation/preflight.py \
  matmaster/mcp/manager.py \
  matmaster/tools/lazy_mcp.py \
  tests/matmaster/mcp/calculation/test_preflight.py \
  tests/matmaster/mcp/calculation/test_selectors.py \
  tests/matmaster/mcp/calculation/test_config_env.py \
  tests/matmaster/mcp/test_manager.py \
  tests/matmaster/tools/test_cache_mcp_schemas.py \
  tests/matmaster/tools/test_lazy_mcp.py \
  tests/matmaster/tools/test_lazy_mcp_actor_routing.py
git commit -m "refactor: move calculation preflight into MCP client namespace"
```

### Task 7: Move Bohrium jobs, OSS helpers, and config consumers onto the new namespaces

**Files:**
- Create: `matmaster/bohrium/oss.py`
- Create: `matmaster/bohrium/jobs.py`
- Create: `tests/matmaster/bohrium/test_jobs.py`
- Modify: `matmaster/core/exp.py`
- Modify: `matmaster/tools/cache_mcp_schemas.py`
- Modify: `evaluation/eval_tooling_snapshot.py`
- Modify: `tests/matmaster/tools/test_cache_mcp_schemas.py`
- Modify: `tests/matmaster/integration/test_runtime_credential_bridge_e2e.py`

- [ ] **Step 1: Write the failing namespace-consumer tests**

```python
"""tests/matmaster/tools/test_cache_mcp_schemas.py"""
from pathlib import Path

from matmaster.mcp.calculation.config_env import resolve_mcp_config_path


def test_resolve_mcp_config_path_is_importable_from_new_namespace(tmp_path: Path) -> None:
    config_file = tmp_path / "mcp_config.json"
    config_file.write_text("{}", encoding="utf-8")

    assert resolve_mcp_config_path(config_file) == config_file
```

```python
"""tests/matmaster/bohrium/test_jobs.py"""
from inspect import signature

from matmaster.bohrium import jobs as jobs_mod


def test_extract_bohr_job_id_keeps_numeric_suffix() -> None:
    assert jobs_mod._extract_bohr_job_id("123456/789") == "789"


def test_jobs_module_keeps_public_surface() -> None:
    public_names = [
        "RUNNING_STATUSES",
        "get_job_detail_raw",
        "get_file_token",
        "iterate_job_files",
        "download_job_file",
        "download_job_directory",
        "query_job_status",
        "get_job_results",
        "terminate_job",
    ]
    for name in public_names:
        assert hasattr(jobs_mod, name)
    assert "Running" in jobs_mod.RUNNING_STATUSES
    assert "bohr_job_id" in signature(jobs_mod.query_job_status).parameters
```

- [ ] **Step 2: Run the config and job tests to show the new modules are absent**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/test_cache_mcp_schemas.py \
  tests/matmaster/bohrium/test_jobs.py -v
```

Expected:

- import errors for `matmaster.bohrium.jobs`
- import errors for `matmaster.mcp.calculation.config_env`

- [ ] **Step 3: Move the old helpers into their new namespaces and update imports**

`matmaster/bohrium/jobs.py` is a full namespace move for the current public job
service surface, not just `_extract_bohr_job_id()`. The new module keeps:

- `RUNNING_STATUSES`
- `get_job_detail_raw()`
- `get_file_token()`
- `iterate_job_files()`
- `download_job_file()`
- `download_job_directory()`
- `query_job_status()`
- `get_job_results()`
- `terminate_job()`

The migration should preserve current signatures, especially the
`bohr_job_id`-first calling convention used by existing consumers and tests.

```python
"""matmaster/bohrium/jobs.py"""
from __future__ import annotations

import os
from typing import Any

from matmaster.bohrium.endpoints import get_bohrium_base_url
from matmaster.bohrium.runtime import get_runtime


def _openapi_host() -> str:
    return get_bohrium_base_url()


def _get_access_key(access_key: str | None = None, session: Any = None) -> str:
    if access_key:
        return access_key.strip()
    runtime = get_runtime(session) if session is not None else None
    if runtime is not None and runtime.credentials().access_key:
        return runtime.credentials().access_key
    env_ak = (os.getenv("BOHRIUM_ACCESS_KEY") or "").strip()
    if env_ak:
        return env_ak
    raise ValueError(
        "Bohrium credentials unavailable for current run. "
        "Provide via session or BOHRIUM_ACCESS_KEY env var."
    )
```

```python
"""matmaster/core/exp.py / matmaster/tools/cache_mcp_schemas.py / evaluation/eval_tooling_snapshot.py"""
from matmaster.mcp.calculation.config_env import resolve_mcp_config_path

# The config key remains `path_adaptor: calculation`; only the imported helper
# moves namespaces. `evaluation/eval_tooling_snapshot.py` does not need any
# `CalculationPreflight` import.
```

```python
"""matmaster/bohrium/oss.py"""
from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
_oss2: object | None = None


def _get_oss2():
    global _oss2
    if _oss2 is None:
        import oss2
        from oss2.credentials import EnvironmentVariableCredentialsProvider

        _oss2 = (oss2, EnvironmentVariableCredentialsProvider)
    return _oss2


def _object_key_last_segment(name: str) -> str:
    seg = Path(name).name.strip()
    if not seg or seg in (".", ".."):
        return "uploaded_file"
    return seg.replace("\\", "_").replace("/", "_")


def upload_file_to_oss(
    local_path: Path,
    workspace_root: Path,
    *,
    oss_prefix: str = "evomaster/calculation",
    object_basename: str | None = None,
) -> str:
    path = Path(local_path)
    if not path.is_absolute():
        path = (workspace_root / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    oss2_module, cred_provider = _get_oss2()
    endpoint = os.environ["OSS_ENDPOINT"]
    bucket_name = os.environ["OSS_BUCKET_NAME"]
    auth = oss2_module.ProviderAuth(cred_provider())
    bucket = oss2_module.Bucket(auth, endpoint, bucket_name)
    raw_name = object_basename if object_basename is not None else path.name
    filename = _object_key_last_segment(raw_name)
    prefix = oss_prefix.strip().strip("/")
    oss_key = f"{prefix}/{uuid.uuid4().hex}/{filename}"
    bucket.put_object(oss_key, path.read_bytes())
    host = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    return f"https://{bucket_name}.{host}/{oss_key}"


def _is_oss_or_http_url(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    v = value.strip().lower()
    return v.startswith("https://") or v.startswith("http://")


def download_oss_to_local(
    oss_url: str,
    workspace_root: Path,
    dest_relative_path: str | None = None,
) -> Path:
    if not _is_oss_or_http_url(oss_url):
        raise ValueError(f"Not an OSS/HTTP URL: {oss_url}")
    workspace_root = Path(workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    if dest_relative_path:
        dest = (workspace_root / dest_relative_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(oss_url)
        path = unquote(parsed.path or "")
        name = re.sub(r"[^\w.\-]", "_", path.split("/")[-1] or "downloaded_file")
        dest = workspace_root / (name or "downloaded_file")

    req = Request(oss_url, headers={"User-Agent": "MatMaster-Calculation/1.0"})
    with urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())
    return dest
```

```python
"""tests/matmaster/integration/test_runtime_credential_bridge_e2e.py"""
from types import SimpleNamespace

from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext


def _session_with_runtime() -> SimpleNamespace:
    session = SimpleNamespace(is_open=True)
    attach_runtime(
        session,
        BohriumRuntimeHandle(
            credentials=BohriumCredentials(
                access_key="e2e-ak",
                project_id=99,
                user_id=7,
                user_no="U001",
                base_url="https://openapi.test.dp.tech",
            ),
            execution=BohriumExecutionContext(
                session_type="ssh",
                execution_workdir="/share",
                remote_workspace_root="/share",
                remote_project_root="/share/.matmaster",
                node_id=1,
                node_ip="10.0.0.1",
                ssh_attached=True,
            ),
            execution_session=session,
        ),
    )
    return session
```

- [ ] **Step 4: Run the jobs/config slice plus one calculation integration smoke test**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/test_cache_mcp_schemas.py \
  tests/matmaster/bohrium/test_jobs.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py \
  tests/matmaster/integration/test_lazy_mcp_integration.py -v
```

Expected:

- new namespace tests `PASS`
- lazy MCP integration still loads calculation MCP server metadata successfully

- [ ] **Step 5: Commit the namespace consumer migration**

```bash
git add \
  matmaster/bohrium/oss.py \
  matmaster/bohrium/jobs.py \
  matmaster/core/exp.py \
  matmaster/tools/cache_mcp_schemas.py \
  evaluation/eval_tooling_snapshot.py \
  tests/matmaster/bohrium/test_jobs.py \
  tests/matmaster/tools/test_cache_mcp_schemas.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py
git commit -m "refactor: move Bohrium services into new namespaces"
```

### Task 8: Remove legacy modules and add structure-guard tests

**Files:**
- Modify: `src/services/agent_run_bohrium.py`
- Delete: `matmaster/integration/runtime_bridge/__init__.py`
- Delete: `matmaster/integration/runtime_bridge/resolver.py`
- Delete: `matmaster/integration/runtime_bridge/env_projector.py`
- Delete: `matmaster/integration/runtime_bridge/models.py`
- Delete: `matmaster/integration/runtime_bridge/adapters/bohrium.py`
- Delete: `matmaster/integration/bohrium_env.py`
- Delete: `matmaster/integration/bohrium_api.py`
- Delete: `matmaster/adaptors/calculation/__init__.py`
- Delete: `matmaster/adaptors/calculation/path_adaptor.py`
- Delete: `matmaster/adaptors/calculation/path_selectors.py`
- Delete: `matmaster/adaptors/calculation/env_config.py`
- Delete: `matmaster/adaptors/calculation/job_service.py`
- Delete: `matmaster/adaptors/calculation/oss_io.py`
- Delete: `tests/matmaster/adaptors/calculation/test_path_adaptor.py`
- Delete: `tests/matmaster/adaptors/calculation/test_path_selectors.py`
- Delete: `tests/matmaster/adaptors/calculation/test_env_config.py`
- Delete: `tests/matmaster/adaptors/calculation/test_job_service.py`
- Create: `tests/matmaster/architecture/test_bohrium_runtime_boundaries.py`
- Modify: `tests/matmaster/integration/test_runtime_bridge.py`
- Modify: `tests/matmaster/test_bohrium_env.py`

- [ ] **Step 1: Write the failing structure-guard tests**

```python
"""tests/matmaster/architecture/test_bohrium_runtime_boundaries.py"""
from pathlib import Path


def _iter_python_sources() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[3]
    return [
        path
        for path in repo_root.rglob("*.py")
        if "tests/" not in str(path).replace("\\", "/")
    ]


def test_production_code_no_longer_imports_runtime_bridge() -> None:
    offenders = []
    for path in _iter_python_sources():
        text = path.read_text(encoding="utf-8")
        if "matmaster.integration.runtime_bridge" in text:
            offenders.append(path)
    assert offenders == []


def test_production_code_no_longer_imports_bohrium_env() -> None:
    offenders = []
    for path in _iter_python_sources():
        text = path.read_text(encoding="utf-8")
        if "matmaster.integration.bohrium_env" in text:
            offenders.append(path)
    assert offenders == []


def test_production_code_no_longer_imports_calculation_adaptors() -> None:
    offenders = []
    for path in _iter_python_sources():
        text = path.read_text(encoding="utf-8")
        if "matmaster.adaptors.calculation" in text:
            offenders.append(path)
    assert offenders == []


def test_production_code_no_longer_reads_session_bohrium_credentials() -> None:
    offenders = []
    for path in _iter_python_sources():
        text = path.read_text(encoding="utf-8")
        if "._bohrium_credentials" in text:
            offenders.append(path)
    assert offenders == []
```

- [ ] **Step 2: Run the structure guards before deleting the old modules**

Run:

```bash
uv run pytest tests/matmaster/architecture/test_bohrium_runtime_boundaries.py -v
```

Expected:

- all four tests `FAIL`
- offenders include the legacy bridge modules and any still-unmigrated consumers

- [ ] **Step 3: Delete the old entrypoints and update the remaining tests**

Before enabling the `_bohrium_credentials` structure guard, remove the
migration-period dual write from `agent_run_bohrium.py`:

```python
"""src/services/agent_run_bohrium.py"""
# delete after Tasks 3-7 land:
# session._bohrium_credentials = dict(run_creds)
```

```bash
rm -rf matmaster/integration/runtime_bridge
rm -f matmaster/integration/bohrium_env.py
rm -f matmaster/integration/bohrium_api.py
rm -rf matmaster/adaptors/calculation
rm -f tests/matmaster/adaptors/calculation/test_path_adaptor.py
rm -f tests/matmaster/adaptors/calculation/test_path_selectors.py
rm -f tests/matmaster/adaptors/calculation/test_env_config.py
rm -f tests/matmaster/adaptors/calculation/test_job_service.py
```

```python
"""tests/matmaster/integration/test_runtime_bridge.py"""
def test_runtime_bridge_package_removed() -> None:
    import importlib.util

    assert importlib.util.find_spec("matmaster.integration.runtime_bridge") is None
```

```python
"""tests/matmaster/test_bohrium_env.py"""
def test_bohrium_env_module_removed() -> None:
    import importlib.util

    assert importlib.util.find_spec("matmaster.integration.bohrium_env") is None
```

- [ ] **Step 4: Run the final focused regression suite**

Run:

```bash
uv run pytest \
  tests/matmaster/bohrium \
  tests/matmaster/mcp/calculation \
  tests/matmaster/mcp/test_manager.py \
  tests/matmaster/tools/test_script_env.py \
  tests/matmaster/tools/test_lazy_mcp.py \
  tests/matmaster/tools/test_lazy_mcp_actor_routing.py \
  tests/matmaster/tools/builtin/test_bash_tool.py \
  tests/matmaster/tools/builtin/test_glob_tool.py \
  tests/matmaster/tools/builtin/test_grep_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_api.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_models.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_poll.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_download.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py \
  tests/matmaster/tools/test_cache_mcp_schemas.py \
  tests/matmaster/test_bohrium_setup_injection.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py \
  tests/matmaster/integration/test_bohrium_job_skill_submit.py \
  tests/matmaster/architecture/test_bohrium_runtime_boundaries.py -v
```

Expected:

- all selected tests `PASS`
- no import from deleted legacy modules remains

- [ ] **Step 5: Commit the legacy-module removal**

```bash
git add \
  src/services/agent_run_bohrium.py \
  tests/matmaster/architecture/test_bohrium_runtime_boundaries.py \
  tests/matmaster/integration/test_runtime_bridge.py \
  tests/matmaster/test_bohrium_env.py \
  matmaster/integration/runtime_bridge/__init__.py \
  matmaster/integration/runtime_bridge/resolver.py \
  matmaster/integration/runtime_bridge/env_projector.py \
  matmaster/integration/runtime_bridge/models.py \
  matmaster/integration/runtime_bridge/adapters/bohrium.py \
  matmaster/integration/bohrium_env.py \
  matmaster/integration/bohrium_api.py \
  matmaster/adaptors/calculation/__init__.py \
  matmaster/adaptors/calculation/path_adaptor.py \
  matmaster/adaptors/calculation/path_selectors.py \
  matmaster/adaptors/calculation/env_config.py \
  matmaster/adaptors/calculation/job_service.py \
  matmaster/adaptors/calculation/oss_io.py \
  tests/matmaster/adaptors/calculation/test_path_adaptor.py \
  tests/matmaster/adaptors/calculation/test_path_selectors.py \
  tests/matmaster/adaptors/calculation/test_env_config.py \
  tests/matmaster/adaptors/calculation/test_job_service.py
git commit -m "refactor: remove legacy Bohrium bridge modules"
```

## Coverage Check

- Runtime contracts, value objects, endpoint resolution: Task 1
- `constant.py` stays in `src/`, while runtime-side endpoint resolution preserves
  current `bohrium_api.py` semantics and does not force node-service host
  unification in the same refactor: Task 1
- Runtime handle, env, submission builder, session attach/get/detach: Task 2
- Startup-first registration and migration-period dual-write: Task 3
- Shell/script env consumer migration: Task 4
- `bohrium_tool` runtime-handle migration with sandbox-vs-HPC protocol compatibility preserved: Task 5
- Calculation preflight extraction and `lazy_mcp` migration: Task 6
- `bohrium_api.py`, `oss_io.py`, `job_service.py`, and config-consumer relocation: Task 7
- Legacy removal and structure guards: Task 8

The plan intentionally keeps `_bohrium_credentials` only during Tasks 3-7. Task 8 is the cleanup wave that removes it permanently.
