from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .errors import BohriumPathError
from .models import BohriumDownloadTarget, BohriumInputSource

_REMOTE_SHARE_PREFIXES = ("/share/", "/personal/")


def _is_remote_share(path: str) -> bool:
    return any(
        path.startswith(prefix) or path == prefix.rstrip("/")
        for prefix in _REMOTE_SHARE_PREFIXES
    )


def _normalize_local_path(raw_path: str, workdir: Path | None) -> str:
    stripped = raw_path.strip()
    if Path(stripped).is_absolute():
        return str(Path(stripped))
    base = workdir or Path(".")
    return str((base / stripped).resolve())


def _require_open_session(session: Any | None, raw_path: str) -> Any:
    if session is None:
        raise BohriumPathError(
            f"path '{raw_path}' requires an active remote session but none is available"
        )
    if not getattr(session, "is_open", False):
        raise BohriumPathError(
            f"path '{raw_path}' requires an open remote session but the current session is not open"
        )
    return session


def resolve_input_source(
    *, raw_path: str, workdir: Path | None, session: Any | None
) -> BohriumInputSource:
    stripped = raw_path.strip()
    if _is_remote_share(stripped):
        active_session = _require_open_session(session, stripped)
        if not active_session.path_exists(stripped):
            raise BohriumPathError(f"Remote input_dir not found: {stripped}")
        if active_session.is_file(stripped):
            raise BohriumPathError(f"Remote input_dir is not a directory: {stripped}")
        return BohriumInputSource(
            kind="remote_share_dir",
            raw_path=raw_path,
            resolved_path=stripped,
        )

    local_path = Path(_normalize_local_path(stripped, workdir))
    if not local_path.exists():
        raise BohriumPathError(f"input_dir not found: {raw_path}")
    if not local_path.is_dir():
        raise BohriumPathError(f"input_dir is not a directory: {raw_path}")
    return BohriumInputSource(
        kind="local_dir",
        raw_path=raw_path,
        resolved_path=str(local_path),
    )


def resolve_download_target(
    *, raw_path: str, workdir: Path | None, session: Any | None
) -> BohriumDownloadTarget:
    stripped = raw_path.strip()
    if _is_remote_share(stripped):
        _require_open_session(session, stripped)
        return BohriumDownloadTarget(
            kind="remote_share_dir",
            raw_path=raw_path,
            resolved_path=stripped,
            staging_dir=Path(tempfile.mkdtemp(prefix="bohrium-download-")),
            publish_mode="staged_upload",
        )

    local_path = Path(_normalize_local_path(stripped, workdir))
    return BohriumDownloadTarget(
        kind="local_dir",
        raw_path=raw_path,
        resolved_path=str(local_path),
        staging_dir=local_path,
        publish_mode="direct",
    )
