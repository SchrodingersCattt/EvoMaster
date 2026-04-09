from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import BohriumPathMaterializationError
from .oss import upload_file_to_oss

_URL_RE = re.compile(r'https?://[^\s,\'"<>)}\]]+')


def is_local_path(value: Any) -> bool:
    if not value or not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if _URL_RE.fullmatch(stripped):
        return False
    parsed = urlparse(stripped)
    return parsed.scheme not in ("http", "https")


def workspace_path_to_local(value: str, workspace_root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (workspace_root / path).resolve()


def _is_remote_session(session: Any) -> bool:
    if session is None:
        return False
    return "Local" not in type(session).__name__


def materialize_input_path(
    value: str,
    *,
    workspace_root: Path,
    session: Any = None,
) -> str:
    if not is_local_path(value):
        return value

    workspace_root = Path(workspace_root)
    resolved = workspace_path_to_local(value, workspace_root)

    if (
        _is_remote_session(session)
        and hasattr(session, "download")
        and hasattr(session, "is_file")
    ):
        remote_path = str(resolved).replace("\\", "/")
        if not session.is_file(remote_path):
            raise BohriumPathMaterializationError(
                f"Remote input file not found: {remote_path}"
            )
        data = session.download(remote_path)
        suffix = Path(remote_path).suffix or ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            return upload_file_to_oss(
                tmp_path,
                tmp_path.parent,
                object_basename=Path(remote_path).name,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    if not resolved.exists() or not resolved.is_file():
        raise BohriumPathMaterializationError(f"Local input file not found: {resolved}")
    return upload_file_to_oss(resolved, workspace_root)
