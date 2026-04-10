from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from matmaster.calculation_runtimes.types import SubmissionRequest

from .env import build_bohrium_env
from .errors import BohriumRuntimeNotInitialized
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
    _bohrium_runtime: BohriumRuntimeHandle | None


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

    def execution_session(self) -> Any | None:
        return self._execution_session

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

    def build_env(self) -> dict[str, str]:
        return build_bohrium_env(self.credentials())

    def build_submission(self, request: SubmissionRequest) -> BohriumSubmissionSpec:
        return BohriumSubmissionSpec(
            executor=build_executor(request.executor_template, self.credentials()),
            storage=(
                build_storage(self.credentials()) if request.needs_storage else None
            ),
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
        active_session = session if session is not None else self.execution_session()
        return materialize_bohrium_input_path(
            value,
            workspace_root=Path(workspace_root),
            session=active_session,
        )


def attach_runtime(
    session: SupportsBohriumRuntimeSlot, runtime: BohriumRuntimeHandle
) -> None:
    session._bohrium_runtime = runtime


def get_runtime(
    session: SupportsBohriumRuntimeSlot | None,
) -> BohriumRuntimeHandle | None:
    if session is None:
        return None
    runtime = getattr(session, "_bohrium_runtime", None)
    if isinstance(runtime, BohriumRuntimeHandle):
        return runtime
    return None


def require_runtime(session: SupportsBohriumRuntimeSlot) -> BohriumRuntimeHandle:
    runtime = get_runtime(session)
    if runtime is None:
        raise BohriumRuntimeNotInitialized(
            "Bohrium runtime is not initialized for this session."
        )
    return runtime


def detach_runtime(session: SupportsBohriumRuntimeSlot) -> None:
    if hasattr(session, "_bohrium_runtime"):
        del session._bohrium_runtime


def attach_local_bohrium_runtime_from_run_credentials(
    session: Any,
    run_creds: dict[str, Any],
) -> None:
    """Attach Bohrium credentials and a local placeholder runtime to the session.

    Used by API agent runs, DevShell, and evaluation so MCP calculation preflight
    (executor/storage injection) can call :meth:`BohriumRuntimeHandle.build_submission`.
    Does not create SSH or remote nodes.
    """
    from .credentials import normalize_bohrium_credentials
    from .endpoints import get_bohrium_base_url

    if not run_creds or session is None:
        return

    normalized = normalize_bohrium_credentials(
        {
            **run_creds,
            "base_url": run_creds.get("base_url") or get_bohrium_base_url(),
        }
    )
    execution = BohriumExecutionContext(
        session_type="local",
        execution_workdir="",
        remote_workspace_root="",
        remote_project_root="",
        node_id=None,
        node_ip=None,
        ssh_attached=False,
    )
    attach_runtime(
        session,
        BohriumRuntimeHandle(
            credentials=normalized,
            execution=execution,
            execution_session=session,
        ),
    )


def try_attach_local_bohrium_runtime_from_env(session: Any) -> None:
    """If ``credentials_from_env()`` succeeds, attach a local placeholder runtime.

    No-op when ``BOHRIUM_ACCESS_KEY`` (etc.) is unset. Intended for DevShell and
    offline evaluation runs aligned with production MCP tools.
    """
    from .credentials import credentials_from_env

    cred = credentials_from_env()
    if cred is None or session is None:
        return
    attach_local_bohrium_runtime_from_run_credentials(
        session,
        {
            "access_key": cred.access_key,
            "project_id": cred.project_id,
            "user_id": cred.user_id,
            "user_no": cred.user_no,
            "base_url": cred.base_url,
        },
    )
