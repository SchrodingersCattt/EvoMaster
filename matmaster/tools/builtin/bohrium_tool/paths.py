from __future__ import annotations

from pathlib import Path
from typing import Any

from matmaster.types.session import REMOTE_ACCESS_ROOTS

from .errors import BohriumPathError
from .models import BohriumDownloadTarget, BohriumInputSource


def _is_remote_share(path: str) -> bool:
    # 远端双根（/share、/personal）：精确匹配根，或根加 `/` 的后代。
    return any(
        path == root or path.startswith(root + "/") for root in REMOTE_ACCESS_ROOTS
    )


def _normalize_path(raw_path: str, workdir: Path | None) -> str:
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
            f"path '{raw_path}' requires an open remote session but the current "
            "session is not open"
        )
    return session


def _reject_local_path(kind: str, raw_path: str) -> None:
    raise BohriumPathError(
        f"{kind} must be an absolute remote path under "
        f"{' or '.join(REMOTE_ACCESS_ROOTS)}; relative or worker-local paths "
        f"are not allowed here: {raw_path}"
    )


def resolve_input_source(
    *,
    raw_path: str,
    workdir: Path | None,
    session: Any | None,
    allow_local_paths: bool = True,
) -> BohriumInputSource:
    normalized = _normalize_path(raw_path, workdir)
    if _is_remote_share(normalized):
        active_session = _require_open_session(session, normalized)
        if not active_session.path_exists(normalized):
            raise BohriumPathError(f"Remote input_dir not found: {normalized}")
        if active_session.is_file(normalized):
            raise BohriumPathError(f"Remote input_dir is not a directory: {normalized}")
        return BohriumInputSource(
            kind="remote_share_dir",
            raw_path=raw_path,
            resolved_path=normalized,
        )

    if not allow_local_paths:
        _reject_local_path("input_dir", raw_path)

    local_path = Path(normalized)
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
    *,
    raw_path: str,
    workdir: Path | None,
    session: Any | None,
    allow_local_paths: bool = True,
) -> BohriumDownloadTarget:
    normalized = _normalize_path(raw_path, workdir)
    if _is_remote_share(normalized):
        _require_open_session(session, normalized)
        return BohriumDownloadTarget(
            kind="remote_share_dir",
            raw_path=raw_path,
            resolved_path=normalized,
            staging_dir=Path(normalized),
            publish_mode="remote_direct",
        )

    if not allow_local_paths:
        _reject_local_path("result_dir", raw_path)

    local_path = Path(normalized)
    return BohriumDownloadTarget(
        kind="local_dir",
        raw_path=raw_path,
        resolved_path=str(local_path),
        staging_dir=local_path,
        publish_mode="direct",
    )
