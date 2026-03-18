"""Planner file I/O abstraction layer.

Provides a unified interface for planner artifact persistence that works
transparently with both local filesystems and SSH remote containers.

In **local mode** (``LocalPlannerFileIO``), all operations delegate to
standard ``pathlib.Path`` methods.

In **SSH mode** (``SSHPlannerFileIO``), operations are forwarded to the
``SSHSession`` SFTP helpers (``read_file``, ``write_file``, ``path_exists``,
``exec_bash``).  This ensures planner state survives API/Worker pod
redeployments because the SSH container has its own persistent filesystem.
"""

from __future__ import annotations

import json
import logging
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evomaster.agent.session.ssh import SSHSession

logger = logging.getLogger('MatMaster.PlannerFileIO')


# ── Base class ────────────────────────────────────────────────────────────

class PlannerFileIO(ABC):
    """Abstract file I/O interface consumed by ``ResearchPlannerRuntimeMixin``."""

    @abstractmethod
    def mkdir(self, path: str) -> None:
        """Create *path* and all parents (``mkdir -p`` semantics)."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Return ``True`` if *path* exists."""

    @abstractmethod
    def read_text(self, path: str) -> str:
        """Return the full text content of *path* (UTF-8)."""

    @abstractmethod
    def write_text(self, path: str, content: str) -> None:
        """Write *content* to *path*, creating/overwriting as needed."""

    @abstractmethod
    def append_text(self, path: str, content: str) -> None:
        """Append *content* to *path*, creating the file if absent."""

    @abstractmethod
    def write_json(self, path: str, obj: Any) -> None:
        """Atomically write a JSON-serialisable *obj* to *path*."""

    @abstractmethod
    def read_json(self, path: str) -> Any:
        """Read and parse a JSON file at *path*."""


# ── Local implementation ──────────────────────────────────────────────────

class LocalPlannerFileIO(PlannerFileIO):
    """File I/O backed by the local filesystem (``pathlib.Path``)."""

    def mkdir(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding='utf-8')

    def write_text(self, path: str, content: str) -> None:
        Path(path).write_text(content, encoding='utf-8')

    def append_text(self, path: str, content: str) -> None:
        with Path(path).open('a', encoding='utf-8') as f:
            f.write(content)

    def write_json(self, path: str, obj: Any) -> None:
        p = Path(path)
        tmp = p.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
        shutil.move(tmp, p)

    def read_json(self, path: str) -> Any:
        with open(path, encoding='utf-8') as f:
            return json.load(f)


# ── SSH implementation ────────────────────────────────────────────────────

class SSHPlannerFileIO(PlannerFileIO):
    """File I/O backed by an ``SSHSession`` (SFTP + exec_bash).

    All paths are interpreted as **remote** paths on the SSH container.
    """

    def __init__(self, session: SSHSession) -> None:
        self._session = session
        self._mkdir_cache: set[str] = set()

    def mkdir(self, path: str) -> None:
        if path in self._mkdir_cache:
            return
        self._session.exec_bash(f'mkdir -p {path}', timeout=10)
        self._mkdir_cache.add(path)

    def exists(self, path: str) -> bool:
        return self._session.path_exists(path)

    def read_text(self, path: str) -> str:
        return self._session.read_file(path, encoding='utf-8')

    def write_text(self, path: str, content: str) -> None:
        self._session.write_file(path, content, encoding='utf-8')

    def append_text(self, path: str, content: str) -> None:
        # SFTP has no native append; read-then-write.
        existing = ''
        if self._session.path_exists(path):
            existing = self._session.read_file(path, encoding='utf-8')
        self._session.write_file(path, existing + content, encoding='utf-8')

    def write_json(self, path: str, obj: Any) -> None:
        content = json.dumps(obj, indent=2, ensure_ascii=False)
        self._session.write_file(path, content, encoding='utf-8')

    def read_json(self, path: str) -> Any:
        text = self._session.read_file(path, encoding='utf-8')
        return json.loads(text)


# ── Factory ───────────────────────────────────────────────────────────────

def create_planner_file_io(session: Any) -> PlannerFileIO:
    """Create the appropriate ``PlannerFileIO`` based on session type.

    Returns ``SSHPlannerFileIO`` when *session* is an ``SSHSession``,
    otherwise falls back to ``LocalPlannerFileIO``.
    """
    from evomaster.agent.session.ssh import SSHSession  # noqa: PLC0415

    if isinstance(session, SSHSession):
        logger.info('[PlannerFileIO] Using SSH-backed file I/O')
        return SSHPlannerFileIO(session)
    logger.info('[PlannerFileIO] Using local file I/O')
    return LocalPlannerFileIO()
