# Bohrium Runtime Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Bohrium integration around a single runtime handle registered during `agent_run_bohrium`, migrate every downstream consumer to that handle, and delete `runtime_bridge`, `bohrium_env`, and `adaptors/calculation`.

**Architecture:** Introduce a focused `matmaster/bohrium/` package as the only production implementation of the calculation runtime, with a thin `matmaster/calculation_runtimes/` contract layer for future non-Bohrium backends. Keep calculation MCP request understanding inside `matmaster/mcp/calculation/`, but make it consume a runtime handle for env, submission building, and path materialization. Migrate in startup-first order: core contracts, runtime registration, shell/script consumers, Bohrium builtin tool, MCP preflight and `lazy_mcp`, then remove legacy modules and add structure guards.

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
  Purpose: normalize raw credential mappings into `BohriumCredentials`.
- `matmaster/bohrium/env.py`
  Purpose: project runtime credentials into `BOHRIUM_*` environment variables.
- `matmaster/bohrium/executor.py`
  Purpose: inject normalized credentials into dispatcher/local executor templates.
- `matmaster/bohrium/storage.py`
  Purpose: build HTTPS storage payloads for calculation submissions.
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
  Purpose: define small request/response carrier types shared by preflight and runtime implementations.
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
  Purpose: switch shell env injection from `build_service_env()` to `require_runtime(session).build_env()`.
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
  Purpose: verify startup registration behavior and the migration-period dual-write.
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
- `tests/matmaster/tools/test_lazy_mcp.py`
  Purpose: verify new calculation preflight factory and call path.
- `tests/matmaster/tools/test_lazy_mcp_actor_routing.py`
  Purpose: verify connector preflight lookup still happens per server.
- `tests/matmaster/mcp/test_manager.py`
  Purpose: rename manager-side adaptor state to calculation preflight state and preserve tool metadata filtering behavior.
- `tests/matmaster/integration/test_runtime_credential_bridge_e2e.py`
  Purpose: rewrite bridge-oriented end-to-end coverage so Bohrium tool, preflight, and jobs all resolve credentials through the runtime handle.

### Existing files to reference while implementing

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


def test_bohrium_service_env_defaults_to_test(monkeypatch) -> None:
    monkeypatch.delenv("SERVICE_ENV", raising=False)
    assert get_bohrium_service_env() == "test"
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


class CalculationRuntime(Protocol):
    def build_env(self) -> dict[str, str]: ...

    def execution(self) -> Any: ...

    def build_submission(self, request: Any) -> Any: ...

    def materialize_input_path(self, *args: Any, **kwargs: Any) -> str: ...
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
```

- [ ] **Step 2: Run tests to confirm runtime helpers do not exist yet**

Run:

```bash
uv run pytest tests/matmaster/bohrium/test_runtime.py -v
```

Expected:

- import errors for `matmaster.bohrium.runtime`
- missing `SubmissionRequest` type

- [ ] **Step 3: Implement runtime data flow and submission helpers**

```python
"""matmaster/calculation_runtimes/types.py"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SubmissionRequest:
    executor_template: dict[str, Any] | None
    needs_storage: bool
    submission_mode: str
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
"""matmaster/bohrium/runtime.py"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from matmaster.calculation_runtimes.types import SubmissionRequest

from .env import build_bohrium_env
from .executor import build_executor
from .storage import build_storage
from .types import (
    BohriumCredentials,
    BohriumExecutionContext,
    BohriumRuntimeSnapshot,
    BohriumSubmissionSpec,
)


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
        return BohriumRuntimeSnapshot(**self._execution.__dict__)

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

    def materialize_input_path(self, value: str, **_: Any) -> str:
        return value


def attach_runtime(session: Any, runtime: BohriumRuntimeHandle) -> None:
    session._bohrium_runtime = runtime


def get_runtime(session: Any) -> BohriumRuntimeHandle | None:
    return getattr(session, "_bohrium_runtime", None)


def require_runtime(session: Any) -> BohriumRuntimeHandle:
    runtime = get_runtime(session)
    if runtime is None:
        raise RuntimeError("Bohrium runtime is not initialized for this session.")
    return runtime


def detach_runtime(session: Any) -> None:
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

- [ ] **Step 1: Write the failing startup registration tests**

```python
"""tests/matmaster/test_bohrium_setup_injection.py"""
from types import SimpleNamespace

from src.services.agent_run_bohrium import _apply_run_credentials_to_session
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
from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
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

### Task 4: Migrate shell and script env consumers to `require_runtime(session).build_env()`

**Files:**
- Modify: `matmaster/tools/script_env.py`
- Modify: `matmaster/tools/builtin/bash_tool.py`
- Modify: `matmaster/tools/builtin/glob_tool.py`
- Modify: `matmaster/tools/builtin/grep_tool.py`
- Modify: `tests/matmaster/tools/test_script_env.py`
- Modify: `tests/matmaster/tools/builtin/test_bash_tool.py`
- Modify: `tests/matmaster/tools/builtin/test_glob_tool.py`
- Modify: `tests/matmaster/tools/builtin/test_grep_tool.py`

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
        "matmaster.tools.builtin.bash_tool.require_runtime",
        lambda _session: runtime,
    )

    tool = BashTool(session=session)
    result = tool._execute({"command": "echo ok"})

    assert result.endswith("[Command finished with exit code 0]")
```

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
from matmaster.bohrium.runtime import get_runtime, require_runtime


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
```

```python
"""matmaster/tools/builtin/glob_tool.py"""
from matmaster.bohrium.runtime import get_runtime


runtime = get_runtime(self.session)
env = runtime.build_env() if runtime is not None else {}
```

```python
"""matmaster/tools/builtin/grep_tool.py"""
from matmaster.bohrium.runtime import get_runtime


runtime = get_runtime(self.session)
env = runtime.build_env() if runtime is not None else {}
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
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_models.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py`
- Modify: `tests/matmaster/tools/builtin/test_bohrium_tool.py`

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
```

- [ ] **Step 2: Run the Bohrium builtin tool tests to expose bridge/model coupling**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/builtin/test_bohrium_tool_models.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py -v
```

Expected:

- model test fails because `BohriumContext.from_credentials()` does not exist
- helper test fails because `build_bohrium_context()` still imports `resolve_bohrium_credentials`

- [ ] **Step 3: Replace bridge-facing code with runtime-facing code**

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
        cls, cred: BohriumCredentials, *, sandbox: bool
    ) -> "BohriumContext":
        if not cred.access_key:
            raise BohriumCredentialError(
                "Bohrium credentials unavailable. Provide via session or BOHRIUM_ACCESS_KEY."
            )
        return cls(
            access_key=cred.access_key,
            project_id=cred.project_id,
            base_url=cred.base_url,
            credential_source="runtime",
            sandbox=sandbox,
            user_id=cred.user_id,
            user_no=cred.user_no,
        )
```

```python
"""matmaster/tools/builtin/bohrium_tool/tool.py"""
from matmaster.bohrium.runtime import require_runtime


def build_bohrium_context(*, session, require_project: bool = False) -> BohriumContext:
    runtime = require_runtime(session)
    ctx = BohriumContext.from_credentials(
        runtime.credentials(),
        sandbox=use_sandbox(),
    )
    if require_project and ctx.project_id <= 0:
        raise BohriumError(
            "Bohrium project ID unavailable. Provide via session or BOHRIUM_PROJECT_ID."
        )
    return ctx
```

- [ ] **Step 4: Run the Bohrium builtin tool test slice**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/builtin/test_bohrium_tool_models.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py -v
```

Expected:

- the selected Bohrium builtin tool tests `PASS`
- no production import remains from `matmaster.integration.runtime_bridge.models`

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
        server_name="mat_sg",
        input_schema=schema,
        tool_description="Args:\\n    input_path (Path): input file",
        runtime=runtime,
        session=None,
    )

    assert resolved["executor"] == {"type": "local"}
    assert resolved["storage"] == {"type": "https"}
    assert resolved["input_path"] == "https://oss/a.in"
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
        path_selectors = collect_path_selectors(input_schema or {})
        request = SubmissionRequest(
            executor_template=self.calculation_executors.get(server_name, {}).get("executor"),
            needs_storage=True,
            submission_mode="sync",
        )
        submission = runtime.build_submission(request)
        resolved = dict(args)
        resolved["executor"] = submission.executor
        resolved["storage"] = submission.storage
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
from matmaster.bohrium.jobs import _extract_bohr_job_id


def test_extract_bohr_job_id_keeps_numeric_suffix() -> None:
    assert _extract_bohr_job_id("123456/789") == "789"
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

```python
"""matmaster/bohrium/jobs.py"""
from __future__ import annotations

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
    raise ValueError(
        "Bohrium credentials unavailable for current run. "
        "Provide via session or BOHRIUM_ACCESS_KEY env var."
    )
```

```python
"""matmaster/core/exp.py / matmaster/tools/cache_mcp_schemas.py / evaluation/eval_tooling_snapshot.py"""
from matmaster.mcp.calculation.config_env import resolve_mcp_config_path
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
  tests/matmaster/tools/builtin/test_bohrium_tool_models.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_helpers.py \
  tests/matmaster/tools/test_cache_mcp_schemas.py \
  tests/matmaster/test_bohrium_setup_injection.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py \
  tests/matmaster/architecture/test_bohrium_runtime_boundaries.py -v
```

Expected:

- all selected tests `PASS`
- no import from deleted legacy modules remains

- [ ] **Step 5: Commit the legacy-module removal**

```bash
git add -A
git commit -m "refactor: remove legacy Bohrium bridge modules"
```

## Coverage Check

- Runtime contracts, value objects, endpoint resolution: Task 1
- Runtime handle, env, submission builder, session attach/get/detach: Task 2
- Startup-first registration and migration-period dual-write: Task 3
- Shell/script env consumer migration: Task 4
- `bohrium_tool` runtime-handle migration: Task 5
- Calculation preflight extraction and `lazy_mcp` migration: Task 6
- `bohrium_api.py`, `oss_io.py`, `job_service.py`, and config-consumer relocation: Task 7
- Legacy removal and structure guards: Task 8

The plan intentionally keeps `_bohrium_credentials` only during Tasks 3-7. Task 8 is the cleanup wave that removes it permanently.
