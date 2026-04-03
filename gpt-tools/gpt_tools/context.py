"""Shared execution context and state stores for GPT-style tools."""

from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .models import (
    AgentRunResult,
    BackgroundCommand,
    FetchedDocument,
    OutboundMessage,
    ReadSnapshot,
    SearchResult,
    SkillDefinition,
)


class ReadStateTracker:
    """Tracks whether a file was fully read before mutation."""

    def __init__(self) -> None:
        self._entries: dict[str, ReadSnapshot] = {}

    def record(
        self,
        path: Path,
        *,
        is_partial: bool,
        offset: int | None = None,
        limit: int | None = None,
        pages: str | None = None,
    ) -> None:
        resolved = path.resolve()
        self._entries[str(resolved)] = ReadSnapshot(
            path=str(resolved),
            mtime_ns=resolved.stat().st_mtime_ns,
            is_partial=is_partial,
            offset=offset,
            limit=limit,
            pages=pages,
        )

    def get(self, path: Path) -> ReadSnapshot | None:
        return self._entries.get(str(path.resolve()))

    def validate_full_fresh_read(self, path: Path) -> str | None:
        resolved = path.resolve()
        snapshot = self.get(resolved)
        if snapshot is None:
            return f"Error: file '{resolved}' requires a prior full read before mutation."
        if snapshot.is_partial:
            return (
                f"Error: file '{resolved}' was only partially read. "
                "A fresh full read is required before mutation."
            )
        current_mtime = resolved.stat().st_mtime_ns
        if current_mtime != snapshot.mtime_ns:
            return (
                f"Error: file '{resolved}' has changed since it was read. "
                "Please read it again to avoid writing against stale content."
            )
        return None


class TodoStore:
    """In-memory session-scoped todo store."""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def replace(self, items: list[dict[str, Any]]) -> None:
        self._items = [dict(item) for item in items]

    def clear(self) -> None:
        self._items = []

    def current_items(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._items]


class SkillRegistry:
    """Minimal skill registry for backend-only execution."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, definition: SkillDefinition) -> None:
        self._skills[definition.name] = definition

    def get(self, name: str) -> SkillDefinition | None:
        return self._skills.get(name)


WebFetcher = Callable[[str], FetchedDocument]
WebFetchSummarizer = Callable[[str, str], str]
WebSearchBackend = Callable[[str], list[SearchResult]]
AgentLauncher = Callable[..., AgentRunResult]
MessageRouter = Callable[[OutboundMessage], None]


@dataclass
class ToolContext:
    """Mutable session context shared by all standalone tools."""

    workspace_root: Path
    session_id: str = "default"
    agent_id: str | None = None
    web_fetcher: WebFetcher | None = None
    web_fetch_summarizer: WebFetchSummarizer | None = None
    web_search_backend: WebSearchBackend | None = None
    agent_launcher: AgentLauncher | None = None
    message_router: MessageRouter | None = None
    pending_mcp_servers: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).resolve()
        self.current_working_directory = self.workspace_root
        self.read_state = ReadStateTracker()
        self.todo_store = TodoStore()
        self.skill_registry = SkillRegistry()
        self.outbox: list[OutboundMessage] = []
        self.background_commands: dict[str, BackgroundCommand] = {}
        self.url_cache: dict[str, tuple[float, FetchedDocument]] = {}
        self.registry: Any | None = None

    def resolve_absolute_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if not path.is_absolute():
            raise ValueError("file_path must be an absolute path")
        return path.resolve()

    def resolve_directory(self, raw_path: str | None = None) -> Path:
        if raw_path is None:
            return self.current_working_directory
        path = Path(raw_path)
        if not path.is_absolute():
            path = (self.current_working_directory / path).resolve()
        else:
            path = path.resolve()
        return path

    def cache_web_document(
        self,
        url: str,
        document: FetchedDocument,
        *,
        ttl_seconds: int = 900,
    ) -> None:
        self.url_cache[url] = (time.time() + ttl_seconds, document)

    def get_cached_web_document(self, url: str) -> FetchedDocument | None:
        cached = self.url_cache.get(url)
        if cached is None:
            return None
        expires_at, document = cached
        if expires_at < time.time():
            self.url_cache.pop(url, None)
            return None
        return document

    def register_background_command(
        self,
        command: str,
        cwd: Path,
        log_path: Path,
        process: subprocess.Popen[str],
        *,
        description: str = "",
    ) -> BackgroundCommand:
        job_id = uuid.uuid4().hex[:12]
        record = BackgroundCommand(
            job_id=job_id,
            command=command,
            cwd=cwd,
            log_path=log_path,
            pid=process.pid,
            description=description,
        )
        self.background_commands[job_id] = record
        return record

    def set_current_working_directory(self, cwd: Path) -> None:
        self.current_working_directory = cwd.resolve()
