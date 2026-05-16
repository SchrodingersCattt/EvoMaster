"""Phase 2B shim delegating to matmaster.context.sources.tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.context.sources.tools import (
    format_active_mcp as _typed_format,
    resolve_declared_servers as _typed_declared,
    resolve_runnable_servers as _typed_runnable,
)

__all__ = [
    "resolve_declared_servers",
    "resolve_runnable_servers",
    "format_active_mcp",
]


def resolve_declared_servers(skills: Iterable[Any]) -> set[str]:
    return _typed_declared(skills)


def resolve_runnable_servers(
    skills: Iterable[Any],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: dict[str, list[dict[str, Any]]] | None = None,
) -> set[str]:
    return _typed_runnable(
        skills,
        legal_servers=legal_servers,
        schemas_by_server=schemas_by_server,
    )


def format_active_mcp(
    skills: Iterable[Any],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    return _typed_format(
        skills,
        legal_servers=legal_servers,
        schemas_by_server=schemas_by_server,
    )
