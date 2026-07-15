"""Stable Session proxy that materializes Bohrium SSH on first remote access."""

from __future__ import annotations

from typing import Any

from matmaster.bohrium.runtime import attach_runtime, get_runtime
from matmaster.types.cancellation import CancellationToken
from matmaster.types.runtime_ports import BohriumNodeAcquirer, BohriumNodeBinding
from matmaster.types.session import SessionFileStat
from matmaster.types.topology import SessionCapabilities


class _DeferredSessionConfig:
    __slots__ = ("timeout", "workspace_path")

    def __init__(self, workspace_path: str, timeout: int = 300) -> None:
        self.workspace_path = workspace_path
        self.timeout = timeout


class DeferredBohriumSession:
    """Session-shaped lazy proxy held by tools for the whole agent run.

    Cold-state metadata deliberately exposes no remote skill roots. This keeps
    user-instruction and skill discovery from becoming accidental Node-start
    triggers before the model asks to use a remote capability.
    """

    def __init__(
        self,
        acquirer: BohriumNodeAcquirer,
        *,
        workspace_path: str,
        timeout: int = 300,
    ) -> None:
        self._acquirer = acquirer
        self._workspace_path = workspace_path
        self._binding: BohriumNodeBinding | None = None
        self._cancel_token: CancellationToken | None = None
        self._is_open = True
        self.config = _DeferredSessionConfig(workspace_path, timeout)

        # Remote discovery is intentionally cold-safe. Once acquired these are
        # copied from the SSH session for callers that inspect them later.
        self.remote_project_root: str | None = None
        self.remote_user_skills_root: str | None = None
        self.remote_skill_roots: list[str] = []
        self.local_user_skills_root: str | None = None

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def capabilities(self) -> SessionCapabilities:
        return SessionCapabilities(
            shell_persistence="stateless",
            shell_input=False,
            file_ops="sftp",
            upload_support=True,
            exec_cancel=True,
        )

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        # The service-layer run cleanup owns SSH close and provider release.
        self._is_open = False

    def _ensure_binding(
        self,
        reason: str,
        cancel_token: CancellationToken | None = None,
    ) -> BohriumNodeBinding:
        binding = self._binding
        if binding is None:
            binding = self._acquirer.ensure_ready_sync(
                reason=reason,
                cancel_token=cancel_token or self._cancel_token,
            )
            self._binding = binding
            runtime = get_runtime(binding.session)
            if runtime is not None:
                attach_runtime(self, runtime)
            self.remote_project_root = getattr(
                binding.session, "remote_project_root", None
            )
            self.remote_user_skills_root = getattr(
                binding.session, "remote_user_skills_root", None
            )
            roots = getattr(binding.session, "remote_skill_roots", None)
            if isinstance(roots, (list, tuple, set)):
                self.remote_skill_roots = list(roots)
        return binding

    def _session(self, reason: str) -> Any:
        return self._ensure_binding(reason).session

    def exec_bash(
        self,
        command: str,
        timeout: int | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> dict[str, Any]:
        session = self._ensure_binding("session.exec_bash", cancel_token).session
        return session.exec_bash(
            command,
            timeout=timeout,
            cancel_token=cancel_token,
        )

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        return self._session("session.read_file").read_file(path, encoding=encoding)

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        self._session("session.write_file").write_file(
            path,
            content,
            encoding=encoding,
        )

    def path_exists(self, path: str) -> bool:
        return self._session("session.path_exists").path_exists(path)

    def is_file(self, path: str) -> bool:
        return self._session("session.is_file").is_file(path)

    def stat_file(self, path: str) -> SessionFileStat:
        return self._session("session.stat_file").stat_file(path)

    def download(self, path: str, timeout: int | None = None) -> bytes:
        return self._session("session.download").download(path, timeout=timeout)

    def upload_directory(
        self,
        local_dir: str,
        remote_dir: str,
        exclude: set[str] | None = None,
    ) -> None:
        self._session("session.upload_directory").upload_directory(
            local_dir,
            remote_dir,
            exclude=exclude,
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._session(f"session.{name}"), name)
