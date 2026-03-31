"""Unified Playground -- physical environment preparation layer.

Responsibilities:
  - Workspace directory creation under ``run_dir/workspaces/{task_id}``
  - Session creation (Local/Docker/SSH) or reuse of injected override
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
import warnings
from pathlib import Path
from typing import Any

import yaml

from evomaster.agent.session.base import BaseSession
from evomaster.agent.session.local import LocalSession, LocalSessionConfig
from evomaster.config import ConfigManager
from evomaster.core.playground_session import PlaygroundSessionMixin
from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig


class Playground(PlaygroundSessionMixin):
    """Unified Playground for physical environment preparation.

    Usage::

        pg = Playground(config_path="configs/mat_master/config.yaml")
        ctx = pg.prepare({"run_dir": "/tmp/runs/run-001", "task_id": "t1"})
        # ... pass ctx to Exp.assemble() ...
        pg.cleanup()
    """

    def __init__(self, config_path: str | Path) -> None:
        config_path = Path(config_path)
        self.config_manager = ConfigManager(
            config_dir=config_path.parent,
            config_file=config_path.name,
        )
        self.config = self.config_manager.load()
        self.config_path = config_path
        self.logger = logging.getLogger(self.__class__.__name__)

        self.session: BaseSession | None = None
        self.agent: Any = None
        self._owns_session: bool = False
        # Needed by _setup_session to restore workspace after Bohrium detach
        self._prepare_run_meta: dict[str, Any] | None = None
        self._log_file_handler: logging.FileHandler | None = None
        self._log_file_stream = None
        self._playground_block: dict[str, Any] | None = None

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
            self.session = self._create_session_from_config()
            self._owns_session = True

        # Sync before open() so session uses the correct workspace path
        self._sync_workspace_to_session_config(workspace_path)

        if self._owns_session and not self.session.is_open:
            self.session.open()

        cache_area = self._resolve_cache_area(workspace_path)
        cache_area.mkdir(parents=True, exist_ok=True)

        self._setup_logging(run_meta)

        session_type = self._resolve_session_type()

        run_meta_copy = dict(run_meta)
        self._prepare_run_meta = run_meta_copy
        return PlaygroundContext(
            workdir=workspace_path,
            session_type=session_type,
            cache_area=cache_area,
            execution_workdir=str(workspace_path),
            env_vars=self._collect_env_vars(),
            archival=self._build_archival_config(),
            run_meta=run_meta_copy,
            session=self.session,
            config_dir=self.config_path.parent,
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
    # Private helpers
    # ------------------------------------------------------------------

    def _create_session_from_config(self) -> BaseSession:
        """Create a session instance based on ``config.session.type``."""
        raw = self.config.session
        session_dict = raw if isinstance(raw, dict) else {}
        session_type = session_dict.get('type', 'local')

        if session_type == 'local':
            cfg = LocalSessionConfig(**session_dict.get('local', {}))
            return LocalSession(config=cfg)

        if session_type == 'docker':
            from evomaster.agent.session.docker import (
                DockerSession,
                DockerSessionConfig,
            )

            cfg = DockerSessionConfig(**session_dict.get('docker', {}))
            return DockerSession(config=cfg)

        if session_type == 'ssh':
            from evomaster.agent.session.ssh import SSHSession, SSHSessionConfig

            cfg = SSHSessionConfig(**session_dict.get('ssh', {}))
            return SSHSession(config=cfg)

        raise ValueError(f"Unsupported session type: {session_type!r}")

    def _resolve_workspace_path(self, run_meta: dict[str, Any]) -> Path:
        """Determine workspace directory path from run_meta.

        - ``run_dir`` + ``task_id`` -> ``run_dir/workspaces/{task_id}``
        - ``run_dir`` only -> ``run_dir/workspace``
        """
        run_dir_raw = run_meta.get('run_dir')
        if run_dir_raw is None:
            # Fallback to a temp-like directory based on config
            return Path(self.config.workspace) / 'default'

        run_dir = Path(run_dir_raw)
        task_id = run_meta.get('task_id')
        if task_id:
            ws = run_dir / 'workspaces' / task_id
        else:
            ws = run_dir / 'workspace'
        return ws

    def _sync_workspace_to_session_config(self, workspace_path: Path) -> None:
        """Synchronize workspace_path and working_dir on session config."""
        if self.session is None:
            return
        ws_str = str(workspace_path.absolute())
        cfg = self.session.config
        try:
            cfg.workspace_path = ws_str
            cfg.working_dir = ws_str
        except Exception as e:
            self.logger.warning('Failed to sync workspace to session config: %s', e)

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

    def _get_playground_block(self) -> dict[str, Any]:
        """Return the ``playground`` sub-dict from config, or ``{}``."""
        if self._playground_block is None:
            raw = self.config.model_dump()
            block = raw.get('playground', None)
            self._playground_block = block if isinstance(block, dict) else {}
        return self._playground_block

    def _build_archival_config(self) -> WorkspaceArchivalConfig | None:
        """Build ``WorkspaceArchivalConfig`` from the YAML playground block.

        Returns ``None`` only when the config does not contain a
        ``playground.archival`` section at all.  When the block is present
        but ``enabled`` is ``False``, the config object is still returned
        so downstream code can inspect the archival settings.
        """
        archival_block = self._get_playground_block().get('archival', None)
        if not isinstance(archival_block, dict):
            return None
        return WorkspaceArchivalConfig(**archival_block)

    def _resolve_cache_area(self, workspace_path: Path) -> Path:
        """Resolve cache directory, preferring ``playground.cache_dir`` config.

        When ``playground.cache_dir`` is set in the YAML config:
          - If the path is relative, it is resolved under *workspace_path*.
          - If the path is absolute, it is used as-is.

        Falls back to ``workspace_path / ".cache"`` when not configured.
        """
        cache_dir = self._get_playground_block().get('cache_dir')
        if cache_dir:
            cache_path = Path(cache_dir)
            if not cache_path.is_absolute():
                cache_path = workspace_path / cache_path
            return cache_path
        return workspace_path / '.cache'

    def _collect_env_vars(self) -> dict[str, str]:
        """Collect environment variables to include in context."""
        return {}

    def _resolve_session_type(self) -> str:
        """Determine session type string from config."""
        session_dict = self.config.session
        if isinstance(session_dict, dict):
            return session_dict.get('type', 'local')
        return 'local'

    def attach_session(self, session: BaseSession) -> None:
        """Attach a new session; closes existing LocalSession to avoid handle leaks."""
        if (
            self.session is not None
            and self.session.is_open
            and isinstance(self.session, LocalSession)
            and not isinstance(session, LocalSession)
        ):
            try:
                self.session.close()
            except Exception as e:
                self.logger.warning('Error closing local session before attach: %s', e)
        super().attach_session(session)
        self._owns_session = True

    def _setup_session(self) -> None:
        """Create and open a session, restoring after Bohrium detach.

        Aligned with ``BasePlayground._setup_session``; called by
        ``cleanup_bohrium_after_run`` after ``detach_session()``.
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
        self.session = self._create_session_from_config()
        self._owns_session = True
        workspace_path = self._resolve_workspace_path(self._prepare_run_meta)
        self._sync_workspace_to_session_config(workspace_path)
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

        Checks config YAML existence, agents key presence, and emits
        evomaster deprecation warning.
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

            try:
                import evomaster  # noqa: F401

                warnings.warn(
                    'evomaster package is deprecated. Use matmaster instead.',
                    DeprecationWarning,
                    stacklevel=2,
                )
            except ImportError:
                pass

            self._init_done.set()
            self._logger.info('Playground config validation complete.')

    def get_or_create(self, session_id: str) -> Playground:
        """Get or create a Playground instance (thread-safe)."""
        with self._lock:
            if session_id in self._playgrounds:
                return self._playgrounds[session_id]
            config_path = self._config_dir / "config.yaml"
            pg = Playground(config_path=config_path)
            self._playgrounds[session_id] = pg
            return pg

    def release(self, session_id: str) -> None:
        """Remove from cache and call cleanup(). Thread-safe."""
        with self._lock:
            pg = self._playgrounds.pop(session_id, None)
        if pg:
            pg.cleanup()
