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

from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig


class Playground:
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

        # Session state
        self.session: BaseSession | None = None
        self._owns_session: bool = False

        # Logging state
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
        # 1. Workspace -- resolve path first (needed before session.open)
        workspace_path = self._resolve_workspace_path(run_meta)
        workspace_path.mkdir(parents=True, exist_ok=True)

        # 2. Session -- reuse override or create from config
        session_override = run_meta.get("session_override")
        if session_override is not None:
            self.session = session_override
            self._owns_session = False
        else:
            self.session = self._create_session_from_config()
            self._owns_session = True

        # 3. Sync workspace onto session config BEFORE open() so that
        #    session.open() / env.setup() uses the correct workspace path
        #    instead of the default /workspace from SessionConfig.
        self._sync_workspace_to_session_config(workspace_path)

        if self._owns_session and not self.session.is_open:
            self.session.open()

        # 4. Cache area -- prefer playground.cache_dir from config
        cache_area = self._resolve_cache_area(workspace_path)
        cache_area.mkdir(parents=True, exist_ok=True)

        # 5. Logging
        self._setup_logging(run_meta)

        # 6. Determine session type string
        session_type = self._resolve_session_type()

        # 7. Build and return frozen context
        return PlaygroundContext(
            workdir=workspace_path,
            session_type=session_type,
            cache_area=cache_area,
            env_vars=self._collect_env_vars(),
            archival=self._build_archival_config(),
            run_meta=dict(run_meta),
            session=self.session,
            config_dir=self.config_path.parent,
        )

    def cleanup(self) -> None:
        """Release resources owned by this Playground.

        - Closes the log file handler/stream if present.
        - Closes the session *only* when ``_owns_session`` is True.
        - Never touches MCP, skill, tool, or LLM resources.
        """
        # Release log handler
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

        # Close session only if we own it
        if self._owns_session and self.session is not None:
            try:
                self.session.close()
            except Exception:
                self.logger.warning("Error closing session", exc_info=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _create_session_from_config(self) -> BaseSession:
        """Create a session instance based on ``config.session.type``."""
        session_dict = self.config.session
        session_type = "local"
        if isinstance(session_dict, dict):
            session_type = session_dict.get("type", "local")

        if session_type == "local":
            local_cfg_dict = (
                session_dict.get("local", {}) if isinstance(session_dict, dict) else {}
            )
            cfg = LocalSessionConfig(**local_cfg_dict)
            return LocalSession(config=cfg)

        if session_type == "docker":
            from evomaster.agent.session.docker import DockerSession, DockerSessionConfig

            docker_cfg_dict = (
                session_dict.get("docker", {})
                if isinstance(session_dict, dict)
                else {}
            )
            cfg = DockerSessionConfig(**docker_cfg_dict)
            return DockerSession(config=cfg)

        if session_type == "ssh":
            from evomaster.agent.session.ssh import SSHSession, SSHSessionConfig

            ssh_cfg_dict = (
                session_dict.get("ssh", {}) if isinstance(session_dict, dict) else {}
            )
            cfg = SSHSessionConfig(**ssh_cfg_dict)
            return SSHSession(config=cfg)

        raise ValueError(f"Unsupported session type: {session_type!r}")

    def _resolve_workspace_path(self, run_meta: dict[str, Any]) -> Path:
        """Determine workspace directory path from run_meta.

        - ``run_dir`` + ``task_id`` -> ``run_dir/workspaces/{task_id}``
        - ``run_dir`` only -> ``run_dir/workspace``
        """
        run_dir_raw = run_meta.get("run_dir")
        if run_dir_raw is None:
            # Fallback to a temp-like directory based on config
            return Path(self.config.workspace) / "default"

        run_dir = Path(run_dir_raw)
        task_id = run_meta.get("task_id")
        if task_id:
            ws = run_dir / "workspaces" / task_id
        else:
            ws = run_dir / "workspace"
        return ws

    def _sync_workspace_to_session_config(self, workspace_path: Path) -> None:
        """Synchronize workspace_path and working_dir on session config.

        Both ``workspace_path`` and ``working_dir`` fields are updated to
        prevent inconsistency between tool execution directory and file
        tree directory.
        """
        ws_str = str(workspace_path.absolute())
        if self.session is None:
            return

        cfg = self.session.config
        if hasattr(cfg, "workspace_path"):
            # Pydantic model -- attempt direct setattr (SessionConfig is
            # not frozen, so this works for Local/Docker/SSH configs).
            try:
                cfg.workspace_path = ws_str
            except Exception:
                pass
        if hasattr(cfg, "working_dir"):
            try:
                cfg.working_dir = ws_str
            except Exception:
                pass

    def _setup_logging(self, run_meta: dict[str, Any]) -> None:
        """Set up a file logger under ``<run_dir>/logs/{task_id}.log``.

        If ``run_dir`` is not provided, logging setup is skipped.
        """
        # Release previous handler if any
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

        run_dir_raw = run_meta.get("run_dir")
        if run_dir_raw is None:
            return

        run_dir = Path(run_dir_raw)
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        task_id = run_meta.get("task_id")
        log_filename = f"{task_id}.log" if task_id else "playground.log"
        log_file = logs_dir / log_filename

        # Line-buffered file stream for real-time tail
        stream = open(log_file, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        self._log_file_stream = stream

        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        fmt = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(fmt)

        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        self._log_file_handler = handler

    def _build_archival_config(self) -> WorkspaceArchivalConfig | None:
        """Build ``WorkspaceArchivalConfig`` from the YAML playground block.

        Returns ``None`` only when the config does not contain a
        ``playground.archival`` section at all.  When the block is present
        but ``enabled`` is ``False``, the config object is still returned
        so downstream code can inspect the archival settings.
        """
        raw = self.config.model_dump()
        playground_block = raw.get("playground", None)
        if not isinstance(playground_block, dict):
            return None

        archival_block = playground_block.get("archival", None)
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
        raw = self.config.model_dump()
        playground_block = raw.get("playground", None)
        if isinstance(playground_block, dict):
            cache_dir = playground_block.get("cache_dir")
            if cache_dir:
                cache_path = Path(cache_dir)
                if not cache_path.is_absolute():
                    cache_path = workspace_path / cache_path
                return cache_path
        return workspace_path / ".cache"

    def _collect_env_vars(self) -> dict[str, str]:
        """Collect environment variables to include in context.

        Currently returns an empty dict; future phases may populate
        from session env or config.
        """
        return {}

    def _resolve_session_type(self) -> str:
        """Determine session type string from config."""
        session_dict = self.config.session
        if isinstance(session_dict, dict):
            return session_dict.get("type", "local")
        return "local"


class PlaygroundManager:
    """Playground 实例的生命周期管理器。

    职责：创建、缓存、启动验证、销毁。
    不涉及 Playground 内部的物理环境准备逻辑（workspace、session、logging）。

    并发前置条件：调用方保证同一 session_id 不会并发执行
    release() 和 get_or_create()。
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._playgrounds: dict[str, Playground] = {}
        self._lock = threading.Lock()
        self._init_done = threading.Event()
        self._logger = logging.getLogger(self.__class__.__name__)

    def validate_startup(self) -> None:
        """启动时快速失败验证。幂等：重复调用直接跳过。

        检查内容：
        - config YAML 文件存在性（mat_master、minimal）
        - config 中 agents key 存在性
        - evomaster 废弃警告

        不检查：LLM 配置交叉校验（属于 Exp 层职责）。
        """
        if self._init_done.is_set():
            return

        for pg_type in ("mat_master", "minimal"):
            config_path = self._project_root / "configs" / pg_type / "config.yaml"
            if not config_path.exists():
                self._logger.warning("Config not found: %s", config_path)
                continue
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            if not isinstance(cfg, dict) or "agents" not in cfg:
                self._logger.warning(
                    "Config missing 'agents' key: %s", config_path
                )

        # Deprecation warnings for old modules (per D-02)
        try:
            import evomaster  # noqa: F401

            warnings.warn(
                "evomaster package is deprecated. Use matmaster instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        except ImportError:
            pass

        self._init_done.set()
        self._logger.info("Playground config validation complete.")

    def get_or_create(
        self, session_id: str, playground_type: str = "mat_master"
    ) -> Playground:
        """线程安全地获取或创建 Playground 实例。

        Raises:
            ValueError: playground_type == "x_master" 时拒绝。
        """
        if playground_type == "x_master":
            raise ValueError(
                "x_master playground_type is not supported in the new pipeline"
            )
        with self._lock:
            if session_id in self._playgrounds:
                return self._playgrounds[session_id]
            config_path = (
                self._project_root / "configs" / playground_type / "config.yaml"
            )
            pg = Playground(config_path=config_path)
            self._playgrounds[session_id] = pg
            return pg

    def release(self, session_id: str) -> None:
        """从缓存移除并调用 cleanup()。线程安全。"""
        with self._lock:
            pg = self._playgrounds.pop(session_id, None)
        if pg:
            pg.cleanup()
