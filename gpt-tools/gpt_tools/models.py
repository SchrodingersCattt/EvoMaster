"""Shared models for the standalone GPT-style tool package."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolResult:
    """Structured tool execution result."""

    status: str = "success"
    content: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, content: str = "", **payload: Any) -> ToolResult:
        return cls(status="success", content=content, payload=payload)

    @classmethod
    def error(cls, content: str, **payload: Any) -> ToolResult:
        return cls(status="error", content=content, payload=payload)

    @classmethod
    def awaiting_input(cls, content: str, **payload: Any) -> ToolResult:
        return cls(status="awaiting_input", content=content, payload=payload)


def normalize_tool_result(raw: str | ToolResult | None) -> ToolResult:
    """Normalize a tool return value into ToolResult."""

    if isinstance(raw, ToolResult):
        return raw
    if raw is None:
        return ToolResult()
    content = str(raw)
    status = "error" if content.lstrip().startswith(("Error:", "Blocked:")) else "success"
    return ToolResult(status=status, content=content)


@dataclass(frozen=True)
class ToolDefinition:
    """Serializable tool metadata exposed to callers or ToolSearch."""

    name: str
    description: str
    input_schema: dict[str, Any]
    strict: bool = True
    defer_loading: bool = False
    search_hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FetchedDocument:
    """Fetched web document before prompt-aware summarization."""

    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    raw_bytes: bytes = b""
    redirect_url: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """Normalized web search result."""

    title: str
    link: str
    snippet: str


@dataclass(frozen=True)
class AgentRunResult:
    """Result returned by the standalone Agent tool."""

    status: str
    content: str
    payload: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


SkillRunner = Callable[[str, Any], str | ToolResult]


@dataclass(frozen=True)
class SkillDefinition:
    """Inline or executable skill definition."""

    name: str
    content: str
    runner: SkillRunner | None = None
    allowed_tools: tuple[str, ...] = ()
    mode: str = "inline"


@dataclass(frozen=True)
class OutboundMessage:
    """Normalized teammate message."""

    to: str
    message: str | dict[str, Any]
    summary: str | None = None


@dataclass
class BackgroundCommand:
    """Tracked background bash command."""

    job_id: str
    command: str
    cwd: Path
    log_path: Path
    pid: int
    description: str = ""


@dataclass(frozen=True)
class ReadSnapshot:
    """State recorded after a successful read."""

    path: str
    mtime_ns: int
    is_partial: bool
    offset: int | None = None
    limit: int | None = None
    pages: str | None = None
