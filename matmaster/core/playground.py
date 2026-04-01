"""Unified Playground -- physical environment preparation layer.

Responsibilities:
  - Workspace directory creation under ``run_dir/workspaces/{task_id}``
  - Session creation (Local/SSH) or reuse of injected override
  - Cache directory creation under the workspace
  - Run-level file logging setup
  - Building an immutable ``PlaygroundContext`` snapshot

Non-responsibilities (belong to Exp / Service layers):
  - MCP manager, Skill registry, Tool registry, LLM provider
  - Workspace archival upload (Service layer, Phase 5)
  - Run orchestration and quota management
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

from matmaster.sessions.local import LocalSession
from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig
from matmaster.types.session import Session, SSHSessionConfig


class Playground:
    """Unified Playground for physical environment preparation.

    Parameterized construction -- receives session_type, session_config,
    archival, workspace_base, cache_dir as explicit parameters.  No
    config file parsing; that is PlaygroundManager's responsibility.

    Usage::

        pg = Playground(
            session_type="local",
            session_config={"workspace_path": "/tmp/ws"},
        )
        ctx = pg.prepare({"run_dir": "/tmp/runs/run-001", "task_id": "t1"})
        # ... pass ctx to Exp.assemble() ...
        pg.cleanup()
    """

    def __init__(
        self,
        *,
        session_type: str = "local",
        session_config: dict[str, Any] | None = None,
        archival: WorkspaceArchivalConfig | None = None,
        workspace_base: str | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self._session_type = session_type
        self._session_config = session_config or {}
        self._archival = archival
        self._workspace_base = workspace_base
        self._cache_dir = cache_dir
        self.logger = logging.getLogger(self.__class__.__name__)

        # Kept directly writable (per Pitfall 3: agent_run_bohrium.py
        # does ``pg.session = ssh_session`` and ``pg._owns_session = False``)
        self.session: Session | None = None
        self.agent: Any = None
        self._owns_session: bool = False
        self._prepare_run_meta: dict[str, Any] | None = None
        self._log_file_handler: logging.FileHandler | None = None
        self._log_file_stream = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, run_meta: dict[str, Any]) -> PlaygroundContext:
        """Create workspace, session, logging and return a frozen context.

        Args:
            run_meta: Runtime metadata dict.  Recognised keys:
                - ``run_dir``: base directory for this run (required for
                  workspace/logging; str or Path)
                - ``task_id``: per-task identifier (optional; determines
                  workspace sub-path and log file name)
                - ``session_override``: caller-owned session to reuse
                  instead of creating a new one

        Returns:
            Immutable ``PlaygroundContext`` snapshot.
        """
        workspace_path = self._resolve_workspace_path(run_meta)
        workspace_path.mkdir(parents=True, exist_ok=True)

        session_override = run_meta.get('session_override')
        if session_override is not None:
            self.session = session_override
            self._owns_session = False
        else:
            # Merge resolved workspace_path into session_config before creation
            effective_config = {
                **self._session_config,
                "workspace_path": str(workspace_path),
                "working_dir": str(workspace_path),
            }
            self._session_config = effective_config
            self.session = self._create_session_from_config()
            self._owns_session = True

        if self._owns_session and self.session is not None and not self.session.is_open:
            self.session.open()

        cache_area = self._resolve_cache_area(workspace_path)
        cache_area.mkdir(parents=True, exist_ok=True)

        self._setup_logging(run_meta)

        run_meta_copy = dict(run_meta)
        self._prepare_run_meta = run_meta_copy
        return PlaygroundContext(
            workdir=workspace_path,
            session_type=self._session_type,
            cache_area=cache_area,
            execution_workdir=str(workspace_path),
            env_vars=self._collect_env_vars(),
            archival=self._archival,
            run_meta=run_meta_copy,
            session=self.session,
        )

    def cleanup(self) -> None:
        """Release resources owned by this Playground.

        - Closes the log file handler/stream if present.
        - Closes the session *only* when ``_owns_session`` is True.
        - Never touches MCP, skill, tool, or LLM resources.
        """
        self._teardown_log_handler()

        if self._owns_session and self.session is not None:
            try:
                self.session.close()
            except Exception:
                self.logger.warning('Error closing session', exc_info=True)

    # ------------------------------------------------------------------
    # Session management (inlined from evomaster mixin)
    # ------------------------------------------------------------------

    def attach_session(self, session: Session) -> None:
        """Attach a new session; closes existing non-local session to avoid handle leaks."""
        if (
            self.session is not None
            and self.session.is_open
            and not isinstance(self.session, LocalSession)
        ):
            try:
                self.session.close()
                self.logger.info('Previous session closed before attach')
            except Exception as e:
                self.logger.warning('Error closing previous session: %s', e)

        self.session = session
        self._owns_session = True

        if not self.session.is_open:
            self.session.open()
            self.logger.info('Attached session opened: %s', type(session).__name__)

        if self.agent is not None:
            self.agent.session = self.session
            self.logger.debug('Agent session reference updated')

    def attach_ssh_session(
        self,
        host: str,
        port: int = 22,
        username: str = 'root',
        password: str | None = None,
        key_file: str | None = None,
        working_dir: str = '/personal/workspace',
        session_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Create and attach an SSHSession from explicit credentials.

        Convenience wrapper around :meth:`attach_session` for the common
        case where the caller has ``(host, port, password)`` from an
        external container allocator (e.g. Bohrium).

        Args:
            session_id: When provided, the remote working directory becomes
                ``{working_dir}/{session_id}`` to isolate concurrent sessions.

        Returns:
            The opened SSHSession instance.
        """
        from matmaster.sessions.ssh import SSHSession

        if session_id:
            working_dir = f"{working_dir.rstrip('/')}/{session_id}"
        cfg = SSHSessionConfig(
            host=host,
            port=port,
            username=username,
            password=password,
            key_file=key_file,
            working_dir=working_dir,
            workspace_path=working_dir,
            **kwargs,
        )
        session = SSHSession(config=cfg)
        self.attach_session(session)
        self.logger.info('SSH workspace: %s', working_dir)
        return session

    def detach_session(self) -> None:
        """Close and remove the current session.

        After this call ``self.session`` is ``None``.  The caller (external
        backend) is responsible for releasing the underlying container.
        """
        if self.session is not None and self.session.is_open:
            if not isinstance(self.session, LocalSession):
                try:
                    self.session.close()
                    self.logger.info('Session detached and closed')
                except Exception as e:
                    self.logger.warning('Error closing session during detach: %s', e)

        self.session = None

        if self.agent is not None:
            self.agent.session = None
            self.logger.debug('Agent session reference cleared')

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _create_session_from_config(self) -> Session:
        """Create a session instance based on ``self._session_type``."""
        session_type = self._session_type
        session_config = self._session_config

        if session_type == 'local':
            workspace_path = session_config.get('workspace_path', '/workspace')
            timeout = session_config.get('timeout', 300)
            encoding = session_config.get('encoding', 'utf-8')
            return LocalSession(
                workspace_path=workspace_path, timeout=timeout, encoding=encoding
            )

        if session_type == 'ssh':
            from matmaster.sessions.ssh import SSHSession

            cfg = SSHSessionConfig(**session_config)
            return SSHSession(config=cfg)

        # Docker sessions are deprecated -- only local and ssh are supported.
        raise ValueError(
            f"Unsupported session type: {session_type!r}. "
            "Only 'local' and 'ssh' are supported."
        )

    def _resolve_workspace_path(self, run_meta: dict[str, Any]) -> Path:
        """Determine workspace directory path from run_meta.

        - ``run_dir`` + ``task_id`` -> ``run_dir/workspaces/{task_id}``
        - ``run_dir`` only -> ``run_dir/workspace``
        - No ``run_dir`` -> ``workspace_base/default``
        """
        run_dir_raw = run_meta.get('run_dir')
        if run_dir_raw is None:
            base = self._workspace_base or '/tmp/matmaster/workspaces'
            return Path(base) / 'default'

        run_dir = Path(run_dir_raw)
        task_id = run_meta.get('task_id')
        if task_id:
            return run_dir / 'workspaces' / task_id
        return run_dir / 'workspace'

    def _setup_logging(self, run_meta: dict[str, Any]) -> None:
        """Set up a file logger under ``<run_dir>/logs/{task_id}.log``.

        If ``run_dir`` is not provided, logging setup is skipped.
        """
        self._teardown_log_handler()

        run_dir_raw = run_meta.get('run_dir')
        if run_dir_raw is None:
            return

        run_dir = Path(run_dir_raw)
        logs_dir = run_dir / 'logs'
        logs_dir.mkdir(parents=True, exist_ok=True)

        task_id = run_meta.get('task_id')
        log_filename = f"{task_id}.log" if task_id else 'playground.log'
        log_file = logs_dir / log_filename

        stream = open(log_file, 'a', buffering=1, encoding='utf-8')  # noqa: SIM115
        self._log_file_stream = stream

        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(fmt)

        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        self._log_file_handler = handler

    def _teardown_log_handler(self) -> None:
        """Remove and close the current log file handler and stream."""
        if self._log_file_handler is not None:
            root_logger = logging.getLogger()
            root_logger.removeHandler(self._log_file_handler)
            self._log_file_handler.close()
            self._log_file_handler = None
        if self._log_file_stream is not None:
            try:
                self._log_file_stream.close()
            except Exception:
                pass
            self._log_file_stream = None

    def _resolve_cache_area(self, workspace_path: Path) -> Path:
        """Resolve cache directory, preferring ``self._cache_dir`` config.

        When ``self._cache_dir`` is set:
          - If the path is relative, it is resolved under *workspace_path*.
          - If the path is absolute, it is used as-is.

        Falls back to ``workspace_path / ".cache"`` when not configured.
        """
        if self._cache_dir:
            cache_path = Path(self._cache_dir)
            if not cache_path.is_absolute():
                cache_path = workspace_path / cache_path
            return cache_path
        return workspace_path / '.cache'

    def _collect_env_vars(self) -> dict[str, str]:
        """Collect environment variables to include in context."""
        return {}

    def _setup_session(self) -> None:
        """Create and open a session, restoring after Bohrium detach.

        Called by ``cleanup_bohrium_after_run`` after ``detach_session()``.
        """
        if self.session is not None:
            if not self.session.is_open:
                self.session.open()
            return
        if self._prepare_run_meta is None:
            self.logger.warning(
                '_setup_session: no prepare() metadata; cannot restore session'
            )
            return
        workspace_path = self._resolve_workspace_path(self._prepare_run_meta)
        effective_config = {
            **self._session_config,
            "workspace_path": str(workspace_path),
            "working_dir": str(workspace_path),
        }
        self._session_config = effective_config
        self.session = self._create_session_from_config()
        self._owns_session = True
        if not self.session.is_open:
            self.session.open()


class PlaygroundManager:
    """Lifecycle manager for Playground instances.

    Handles creation, caching, startup validation, and teardown.
    Does not touch Playground-internal environment prep (workspace, session, logging).

    Concurrency contract: callers must not concurrently call
    release() and get_or_create() for the same session_id.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._playgrounds: dict[str, Playground] = {}
        self._lock = threading.Lock()
        self._init_done = threading.Event()
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    def _config_dir(self) -> Path:
        """Return the matmaster_config directory."""
        return self._project_root / "matmaster_config"

    def validate_startup(self) -> None:
        """Fail-fast startup validation. Idempotent: repeated calls are no-ops.

        Checks config YAML existence and agents key presence.
        Does not cross-validate LLM config (that belongs to Exp layer).
        """
        if self._init_done.is_set():
            return
        with self._lock:
            if self._init_done.is_set():
                return

            config_path = self._config_dir / "config.yaml"
            if not config_path.exists():
                self._logger.warning('Config not found: %s', config_path)
            else:
                with open(config_path, encoding='utf-8') as f:
                    cfg = yaml.safe_load(f)
                if not isinstance(cfg, dict) or 'agents' not in cfg:
                    self._logger.warning("Config missing 'agents' key: %s", config_path)

            self._init_done.set()
            self._logger.info('Playground config validation complete.')

    def get_or_create(self, session_id: str) -> Playground:
        """Get or create a Playground instance (thread-safe).

        Reads config.yaml, extracts session/playground/workspace parameters,
        and passes them to Playground's parameterized constructor.
        """
        with self._lock:
            if session_id in self._playgrounds:
                return self._playgrounds[session_id]
            raw_config = self._load_raw_config()
            session_block = raw_config.get('session', {})
            if not isinstance(session_block, dict):
                session_block = {}
            session_type = session_block.get('type', 'local')
            session_config = session_block.get(session_type, {})
            if not isinstance(session_config, dict):
                session_config = {}
            playground_block = raw_config.get('playground', {})
            if not isinstance(playground_block, dict):
                playground_block = {}
            archival = self._build_archival(playground_block)
            pg = Playground(
                session_type=session_type,
                session_config=session_config,
                archival=archival,
                workspace_base=raw_config.get('workspace'),
                cache_dir=playground_block.get('cache_dir'),
            )
            self._playgrounds[session_id] = pg
            return pg

    def release(self, session_id: str) -> None:
        """Remove from cache and call cleanup(). Thread-safe."""
        with self._lock:
            pg = self._playgrounds.pop(session_id, None)
        if pg:
            pg.cleanup()

    def _load_raw_config(self) -> dict[str, Any]:
        """Load config.yaml as a raw dict. Returns {} if missing."""
        config_path = self._config_dir / "config.yaml"
        if not config_path.exists():
            return {}
        with open(config_path, encoding='utf-8') as f:
            return yaml.safe_load(f) or {}

    def _build_archival(
        self, playground_block: dict[str, Any]
    ) -> WorkspaceArchivalConfig | None:
        """Build WorkspaceArchivalConfig from playground config block."""
        archival_block = playground_block.get('archival')
        if not isinstance(archival_block, dict):
            return None
        return WorkspaceArchivalConfig(**archival_block)
