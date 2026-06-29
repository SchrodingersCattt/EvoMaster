"""Helpers for validating and publishing figure artifacts from a session."""

from __future__ import annotations

import hashlib
import posixpath
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from matmaster.types.figures import (
    FigureDescriptor,
    FigureUploadConfig,
)
from matmaster.types.session import Session

_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_FIGURE_BYTES = 10 * 1024 * 1024
_DOWNLOAD_ATTEMPTS = 2
_UPLOAD_ATTEMPTS = 3
_UPLOAD_RETRY_BACKOFF_SECONDS = 0.01
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
        super().__init__(reason, detail)

    def __str__(self) -> str:
        return f"{self.reason}:{self.detail}" if self.detail else self.reason


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
        raw_path if posixpath.isabs(raw_path) else posixpath.join(str(root), raw_path)
    )
    resolved = PurePosixPath(posixpath.normpath(candidate))
    if not resolved.is_relative_to(root):
        return None
    return str(resolved)


def build_figure_id(*, output_path: str) -> str:
    """Base figure_id derived from the filename stem alone (no content hash).

    Charset limited to [A-Za-z0-9._-]; other runs fold to '-'; consecutive
    '-' merge; leading/trailing '-' stripped; empty stem -> 'figure';
    capped at 48 chars. Never contains '/', NUL, control chars, or whitespace.
    Cross-figure disambiguation in one response is handled by assign_figure_id.
    """
    stem = posixpath.splitext(posixpath.basename(output_path))[0]
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-")
    sanitized = sanitized[:_FIGURE_ID_STEM_MAX].strip("-")
    return sanitized or "figure"


def assign_figure_id(used: set[str], base: str) -> str:
    """Response-unique figure_id from ``base``, suffixing -2, -3 ... on clash.

    ``used`` is a run-scoped set of already-assigned ids; the chosen id is
    added to it. Must be called in the event-loop thread (ToolRunnerState
    contract) so this read-modify-write stays atomic between await points.
    """
    fid = base
    i = 2
    while fid in used:
        fid = f"{base}-{i}"[:_FIGURE_ID_TOTAL_MAX]
        i += 1
    used.add(fid)
    return fid


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
    content_sha256: str,
) -> str:
    digest = content_sha256[:16]
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


@dataclass(slots=True)
class PreparedFigure:
    """A validated figure ready to upload (no upload, no id assigned yet)."""

    image_bytes: bytes
    content_sha256: str
    resolved_path: str
    output_path: str
    caption: str


@dataclass(slots=True)
class FigurePrepareResult:
    prepared: PreparedFigure | None
    failure_reason: str | None
    guidance: str | None = None


@dataclass(slots=True)
class FigurePublishResult:
    figure: FigureDescriptor | None
    failure_reason: str | None
    guidance: str | None = None


def _declared_failure_guidance(reason: str, output_path: str) -> str:
    table = {
        "outside_workspace": (
            f"Expected image inside the workspace: {output_path}\n"
            "Provide an output_path that is absolute and inside the workspace."
        ),
        "file_not_found": (
            f"Expected image: {output_path}\n"
            "No file exists at this path. Generate the image first (e.g. with "
            "Bash), then attach it, or fix output_path to point at an existing image."
        ),
        "not_a_file": (
            f"Path is not a regular file: {output_path}\n"
            "Point output_path at an image file, not a directory."
        ),
        "unsupported_format": (
            f"Unsupported image format: {output_path}\n"
            "Use one of: .png, .jpg, .jpeg, .webp."
        ),
        "image_header_mismatch": (
            f"File contents are not a valid image or do not match the extension: "
            f"{output_path}\n"
            "Re-export the figure in a supported image format."
        ),
        "figure_too_large": (
            f"Image exceeds the size limit: {output_path}\n"
            "Reduce resolution or file size and retry."
        ),
        "download_failed": (
            f"Could not read the image from the session: {output_path}\n"
            "Retry AttachFigure; if it persists the session storage may be unavailable."
        ),
        "upload_failed": (
            f"Image was read but upload failed: {output_path}\n"
            "Retry AttachFigure; if it persists the asset backend may be unavailable."
        ),
    }
    return table.get(reason, f"Figure attachment failed for {output_path}.")


def prepare_declared_figure(
    *,
    session: Session,
    workdir: str,
    output_path: str,
    caption: str,
) -> FigurePrepareResult:
    """Resolve -> exists -> is_file -> download -> validate. No id, no upload.

    Returns a FigurePrepareResult carrying a PreparedFigure (validated bytes
    and content_sha256) on success, or a stable failure_reason plus actionable
    guidance. Never raises for expected failures, and never uploads.
    """

    def _fail(reason: str) -> FigurePrepareResult:
        return FigurePrepareResult(
            prepared=None,
            failure_reason=reason,
            guidance=_declared_failure_guidance(reason, output_path),
        )

    resolved = resolve_workspace_output_path(raw_path=output_path, workdir=workdir)
    if resolved is None:
        return _fail("outside_workspace")
    if not session.path_exists(resolved):
        return _fail("file_not_found")
    if not session.is_file(resolved):
        return _fail("not_a_file")

    try:
        payload = _download_with_retry(session=session, path=resolved)
    except Exception:
        return _fail("download_failed")

    try:
        _validate_image_bytes(payload=payload, path=resolved)
    except FigureValidationError as exc:
        return _fail(exc.reason)

    content_sha256 = hashlib.sha256(payload).hexdigest()
    return FigurePrepareResult(
        prepared=PreparedFigure(
            image_bytes=payload,
            content_sha256=content_sha256,
            resolved_path=resolved,
            output_path=output_path,
            caption=caption,
        ),
        failure_reason=None,
    )


def publish_prepared_figure(
    *,
    prepared: PreparedFigure,
    figure_id: str,
    upload_config: FigureUploadConfig,
    tool_call_id: str,
) -> FigurePublishResult:
    """Upload an already-prepared figure under ``figure_id`` -> descriptor.

    The asset key is content-addressed (digest segment), so a retried or
    partial batch re-upload is idempotent.
    """
    try:
        asset_key = _build_asset_key(
            upload_config=upload_config,
            tool_call_id=tool_call_id,
            figure_id=figure_id,
            source_path=prepared.resolved_path,
            content_sha256=prepared.content_sha256,
        )
        asset_url = _upload_with_retry(
            upload_bytes=upload_config.upload_bytes,
            payload=prepared.image_bytes,
            asset_key=asset_key,
        )
    except Exception:
        return FigurePublishResult(
            figure=None,
            failure_reason="upload_failed",
            guidance=_declared_failure_guidance("upload_failed", prepared.output_path),
        )

    return FigurePublishResult(
        figure=FigureDescriptor(
            figure_id=figure_id,
            asset_url=asset_url,
            caption=prepared.caption,
            source_tool_call_id=tool_call_id,
            remote_path=prepared.resolved_path,
        ),
        failure_reason=None,
    )
