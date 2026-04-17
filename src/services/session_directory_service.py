"""Resolve per-run Bohrium remote working directories for chat sessions."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Literal

from src.services.sessions_service import ChatSessionsService

SessionDirectorySource = Literal["request", "session", "none"]


class SessionDirectoryError(Exception):
    def __init__(
        self,
        message: str,
        error_code: str,
        http_status: int = 400,
    ) -> None:
        super().__init__(message, error_code, http_status)
        self.message = message
        self.error_code = error_code
        self.http_status = http_status


@dataclass(frozen=True)
class ResolvedSessionDirectory:
    remote_workdir: str | None
    source: SessionDirectorySource
    bohrium_required: bool


def _blank_to_none(raw: str | None) -> str | None:
    # Treat whitespace-only strings as absent so blank input falls through to the next priority.
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def normalize_remote_share_path(raw: object) -> str:
    if not isinstance(raw, str):
        raise SessionDirectoryError(
            "directory must be a string",
            error_code="directory_invalid_type",
        )

    stripped = raw.strip()
    if "\0" in stripped:
        raise SessionDirectoryError(
            "directory contains invalid characters",
            error_code="directory_invalid_chars",
        )
    if not stripped.startswith("/"):
        raise SessionDirectoryError(
            "directory must be an absolute POSIX path",
            error_code="directory_must_be_absolute",
        )

    normalized = posixpath.normpath(stripped)
    if normalized != "/share" and not normalized.startswith("/share/"):
        raise SessionDirectoryError(
            "directory must be /share or a descendant of /share",
            error_code="directory_outside_share",
        )
    return normalized


def normalize_session_directory_for_storage(raw: str | None) -> str | None:
    selected = _blank_to_none(raw)
    if selected is None:
        return None
    return normalize_remote_share_path(selected)


class SessionDirectoryResolver:
    def __init__(self, sessions_service: ChatSessionsService) -> None:
        self._sessions_service = sessions_service

    def resolve(
        self,
        *,
        session_id: str,
        request_directory: str | None,
        request_directory_provided: bool,
    ) -> ResolvedSessionDirectory:
        if request_directory_provided:
            selected_request = _blank_to_none(request_directory)
            if selected_request is not None:
                return ResolvedSessionDirectory(
                    remote_workdir=normalize_remote_share_path(selected_request),
                    source="request",
                    bohrium_required=True,
                )

        row = self._sessions_service.get_session(session_id)
        session_directory = None
        if row:
            session_directory = _blank_to_none(row.get("session_directory"))
        if session_directory is None:
            return ResolvedSessionDirectory(
                remote_workdir=None,
                source="none",
                bohrium_required=False,
            )

        try:
            remote_workdir = normalize_remote_share_path(session_directory)
        except SessionDirectoryError as exc:
            raise SessionDirectoryError(
                "persistent session directory is invalid",
                error_code="session_directory_invalid",
                http_status=exc.http_status,
            ) from exc

        return ResolvedSessionDirectory(
            remote_workdir=remote_workdir,
            source="session",
            bohrium_required=True,
        )
