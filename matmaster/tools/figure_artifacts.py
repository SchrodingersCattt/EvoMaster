"""Helpers for collecting generated figure artifacts from a session."""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError

from matmaster.types.figures import (
    FigureDescriptor,
    FigureManifestEntry,
    FigureUploadConfig,
)
from matmaster.types.session import Session

logger = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_FIGURE_BYTES = 10 * 1024 * 1024
_DOWNLOAD_ATTEMPTS = 2
_UPLOAD_ATTEMPTS = 3
_UPLOAD_RETRY_BACKOFF_SECONDS = 0.01
_SYMLINK_EXISTS_MARKER = "FIGURE_SYMLINK_EXISTS"
_SYMLINK_EXISTS_EXIT_CODE = 73
_FIGURE_ID_MAX_DISPLAY_CHARS = 64
_FIGURE_ID_STEM_MAX = 48
_FIGURE_ID_TOTAL_MAX = 64


class FigureValidationError(ValueError):
    """Image validation failure carrying a stable classification reason.

    Subclasses ValueError so existing callers that catch ValueError keep
    working; new callers read ``.reason`` for stable classification.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}:{detail}" if detail else reason)


def _format_figure_id_for_diagnostic(figure_id: str) -> str:
    return repr(figure_id[:_FIGURE_ID_MAX_DISPLAY_CHARS])


def _figure_id_has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def resolve_workspace_output_path(
    *,
    raw_path: str,
    workdir: str | PurePosixPath,
) -> str | None:
    """Resolve a declared output path against the workspace root.

    Returns the normalized absolute path if it stays inside ``workdir``,
    or None if it escapes. Containment is lexical (no symlink resolution),
    matching WriteTool's boundary model. Unlike resolve_safe_path, an
    escape returns None (deny) rather than silently falling back to workdir.
    """
    root = PurePosixPath(posixpath.normpath(str(workdir)))
    candidate = (
        raw_path
        if posixpath.isabs(raw_path)
        else posixpath.join(str(root), raw_path)
    )
    resolved = PurePosixPath(posixpath.normpath(candidate))
    if not resolved.is_relative_to(root):
        return None
    return str(resolved)


def build_figure_id(*, output_path: str, image_bytes: bytes) -> str:
    """Stable, sanitized figure_id: sanitized stem + sha256(bytes)[:12].

    Charset limited to [A-Za-z0-9._-]; other runs fold to '-'; consecutive
    '-' merge; leading/trailing '-' stripped; empty stem -> 'figure';
    stem capped at 48 chars, total capped at 64. Never contains '/', NUL,
    control chars, or whitespace.
    """
    stem = posixpath.splitext(posixpath.basename(output_path))[0]
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-")
    sanitized = sanitized[:_FIGURE_ID_STEM_MAX].strip("-")
    if not sanitized:
        sanitized = "figure"
    digest = hashlib.sha256(image_bytes).hexdigest()[:12]
    return f"{sanitized}-{digest}"[:_FIGURE_ID_TOTAL_MAX]


@dataclass(slots=True)
class FigureCollectionResult:
    figures: list[FigureDescriptor] = field(default_factory=list)
    failure_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ManifestLoadResult:
    entries: list[tuple[FigureManifestEntry, str]] | None
    warning: str | None = None


def build_figure_env(workdir: str, tool_call_id: str) -> tuple[str, str]:
    """Return the scoped artifact directory and manifest path for one tool call."""

    base_dir = posixpath.join(workdir, ".matmaster", "figures", tool_call_id)
    return (
        posixpath.join(base_dir, "artifacts"),
        posixpath.join(base_dir, "manifest.json"),
    )


def _link_figure_flat(
    *,
    session: Session,
    flat_dir: str,
    resolved_path: str,
    figure_id: str,
) -> None:
    """Create a flat-view symlink <flat_dir>/<figure_id><suffix> -> resolved_path.

    Diagnostics are logged only; symlink failures never affect figure
    collection. Uses an explicit [ -e ]/[ -L ] guard to reject any existing
    link_path, including dangling symlinks.
    """
    suffix = posixpath.splitext(resolved_path)[1].lower()
    link_path = posixpath.join(flat_dir, f"{figure_id}{suffix}")
    rel_target = posixpath.relpath(resolved_path, start=flat_dir)
    safe_figure_id = _format_figure_id_for_diagnostic(figure_id)

    q_flat = shlex.quote(flat_dir)
    q_link = shlex.quote(link_path)
    q_target = shlex.quote(rel_target)
    q_marker = shlex.quote(_SYMLINK_EXISTS_MARKER)

    cmd = (
        f"mkdir -p -- {q_flat} && "
        f"if [ -e {q_link} ] || [ -L {q_link} ]; then "
        f"printf '%s\\n' {q_marker} && "
        f"exit {_SYMLINK_EXISTS_EXIT_CODE}; "
        f"fi && "
        f"ln -s -- {q_target} {q_link}"
    )

    try:
        exec_result = session.exec_bash(command=cmd)
    except Exception as exc:
        logger.warning("figure_symlink_failed:%s:%s", safe_figure_id, exc)
        return

    exit_code = exec_result.get("exit_code", 0)
    if exit_code == 0:
        return

    stdout = exec_result.get("stdout", "")
    if exit_code == _SYMLINK_EXISTS_EXIT_CODE or _SYMLINK_EXISTS_MARKER in stdout:
        logger.warning("figure_symlink_exists:%s", safe_figure_id)
        return

    err = exec_result.get("stderr", "") or stdout
    snippet = err[:200].strip()
    logger.warning("figure_symlink_failed:%s:%s", safe_figure_id, snippet)


def _link_figure_into_flat_view(
    *,
    session: Session,
    artifact_dir: str,
    resolved_path: str,
    figure_id: str,
) -> None:
    flat_dir = posixpath.dirname(posixpath.dirname(posixpath.normpath(artifact_dir)))
    _link_figure_flat(
        session=session,
        flat_dir=flat_dir,
        resolved_path=resolved_path,
        figure_id=figure_id,
    )


def collect_figures_from_session(
    *,
    session: Session,
    artifact_dir: str,
    manifest_path: str,
    tool_call_id: str,
    upload_config: FigureUploadConfig,
) -> FigureCollectionResult:
    """Collect, validate, and upload figures described by a manifest."""

    result = FigureCollectionResult()
    if not session.path_exists(manifest_path):
        return result

    manifest_entries = _load_manifest(
        session=session,
        manifest_path=manifest_path,
        artifact_dir=artifact_dir,
    )
    if manifest_entries.entries is None:
        if manifest_entries.warning is not None:
            result.warnings.append(manifest_entries.warning)
        return result

    for entry, resolved_path in manifest_entries.entries:
        try:
            payload = _download_with_retry(session=session, path=resolved_path)
            _validate_image_bytes(payload=payload, path=resolved_path)
            asset_key = _build_asset_key(
                upload_config=upload_config,
                tool_call_id=tool_call_id,
                figure_id=entry.figure_id,
                source_path=resolved_path,
                payload=payload,
            )
            asset_url = _upload_with_retry(
                upload_bytes=upload_config.upload_bytes,
                payload=payload,
                asset_key=asset_key,
            )
        except Exception:
            result.failure_ids.append(entry.figure_id)
            continue

        _link_figure_into_flat_view(
            session=session,
            artifact_dir=artifact_dir,
            resolved_path=resolved_path,
            figure_id=entry.figure_id,
        )
        result.figures.append(
            FigureDescriptor(
                figure_id=entry.figure_id,
                asset_url=asset_url,
                caption=entry.caption,
                alt=entry.alt,
                importance=entry.importance,
                placement_hint=entry.placement_hint,
                source_tool_call_id=tool_call_id,
                remote_path=resolved_path,
            )
        )

    return result


def _load_manifest(
    *,
    session: Session,
    manifest_path: str,
    artifact_dir: str,
) -> _ManifestLoadResult:
    try:
        raw_manifest = session.read_file(manifest_path)
        document = json.loads(raw_manifest)
        figures = document["figures"]
        if not isinstance(figures, list):
            raise TypeError("figures must be a list")
    except (KeyError, TypeError, json.JSONDecodeError):
        return _ManifestLoadResult(
            entries=None,
            warning="invalid_manifest: malformed_or_missing_figures_list",
        )

    seen_ids: set[str] = set()
    normalized_entries: list[tuple[FigureManifestEntry, str]] = []

    for raw_entry in figures:
        try:
            entry = FigureManifestEntry.model_validate(raw_entry)
        except ValidationError:
            return _ManifestLoadResult(
                entries=None,
                warning="invalid_manifest: invalid_figure_entry",
            )

        if "/" in entry.figure_id or _figure_id_has_control_chars(entry.figure_id):
            return _ManifestLoadResult(
                entries=None,
                warning=(
                    "invalid_manifest: invalid_figure_id:"
                    f"{_format_figure_id_for_diagnostic(entry.figure_id)}"
                ),
            )

        if entry.figure_id in seen_ids:
            return _ManifestLoadResult(
                entries=None,
                warning=f"invalid_manifest: duplicate_figure_id:{entry.figure_id}",
            )
        seen_ids.add(entry.figure_id)

        resolved_path = _resolve_artifact_path(
            artifact_dir=artifact_dir,
            entry_path=entry.path,
        )
        if resolved_path is None:
            return _ManifestLoadResult(
                entries=None,
                warning=f"invalid_manifest: unsafe_path:{entry.path}",
            )

        normalized_entries.append((entry, resolved_path))

    return _ManifestLoadResult(entries=normalized_entries)


def _resolve_artifact_path(*, artifact_dir: str, entry_path: str) -> str | None:
    artifact_root = posixpath.normpath(artifact_dir)
    if posixpath.isabs(entry_path):
        candidate = posixpath.normpath(entry_path)
    else:
        candidate = posixpath.normpath(posixpath.join(artifact_root, entry_path))

    try:
        if posixpath.commonpath([artifact_root, candidate]) != artifact_root:
            return None
    except ValueError:
        return None

    return candidate


def _download_with_retry(*, session: Session, path: str) -> bytes:
    last_error: Exception | None = None
    for _ in range(_DOWNLOAD_ATTEMPTS):
        try:
            return session.download(path)
        except Exception as exc:  # pragma: no cover - exercised via call count + result
            last_error = exc
    assert last_error is not None
    raise last_error


def _validate_image_bytes(*, payload: bytes, path: str) -> None:
    suffix = posixpath.splitext(path)[1].lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise FigureValidationError("unsupported_format", suffix)
    if len(payload) > _MAX_FIGURE_BYTES:
        raise FigureValidationError("figure_too_large", str(len(payload)))
    sniffed = _sniff_image_format(payload)
    if sniffed is None:
        raise FigureValidationError("image_header_mismatch", suffix)
    if suffix in {".jpg", ".jpeg"} and sniffed == ".jpg":
        return
    if sniffed != suffix:
        raise FigureValidationError("image_header_mismatch", suffix)


def _sniff_image_format(payload: bytes) -> str | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return ".webp"
    return None


def _build_asset_key(
    *,
    upload_config: FigureUploadConfig,
    tool_call_id: str,
    figure_id: str,
    source_path: str,
    payload: bytes,
) -> str:
    digest = hashlib.sha256(payload).hexdigest()[:16]
    basename = posixpath.basename(source_path)
    parts = [
        upload_config.asset_key_prefix.strip("/"),
        _sanitize_key_segment(upload_config.session_id),
        _sanitize_key_segment(upload_config.task_id),
        _sanitize_key_segment(tool_call_id),
        _sanitize_key_segment(figure_id),
        digest,
        basename,
    ]
    return "/".join(parts)


def _sanitize_key_segment(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return sanitized or "unknown"


def _upload_with_retry(
    *,
    upload_bytes: Any,
    payload: bytes,
    asset_key: str,
) -> str:
    last_error: Exception | None = None
    for attempt in range(_UPLOAD_ATTEMPTS):
        try:
            return upload_bytes(payload, asset_key)
        except Exception as exc:  # pragma: no cover - exercised via retry counts
            last_error = exc
            if attempt < _UPLOAD_ATTEMPTS - 1:
                time.sleep(_UPLOAD_RETRY_BACKOFF_SECONDS)
    assert last_error is not None
    raise last_error
